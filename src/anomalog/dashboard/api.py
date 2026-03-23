"""Dashboard JSON API endpoints for htmx partial updates."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/anomalies")
async def get_anomalies(
    request: Request,
    source: str | None = None,
    limit: int = 50,
) -> list[dict]:
    state = request.app.state.extra
    duck = state.get("duck")
    if not duck:
        return []
    return duck.get_recent_anomalies(source=source, limit=limit)


@router.get("/sources")
async def get_sources(request: Request) -> list[dict]:
    state = request.app.state.extra
    config = state.get("config")
    baselines = state.get("baselines", {})
    if not config:
        return []
    return [
        {
            "name": src.name,
            "method": src.method,
            "has_baseline": src.name in baselines,
            "sensitivity": src.sensitivity,
        }
        for src in config.sources
    ]
