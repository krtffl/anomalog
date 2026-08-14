"""Tests for the ingestion pipeline: router, HTTP endpoint, and Loki push."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from anomalog.ingest.router import IngestionRouter
from anomalog.storage.duckdb import DuckDBStorage


@pytest.fixture()
def duck(tmp_path):
    db = DuckDBStorage(str(tmp_path / "test.duckdb"))
    yield db
    db.close()


@pytest.fixture()
def router(duck):
    return IngestionRouter(duck)


# --- Router tests ---


async def test_router_processes_and_stores(duck, router):
    """Router parses a submitted line and flushes it to DuckDB."""
    await router.submit('{"level":"info","message":"hello world"}', "test-src", 1)

    # Process the item manually (not running the full loop)
    item = await asyncio.wait_for(router.queue.get(), timeout=2.0)
    from anomalog.parser.bridge import parse_line

    parsed = parse_line(item["raw"])
    assert parsed is not None

    log_entry = {
        "timestamp": parsed.get("timestamp"),
        "source": item["source"],
        "level": parsed.get("level"),
        "message": parsed.get("message"),
        "template_id": None,
        "fields": parsed.get("fields", {}),
        "line_number": item["line_number"],
    }
    router._batch.append(log_entry)
    await router._flush()

    # Verify stored in DuckDB (select non-timestamp columns to avoid pytz dep)
    result = duck.conn.execute("SELECT source, level, message, line_number FROM logs").fetchall()
    assert len(result) == 1
    cols = [
        desc[0]
        for desc in duck.conn.execute(
            "SELECT source, level, message, line_number FROM logs"
        ).description
    ]
    row = dict(zip(cols, result[0], strict=False))
    assert row["source"] == "test-src"
    assert row["message"] == "hello world"
    assert row["level"] == "info"


async def test_router_handles_queue_full():
    """When the queue is full, submit logs a warning and drops the line."""
    duck_mock = type("FakeDuck", (), {"insert_logs": lambda self, batch: None})()
    small_router = IngestionRouter(duck_mock)
    small_router.queue = asyncio.Queue(maxsize=2)

    await small_router.submit("line1", "src", 1)
    await small_router.submit("line2", "src", 2)
    # Third should be dropped (queue full), not raise
    await small_router.submit("line3", "src", 3)

    assert small_router.queue.qsize() == 2


# --- HTTP endpoint tests ---


@pytest.fixture()
def http_app(router):
    """Create a minimal FastAPI app with the HTTP ingest route."""
    from anomalog.ingest import http as http_mod

    http_mod.configure(router, api_keys=["test-key-123"])

    app = FastAPI()
    app.include_router(http_mod.router)
    return app


async def test_http_accepts_json_object(http_app, duck, router):
    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ingest",
            json={"message": "disk full"},
            headers={"x-api-key": "test-key-123", "x-source": "myapp"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 1
    assert router.queue.qsize() == 1


async def test_http_accepts_json_array(http_app, router):
    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ingest",
            json=[
                {"message": "line 1"},
                {"message": "line 2"},
                {"message": "line 3"},
            ],
            headers={"x-api-key": "test-key-123"},
        )
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 3
    assert router.queue.qsize() == 3


async def test_http_rejects_invalid_api_key(http_app):
    transport = ASGITransport(app=http_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/ingest",
            json={"message": "nope"},
            headers={"x-api-key": "wrong-key"},
        )
    assert resp.status_code == 401


# --- Loki endpoint tests ---


@pytest.fixture()
def loki_app(router):
    """Create a minimal FastAPI app with the Loki push route."""
    from anomalog.ingest import loki as loki_mod

    loki_mod.configure(
        router,
        label_matchers={"nginx": {"job": "nginx", "env": "prod"}},
    )

    app = FastAPI()
    app.include_router(loki_mod.router)
    return app


async def test_loki_push_accepts_matching_streams(loki_app, router):
    payload = {
        "streams": [
            {
                "stream": {"job": "nginx", "env": "prod"},
                "values": [
                    ["1679000000000000000", "GET /api/health 200"],
                    ["1679000001000000000", "POST /api/data 500"],
                ],
            }
        ]
    }
    transport = ASGITransport(app=loki_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/loki/api/v1/push",
            json=payload,
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 204
    assert router.queue.qsize() == 2


async def test_loki_push_ignores_unmatched_streams(loki_app, router):
    payload = {
        "streams": [
            {
                "stream": {"job": "postgres", "env": "staging"},
                "values": [
                    ["1679000000000000000", "LOG: checkpoint complete"],
                ],
            }
        ]
    }
    transport = ASGITransport(app=loki_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/loki/api/v1/push",
            json=payload,
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 204
    assert router.queue.qsize() == 0
