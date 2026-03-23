"""Tests for log-metric correlation engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anomalog.correlation.engine import find_correlations
from anomalog.correlation.scorer import compute_confidence


class TestConfidenceSameTimeSameSource:
    def test_max_confidence(self) -> None:
        """Same time, same source, max severity -> highest confidence."""
        score = compute_confidence(
            time_delta_sec=0.0,
            window_seconds=300,
            same_source=True,
            log_severity_order=3,
            metric_severity_order=3,
        )
        # 0.4 * 1.0 + 0.3 * 1.0 + 0.3 * (9/9) = 0.4 + 0.3 + 0.3 = 1.0
        assert abs(score - 1.0) < 1e-6

    def test_zero_delta_same_source_low_severity(self) -> None:
        score = compute_confidence(
            time_delta_sec=0.0,
            window_seconds=300,
            same_source=True,
            log_severity_order=1,
            metric_severity_order=1,
        )
        # 0.4 * 1.0 + 0.3 * 1.0 + 0.3 * (1/9)
        expected = 0.4 + 0.3 + 0.3 * (1.0 / 9.0)
        assert abs(score - expected) < 1e-6


class TestConfidenceEdgeOfWindow:
    def test_low_time_proximity(self) -> None:
        """At the edge of the window, time proximity should be near zero."""
        score = compute_confidence(
            time_delta_sec=299.0,
            window_seconds=300,
            same_source=True,
            log_severity_order=3,
            metric_severity_order=3,
        )
        # time_proximity = 1 - 299/300 ≈ 0.0033
        # 0.4 * 0.0033 + 0.3 * 1.0 + 0.3 * 1.0 ≈ 0.6013
        assert score < 0.7
        assert score > 0.5

    def test_exact_window_boundary(self) -> None:
        score = compute_confidence(
            time_delta_sec=300.0,
            window_seconds=300,
            same_source=True,
            log_severity_order=3,
            metric_severity_order=3,
        )
        # time_proximity = 0.0
        # 0.4 * 0.0 + 0.3 * 1.0 + 0.3 * 1.0 = 0.6
        assert abs(score - 0.6) < 1e-6


class TestConfidenceDifferentSource:
    def test_lower_score_different_source(self) -> None:
        same_source_score = compute_confidence(
            time_delta_sec=0.0,
            window_seconds=300,
            same_source=True,
            log_severity_order=2,
            metric_severity_order=2,
        )
        diff_source_score = compute_confidence(
            time_delta_sec=0.0,
            window_seconds=300,
            same_source=False,
            log_severity_order=2,
            metric_severity_order=2,
        )
        assert diff_source_score < same_source_score
        # Difference should be exactly 0.3 (source_affinity weight)
        assert abs(same_source_score - diff_source_score - 0.3) < 1e-6


class TestFindCorrelations:
    def test_known_pairs_detected(self) -> None:
        now = datetime.now(timezone.utc)
        log_anomalies = [
            {
                "id": "log-1",
                "detected_at": now,
                "source": "app-server",
                "severity": "high",
            },
        ]
        metric_anomalies = [
            {
                "id": "metric-1",
                "detected_at": now + timedelta(seconds=10),
                "source": "app-server",
                "severity": "high",
            },
        ]
        results = find_correlations(
            log_anomalies, metric_anomalies, window_seconds=300, min_confidence=0.5
        )
        assert len(results) == 1
        assert results[0]["log_event_id"] == "log-1"
        assert results[0]["metric_event_id"] == "metric-1"
        assert abs(results[0]["time_delta_sec"] - 10.0) < 0.01

    def test_multiple_pairs(self) -> None:
        now = datetime.now(timezone.utc)
        log_anomalies = [
            {"id": "log-1", "detected_at": now, "source": "app", "severity": "high"},
            {"id": "log-2", "detected_at": now + timedelta(seconds=5), "source": "app",
             "severity": "critical"},
        ]
        metric_anomalies = [
            {"id": "metric-1", "detected_at": now + timedelta(seconds=2), "source": "app",
             "severity": "high"},
        ]
        results = find_correlations(
            log_anomalies, metric_anomalies, window_seconds=300, min_confidence=0.5
        )
        assert len(results) == 2


class TestNoCorrelationOutsideWindow:
    def test_too_far_apart_not_correlated(self) -> None:
        now = datetime.now(timezone.utc)
        log_anomalies = [
            {
                "id": "log-1",
                "detected_at": now,
                "source": "app",
                "severity": "critical",
            },
        ]
        metric_anomalies = [
            {
                "id": "metric-1",
                "detected_at": now + timedelta(seconds=600),
                "source": "app",
                "severity": "critical",
            },
        ]
        results = find_correlations(
            log_anomalies, metric_anomalies, window_seconds=300, min_confidence=0.5
        )
        assert len(results) == 0

    def test_low_confidence_filtered(self) -> None:
        now = datetime.now(timezone.utc)
        log_anomalies = [
            {
                "id": "log-1",
                "detected_at": now,
                "source": "server-a",
                "severity": "low",
            },
        ]
        metric_anomalies = [
            {
                "id": "metric-1",
                "detected_at": now + timedelta(seconds=280),
                "source": "server-b",
                "severity": "low",
            },
        ]
        results = find_correlations(
            log_anomalies, metric_anomalies, window_seconds=300, min_confidence=0.5
        )
        # Very low: different source, edge of window, low severity
        assert len(results) == 0
