"""Prometheus Alertmanager alert channel."""

from __future__ import annotations

import httpx
import structlog

from anomalog.types import Anomaly

logger = structlog.get_logger(__name__)


async def send_alert(anomaly: Anomaly, alertmanager_url: str) -> bool:
    """Push alert to Alertmanager API. Returns True on success."""
    payload = [
        {
            "labels": {
                "alertname": f"anomalog_{anomaly.anomaly_type.value}",
                "severity": anomaly.severity.value,
                "source": anomaly.source,
            },
            "annotations": {
                "summary": anomaly.description,
                "score": str(anomaly.score),
            },
            "startsAt": anomaly.detected_at.isoformat(),
        }
    ]

    url = f"{alertmanager_url.rstrip('/')}/api/v2/alerts"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code in (200, 202):
                logger.info("alertmanager_alert_sent", source=anomaly.source)
                return True
            logger.error("alertmanager_alert_failed", status=resp.status_code)
            return False
    except httpx.HTTPError as e:
        logger.error("alertmanager_alert_error", error=str(e))
        return False
