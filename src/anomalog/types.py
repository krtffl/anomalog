"""Shared data types for anomalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class LogLevel(StrEnum):
    TRACE = "trace"
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    FATAL = "fatal"


class AnomalyType(StrEnum):
    ERROR_RATE_SPIKE = "error_rate_spike"
    NOVEL_PATTERN = "novel_pattern"
    LATENCY_SHIFT = "latency_shift"
    FREQUENCY_DEVIATION = "frequency_deviation"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class ParsedLog:
    raw: str
    timestamp: datetime | None
    level: LogLevel | None
    message: str | None
    fields: dict[str, str | int | float | bool | None]
    format: str
    source: str
    line_number: int


@dataclass
class Anomaly:
    id: str
    anomaly_type: AnomalyType
    severity: Severity
    score: float
    source: str
    detected_at: datetime
    description: str
    context: dict  # Flexible context data
    sample_lines: list[str] = field(default_factory=list)
    alerted: bool = False


@dataclass
class MetricSample:
    name: str
    labels_hash: str
    labels: dict[str, str]
    value: float
    timestamp: datetime
    metric_type: str  # gauge, counter, histogram, summary
    source: str


@dataclass
class CapacityPrediction:
    metric_name: str
    labels_hash: str
    model_type: str  # "autoarima" or "arima"
    horizon_hours: int
    predictions: list[tuple[str, float]]  # (iso_timestamp, value)
    exhaustion_time: datetime | None
    threshold: float | None
    rmse: float
    predicted_at: datetime


@dataclass
class CorrelatedEvent:
    id: str
    log_event_id: str
    metric_event_id: str
    time_delta_sec: float
    confidence: float
    detected_at: datetime
