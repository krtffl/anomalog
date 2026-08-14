"""Baseline model training orchestrator."""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import joblib
import numpy as np
import structlog
from sklearn.ensemble import IsolationForest

if TYPE_CHECKING:
    from anomalog.storage.duckdb import DuckDBStorage
    from anomalog.storage.sqlite import SQLiteStorage

logger = structlog.get_logger(__name__)

MIN_TRAINING_LINES = 1000


class BaselineModel:
    """Trained baseline for a single log source."""

    def __init__(
        self,
        source: str,
        error_rate_mean: float,
        error_rate_std: float,
        template_inventory: set[str],
        template_frequencies: dict[str, float],
        latency_baseline: np.ndarray | None,
        isolation_forest: IsolationForest | None,
        trained_at: datetime,
        lines_trained: int,
    ) -> None:
        self.source = source
        self.error_rate_mean = error_rate_mean
        self.error_rate_std = error_rate_std
        self.template_inventory = template_inventory
        self.template_frequencies = template_frequencies
        self.latency_baseline = latency_baseline
        self.isolation_forest = isolation_forest
        self.trained_at = trained_at
        self.lines_trained = lines_trained

    def save(self, path: str) -> None:
        joblib.dump(self, path)
        logger.info("baseline_saved", source=self.source, path=path)

    @staticmethod
    def load(path: str) -> BaselineModel:
        return joblib.load(path)


def train_baseline(
    source: str,
    duck: DuckDBStorage,
    sqlite: SQLiteStorage,
    training_window_hours: int = 168,
    sensitivity: float = 0.5,
    model_dir: str = "./data/models",
) -> BaselineModel | None:
    """Train a baseline model for a log source.

    Returns the trained model, or None if insufficient data.
    """
    since = datetime.now(UTC) - timedelta(hours=training_window_hours)
    logs = duck.get_recent_logs(source, since, limit=100_000)

    if len(logs) < MIN_TRAINING_LINES:
        logger.warning(
            "insufficient_training_data",
            source=source,
            lines=len(logs),
            minimum=MIN_TRAINING_LINES,
        )
        return None

    # Error rate statistics
    total = len(logs)

    # Compute per-bucket error rates for mean/std
    bucket_rates = _compute_bucket_error_rates(logs, bucket_minutes=5)
    error_rate_mean = float(np.mean(bucket_rates)) if bucket_rates else 0.0
    error_rate_std = float(np.std(bucket_rates)) if bucket_rates else 0.01

    # Template inventory
    template_ids = {log["template_id"] for log in logs if log.get("template_id")}

    # Template frequencies (count per template / total)
    template_counts: dict[str, int] = {}
    for log in logs:
        tid = log.get("template_id")
        if tid:
            template_counts[tid] = template_counts.get(tid, 0) + 1
    template_frequencies = {tid: count / total for tid, count in template_counts.items()}

    # Latency baseline (extract numeric latency values from fields)
    latencies = _extract_latencies(logs)
    latency_baseline = np.array(latencies) if latencies else None

    # Isolation forest (on feature vectors)
    isolation_forest = None
    features = _build_feature_vectors(logs, bucket_minutes=5)
    if len(features) >= 10:
        contamination = 0.001 + sensitivity * 0.049  # 0.001-0.05
        iso = IsolationForest(
            n_estimators=100,
            contamination=contamination,
            random_state=42,
            n_jobs=-1,
        )
        iso.fit(features)
        isolation_forest = iso

    model = BaselineModel(
        source=source,
        error_rate_mean=error_rate_mean,
        error_rate_std=error_rate_std,
        template_inventory=template_ids,
        template_frequencies=template_frequencies,
        latency_baseline=latency_baseline,
        isolation_forest=isolation_forest,
        trained_at=datetime.now(UTC),
        lines_trained=total,
    )

    # Save model
    Path(model_dir).mkdir(parents=True, exist_ok=True)
    model_path = f"{model_dir}/{source}_baseline.joblib"
    model.save(model_path)

    # Update SQLite metadata
    sqlite.set_model_metadata(source, model.trained_at, total, model_path)

    logger.info(
        "baseline_trained",
        source=source,
        lines=total,
        error_rate_mean=error_rate_mean,
        templates=len(template_ids),
        latency_samples=len(latencies),
    )

    return model


def _parse_timestamp(ts: str | datetime | None) -> datetime | None:
    """Parse a timestamp from a log entry, handling str and datetime."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _compute_bucket_error_rates(logs: list[dict], bucket_minutes: int = 5) -> list[float]:
    """Compute error rate per time bucket."""
    if not logs:
        return []

    buckets: dict[int, tuple[int, int]] = {}  # bucket_key -> (total, errors)
    for log in logs:
        ts = _parse_timestamp(log.get("timestamp"))
        if ts is None:
            continue

        bucket_key = int(ts.timestamp()) // (bucket_minutes * 60)
        total, errors = buckets.get(bucket_key, (0, 0))
        is_error = log.get("level") in ("error", "fatal")
        buckets[bucket_key] = (total + 1, errors + (1 if is_error else 0))

    return [errors / total if total > 0 else 0.0 for total, errors in buckets.values()]


def _parse_fields(raw: object) -> dict | None:
    """Parse fields from a log entry, handling DuckDB's double-encoded JSON."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    # DuckDB JSON columns double-encode: the first parse yields a string
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed)
        except (json.JSONDecodeError, TypeError):
            return None
    return parsed if isinstance(parsed, dict) else None


def _extract_latencies(logs: list[dict]) -> list[float]:
    """Extract numeric latency values from log fields."""
    latencies: list[float] = []
    latency_keys = {"latency", "duration", "response_time", "elapsed", "request_time"}
    for log in logs:
        fields = _parse_fields(log.get("fields"))
        if not fields:
            continue
        for key in latency_keys:
            if key in fields:
                try:
                    val = float(fields[key])
                    if val >= 0:
                        latencies.append(val)
                except (ValueError, TypeError):
                    pass
                break
    return latencies


def _build_feature_vectors(logs: list[dict], bucket_minutes: int = 5) -> np.ndarray:
    """Build feature vectors for isolation forest training.

    Features per time bucket: [error_rate, log_count, unique_templates, mean_latency]
    """
    buckets: dict[int, dict] = {}
    for log in logs:
        ts = _parse_timestamp(log.get("timestamp"))
        if ts is None:
            continue

        bucket_key = int(ts.timestamp()) // (bucket_minutes * 60)

        if bucket_key not in buckets:
            buckets[bucket_key] = {
                "total": 0,
                "errors": 0,
                "templates": set(),
                "latencies": [],
            }

        b = buckets[bucket_key]
        b["total"] += 1
        if log.get("level") in ("error", "fatal"):
            b["errors"] += 1
        if log.get("template_id"):
            b["templates"].add(log["template_id"])

        # Extract latency
        fields = _parse_fields(log.get("fields"))
        if fields:
            for key in ("latency", "duration", "response_time", "elapsed"):
                if key in fields:
                    with contextlib.suppress(ValueError, TypeError):
                        b["latencies"].append(float(fields[key]))
                    break

    if not buckets:
        return np.empty((0, 4))

    features = []
    for b in buckets.values():
        total = b["total"]
        error_rate = b["errors"] / total if total > 0 else 0.0
        mean_lat = float(np.mean(b["latencies"])) if b["latencies"] else 0.0
        features.append([error_rate, total, len(b["templates"]), mean_lat])

    return np.array(features)
