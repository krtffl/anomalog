"""Tests for the Rust PyO3 extension directly."""

import pytest

from anomalog.parser.bridge import RUST_AVAILABLE

pytestmark = pytest.mark.skipif(not RUST_AVAILABLE, reason="Rust extension not available")


class TestRustDetectFormat:
    def test_detects_json(self) -> None:
        from anomalog._rust import detect_format

        result = detect_format(
            ['{"level":"info","message":"hello"}', '{"level":"error","message":"fail"}']
        )
        assert result == "json"

    def test_detects_logfmt(self) -> None:
        from anomalog._rust import detect_format

        result = detect_format(["time=2026-03-23T10:00:00Z level=info msg=started"])
        assert result == "logfmt"


class TestRustParseLine:
    def test_parse_json(self) -> None:
        from anomalog._rust import parse_line

        result = parse_line('{"level":"error","message":"disk full","host":"srv1"}', None)
        assert result is not None
        assert result["level"] == "error"
        assert result["message"] == "disk full"
        assert result["fields"]["host"] == "srv1"
        assert result["format"] == "json"

    def test_parse_with_format_hint(self) -> None:
        from anomalog._rust import parse_line

        result = parse_line('{"level":"info","msg":"hello"}', "json")
        assert result is not None

    def test_parse_returns_none_on_failure(self) -> None:
        from anomalog._rust import parse_line

        result = parse_line("completely unparseable garbage @@#$", "json")
        assert result is None


class TestRustParseLines:
    def test_parse_batch(self) -> None:
        from anomalog._rust import parse_lines

        lines = [
            '{"level":"info","message":"line 1"}',
            '{"level":"error","message":"line 2"}',
            "not json at all",
            '{"level":"warn","message":"line 4"}',
        ]
        results = parse_lines(lines, "json")
        assert len(results) >= 3


class TestRustDrainTree:
    def test_drain_basic(self) -> None:
        from anomalog._rust import DrainTree

        tree = DrainTree(depth=4, similarity_threshold=0.4, max_clusters=100)

        # Process similar messages
        id1 = tree.process("GET /api/users/123 -> 200")
        id2 = tree.process("GET /api/users/456 -> 200")
        id3 = tree.process("GET /api/users/789 -> 200")

        # Same template should get same ID
        assert id1 == id2 == id3

        # Different message should get different ID
        id4 = tree.process("POST /api/login -> 401")
        assert id4 != id1

        assert tree.template_count() >= 2

    def test_drain_templates_list(self) -> None:
        from anomalog._rust import DrainTree

        tree = DrainTree()
        tree.process("Connection from 192.168.1.1")
        tree.process("Connection from 10.0.0.5")
        tree.process("Error: timeout")

        templates = tree.templates()
        assert len(templates) >= 2

        # Each template is (id, pattern, count)
        for tid, pattern, count in templates:
            assert isinstance(tid, int)
            assert isinstance(pattern, str)
            assert isinstance(count, int)
            assert count > 0

    def test_drain_novel_detection(self) -> None:
        from anomalog._rust import DrainTree

        tree = DrainTree()
        # Build baseline
        for i in range(10):
            tree.process(f"GET /api/users/{i} -> 200")

        baseline_max = tree.max_id()

        # New pattern
        tree.process("CRITICAL: database connection lost")
        new_id = tree.process("CRITICAL: database connection lost")

        assert tree.is_novel(new_id, baseline_max)
