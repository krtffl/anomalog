"""Dashboard HTML routes using Jinja2 + htmx."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    state = request.app.state.extra
    duck = state.get("duck")
    config = state.get("config")

    anomalies = []
    sources_info = []
    if duck and config:
        anomalies = duck.get_recent_anomalies(limit=20)
        for src in config.sources:
            baseline = state.get("baselines", {}).get(src.name)
            sources_info.append({
                "name": src.name,
                "method": src.method,
                "has_baseline": baseline is not None,
                "sensitivity": src.sensitivity,
            })

    return templates.TemplateResponse(
        request,
        "index.html",
        context={
            "anomalies": anomalies,
            "sources": sources_info,
            "total_sources": len(sources_info),
        },
    )


@router.get("/source/{name}", response_class=HTMLResponse)
async def source_detail(request: Request, name: str) -> HTMLResponse:
    state = request.app.state.extra
    duck = state.get("duck")

    anomalies = duck.get_recent_anomalies(source=name, limit=50) if duck else []

    return templates.TemplateResponse(
        request,
        "source.html",
        context={
            "source_name": name,
            "anomalies": anomalies,
        },
    )


@router.get("/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request) -> HTMLResponse:
    state = request.app.state.extra
    duck = state.get("duck")
    anomalies = duck.get_recent_anomalies(limit=100) if duck else []

    return templates.TemplateResponse(
        request,
        "alerts.html",
        context={
            "anomalies": anomalies,
        },
    )
