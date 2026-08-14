"""Tests for ML detection algorithms."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from anomalog.config import SourceConfig
from anomalog.ml.baseline import BaselineModel, train_baseline
from anomalog.ml.detector import run_detection
from anomalog.ml.error_rate import detect_error_rate_spike
from anomalog.ml.frequency import detect_frequency_deviations
from anomalog.ml.latency import detect_latency_shift
from anomalog.ml.novel import detect_novel_patterns
from anomalog.storage.duckdb import DuckDBStorage
from anomalog.storage.sqlite import SQLiteStorage
from anomalog.types import AnomalyType, Severity


def _make_baseline(**overrides) -> BaselineModel:
    """Create a BaselineModel with sensible defaults, overridable."""
    defaults = {
        "source": "test-source",
        "error_rate_mean": 0.01,
        "error_rate_std": 0.005,
        "template_inventory": {"tpl-a", "tpl-b", "tpl-c"},
        "template_frequencies": {"tpl-a": 0.5, "tpl-b": 0.3, "tpl-c": 0.2},
        "latency_baseline": np.random.default_rng(42).normal(100, 10, 500),
        "isolation_forest": None,
        "trained_at": datetime.now(UTC),
        "lines_trained": 5000,
    }
    defaults.update(overrides)
    return BaselineModel(**defaults)


# ── Error rate spike ──


class TestErrorRateSpike:
    def test_error_rate_spike_detected(self) -> None:
        baseline = _make_baseline(error_rate_mean=0.01, error_rate_std=0.005)
        # Current rate 0.5 is massive deviation from 0.01 mean
        result = detect_error_rate_spike(0.5, baseline, sensitivity=0.5)
        assert result is not None
        assert result.anomaly_type == AnomalyType.ERROR_RATE_SPIKE
        assert result.source == "test-source"
        assert result.context["observed_rate"] == 0.5
        assert result.context["z_score"] > 0

    def test_error_rate_no_spike(self) -> None:
        baseline = _make_baseline(error_rate_mean=0.01, error_rate_std=0.005)
        result = detect_error_rate_spike(0.01, baseline, sensitivity=0.5)
        assert result is None

    def test_error_rate_low_sensitivity(self) -> None:
        """sensitivity=0.0 means threshold=5.0, so requires z>5."""
        baseline = _make_baseline(error_rate_mean=0.01, error_rate_std=0.005)
        # z-score = (0.03 - 0.01) / 0.005 = 4.0, below threshold 5.0
        result = detect_error_rate_spike(0.03, baseline, sensitivity=0.0)
        assert result is None

        # z-score = (0.04 - 0.01) / 0.005 = 6.0, above threshold 5.0
        result = detect_error_rate_spike(0.04, baseline, sensitivity=0.0)
        assert result is not None

    def test_error_rate_zero_std(self) -> None:
        """When std is 0, any increase above mean+0.01 should trigger."""
        baseline = _make_baseline(error_rate_mean=0.01, error_rate_std=0.0)
        result = detect_error_rate_spike(0.05, baseline, sensitivity=0.5)
        assert result is not None
        assert result.context["z_score"] == 10.0

    def test_error_rate_severity_scales(self) -> None:
        baseline = _make_baseline(error_rate_mean=0.01, error_rate_std=0.005)
        result = detect_error_rate_spike(0.5, baseline, sensitivity=0.5)
        assert result is not None
        # z-score ~ 98, score = min(1.0, 98/10 + 0.2) = 1.0
        assert result.score == 1.0
        assert result.severity == Severity.CRITICAL


# ── Novel pattern detection ──


class TestNovelPattern:
    def test_novel_pattern_detected(self) -> None:
        baseline = _make_baseline(template_inventory={"tpl-a", "tpl-b"})
        current = {"tpl-a": 10, "tpl-x": 5}
        results = detect_novel_patterns(current, baseline)
        assert len(results) == 1
        assert results[0].anomaly_type == AnomalyType.NOVEL_PATTERN
        assert results[0].context["template_id"] == "tpl-x"
        assert results[0].context["count"] == 5

    def test_novel_pattern_below_threshold(self) -> None:
        baseline = _make_baseline(template_inventory={"tpl-a"})
        current = {"tpl-new": 1}  # Below min_occurrences=3
        results = detect_novel_patterns(current, baseline)
        assert len(results) == 0

    def test_novel_pattern_in_baseline(self) -> None:
        baseline = _make_baseline(template_inventory={"tpl-a", "tpl-b"})
        current = {"tpl-a": 100, "tpl-b": 50}
        results = detect_novel_patterns(current, baseline)
        assert len(results) == 0

    def test_novel_pattern_high_count_severity(self) -> None:
        baseline = _make_baseline(template_inventory=set())
        current = {"tpl-new": 15}
        results = detect_novel_patterns(current, baseline)
        assert len(results) == 1
        assert results[0].severity == Severity.HIGH
        assert results[0].score == 0.7

    def test_novel_pattern_low_count_severity(self) -> None:
        baseline = _make_baseline(template_inventory=set())
        current = {"tpl-new": 3}
        results = detect_novel_patterns(current, baseline)
        assert len(results) == 1
        assert results[0].severity == Severity.MEDIUM
        assert results[0].score == 0.5


# ── Latency shift detection ──


class TestLatencyShift:
    def test_latency_shift_detected(self) -> None:
        rng = np.random.default_rng(42)
        baseline = _make_baseline(latency_baseline=rng.normal(100, 10, 500))
        current = list(rng.normal(200, 20, 100))
        result = detect_latency_shift(current, baseline)
        assert result is not None
        assert result.anomaly_type == AnomalyType.LATENCY_SHIFT
        assert result.context["shift_percent"] > 50

    def test_latency_no_shift(self) -> None:
        rng = np.random.default_rng(42)
        baseline = _make_baseline(latency_baseline=rng.normal(100, 10, 500))
        # Same distribution
        current = list(rng.normal(100, 10, 100))
        result = detect_latency_shift(current, baseline)
        assert result is None

    def test_latency_improvement_ignored(self) -> None:
        rng = np.random.default_rng(42)
        baseline = _make_baseline(latency_baseline=rng.normal(200, 20, 500))
        # Current is LOWER (improvement)
        current = list(rng.normal(100, 10, 100))
        result = detect_latency_shift(current, baseline)
        assert result is None

    def test_latency_insufficient_baseline(self) -> None:
        baseline = _make_baseline(latency_baseline=np.array([1.0, 2.0]))
        result = detect_latency_shift([100.0] * 20, baseline)
        assert result is None

    def test_latency_insufficient_current(self) -> None:
        rng = np.random.default_rng(42)
        baseline = _make_baseline(latency_baseline=rng.normal(100, 10, 500))
        result = detect_latency_shift([200.0] * 5, baseline)
        assert result is None

    def test_latency_no_baseline(self) -> None:
        baseline = _make_baseline(latency_baseline=None)
        result = detect_latency_shift([100.0] * 20, baseline)
        assert result is None


# ── Frequency deviation detection ──


class TestFrequencyDeviation:
    def test_frequency_spike(self) -> None:
        baseline = _make_baseline(template_frequencies={"tpl-a": 0.1})
        # 10x baseline -> ratio 10.0 > 3.0 threshold
        current = {"tpl-a": 1.0}
        results = detect_frequency_deviations(current, baseline)
        assert len(results) == 1
        assert results[0].anomaly_type == AnomalyType.FREQUENCY_DEVIATION
        assert results[0].context["direction"] == "increase"
        assert results[0].context["deviation_factor"] == pytest.approx(10.0)

    def test_frequency_drop(self) -> None:
        baseline = _make_baseline(template_frequencies={"tpl-a": 0.5})
        # 0.1x baseline -> ratio 0.2 < 0.33 threshold
        current = {"tpl-a": 0.05}
        results = detect_frequency_deviations(current, baseline)
        assert len(results) == 1
        assert results[0].context["direction"] == "decrease"

    def test_frequency_normal(self) -> None:
        baseline = _make_baseline(template_frequencies={"tpl-a": 0.1})
        # 1.5x baseline -> within [0.33, 3.0] tolerance
        current = {"tpl-a": 0.15}
        results = detect_frequency_deviations(current, baseline)
        assert len(results) == 0

    def test_frequency_unknown_template(self) -> None:
        """Template not in baseline frequencies should be skipped."""
        baseline = _make_baseline(template_frequencies={"tpl-a": 0.1})
        current = {"tpl-unknown": 0.5}
        results = detect_frequency_deviations(current, baseline)
        assert len(results) == 0

    def test_frequency_zero_baseline(self) -> None:
        """Baseline frequency near zero should be skipped."""
        baseline = _make_baseline(template_frequencies={"tpl-a": 0.0})
        current = {"tpl-a": 0.5}
        results = detect_frequency_deviations(current, baseline)
        assert len(results) == 0


# ── Baseline training ──


class TestBaselineTraining:
    def test_baseline_training(self, tmp_path: Path) -> None:
        """Create synthetic logs in DuckDB, train baseline, verify model."""
        duck_path = str(tmp_path / "test.duckdb")
        sqlite_path = str(tmp_path / "test.sqlite")
        model_dir = str(tmp_path / "models")

        duck = DuckDBStorage(duck_path)
        sqlite = SQLiteStorage(sqlite_path)

        now = datetime.now(UTC)
        rng = np.random.default_rng(42)
        logs = []
        templates = ["tpl-a", "tpl-b", "tpl-c"]
        for i in range(1500):
            ts = now - timedelta(hours=int(rng.integers(0, 168)))
            level = rng.choice(
                ["info", "info", "info", "warn", "error"], p=[0.6, 0.15, 0.1, 0.1, 0.05]
            )
            tpl = rng.choice(templates)
            latency = float(rng.normal(100, 15))
            logs.append(
                {
                    "timestamp": ts,
                    "source": "test-svc",
                    "level": level,
                    "message": f"log message {i}",
                    "template_id": tpl,
                    "fields": json.dumps({"latency": max(0, latency)}),
                    "line_number": i,
                }
            )

        duck.insert_logs(logs)

        model = train_baseline(
            source="test-svc",
            duck=duck,
            sqlite=sqlite,
            training_window_hours=168,
            sensitivity=0.5,
            model_dir=model_dir,
        )

        assert model is not None
        assert model.source == "test-svc"
        assert model.lines_trained == 1500
        assert model.error_rate_mean >= 0
        assert model.error_rate_std >= 0
        assert len(model.template_inventory) == 3
        assert len(model.template_frequencies) == 3
        assert model.latency_baseline is not None
        assert len(model.latency_baseline) > 0
        # Isolation forest should be trained (we have enough buckets)
        assert model.isolation_forest is not None

        # Verify model was saved
        model_path = Path(model_dir) / "test-svc_baseline.joblib"
        assert model_path.exists()

        # Verify model can be loaded
        loaded = BaselineModel.load(str(model_path))
        assert loaded.source == "test-svc"
        assert loaded.lines_trained == 1500

        # Verify SQLite metadata was updated
        meta = sqlite.get_model_metadata("test-svc")
        assert meta is not None
        assert meta["lines_trained"] == 1500

        duck.close()
        sqlite.close()

    def test_baseline_insufficient_data(self, tmp_path: Path) -> None:
        """Training with < 1000 lines should return None."""
        duck_path = str(tmp_path / "test.duckdb")
        sqlite_path = str(tmp_path / "test.sqlite")

        duck = DuckDBStorage(duck_path)
        sqlite = SQLiteStorage(sqlite_path)

        now = datetime.now(UTC)
        logs = [
            {
                "timestamp": now - timedelta(hours=1),
                "source": "test-svc",
                "level": "info",
                "message": f"log {i}",
                "template_id": "tpl-a",
                "fields": "{}",
                "line_number": i,
            }
            for i in range(100)
        ]
        duck.insert_logs(logs)

        model = train_baseline(
            source="test-svc",
            duck=duck,
            sqlite=sqlite,
            model_dir=str(tmp_path / "models"),
        )
        assert model is None

        duck.close()
        sqlite.close()


# ── Detection orchestrator ──


class TestRunDetection:
    def test_run_detection_orchestrator(self, tmp_path: Path) -> None:
        """Create baseline + inject anomalous logs, verify detection."""
        duck_path = str(tmp_path / "test.duckdb")
        duck = DuckDBStorage(duck_path)

        # Create a baseline with low error rate and known templates
        baseline = _make_baseline(
            source="my-service",
            error_rate_mean=0.01,
            error_rate_std=0.005,
            template_inventory={"tpl-a", "tpl-b"},
            template_frequencies={"tpl-a": 0.7, "tpl-b": 0.3},
            latency_baseline=np.random.default_rng(42).normal(100, 10, 500),
        )

        # Insert anomalous logs: high error rate, novel template, high latency
        now = datetime.now(UTC)
        logs = []
        for i in range(200):
            logs.append(
                {
                    "timestamp": now - timedelta(seconds=i * 2),
                    "source": "my-service",
                    "level": "error",  # All errors -> 100% error rate
                    "message": f"something broke {i}",
                    "template_id": "tpl-novel",  # Novel pattern
                    "fields": json.dumps({"latency": 500.0}),  # High latency
                    "line_number": i,
                }
            )
        duck.insert_logs(logs)

        source_config = SourceConfig(
            name="my-service",
            method="file",
            path="/var/log/test.log",
            anomaly_types=[
                "error_rate_spike",
                "novel_pattern",
                "frequency_deviation",
            ],
        )

        anomalies = run_detection(
            source_config=source_config,
            baseline=baseline,
            duck=duck,
            window_minutes=10,
        )

        assert len(anomalies) > 0

        types = {a.anomaly_type for a in anomalies}
        # Should detect error rate spike (100% vs 1% baseline)
        assert AnomalyType.ERROR_RATE_SPIKE in types
        # Should detect novel pattern (tpl-novel not in baseline)
        assert AnomalyType.NOVEL_PATTERN in types

        # Verify all anomalies have required fields
        for anomaly in anomalies:
            assert anomaly.source == "my-service"
            assert anomaly.id
            assert anomaly.detected_at
            assert anomaly.description

        duck.close()

    def test_run_detection_no_logs(self, tmp_path: Path) -> None:
        """No recent logs should return empty list."""
        duck_path = str(tmp_path / "test.duckdb")
        duck = DuckDBStorage(duck_path)
        baseline = _make_baseline(source="empty-service")

        source_config = SourceConfig(
            name="empty-service",
            method="file",
            path="/var/log/test.log",
        )

        anomalies = run_detection(
            source_config=source_config,
            baseline=baseline,
            duck=duck,
            window_minutes=5,
        )
        assert anomalies == []

        duck.close()

    def test_run_detection_with_latency_shift(self, tmp_path: Path) -> None:
        """Verify latency shift detection works through orchestrator."""
        duck_path = str(tmp_path / "test.duckdb")
        duck = DuckDBStorage(duck_path)

        rng = np.random.default_rng(42)
        baseline = _make_baseline(
            source="latency-svc",
            error_rate_mean=0.01,
            error_rate_std=0.005,
            latency_baseline=rng.normal(100, 10, 500),
        )

        # Insert logs with high latency
        now = datetime.now(UTC)
        logs = []
        for i in range(100):
            logs.append(
                {
                    "timestamp": now - timedelta(seconds=i),
                    "source": "latency-svc",
                    "level": "info",
                    "message": f"request {i}",
                    "template_id": "tpl-a",
                    "fields": json.dumps({"latency": float(rng.normal(300, 30))}),
                    "line_number": i,
                }
            )
        duck.insert_logs(logs)

        source_config = SourceConfig(
            name="latency-svc",
            method="file",
            path="/var/log/test.log",
            anomaly_types=["latency_shift"],
        )

        anomalies = run_detection(
            source_config=source_config,
            baseline=baseline,
            duck=duck,
            window_minutes=5,
        )

        latency_anomalies = [a for a in anomalies if a.anomaly_type == AnomalyType.LATENCY_SHIFT]
        assert len(latency_anomalies) == 1
        assert latency_anomalies[0].context["shift_percent"] > 50

        duck.close()
