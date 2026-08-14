"""OpenTelemetry OTLP HTTP metrics receiver."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Header, HTTPException, Request, Response

if TYPE_CHECKING:
    from anomalog.storage.duckdb import DuckDBStorage

logger = structlog.get_logger(__name__)

router = APIRouter()
_duck: DuckDBStorage | None = None
_api_keys: list[str] = []


def configure(duck: DuckDBStorage, api_keys: list[str] | None = None) -> None:
    """Configure the OTel receiver with storage and optional API keys."""
    global _duck, _api_keys  # noqa: PLW0603
    _duck = duck
    _api_keys = api_keys or []


@router.post("/v1/metrics")
async def receive_metrics(
    request: Request,
    x_api_key: str | None = Header(default=None),
) -> Response:
    """Receive OTLP JSON metrics (HTTP/JSON transport).

    Expects the standard OTLP ExportMetricsServiceRequest JSON structure:
    {"resourceMetrics": [{"scopeMetrics": [{"metrics": [...]}]}]}
    """
    if _duck is None:
        raise HTTPException(status_code=503, detail="Not initialized")

    if _api_keys and x_api_key not in _api_keys:
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        body = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    # Parse OTLP JSON format
    samples: list[dict] = []
    for rm in body.get("resourceMetrics", []):
        resource_attrs: dict[str, str] = {}
        if "resource" in rm:
            for attr in rm["resource"].get("attributes", []):
                key = attr.get("key", "")
                value_obj = attr.get("value", {})
                resource_attrs[key] = str(
                    value_obj.get("stringValue", value_obj.get("intValue", ""))
                )

        source = resource_attrs.get("service.name", "otel")

        for sm in rm.get("scopeMetrics", []):
            for metric in sm.get("metrics", []):
                name = metric.get("name", "")
                if not name:
                    continue

                # Handle different metric types
                data_points: list[dict] = []
                metric_type = "gauge"
                if "gauge" in metric:
                    data_points = metric["gauge"].get("dataPoints", [])
                    metric_type = "gauge"
                elif "sum" in metric:
                    data_points = metric["sum"].get("dataPoints", [])
                    metric_type = "counter"

                for dp in data_points:
                    labels: dict[str, str] = {}
                    for attr in dp.get("attributes", []):
                        dp_key = attr.get("key", "")
                        dp_value_obj = attr.get("value", {})
                        labels[dp_key] = str(
                            dp_value_obj.get("stringValue", dp_value_obj.get("intValue", ""))
                        )

                    value = dp.get("asDouble") or dp.get("asInt", 0)
                    labels_str = json.dumps(labels, sort_keys=True)
                    labels_hash = hashlib.sha256(labels_str.encode()).hexdigest()[:16]

                    samples.append(
                        {
                            "name": name,
                            "labels_hash": labels_hash,
                            "labels": labels_str,
                            "value": float(value),
                            "timestamp": datetime.now(UTC),
                            "metric_type": metric_type,
                            "source": source,
                        }
                    )

    if samples:
        _duck.insert_metric_samples(samples)
        logger.debug("otel_metrics_received", count=len(samples))

    return Response(status_code=200)
