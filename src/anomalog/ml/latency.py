"""Latency distribution shift detection via Kolmogorov-Smirnov test."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import numpy as np
from scipy.stats import ks_2samp

from anomalog.ml.baseline import BaselineModel
from anomalog.types import Anomaly, AnomalyType, Severity


def detect_latency_shift(
    current_latencies: list[float],
    baseline: BaselineModel,
    window_minutes: int = 15,
) -> Anomaly | None:
    """Detect latency distribution shift using KS-test.

    Only flags shifts toward HIGHER latency (degradation, not improvement).
    Returns an Anomaly if shift detected, None otherwise.
    """
    if baseline.latency_baseline is None or len(baseline.latency_baseline) < 10:
        return None
    if len(current_latencies) < 10:
        return None

    current = np.array(current_latencies)

    # Only flag degradation (higher latency)
    if np.mean(current) <= np.mean(baseline.latency_baseline):
        return None

    statistic, pvalue = ks_2samp(baseline.latency_baseline, current)

    if pvalue >= 0.01:
        return None

    shift_pct = (
        (np.mean(current) - np.mean(baseline.latency_baseline))
        / np.mean(baseline.latency_baseline)
        * 100
    )

    if pvalue < 0.001 and shift_pct > 50:
        score = 0.85
        severity = Severity.HIGH
    elif shift_pct > 20:
        score = 0.6
        severity = Severity.MEDIUM
    else:
        score = 0.4
        severity = Severity.LOW

    baseline_p99 = float(np.percentile(baseline.latency_baseline, 99))
    current_p99 = float(np.percentile(current, 99))

    return Anomaly(
        id=str(uuid.uuid4()),
        anomaly_type=AnomalyType.LATENCY_SHIFT,
        severity=severity,
        score=score,
        source=baseline.source,
        detected_at=datetime.now(timezone.utc),
        description=(
            f"Latency shift detected "
            f"(p99: {baseline_p99:.1f}ms -> {current_p99:.1f}ms, "
            f"shift={shift_pct:.0f}%, KS p={pvalue:.4f})"
        ),
        context={
            "baseline_p99_ms": baseline_p99,
            "current_p99_ms": current_p99,
            "ks_statistic": float(statistic),
            "ks_pvalue": float(pvalue),
            "shift_percent": float(shift_pct),
            "window_minutes": window_minutes,
        },
    )
