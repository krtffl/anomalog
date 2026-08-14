"""Detection orchestrator -- runs all enabled anomaly detection algorithms."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from anomalog.ml.baseline import BaselineModel, _extract_latencies
from anomalog.ml.error_rate import detect_error_rate_spike
from anomalog.ml.frequency import detect_frequency_deviations
from anomalog.ml.latency import detect_latency_shift
from anomalog.ml.novel import detect_novel_patterns

if TYPE_CHECKING:
    from anomalog.config import SourceConfig
    from anomalog.storage.duckdb import DuckDBStorage
    from anomalog.types import Anomaly

logger = structlog.get_logger(__name__)


def run_detection(
    source_config: SourceConfig,
    baseline: BaselineModel,
    duck: DuckDBStorage,
    window_minutes: int = 5,
) -> list[Anomaly]:
    """Run all enabled detection algorithms for a source.

    Returns list of detected anomalies.
    """
    anomalies: list[Anomaly] = []
    since = datetime.now(UTC) - timedelta(minutes=window_minutes)
    recent_logs = duck.get_recent_logs(source_config.name, since, limit=50_000)

    if not recent_logs:
        return anomalies

    enabled = set(source_config.anomaly_types)
    sensitivity = source_config.sensitivity

    # 1. Error rate spike
    if "error_rate_spike" in enabled:
        total = len(recent_logs)
        errors = sum(1 for log in recent_logs if log.get("level") in ("error", "fatal"))
        current_rate = errors / total if total > 0 else 0.0

        anomaly = detect_error_rate_spike(current_rate, baseline, sensitivity, window_minutes)
        if anomaly:
            anomaly.sample_lines = [
                log.get("message", "")
                for log in recent_logs
                if log.get("level") in ("error", "fatal")
            ][:5]
            anomalies.append(anomaly)

    # 2. Novel patterns
    if "novel_pattern" in enabled:
        template_counts: dict[str, int] = {}
        for log in recent_logs:
            tid = log.get("template_id")
            if tid:
                template_counts[tid] = template_counts.get(tid, 0) + 1

        novel = detect_novel_patterns(template_counts, baseline)
        anomalies.extend(novel)

    # 3. Latency shift
    if "latency_shift" in enabled:
        current_latencies = _extract_latencies(recent_logs)
        if current_latencies:
            anomaly = detect_latency_shift(current_latencies, baseline, window_minutes=15)
            if anomaly:
                anomalies.append(anomaly)

    # 4. Frequency deviation
    if "frequency_deviation" in enabled:
        template_counts_freq: dict[str, int] = {}
        for log in recent_logs:
            tid = log.get("template_id")
            if tid:
                template_counts_freq[tid] = template_counts_freq.get(tid, 0) + 1

        total = len(recent_logs)
        current_frequencies = (
            {tid: count / total for tid, count in template_counts_freq.items()}
            if total > 0
            else {}
        )

        freq_anomalies = detect_frequency_deviations(current_frequencies, baseline, sensitivity)
        anomalies.extend(freq_anomalies)

    if anomalies:
        logger.info(
            "anomalies_detected",
            source=source_config.name,
            count=len(anomalies),
            types=[a.anomaly_type.value for a in anomalies],
        )

    return anomalies
