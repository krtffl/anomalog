"""Parser bridge: tries Rust (logmole-core via PyO3), falls back to Python."""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

try:
    from anomalog._rust import DrainTree as RustDrainTree
    from anomalog._rust import detect_format as _rust_detect_format
    from anomalog._rust import parse_line as _rust_parse_line
    from anomalog._rust import parse_lines as _rust_parse_lines

    RUST_AVAILABLE = True
    logger.info("rust_extension_loaded", module="anomalog._rust")
except ImportError:
    RUST_AVAILABLE = False
    logger.warning("rust_extension_unavailable", fallback="python")

if not RUST_AVAILABLE:
    from anomalog.parser.fallback import (
        detect_format as _python_detect_format,
    )
    from anomalog.parser.fallback import (
        parse_line as _python_parse_line,
    )
    from anomalog.parser.fallback import (
        parse_lines as _python_parse_lines,
    )


def detect_format(lines: list[str]) -> str:
    """Detect the log format from a sample of lines."""
    if RUST_AVAILABLE:
        return _rust_detect_format(lines)
    return _python_detect_format(lines)


def parse_line(line: str, format_hint: str | None = None) -> dict | None:
    """Parse a single log line. Returns dict or None if unparsable."""
    if RUST_AVAILABLE:
        return _rust_parse_line(line, format_hint)
    return _python_parse_line(line, format_hint)


def parse_lines(lines: list[str], format_hint: str | None = None) -> list[dict]:
    """Parse multiple log lines. Returns list of dicts for successful parses."""
    if RUST_AVAILABLE:
        return _rust_parse_lines(lines, format_hint)
    return _python_parse_lines(lines, format_hint)


def create_drain_tree(
    depth: int = 4,
    similarity_threshold: float = 0.4,
    max_clusters: int = 1000,
) -> object:
    """Create a Drain template extraction tree.

    Returns Rust DrainTree if available, otherwise None (use Drain3 fallback).
    """
    if RUST_AVAILABLE:
        return RustDrainTree(depth, similarity_threshold, max_clusters)
    return None
