"""Prediction model lifecycle management: train, store, load with caching."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import joblib
import structlog

from anomalog.prediction.capacity import predict, train_model

if TYPE_CHECKING:
    from anomalog.config import PredictionsConfig
    from anomalog.storage.duckdb import DuckDBStorage

logger = structlog.get_logger(__name__)

# Simple LRU cache: {(metric_name, labels_hash): (model, model_type)}
_model_cache: dict[tuple[str, str], tuple[object, str]] = {}
_MAX_CACHE_SIZE = 64


def train_and_store(
    metric_name: str,
    labels_hash: str,
    samples: list[dict],
    config: PredictionsConfig,
    duck: DuckDBStorage,
) -> dict | None:
    """Full training pipeline: train model, store in DB, generate predictions.

    Returns a prediction dict or None if training failed.
    """
    if not samples:
        logger.debug("no_samples_for_training", metric=metric_name)
        return None

    timestamps = [s["timestamp"] for s in samples]
    values = [float(s["value"]) for s in samples]

    result = train_model(timestamps, values, metric_hint=metric_name)
    if result is None:
        logger.info("insufficient_data", metric=metric_name, points=len(samples))
        return None

    model, model_type, rmse = result

    # Serialize and store model
    buf = io.BytesIO()
    joblib.dump(model, buf)
    model_blob = buf.getvalue()
    duck.save_prediction_model(metric_name, labels_hash, model_type, model_blob, rmse)

    # Update cache
    _cache_put(metric_name, labels_hash, model, model_type)

    logger.info(
        "model_trained",
        metric=metric_name,
        model_type=model_type,
        rmse=round(rmse, 4),
    )

    # Generate predictions for configured horizons
    import uuid

    for horizon in config.horizons:
        predictions = predict(model, model_type, horizon)
        pred_dict = {
            "id": str(uuid.uuid4()),
            "metric_name": metric_name,
            "labels_hash": labels_hash,
            "horizon_hours": horizon,
            "predictions_json": predictions,
            "exhaustion_time": None,
            "threshold": None,
            "predicted_at": datetime.now(UTC),
        }
        duck.insert_prediction(pred_dict)

    return {
        "metric_name": metric_name,
        "model_type": model_type,
        "rmse": rmse,
    }


def load_cached(
    metric_name: str,
    labels_hash: str,
    duck: DuckDBStorage,
) -> tuple[object, str] | None:
    """Load a model from cache or DB. Returns (model, model_type) or None."""
    key = (metric_name, labels_hash)
    if key in _model_cache:
        return _model_cache[key]

    row = duck.load_prediction_model(metric_name, labels_hash)
    if row is None:
        return None

    model = joblib.load(io.BytesIO(row["model_blob"]))
    model_type = row["model_type"]
    _cache_put(metric_name, labels_hash, model, model_type)
    return model, model_type


def _cache_put(metric_name: str, labels_hash: str, model: object, model_type: str) -> None:
    """Add to cache with simple eviction."""
    key = (metric_name, labels_hash)
    if len(_model_cache) >= _MAX_CACHE_SIZE and key not in _model_cache:
        # Evict oldest entry
        oldest = next(iter(_model_cache))
        del _model_cache[oldest]
    _model_cache[key] = (model, model_type)
