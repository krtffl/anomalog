"""HTTP POST ingestion endpoint for direct log submission."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request

import structlog

from anomalog.ingest.router import IngestionRouter

logger = structlog.get_logger(__name__)

router = APIRouter()

# Will be set by server.py during app initialization
_ingestion_router: IngestionRouter | None = None
_api_keys: list[str] = []


def configure(
    ingestion_router: IngestionRouter, api_keys: list[str] | None = None
) -> None:
    """Configure the HTTP ingestion endpoint with a router and optional API keys."""
    global _ingestion_router, _api_keys  # noqa: PLW0603
    _ingestion_router = ingestion_router
    _api_keys = api_keys or []


@router.post("/api/v1/ingest")
async def ingest_logs(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_source: str | None = Header(default=None),
) -> dict:
    """Ingest log entries via HTTP POST.

    Body: JSON array of log objects, or a single object.
    Each object should have at least a "message" field.
    Optional: "timestamp", "level", "source".
    """
    if _ingestion_router is None:
        raise HTTPException(status_code=503, detail="Ingestion not initialized")

    # API key auth (if configured)
    if _api_keys and x_api_key not in _api_keys:
        raise HTTPException(status_code=401, detail="Invalid API key")

    body = await request.json()

    if isinstance(body, dict):
        entries = [body]
    elif isinstance(body, list):
        entries = body
    else:
        raise HTTPException(status_code=400, detail="Expected JSON object or array")

    source = x_source or "http"
    count = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        message = entry.get("message", "")
        if not message:
            continue

        # Reconstruct as a log line for the parser
        raw_line = str(message)
        await _ingestion_router.submit(raw_line, source, count)
        count += 1

    logger.debug("http_ingest_received", source=source, count=count)
    return {"accepted": count}
