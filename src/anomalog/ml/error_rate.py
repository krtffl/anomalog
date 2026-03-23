"""Error rate spike detection via z-score."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from anomalog.ml.baseline import BaselineModel
from anomalog.types import Anomaly, AnomalyType, Severity


def detect_error_rate_spike(
    current_error_rate: float,
    baseline: BaselineModel,
    sensitivity: float = 0.5,
    window_minutes: int = 5,
) -> Anomaly | None:
    """Detect error rate spike using z-score against baseline.

    Returns an Anomaly if spike detected, None otherwise.
    """
    if baseline.error_rate_std < 1e-10:
        # All baseline values nearly identical
        if current_error_rate > baseline.error_rate_mean + 0.01:
            z_score = 10.0
        else:
            return None
    else:
        z_score = (
            current_error_rate - baseline.error_rate_mean
        ) / baseline.error_rate_std

    threshold = 5.0 - sensitivity * 3.0  # Range: 2.0-5.0
    if z_score <= threshold:
        return None

    score = min(1.0, z_score / 10.0 + 0.2)
    severity = _score_to_severity(score)

    return Anomaly(
        id=str(uuid.uuid4()),
        anomaly_type=AnomalyType.ERROR_RATE_SPIKE,
        severity=severity,
        score=score,
        source=baseline.source,
        detected_at=datetime.now(timezone.utc),
        description=(
            f"Error rate spike detected "
            f"(z-score={z_score:.1f}, rate={current_error_rate:.3f}, "
            f"baseline={baseline.error_rate_mean:.3f})"
        ),
        context={
            "observed_rate": current_error_rate,
            "baseline_rate": baseline.error_rate_mean,
            "z_score": z_score,
            "window_minutes": window_minutes,
        },
    )


def _score_to_severity(score: float) -> Severity:
    if score >= 0.9:
        return Severity.CRITICAL
    if score >= 0.7:
        return Severity.HIGH
    if score >= 0.4:
        return Severity.MEDIUM
    return Severity.LOW
