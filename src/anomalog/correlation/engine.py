"""Correlation engine: find correlated log + metric anomaly pairs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from anomalog.correlation.scorer import compute_confidence


def _get_timestamp(event: dict) -> datetime:
    """Extract timestamp from an event dict."""
    ts = event.get("detected_at") or event.get("timestamp")
    if isinstance(ts, str):
        return datetime.fromisoformat(ts)
    if isinstance(ts, datetime):
        return ts
    return datetime.now(UTC)


def _severity_order(severity: str) -> int:
    """Map severity string to numeric order (0-3)."""
    mapping = {
        "low": 1,
        "medium": 2,
        "high": 3,
        "critical": 3,
    }
    return mapping.get(severity.lower(), 1)


def find_correlations(
    log_anomalies: list[dict],
    metric_anomalies: list[dict],
    window_seconds: int,
    min_confidence: float,
) -> list[dict]:
    """Find correlated log + metric anomaly pairs within time window.

    Each anomaly dict should have at minimum:
        - id: unique identifier
        - detected_at or timestamp: when it occurred
        - source: origin source name
        - severity: severity level string

    Returns list of correlated event dicts.
    """
    results: list[dict] = []

    for log_event in log_anomalies:
        log_ts = _get_timestamp(log_event)
        log_source = log_event.get("source", "")
        log_severity = _severity_order(log_event.get("severity", "low"))

        for metric_event in metric_anomalies:
            metric_ts = _get_timestamp(metric_event)
            metric_source = metric_event.get("source", "")
            metric_severity = _severity_order(metric_event.get("severity", "low"))

            time_delta = (metric_ts - log_ts).total_seconds()

            if abs(time_delta) > window_seconds:
                continue

            same_source = log_source == metric_source

            confidence = compute_confidence(
                time_delta_sec=time_delta,
                window_seconds=window_seconds,
                same_source=same_source,
                log_severity_order=log_severity,
                metric_severity_order=metric_severity,
            )

            if confidence >= min_confidence:
                results.append(
                    {
                        "id": str(uuid.uuid4()),
                        "log_event_id": log_event["id"],
                        "metric_event_id": metric_event["id"],
                        "time_delta_sec": time_delta,
                        "confidence": confidence,
                        "detected_at": datetime.now(UTC),
                    }
                )

    return results
