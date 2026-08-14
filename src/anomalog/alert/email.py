"""Email alert channel via SMTP."""

from __future__ import annotations

from email.mime.text import MIMEText
from typing import TYPE_CHECKING

import aiosmtplib
import structlog

if TYPE_CHECKING:
    from anomalog.types import Anomaly

logger = structlog.get_logger(__name__)


async def send_alert(
    anomaly: Anomaly,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str | None,
    smtp_password: str | None,
    email_to: list[str],
) -> bool:
    """Send alert via email. Returns True on success."""
    subject = (
        f"[anomalog] {anomaly.severity.value.upper()}: "
        f"{anomaly.anomaly_type.value.replace('_', ' ')} on {anomaly.source}"
    )

    body = (
        f"Anomaly detected by anomalog\n\n"
        f"Type: {anomaly.anomaly_type.value}\n"
        f"Severity: {anomaly.severity.value}\n"
        f"Source: {anomaly.source}\n"
        f"Score: {anomaly.score:.2f}\n"
        f"Time: {anomaly.detected_at.isoformat()}\n\n"
        f"{anomaly.description}\n"
    )
    if anomaly.sample_lines:
        body += "\nSample lines:\n"
        for line in anomaly.sample_lines[:5]:
            body += f"  {line[:300]}\n"

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user or "anomalog@localhost"
    msg["To"] = ", ".join(email_to)

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_password,
            start_tls=smtp_port == 587,
        )
        logger.info("email_alert_sent", source=anomaly.source, recipients=len(email_to))
        return True
    except Exception as e:
        logger.error("email_alert_error", error=str(e))
        return False
