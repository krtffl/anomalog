"""Telegram alert channel via Bot API."""

from __future__ import annotations

import httpx
import structlog

from anomalog.types import Anomaly

logger = structlog.get_logger(__name__)

SEVERITY_EMOJI = {"low": "\U0001f535", "medium": "\U0001f7e1", "high": "\U0001f7e0", "critical": "\U0001f534"}


async def send_alert(
    anomaly: Anomaly,
    bot_token: str,
    chat_id: str,
) -> bool:
    """Send alert to Telegram. Returns True on success."""
    emoji = SEVERITY_EMOJI.get(anomaly.severity.value, "\u26aa")
    text = (
        f"{emoji} *anomalog alert*\n\n"
        f"*Type:* {anomaly.anomaly_type.value.replace('_', ' ')}\n"
        f"*Severity:* {anomaly.severity.value}\n"
        f"*Source:* {anomaly.source}\n"
        f"*Score:* {anomaly.score:.2f}\n\n"
        f"{anomaly.description}\n"
    )
    if anomaly.sample_lines:
        samples = "\n".join(f"  {line[:200]}" for line in anomaly.sample_lines[:3])
        text += f"\n*Sample lines:*\n```\n{samples}\n```"

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info(
                    "telegram_alert_sent",
                    source=anomaly.source,
                    type=anomaly.anomaly_type.value,
                )
                return True
            logger.error(
                "telegram_alert_failed", status=resp.status_code, body=resp.text[:200]
            )
            return False
    except httpx.HTTPError as e:
        logger.error("telegram_alert_error", error=str(e))
        return False
