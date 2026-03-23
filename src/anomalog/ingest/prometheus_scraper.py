"""Prometheus metrics scraper — pulls from /metrics endpoints."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import httpx
import structlog

from anomalog.config import MetricsTargetConfig

logger = structlog.get_logger(__name__)


def compute_labels_hash(labels: dict[str, str]) -> str:
    """Deterministic hash of metric labels."""
    sorted_labels = json.dumps(labels, sort_keys=True)
    return hashlib.sha256(sorted_labels.encode()).hexdigest()[:16]


async def scrape_target(
    target: MetricsTargetConfig,
    include_patterns: list[str],
    exclude_patterns: list[str],
) -> list[dict]:
    """Scrape a single Prometheus target. Returns list of metric sample dicts."""
    try:
        async with httpx.AsyncClient(timeout=target.timeout) as client:
            resp = await client.get(target.url)
            if resp.status_code != 200:
                logger.warning("scrape_failed", target=target.name, status=resp.status_code)
                return []
    except httpx.HTTPError as e:
        logger.warning("scrape_error", target=target.name, error=str(e))
        return []

    return parse_prometheus_text(resp.text, target.name, include_patterns, exclude_patterns)


def parse_prometheus_text(
    text: str,
    source: str,
    include_patterns: list[str],
    exclude_patterns: list[str],
) -> list[dict]:
    """Parse Prometheus exposition format text into metric samples."""
    samples: list[dict] = []
    now = datetime.now(timezone.utc)
    metric_type_map: dict[str, str] = {}

    compiled_includes = [re.compile(p) for p in include_patterns] if include_patterns else []
    compiled_excludes = [re.compile(p) for p in exclude_patterns]

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # TYPE comment
        if line.startswith("# TYPE "):
            parts = line.split()
            if len(parts) >= 4:
                metric_type_map[parts[2]] = parts[3]
            continue

        # Skip other comments
        if line.startswith("#"):
            continue

        # Parse metric line: metric_name{label1="val1",...} value [timestamp]
        name, labels, value = _parse_metric_line(line)
        if name is None:
            continue

        # Apply filters
        if compiled_includes and not any(p.search(name) for p in compiled_includes):
            continue
        if any(p.search(name) for p in compiled_excludes):
            continue

        # Determine metric type
        base_name = name.rsplit("_", 1)[0] if "_" in name else name
        metric_type = metric_type_map.get(base_name, metric_type_map.get(name, "gauge"))

        labels_hash = compute_labels_hash(labels)

        samples.append({
            "name": name,
            "labels_hash": labels_hash,
            "labels": json.dumps(labels),
            "value": value,
            "timestamp": now,
            "metric_type": metric_type,
            "source": source,
        })

    return samples


def _parse_metric_line(line: str) -> tuple[str | None, dict[str, str], float]:
    """Parse a single Prometheus metric line."""
    labels: dict[str, str] = {}

    # Check for labels
    brace_start = line.find("{")
    if brace_start != -1:
        brace_end = line.find("}")
        if brace_end == -1:
            return None, {}, 0.0
        name = line[:brace_start]
        labels_str = line[brace_start + 1 : brace_end]
        # Parse label pairs
        for pair in re.finditer(r'(\w+)="([^"]*)"', labels_str):
            labels[pair.group(1)] = pair.group(2)
        rest = line[brace_end + 1 :].strip()
        value_str = rest.split()[0] if rest else ""
    else:
        parts = line.split()
        if len(parts) < 2:
            return None, {}, 0.0
        name = parts[0]
        value_str = parts[1]

    try:
        value = float(value_str)
    except (ValueError, IndexError):
        return None, {}, 0.0

    return name, labels, value
