"""Tests for the OpenTelemetry OTLP HTTP metrics receiver."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from anomalog.ingest import otel_receiver
from anomalog.storage.duckdb import DuckDBStorage


@pytest.fixture()
def duck(tmp_path):
    db = DuckDBStorage(str(tmp_path / "otel_test.duckdb"))
    yield db
    db.close()


@pytest.fixture()
def otel_app(duck):
    """Create a minimal FastAPI app with the OTel receiver route."""
    otel_receiver.configure(duck, api_keys=["test-otel-key"])
    app = FastAPI()
    app.include_router(otel_receiver.router)
    return app


def _make_otlp_payload(
    metric_name: str = "cpu_usage",
    value: float = 42.5,
    service_name: str = "my-service",
    metric_type: str = "gauge",
    labels: dict | None = None,
) -> dict:
    """Build a minimal OTLP ExportMetricsServiceRequest JSON payload."""
    dp_attrs = []
    if labels:
        for k, v in labels.items():
            dp_attrs.append({"key": k, "value": {"stringValue": str(v)}})

    data_key = metric_type if metric_type == "gauge" else "sum"
    return {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [{"key": "service.name", "value": {"stringValue": service_name}}]
                },
                "scopeMetrics": [
                    {
                        "metrics": [
                            {
                                "name": metric_name,
                                data_key: {
                                    "dataPoints": [
                                        {
                                            "asDouble": value,
                                            "attributes": dp_attrs,
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                ],
            }
        ]
    }


class TestValidOTLPJson:
    async def test_valid_gauge_metric(self, otel_app, duck) -> None:
        payload = _make_otlp_payload(
            metric_name="cpu_usage",
            value=72.5,
            labels={"host": "server-1"},
        )
        transport = ASGITransport(app=otel_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/metrics",
                json=payload,
                headers={"x-api-key": "test-otel-key"},
            )
        assert resp.status_code == 200

        # Verify metric stored in DuckDB
        rows = duck.conn.execute(
            "SELECT name, value, source, metric_type FROM metric_samples"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "cpu_usage"
        assert rows[0][1] == 72.5
        assert rows[0][2] == "my-service"
        assert rows[0][3] == "gauge"

    async def test_counter_metric(self, otel_app, duck) -> None:
        payload = _make_otlp_payload(
            metric_name="http_requests_total",
            value=1027.0,
            metric_type="sum",
        )
        transport = ASGITransport(app=otel_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/metrics",
                json=payload,
                headers={"x-api-key": "test-otel-key"},
            )
        assert resp.status_code == 200

        rows = duck.conn.execute(
            "SELECT metric_type FROM metric_samples WHERE name = 'http_requests_total'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "counter"

    async def test_multiple_metrics_in_single_payload(self, otel_app, duck) -> None:
        payload = {
            "resourceMetrics": [
                {
                    "resource": {
                        "attributes": [{"key": "service.name", "value": {"stringValue": "svc"}}]
                    },
                    "scopeMetrics": [
                        {
                            "metrics": [
                                {
                                    "name": "metric_a",
                                    "gauge": {"dataPoints": [{"asDouble": 1.0, "attributes": []}]},
                                },
                                {
                                    "name": "metric_b",
                                    "gauge": {"dataPoints": [{"asDouble": 2.0, "attributes": []}]},
                                },
                            ]
                        }
                    ],
                }
            ]
        }
        transport = ASGITransport(app=otel_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/metrics",
                json=payload,
                headers={"x-api-key": "test-otel-key"},
            )
        assert resp.status_code == 200

        count = duck.conn.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0]
        assert count == 2

    async def test_labels_stored_correctly(self, otel_app, duck) -> None:
        payload = _make_otlp_payload(
            metric_name="disk_usage",
            value=85.0,
            labels={"host": "web-1", "mount": "/data"},
        )
        transport = ASGITransport(app=otel_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/metrics",
                json=payload,
                headers={"x-api-key": "test-otel-key"},
            )
        assert resp.status_code == 200

        rows = duck.conn.execute("SELECT labels FROM metric_samples").fetchall()
        assert len(rows) == 1
        labels = json.loads(rows[0][0])
        assert labels["host"] == "web-1"
        assert labels["mount"] == "/data"


class TestInvalidAPIKey:
    async def test_missing_api_key_rejected(self, otel_app) -> None:
        payload = _make_otlp_payload()
        transport = ASGITransport(app=otel_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/metrics", json=payload)
        assert resp.status_code == 401

    async def test_wrong_api_key_rejected(self, otel_app) -> None:
        payload = _make_otlp_payload()
        transport = ASGITransport(app=otel_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/metrics",
                json=payload,
                headers={"x-api-key": "wrong-key"},
            )
        assert resp.status_code == 401


class TestEmptyPayload:
    async def test_empty_resource_metrics(self, otel_app, duck) -> None:
        transport = ASGITransport(app=otel_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/metrics",
                json={"resourceMetrics": []},
                headers={"x-api-key": "test-otel-key"},
            )
        assert resp.status_code == 200

        count = duck.conn.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0]
        assert count == 0

    async def test_empty_scope_metrics(self, otel_app, duck) -> None:
        transport = ASGITransport(app=otel_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/metrics",
                json={"resourceMetrics": [{"resource": {"attributes": []}, "scopeMetrics": []}]},
                headers={"x-api-key": "test-otel-key"},
            )
        assert resp.status_code == 200

        count = duck.conn.execute("SELECT COUNT(*) FROM metric_samples").fetchone()[0]
        assert count == 0


class TestMalformedJson:
    async def test_non_json_body_rejected(self, otel_app) -> None:
        transport = ASGITransport(app=otel_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/metrics",
                content=b"not json at all",
                headers={
                    "x-api-key": "test-otel-key",
                    "content-type": "application/json",
                },
            )
        assert resp.status_code == 400


class TestNoAPIKeysConfigured:
    async def test_accepts_without_key_when_no_keys_configured(self, duck) -> None:
        """When no API keys are configured, all requests pass auth."""
        otel_receiver.configure(duck, api_keys=None)
        app = FastAPI()
        app.include_router(otel_receiver.router)

        payload = _make_otlp_payload()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/v1/metrics", json=payload)
        assert resp.status_code == 200
