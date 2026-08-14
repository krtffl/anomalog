"""Novel log pattern detection via Drain template comparison."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from anomalog.types import Anomaly, AnomalyType, Severity

if TYPE_CHECKING:
    from anomalog.ml.baseline import BaselineModel


def detect_novel_patterns(
    current_templates: dict[str, int],  # template_id -> count
    baseline: BaselineModel,
    min_occurrences: int = 3,
) -> list[Anomaly]:
    """Detect log templates not seen during baseline training.

    Returns a list of Anomaly objects for novel patterns.
    """
    anomalies = []

    for template_id, count in current_templates.items():
        if template_id in baseline.template_inventory:
            continue
        if count < min_occurrences:
            continue

        score = 0.7 if count >= 10 else 0.5
        severity = Severity.HIGH if count >= 10 else Severity.MEDIUM

        anomalies.append(
            Anomaly(
                id=str(uuid.uuid4()),
                anomaly_type=AnomalyType.NOVEL_PATTERN,
                severity=severity,
                score=score,
                source=baseline.source,
                detected_at=datetime.now(UTC),
                description=(
                    f"Novel log pattern detected "
                    f"(template_id={template_id}, seen {count} times, not in baseline)"
                ),
                context={
                    "template_id": template_id,
                    "count": count,
                },
            )
        )

    return anomalies
