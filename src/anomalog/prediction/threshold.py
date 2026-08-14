"""Threshold checking for capacity predictions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anomalog.config import PredictionThreshold


def check_thresholds(
    predictions: list[tuple[str, float]],
    metric_name: str,
    thresholds_config: dict[str, PredictionThreshold],
) -> list[dict]:
    """Check predictions against configured thresholds.

    For each metric with a threshold, find the first predicted crossing
    and compute exhaustion_time.

    Returns list of alert dicts.
    """
    alerts: list[dict] = []

    threshold_cfg = thresholds_config.get(metric_name)
    if threshold_cfg is None:
        return alerts

    direction = threshold_cfg.direction
    threshold_value = threshold_cfg.value

    for ts_iso, predicted_value in predictions:
        crossed = False
        if (direction == "above" and predicted_value >= threshold_value) or (
            direction == "below" and predicted_value <= threshold_value
        ):
            crossed = True

        if crossed:
            try:
                exhaustion_time = datetime.fromisoformat(ts_iso)
            except ValueError:
                exhaustion_time = None

            alerts.append(
                {
                    "metric_name": metric_name,
                    "threshold_value": threshold_value,
                    "direction": direction,
                    "predicted_value": predicted_value,
                    "exhaustion_time": exhaustion_time,
                    "detected_at": datetime.now(UTC),
                    "description": (
                        f"Metric '{metric_name}' predicted to cross {direction} "
                        f"threshold {threshold_value} at {ts_iso} "
                        f"(predicted: {predicted_value:.2f})"
                    ),
                }
            )
            # Only report first crossing per metric
            break

    return alerts
