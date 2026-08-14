"""Pure-Python fallback parser for environments without the Rust extension."""

from __future__ import annotations

import json
import re


def detect_format(lines: list[str]) -> str:
    """Detect log format from sample lines."""
    if not lines:
        return "plain"

    json_count = 0
    logfmt_count = 0

    for line in lines[:10]:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                json.loads(stripped)
                json_count += 1
                continue
            except json.JSONDecodeError:
                pass
        if re.match(r"^\w+=\S+(\s+\w+=\S+)+", stripped):
            logfmt_count += 1

    total = len(lines[:10])
    if total == 0:
        return "plain"

    if json_count / total > 0.5:
        return "json"
    if logfmt_count / total > 0.5:
        return "logfmt"

    return "plain"


def parse_line(line: str, format_hint: str | None = None) -> dict | None:
    """Parse a single log line using pure Python."""
    fmt = format_hint or detect_format([line])

    if fmt == "json":
        return _parse_json(line)
    if fmt == "logfmt":
        return _parse_logfmt(line)
    return _parse_plain(line)


def parse_lines(lines: list[str], format_hint: str | None = None) -> list[dict]:
    """Parse multiple lines."""
    fmt = format_hint or detect_format(lines[:10])
    results = []
    for i, line in enumerate(lines):
        parsed = parse_line(line, fmt)
        if parsed is not None:
            parsed["line_number"] = i
            results.append(parsed)
    return results


def _parse_json(line: str) -> dict | None:
    try:
        obj = json.loads(line.strip())
    except json.JSONDecodeError:
        return None

    if not isinstance(obj, dict):
        return None

    level = None
    for key in ("level", "severity", "lvl"):
        if key in obj:
            level = str(obj.pop(key)).lower()
            break

    message = None
    for key in ("message", "msg", "log"):
        if key in obj:
            message = str(obj.pop(key))
            break

    timestamp = None
    for key in ("timestamp", "time", "ts", "@timestamp"):
        if key in obj:
            ts_val = obj.pop(key)
            timestamp = str(ts_val)
            break

    return {
        "timestamp": timestamp,
        "level": level,
        "message": message,
        "fields": obj,
        "format": "json",
        "line_number": 0,
    }


_LOGFMT_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([\S]+))')


def _parse_logfmt(line: str) -> dict | None:
    fields: dict = {}
    for match in _LOGFMT_RE.finditer(line):
        key = match.group(1)
        value = match.group(2) if match.group(2) is not None else match.group(3)
        fields[key] = value

    if not fields:
        return None

    level = fields.pop("level", fields.pop("lvl", None))
    message = fields.pop("msg", fields.pop("message", None))
    timestamp = fields.pop("time", fields.pop("ts", fields.pop("timestamp", None)))

    return {
        "timestamp": timestamp,
        "level": level,
        "message": message,
        "fields": fields,
        "format": "logfmt",
        "line_number": 0,
    }


def _parse_plain(line: str) -> dict | None:
    stripped = line.strip()
    if not stripped:
        return None
    return {
        "timestamp": None,
        "level": None,
        "message": stripped,
        "fields": {},
        "format": "plain",
        "line_number": 0,
    }
