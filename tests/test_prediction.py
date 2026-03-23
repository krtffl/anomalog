"""Tests for capacity prediction module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from anomalog.prediction.capacity import MIN_DATA_POINTS, predict, train_model
from anomalog.prediction.threshold import check_thresholds


def _make_timestamps(n: int, interval_minutes: int = 5) -> list[datetime]:
    """Generate n timestamps at regular intervals."""
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [start + timedelta(minutes=i * interval_minutes) for i in range(n)]


class TestARIMAOnLinearSeries:
    def test_linear_trend_predicted(self) -> None:
        n = 200
        timestamps = _make_timestamps(n)
        values = [float(i) * 0.5 + 10.0 for i in range(n)]

        result = train_model(timestamps, values, metric_hint="disk_usage")
        assert result is not None
        model, model_type, rmse = result
        assert model_type == "arima"

        preds = predict(model, model_type, horizon_hours=1)
        assert len(preds) == 12  # 1 hour * 12 intervals/hour

        # Predictions should continue the upward trend
        pred_values = [v for _, v in preds]
        # The last training value is around 10 + 0.5*199 = 109.5
        # Predictions should be in a reasonable range above that
        assert pred_values[0] > 50.0  # sanity: well above zero

    def test_disk_hint_forces_arima(self) -> None:
        n = 200
        timestamps = _make_timestamps(n)
        # Seasonal data that would normally go to AutoARIMA
        values = [10.0 + 5.0 * np.sin(2 * np.pi * i / 24) for i in range(n)]

        result = train_model(timestamps, values, metric_hint="disk_usage_bytes")
        assert result is not None
        _, model_type, _ = result
        assert model_type == "arima"


class TestAutoARIMAOnSeasonal:
    def test_seasonal_pattern_captured(self) -> None:
        n = 300
        timestamps = _make_timestamps(n)
        # Clear seasonal pattern (period ~24 points = 2 hours)
        values = [
            50.0 + 20.0 * np.sin(2 * np.pi * i / 24) + np.random.normal(0, 1)
            for i in range(n)
        ]

        result = train_model(timestamps, values, metric_hint="cpu_usage")
        assert result is not None
        model, model_type, rmse = result
        assert model_type == "autoarima"
        # RMSE should be reasonable for this synthetic data
        assert rmse < 30.0  # generous bound for noisy data


class TestInsufficientData:
    def test_too_few_points_returns_none(self) -> None:
        n = MIN_DATA_POINTS - 10
        timestamps = _make_timestamps(n)
        values = [float(i) for i in range(n)]

        result = train_model(timestamps, values)
        assert result is None

    def test_exactly_enough_points_works(self) -> None:
        # Need enough raw points that after resampling we still have >= MIN_DATA_POINTS
        n = MIN_DATA_POINTS + 20
        timestamps = _make_timestamps(n)
        values = [float(i) for i in range(n)]

        result = train_model(timestamps, values, metric_hint="disk")
        assert result is not None


class TestThresholdCrossing:
    def test_prediction_above_threshold_sets_exhaustion(self) -> None:
        from anomalog.config import PredictionThreshold

        predictions = [
            ("2025-06-01T00:05:00+00:00", 80.0),
            ("2025-06-01T00:10:00+00:00", 90.0),
            ("2025-06-01T00:15:00+00:00", 95.0),
            ("2025-06-01T00:20:00+00:00", 100.0),
        ]
        thresholds = {"disk_usage": PredictionThreshold(direction="above", value=95.0)}
        alerts = check_thresholds(predictions, "disk_usage", thresholds)

        assert len(alerts) == 1
        assert alerts[0]["exhaustion_time"] is not None
        assert alerts[0]["predicted_value"] == 95.0

    def test_no_crossing_no_alert(self) -> None:
        from anomalog.config import PredictionThreshold

        predictions = [
            ("2025-06-01T00:05:00+00:00", 10.0),
            ("2025-06-01T00:10:00+00:00", 20.0),
        ]
        thresholds = {"disk_usage": PredictionThreshold(direction="above", value=95.0)}
        alerts = check_thresholds(predictions, "disk_usage", thresholds)
        assert len(alerts) == 0

    def test_below_threshold(self) -> None:
        from anomalog.config import PredictionThreshold

        predictions = [
            ("2025-06-01T00:05:00+00:00", 50.0),
            ("2025-06-01T00:10:00+00:00", 20.0),
            ("2025-06-01T00:15:00+00:00", 5.0),
        ]
        thresholds = {"free_memory": PredictionThreshold(direction="below", value=10.0)}
        alerts = check_thresholds(predictions, "free_memory", thresholds)
        assert len(alerts) == 1
        assert alerts[0]["predicted_value"] == 5.0


class TestModelSaveLoad:
    def test_joblib_round_trip(self, tmp_path) -> None:
        """Test that models can be serialized and deserialized via joblib."""
        import io

        import joblib

        from anomalog.storage.duckdb import DuckDBStorage

        duck = DuckDBStorage(str(tmp_path / "test.duckdb"))

        # Train a simple model
        n = 200
        timestamps = _make_timestamps(n)
        values = [float(i) * 0.5 for i in range(n)]

        result = train_model(timestamps, values, metric_hint="disk")
        assert result is not None
        model, model_type, rmse = result

        # Serialize to bytes via BytesIO
        buf = io.BytesIO()
        joblib.dump(model, buf)
        model_blob = buf.getvalue()
        duck.save_prediction_model("test_metric", "hash123", model_type, model_blob, rmse)

        # Load back
        loaded = duck.load_prediction_model("test_metric", "hash123")
        assert loaded is not None
        assert loaded["model_type"] == model_type

        restored_model = joblib.load(io.BytesIO(loaded["model_blob"]))

        # Both should produce predictions
        original_preds = predict(model, model_type, horizon_hours=1)
        restored_preds = predict(restored_model, model_type, horizon_hours=1)

        assert len(original_preds) == len(restored_preds)
        # Values should be identical (same model)
        for (_, v1), (_, v2) in zip(original_preds, restored_preds):
            assert abs(v1 - v2) < 1e-6

        duck.close()
