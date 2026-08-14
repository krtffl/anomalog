"""Loki push API endpoint (POST /loki/api/v1/push) for Promtail compatibility."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, HTTPException, Request, Response

if TYPE_CHECKING:
    from anomalog.ingest.router import IngestionRouter

logger = structlog.get_logger(__name__)

router = APIRouter()

_ingestion_router: IngestionRouter | None = None
_label_matchers: dict[str, dict[str, str]] = {}  # source_name -> {label: value}


def configure(
    ingestion_router: IngestionRouter,
    label_matchers: dict[str, dict[str, str]],
) -> None:
    """Configure the Loki endpoint with an ingestion router and label matchers."""
    global _ingestion_router, _label_matchers  # noqa: PLW0603
    _ingestion_router = ingestion_router
    _label_matchers = label_matchers


def _match_source(stream_labels: dict[str, str]) -> str | None:
    """Match incoming stream labels against configured sources."""
    for source_name, required_labels in _label_matchers.items():
        if all(stream_labels.get(k) == v for k, v in required_labels.items()):
            return source_name
    return None


@router.post("/loki/api/v1/push")
async def loki_push(request: Request) -> Response:
    """Accept Promtail-compatible log pushes (JSON format)."""
    if _ingestion_router is None:
        raise HTTPException(status_code=503, detail="Ingestion not initialized")

    content_type = request.headers.get("content-type", "")
    body = await request.body()

    # Try JSON (most common for testing and simple setups)
    try:
        if "json" in content_type or body.startswith(b"{"):
            payload = json.loads(body)
        else:
            # For protobuf/snappy, we'd need python-snappy + protobuf parsing
            # For v1.0, support JSON only and log a warning for other formats
            logger.warning("loki_unsupported_format", content_type=content_type)
            return Response(status_code=204)
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}") from e

    count = 0
    for stream in payload.get("streams", []):
        labels = stream.get("stream", {})
        source = _match_source(labels)
        if source is None:
            logger.debug("loki_unmatched_stream", labels=labels)
            continue

        for value in stream.get("values", []):
            if len(value) >= 2:  # noqa: PLR2004
                line = str(value[1])
                if line:
                    await _ingestion_router.submit(line, source, count)
                    count += 1

    logger.debug(
        "loki_push_received",
        streams=len(payload.get("streams", [])),
        lines=count,
    )
    return Response(status_code=204)
