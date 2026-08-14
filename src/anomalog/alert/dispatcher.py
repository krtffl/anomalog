"""Alert dispatcher: routes anomalies to configured channels with cooldown."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from anomalog.alert import alertmanager, email, slack, telegram
from anomalog.types import Anomaly, Severity

if TYPE_CHECKING:
    from anomalog.config import AlertChannelConfig
    from anomalog.storage.sqlite import SQLiteStorage

logger = structlog.get_logger(__name__)

SEVERITY_ORDER = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


def _severity_meets_minimum(anomaly_severity: Severity, min_severity: str) -> bool:
    min_sev = Severity(min_severity)
    return SEVERITY_ORDER.get(anomaly_severity, 0) >= SEVERITY_ORDER.get(min_sev, 0)


async def dispatch_alert(
    anomaly: Anomaly,
    channels: list[AlertChannelConfig],
    sqlite: SQLiteStorage,
    cooldown_minutes: int = 30,
) -> bool:
    """Dispatch an anomaly alert to all matching channels.

    Checks cooldown, filters by min_severity, dispatches in parallel.
    Returns True if at least one channel succeeded.
    """
    # Check cooldown
    template_id = anomaly.context.get("template_id")
    if sqlite.is_in_cooldown(anomaly.source, anomaly.anomaly_type.value, template_id):
        logger.debug("alert_in_cooldown", source=anomaly.source, type=anomaly.anomaly_type.value)
        return False

    # Filter channels by min_severity
    matching = [
        ch for ch in channels if _severity_meets_minimum(anomaly.severity, ch.min_severity)
    ]

    if not matching:
        return False

    # Dispatch to all matching channels in parallel
    tasks = []
    for ch in matching:
        task = _dispatch_to_channel(anomaly, ch)
        tasks.append(task)

    results = await asyncio.gather(*tasks, return_exceptions=True)
    successes = sum(1 for r in results if r is True)
    failures = sum(1 for r in results if r is not True)

    if failures > 0:
        logger.warning("alert_partial_failure", successes=successes, failures=failures)

    # Set cooldown if any channel succeeded
    if successes > 0:
        expires = datetime.now(UTC) + timedelta(minutes=cooldown_minutes)
        sqlite.set_cooldown(anomaly.source, anomaly.anomaly_type.value, expires, template_id)

    return successes > 0


async def _dispatch_to_channel(anomaly: Anomaly, channel: AlertChannelConfig) -> bool:
    """Dispatch to a single channel. Retry once on failure."""
    success = await _send(anomaly, channel)
    if not success:
        logger.info("alert_retry", channel=channel.type, source=anomaly.source)
        await asyncio.sleep(2)  # Brief wait before retry
        success = await _send(anomaly, channel)
    return success


async def _send(anomaly: Anomaly, channel: AlertChannelConfig) -> bool:
    """Send alert to a single channel."""
    match channel.type:
        case "telegram":
            if channel.telegram_bot_token and channel.telegram_chat_id:
                return await telegram.send_alert(
                    anomaly, channel.telegram_bot_token, channel.telegram_chat_id
                )
        case "email":
            if channel.smtp_host and channel.email_to:
                return await email.send_alert(
                    anomaly,
                    channel.smtp_host,
                    channel.smtp_port,
                    channel.smtp_user,
                    channel.smtp_password,
                    channel.email_to,
                )
        case "slack":
            if channel.slack_webhook_url:
                return await slack.send_alert(anomaly, channel.slack_webhook_url)
        case "alertmanager":
            if channel.alertmanager_url:
                return await alertmanager.send_alert(anomaly, channel.alertmanager_url)
        case _:
            logger.error("unknown_channel_type", type=channel.type)
    return False
