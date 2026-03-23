"""Shared data types for anomalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class LogLevel(str, Enum):
    TRACE = "trace"
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    FATAL = "fatal"


class AnomalyType(str, Enum):
    ERROR_RATE_SPIKE = "error_rate_spike"
    NOVEL_PATTERN = "novel_pattern"
    LATENCY_SHIFT = "latency_shift"
    FREQUENCY_DEVIATION = "frequency_deviation"


class Severity(str, Enum):
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
