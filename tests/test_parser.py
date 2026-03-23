"""Tests for the parser bridge and fallback."""

from anomalog.parser.fallback import detect_format, parse_line, parse_lines


class TestFallbackDetectFormat:
    def test_detects_json(self) -> None:
        lines = [
            '{"level":"info","message":"started","ts":"2026-03-23T10:00:00Z"}',
            '{"level":"error","message":"failed","ts":"2026-03-23T10:01:00Z"}',
        ]
        assert detect_format(lines) == "json"

    def test_detects_logfmt(self) -> None:
        lines = [
            "time=2026-03-23T10:00:00Z level=info msg=started",
            "time=2026-03-23T10:01:00Z level=error msg=failed",
        ]
        assert detect_format(lines) == "logfmt"

    def test_detects_plain(self) -> None:
        lines = ["just a plain log line", "another plain line"]
        assert detect_format(lines) == "plain"

    def test_empty_lines(self) -> None:
        assert detect_format([]) == "plain"


class TestFallbackParseLine:
    def test_parse_json(self) -> None:
        line = '{"level":"error","message":"disk full","host":"srv1"}'
        result = parse_line(line, "json")
        assert result is not None
        assert result["level"] == "error"
        assert result["message"] == "disk full"
        assert result["fields"]["host"] == "srv1"
        assert result["format"] == "json"

    def test_parse_logfmt(self) -> None:
        line = 'time=2026-03-23T10:00:00Z level=warn msg="high latency" duration=250ms'
        result = parse_line(line, "logfmt")
        assert result is not None
        assert result["level"] == "warn"
        assert result["message"] == "high latency"
        assert result["fields"]["duration"] == "250ms"

    def test_parse_plain(self) -> None:
        line = "Mar 23 10:00:00 server kernel: something happened"
        result = parse_line(line, "plain")
        assert result is not None
        assert result["message"] == line.strip()

    def test_parse_invalid_json(self) -> None:
        result = parse_line("{not valid json", "json")
        assert result is None

    def test_parse_empty_line(self) -> None:
        result = parse_line("", "plain")
        assert result is None


class TestFallbackParseLines:
    def test_parse_multiple_json(self) -> None:
        lines = [
            '{"level":"info","message":"line 1"}',
            '{"level":"error","message":"line 2"}',
            "not json",
            '{"level":"warn","message":"line 4"}',
        ]
        results = parse_lines(lines, "json")
        assert len(results) == 3
        assert results[0]["message"] == "line 1"
        assert results[1]["message"] == "line 2"
        assert results[2]["message"] == "line 4"


class TestBridge:
    """Tests that verify the bridge module loads correctly."""

    def test_bridge_imports(self) -> None:
        from anomalog.parser.bridge import RUST_AVAILABLE, detect_format

        # Should work regardless of Rust availability
        result = detect_format(
            ['{"level":"info","msg":"test"}', '{"level":"error","msg":"fail"}']
        )
        assert result in ("json", "plain")  # Python fallback may return "plain"

    def test_bridge_parse_line(self) -> None:
        from anomalog.parser.bridge import parse_line

        result = parse_line('{"level":"info","message":"hello"}')
        assert result is not None
        assert result["message"] == "hello"
