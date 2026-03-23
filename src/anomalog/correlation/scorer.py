"""Confidence scoring for log-metric correlations."""

from __future__ import annotations


def compute_confidence(
    time_delta_sec: float,
    window_seconds: int,
    same_source: bool,
    log_severity_order: int,
    metric_severity_order: int,
) -> float:
    """Compute confidence score for a potential log-metric correlation.

    Weights:
        - 40% time proximity (closer in time = higher score)
        - 30% source affinity (same source = 1.0, different = 0.0)
        - 30% severity product (both high severity = higher score)
    """
    time_proximity = max(0.0, 1.0 - abs(time_delta_sec) / window_seconds)
    source_affinity = 1.0 if same_source else 0.0
    max_order = 3  # critical = 3
    severity_product = (log_severity_order * metric_severity_order) / (max_order**2)
    return 0.4 * time_proximity + 0.3 * source_affinity + 0.3 * severity_product
