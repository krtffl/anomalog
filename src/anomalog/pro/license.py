"""License validation for anomalog-pro features."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Embedded HMAC key -- "honest user" protection, not cryptographic security
_HMAC_KEY = b"anomalog-pro-license-v1-2026"
_GRACE_DAYS = 7


class LicenseValidator:
    """Validate and query a license file for pro feature access."""

    def __init__(self, license_path: str | None = None) -> None:
        self.is_valid = False
        self.features: list[str] = []
        self.expires_at: datetime | None = None
        self.in_grace_period = False
        self._load(license_path)

    def _load(self, path: str | None) -> None:
        if path is None:
            logger.info("no_license_configured")
            return

        license_file = Path(path)
        if not license_file.exists():
            logger.warning("license_file_not_found", path=path)
            return

        try:
            data = json.loads(license_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("license_read_error", error=str(e))
            return

        # Verify HMAC signature
        signature = data.pop("signature", "")
        payload = json.dumps(data, sort_keys=True).encode()
        expected = hmac.new(_HMAC_KEY, payload, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected):
            logger.error("license_signature_invalid")
            return

        # Check expiry
        expires_str = data.get("expires_at", "")
        try:
            self.expires_at = datetime.fromisoformat(expires_str)
        except (ValueError, TypeError):
            logger.error("license_invalid_expiry")
            return

        now = datetime.now(timezone.utc)
        if self.expires_at < now:
            grace_end = self.expires_at + timedelta(days=_GRACE_DAYS)
            if now < grace_end:
                self.in_grace_period = True
                logger.warning(
                    "license_in_grace_period",
                    days_remaining=(grace_end - now).days,
                )
            else:
                logger.error("license_expired", expired_at=expires_str)
                return

        self.is_valid = True
        self.features = data.get("features", [])
        logger.info("license_validated", features=self.features, expires=expires_str)

    def check_feature(self, feature: str) -> bool:
        """Return True if the license is valid and includes the given feature."""
        return self.is_valid and feature in self.features


def generate_license(
    customer_email: str,
    features: list[str],
    max_servers: int,
    days_valid: int = 365,
) -> dict:
    """Generate a signed license (for internal use / testing)."""
    data = {
        "license_id": hashlib.sha256(customer_email.encode()).hexdigest()[:12],
        "customer_email": customer_email,
        "plan": "pro",
        "max_servers": max_servers,
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(days=days_valid)
        ).isoformat(),
        "features": features,
    }
    payload = json.dumps(data, sort_keys=True).encode()
    data["signature"] = hmac.new(_HMAC_KEY, payload, hashlib.sha256).hexdigest()
    return data
