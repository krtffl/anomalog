"""YAML configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class SourceConfig(BaseModel):
    name: str
    method: str  # "file", "loki", "http"
    path: str | None = None
    loki_labels: dict[str, str] | None = None
    sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    training_window_hours: int = 168  # 7 days
    anomaly_types: list[str] = Field(
        default=["error_rate_spike", "novel_pattern", "latency_shift", "frequency_deviation"]
    )


class AlertChannelConfig(BaseModel):
    type: str  # "telegram", "slack", "email", "alertmanager"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    slack_webhook_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_to: list[str] = Field(default_factory=list)
    alertmanager_url: str | None = None
    min_severity: str = "medium"


class StorageConfig(BaseModel):
    duckdb_path: str = "./data/anomalog.duckdb"
    sqlite_path: str = "./data/anomalog.sqlite"
    retention_days: int = 30


class DashboardConfig(BaseModel):
    enabled: bool = True
    listen_host: str = "127.0.0.1"
    listen_port: int = 8701


class MetricsTargetConfig(BaseModel):
    name: str
    url: str
    scrape_interval: int = 60
    timeout: int = 10


class MetricsConfig(BaseModel):
    targets: list[MetricsTargetConfig] = Field(default_factory=list)
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)


class PredictionThreshold(BaseModel):
    direction: str = "above"  # "above" or "below"
    value: float


class PredictionsConfig(BaseModel):
    enabled: bool = False
    horizons: list[int] = Field(default=[24, 48, 168])
    retrain_interval: int = 21600  # 6 hours
    thresholds: dict[str, PredictionThreshold] = Field(default_factory=dict)


class CorrelationConfig(BaseModel):
    enabled: bool = False
    window_seconds: int = 300
    min_confidence: float = 0.5
    check_interval: int = 60


class AnomalogConfig(BaseModel):
    sources: list[SourceConfig] = Field(min_length=1)
    alerts: list[AlertChannelConfig] = Field(default_factory=list)
    storage: StorageConfig = StorageConfig()
    dashboard: DashboardConfig = DashboardConfig()
    alert_cooldown_minutes: int = 30
    log_level: str = "info"
    metrics: MetricsConfig = MetricsConfig()
    predictions: PredictionsConfig = PredictionsConfig()
    correlation: CorrelationConfig = CorrelationConfig()
    license_path: str | None = None


def load_config(path: Path) -> AnomalogConfig:
    """Load and validate configuration from a YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    return AnomalogConfig.model_validate(raw)
