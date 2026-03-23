"""Tests for Prometheus metrics scraper."""

from __future__ import annotations

from anomalog.ingest.prometheus_scraper import (
    compute_labels_hash,
    parse_prometheus_text,
)

SAMPLE_EXPOSITION = """\
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total 1027
# HELP node_cpu_seconds_total CPU time in seconds
# TYPE node_cpu_seconds_total counter
node_cpu_seconds_total{cpu="0",mode="idle"} 362810.23
node_cpu_seconds_total{cpu="0",mode="system"} 4500.12
# TYPE go_goroutines gauge
go_goroutines 42
"""


class TestParsePrometheusTextBasic:
    def test_parse_basic_metrics(self) -> None:
        samples = parse_prometheus_text(SAMPLE_EXPOSITION, "test-target", [], [])
        assert len(samples) == 4
        names = [s["name"] for s in samples]
        assert "http_requests_total" in names
        assert "go_goroutines" in names

    def test_values_parsed_correctly(self) -> None:
        samples = parse_prometheus_text(SAMPLE_EXPOSITION, "test-target", [], [])
        by_name = {s["name"]: s for s in samples if s["name"] == "go_goroutines"}
        goroutines = by_name["go_goroutines"]
        assert goroutines["value"] == 42.0

    def test_source_set(self) -> None:
        samples = parse_prometheus_text(SAMPLE_EXPOSITION, "my-source", [], [])
        assert all(s["source"] == "my-source" for s in samples)

    def test_metric_type_resolved(self) -> None:
        samples = parse_prometheus_text(SAMPLE_EXPOSITION, "test-target", [], [])
        goroutines = [s for s in samples if s["name"] == "go_goroutines"][0]
        assert goroutines["metric_type"] == "gauge"


class TestParseWithLabels:
    def test_labels_parsed_correctly(self) -> None:
        text = '# TYPE my_metric gauge\nmy_metric{env="prod",region="us"} 3.14'
        samples = parse_prometheus_text(text, "src", [], [])
        assert len(samples) == 1
        import json

        labels = json.loads(samples[0]["labels"])
        assert labels["env"] == "prod"
        assert labels["region"] == "us"

    def test_labeled_metrics_from_exposition(self) -> None:
        samples = parse_prometheus_text(SAMPLE_EXPOSITION, "test", [], [])
        cpu_samples = [s for s in samples if s["name"] == "node_cpu_seconds_total"]
        assert len(cpu_samples) == 2


class TestIncludeFilter:
    def test_only_matching_metrics_pass(self) -> None:
        samples = parse_prometheus_text(
            SAMPLE_EXPOSITION, "test", include_patterns=["go_"], exclude_patterns=[]
        )
        assert len(samples) == 1
        assert samples[0]["name"] == "go_goroutines"

    def test_no_include_passes_all(self) -> None:
        samples = parse_prometheus_text(SAMPLE_EXPOSITION, "test", [], [])
        assert len(samples) == 4


class TestExcludeFilter:
    def test_matching_metrics_excluded(self) -> None:
        samples = parse_prometheus_text(
            SAMPLE_EXPOSITION, "test", include_patterns=[], exclude_patterns=["go_"]
        )
        names = [s["name"] for s in samples]
        assert "go_goroutines" not in names
        assert len(samples) == 3

    def test_no_exclude_passes_all(self) -> None:
        samples = parse_prometheus_text(SAMPLE_EXPOSITION, "test", [], [])
        assert len(samples) == 4


class TestLabelsHash:
    def test_deterministic(self) -> None:
        labels = {"env": "prod", "region": "us"}
        h1 = compute_labels_hash(labels)
        h2 = compute_labels_hash(labels)
        assert h1 == h2

    def test_order_independent(self) -> None:
        h1 = compute_labels_hash({"a": "1", "b": "2"})
        h2 = compute_labels_hash({"b": "2", "a": "1"})
        assert h1 == h2

    def test_different_labels_different_hash(self) -> None:
        h1 = compute_labels_hash({"env": "prod"})
        h2 = compute_labels_hash({"env": "staging"})
        assert h1 != h2
