"""Slack alert channel via incoming webhook."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from anomalog.types import Anomaly

logger = structlog.get_logger(__name__)

SEVERITY_COLOR = {
    "low": "#3498db",
    "medium": "#f39c12",
    "high": "#e67e22",
    "critical": "#e74c3c",
}


async def send_alert(anomaly: Anomaly, webhook_url: str) -> bool:
    """Send alert to Slack via incoming webhook. Returns True on success."""
    color = SEVERITY_COLOR.get(anomaly.severity.value, "#95a5a6")

    blocks = {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": (f"anomalog: {anomaly.anomaly_type.value.replace('_', ' ')}"),
                        },
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Severity:* {anomaly.severity.value}"},
                            {"type": "mrkdwn", "text": f"*Source:* {anomaly.source}"},
                            {"type": "mrkdwn", "text": f"*Score:* {anomaly.score:.2f}"},
                            {
                                "type": "mrkdwn",
                                "text": f"*Time:* {anomaly.detected_at.isoformat()}",
                            },
                        ],
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": anomaly.description},
                    },
                ],
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=blocks)
            if resp.status_code == 200:
                logger.info("slack_alert_sent", source=anomaly.source)
                return True
            logger.error("slack_alert_failed", status=resp.status_code, body=resp.text[:200])
            return False
    except httpx.HTTPError as e:
        logger.error("slack_alert_error", error=str(e))
        return False
