"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from anomalog.config import AnomalogConfig
from anomalog.ingest import http as http_ingest
from anomalog.ingest import loki as loki_ingest
from anomalog.ingest.router import IngestionRouter
from anomalog.ingest.tail import start_file_tailer
from anomalog.ml.baseline import BaselineModel, train_baseline
from anomalog.ml.detector import run_detection
from anomalog.alert.dispatcher import dispatch_alert
from anomalog.storage.duckdb import DuckDBStorage
from anomalog.storage.sqlite import SQLiteStorage

logger = structlog.get_logger(__name__)


def create_app(config: AnomalogConfig) -> FastAPI:
    """Create the FastAPI application with all components wired."""

    # State containers
    state: dict = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # --- Startup ---
        duck = DuckDBStorage(config.storage.duckdb_path)
        sqlite = SQLiteStorage(config.storage.sqlite_path)
        router = IngestionRouter(duck)
        state["duck"] = duck
        state["sqlite"] = sqlite
        state["router"] = router
        state["baselines"] = {}
        state["config"] = config

        # Configure ingestion endpoints
        http_ingest.configure(router)
        label_matchers = {}
        for src in config.sources:
            if src.method == "loki" and src.loki_labels:
                label_matchers[src.name] = src.loki_labels
        loki_ingest.configure(router, label_matchers)

        # Start ingestion router task
        router_task = asyncio.create_task(router.run())

        # Start file tailers
        observers = []
        loop = asyncio.get_event_loop()
        for src in config.sources:
            if src.method == "file" and src.path:
                obs = start_file_tailer(src.name, src.path, router, sqlite, loop)
                observers.append(obs)

        # Load existing baseline models
        for src in config.sources:
            meta = sqlite.get_model_metadata(src.name)
            if meta and Path(meta["model_path"]).exists():
                try:
                    baseline = BaselineModel.load(meta["model_path"])
                    state["baselines"][src.name] = baseline
                    logger.info("baseline_loaded", source=src.name)
                except Exception:
                    logger.warning("baseline_load_failed", source=src.name)

        # Start scheduler for periodic detection + training
        scheduler = AsyncIOScheduler()

        async def detection_cycle():
            for src in config.sources:
                baseline = state["baselines"].get(src.name)
                if baseline is None:
                    continue
                anomalies = run_detection(src, baseline, duck)
                for anomaly in anomalies:
                    duck.insert_anomaly({
                        "id": anomaly.id,
                        "anomaly_type": anomaly.anomaly_type.value,
                        "severity": anomaly.severity.value,
                        "score": anomaly.score,
                        "source": anomaly.source,
                        "detected_at": anomaly.detected_at,
                        "description": anomaly.description,
                        "context": anomaly.context,
                        "sample_lines": anomaly.sample_lines,
                        "alerted": False,
                    })
                    await dispatch_alert(
                        anomaly, config.alerts, sqlite, config.alert_cooldown_minutes
                    )

        async def training_cycle():
            for src in config.sources:
                baseline = train_baseline(
                    src.name,
                    duck,
                    sqlite,
                    training_window_hours=src.training_window_hours,
                    sensitivity=src.sensitivity,
                )
                if baseline:
                    state["baselines"][src.name] = baseline

        scheduler.add_job(detection_cycle, "interval", minutes=5, id="detection")
        scheduler.add_job(training_cycle, "interval", hours=6, id="training")
        scheduler.start()
        state["scheduler"] = scheduler

        logger.info("anomalog_started", sources=len(config.sources))

        yield  # --- App running ---

        # --- Shutdown ---
        scheduler.shutdown(wait=False)
        for obs in observers:
            obs.stop()
        await router.stop()
        router_task.cancel()
        duck.close()
        sqlite.close()
        logger.info("anomalog_stopped")

    app = FastAPI(
        title="anomalog",
        description="Self-hosted ML anomaly detection for logs",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Store state on app for access from routes
    app.state.extra = state

    # Mount routes
    app.include_router(http_ingest.router)
    app.include_router(loki_ingest.router)

    # Dashboard routes (import here to avoid circular imports)
    from anomalog.dashboard.routes import router as dashboard_router
    from anomalog.dashboard.api import router as api_router

    app.include_router(dashboard_router)
    app.include_router(api_router)

    # Static files for dashboard
    static_dir = Path(__file__).parent / "dashboard" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/healthz")
    async def healthz():
        sources = len(config.sources)
        models_healthy = len(state.get("baselines", {}))
        return {"status": "ok", "sources": sources, "models_healthy": models_healthy}

    return app
