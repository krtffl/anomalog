"""Per-template frequency deviation detection via z-score."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from anomalog.ml.baseline import BaselineModel
from anomalog.types import Anomaly, AnomalyType, Severity


def detect_frequency_deviations(
    current_frequencies: dict[str, float],  # template_id -> current frequency
    baseline: BaselineModel,
    sensitivity: float = 0.5,
) -> list[Anomaly]:
    """Detect templates whose frequency deviates significantly from baseline.

    Flags both spikes (increase) and drops (disappearance).
    Returns list of Anomaly objects.
    """
    anomalies = []

    for template_id, current_freq in current_frequencies.items():
        baseline_freq = baseline.template_frequencies.get(template_id)
        if baseline_freq is None or baseline_freq < 1e-6:
            continue

        ratio = current_freq / baseline_freq

        if 0.33 <= ratio <= 3.0:
            continue

        if ratio > 3.0:
            score = min(1.0, (ratio - 3.0) / 7.0)
            direction = "increase"
        else:
            score = min(1.0, (1.0 / ratio - 3.0) / 7.0)
            direction = "decrease"

        severity = _score_to_severity(score)

        anomalies.append(
            Anomaly(
                id=str(uuid.uuid4()),
                anomaly_type=AnomalyType.FREQUENCY_DEVIATION,
                severity=severity,
                score=score,
                source=baseline.source,
                detected_at=datetime.now(timezone.utc),
                description=(
                    f"Template frequency {direction} "
                    f"({ratio:.1f}x baseline, template_id={template_id})"
                ),
                context={
                    "template_id": template_id,
                    "template_frequency_baseline": baseline_freq,
                    "template_frequency_current": current_freq,
                    "deviation_factor": ratio,
                    "direction": direction,
                },
            )
        )

    return anomalies


def _score_to_severity(score: float) -> Severity:
    if score >= 0.9:
        return Severity.CRITICAL
    if score >= 0.7:
        return Severity.HIGH
    if score >= 0.4:
        return Severity.MEDIUM
    return Severity.LOW
