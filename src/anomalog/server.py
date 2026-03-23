"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
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

        # --- Pro features ---
        pro_enabled = False
        if config.license_path:
            from anomalog.pro.license import LicenseValidator

            license_validator = LicenseValidator(config.license_path)
            state["license"] = license_validator
            pro_enabled = license_validator.is_valid
            logger.info("pro_license_checked", valid=pro_enabled)
        state["pro_enabled"] = pro_enabled

        # Configure OTel receiver if pro enabled
        if pro_enabled:
            from anomalog.ingest import otel_receiver

            otel_receiver.configure(duck)

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

        # Pro scheduler jobs
        if pro_enabled:
            # Prometheus scraping
            if config.metrics.targets:
                from anomalog.ingest.prometheus_scraper import scrape_target

                async def prometheus_scrape_cycle():
                    for target in config.metrics.targets:
                        samples = await scrape_target(
                            target,
                            config.metrics.include_patterns,
                            config.metrics.exclude_patterns,
                        )
                        if samples:
                            duck.insert_metric_samples(samples)
                            logger.debug(
                                "prometheus_scraped",
                                target=target.name,
                                samples=len(samples),
                            )

                scheduler.add_job(
                    prometheus_scrape_cycle,
                    "interval",
                    seconds=min(
                        t.scrape_interval for t in config.metrics.targets
                    ),
                    id="prometheus_scrape",
                )

            # Prediction retraining
            if config.predictions.enabled:
                from anomalog.prediction.model_manager import train_and_store

                async def prediction_retrain_cycle():
                    metric_names = duck.get_metric_names()
                    since = datetime.now(timezone.utc) - timedelta(days=7)
                    for name in metric_names:
                        samples = duck.get_recent_metrics(name, since=since)
                        if samples:
                            # Use labels_hash from first sample as representative
                            labels_hash = samples[0].get("labels_hash", "default")
                            train_and_store(
                                name, labels_hash, samples, config.predictions, duck
                            )

                scheduler.add_job(
                    prediction_retrain_cycle,
                    "interval",
                    seconds=config.predictions.retrain_interval,
                    id="prediction_retrain",
                )

            # Correlation engine
            if config.correlation.enabled:
                from anomalog.correlation.engine import find_correlations

                async def correlation_cycle():
                    since = datetime.now(timezone.utc) - timedelta(
                        seconds=config.correlation.window_seconds * 2
                    )
                    log_anomalies = duck.get_recent_anomalies(limit=100)
                    # Filter to recent ones
                    recent_log = [
                        a for a in log_anomalies
                        if isinstance(a.get("detected_at"), datetime)
                        and a["detected_at"] >= since
                    ]
                    metric_anomalies = duck.get_recent_anomalies(limit=100)
                    recent_metric = [
                        a for a in metric_anomalies
                        if isinstance(a.get("detected_at"), datetime)
                        and a["detected_at"] >= since
                    ]

                    if recent_log and recent_metric:
                        results = find_correlations(
                            recent_log,
                            recent_metric,
                            window_seconds=config.correlation.window_seconds,
                            min_confidence=config.correlation.min_confidence,
                        )
                        for event in results:
                            duck.insert_correlated_event(event)

                scheduler.add_job(
                    correlation_cycle,
                    "interval",
                    seconds=config.correlation.check_interval,
                    id="correlation_check",
                )

        scheduler.start()
        state["scheduler"] = scheduler

        logger.info(
            "anomalog_started",
            sources=len(config.sources),
            pro_enabled=pro_enabled,
        )

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

    # OTel receiver (always mount, handler checks pro_enabled at runtime)
    from anomalog.ingest import otel_receiver

    app.include_router(otel_receiver.router)

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
        pro = state.get("pro_enabled", False)
        return {
            "status": "ok",
            "sources": sources,
            "models_healthy": models_healthy,
            "pro_enabled": pro,
        }

    return app
