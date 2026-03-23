"""CLI entry point for anomalog."""

from __future__ import annotations

from pathlib import Path

import click
import structlog
import uvicorn

from anomalog.config import load_config


@click.group()
@click.version_option(version="0.1.0")
def main() -> None:
    """anomalog: Self-hosted ML anomaly detection for your logs."""


@main.command()
@click.option(
    "--config", "-c", required=True, type=click.Path(exists=True), help="Path to config.yaml"
)
@click.option("--host", default=None, help="Override dashboard listen host")
@click.option("--port", default=None, type=int, help="Override dashboard listen port")
def serve(config: str, host: str | None, port: int | None) -> None:
    """Start the anomalog server."""
    cfg = load_config(Path(config))

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(structlog, cfg.log_level.upper(), structlog.INFO)
        ),
    )

    from anomalog.server import create_app

    app = create_app(cfg)

    listen_host = host or cfg.dashboard.listen_host
    listen_port = port or cfg.dashboard.listen_port

    uvicorn.run(
        app,
        host=listen_host,
        port=listen_port,
        log_level=cfg.log_level,
    )


@main.command()
@click.option(
    "--config", "-c", required=True, type=click.Path(exists=True), help="Path to config.yaml"
)
def check_config(config: str) -> None:
    """Validate configuration file."""
    try:
        cfg = load_config(Path(config))
        click.echo(
            f"Configuration valid: {len(cfg.sources)} source(s), {len(cfg.alerts)} alert channel(s)"
        )
    except Exception as e:
        click.echo(f"Configuration error: {e}", err=True)
        raise SystemExit(1)


@main.command()
@click.option(
    "--config", "-c", required=True, type=click.Path(exists=True), help="Path to config.yaml"
)
@click.option("--source", "-s", required=True, help="Source name to train")
def train(config: str, source: str) -> None:
    """Manually trigger baseline training for a source."""
    cfg = load_config(Path(config))
    source_cfg = next((s for s in cfg.sources if s.name == source), None)
    if source_cfg is None:
        click.echo(f"Source '{source}' not found in config", err=True)
        raise SystemExit(1)

    from anomalog.ml.baseline import train_baseline
    from anomalog.storage.duckdb import DuckDBStorage
    from anomalog.storage.sqlite import SQLiteStorage

    duck = DuckDBStorage(cfg.storage.duckdb_path)
    sqlite = SQLiteStorage(cfg.storage.sqlite_path)

    try:
        baseline = train_baseline(
            source,
            duck,
            sqlite,
            training_window_hours=source_cfg.training_window_hours,
            sensitivity=source_cfg.sensitivity,
        )

        if baseline:
            click.echo(
                f"Baseline trained: {baseline.lines_trained} lines, "
                f"{len(baseline.template_inventory)} templates"
            )
        else:
            click.echo("Training failed: insufficient data", err=True)
            raise SystemExit(1)
    finally:
        duck.close()
        sqlite.close()
