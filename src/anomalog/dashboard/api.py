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


@router.get("/predictions")
async def get_predictions(
    request: Request, metric_name: str | None = None, limit: int = 50
) -> list[dict]:
    state = request.app.state.extra
    duck = state.get("duck")
    if not duck or not state.get("pro_enabled", False):
        return []
    return duck.get_predictions(metric_name=metric_name, limit=limit)


@router.get("/correlated-events")
async def get_correlated_events(request: Request, limit: int = 50) -> list[dict]:
    state = request.app.state.extra
    duck = state.get("duck")
    if not duck or not state.get("pro_enabled", False):
        return []
    return duck.get_correlated_events(limit=limit)


@router.get("/metrics")
async def get_metrics(request: Request) -> list[str]:
    state = request.app.state.extra
    duck = state.get("duck")
    if not duck or not state.get("pro_enabled", False):
        return []
    return duck.get_metric_names()
