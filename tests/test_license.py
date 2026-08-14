"""Tests for pro license validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from anomalog.pro.license import LicenseValidator, generate_license


@pytest.fixture()
def valid_license_path(tmp_path):
    """Generate a valid license file and return its path."""
    data = generate_license(
        customer_email="test@example.com",
        features=["predictions", "correlation", "otel"],
        max_servers=5,
        days_valid=365,
    )
    path = tmp_path / "license.json"
    path.write_text(json.dumps(data))
    return str(path)


@pytest.fixture()
def expired_license_path(tmp_path):
    """Generate a license expired beyond the grace period."""
    data = generate_license(
        customer_email="expired@example.com",
        features=["predictions"],
        max_servers=1,
        days_valid=-30,  # expired 30 days ago (beyond 7-day grace)
    )
    path = tmp_path / "expired_license.json"
    path.write_text(json.dumps(data))
    return str(path)


@pytest.fixture()
def grace_period_license_path(tmp_path):
    """Generate a license that expired 3 days ago (within 7-day grace)."""
    data = generate_license(
        customer_email="grace@example.com",
        features=["predictions", "correlation"],
        max_servers=2,
        days_valid=-3,  # expired 3 days ago
    )
    path = tmp_path / "grace_license.json"
    path.write_text(json.dumps(data))
    return str(path)


class TestValidLicense:
    def test_is_valid(self, valid_license_path) -> None:
        validator = LicenseValidator(valid_license_path)
        assert validator.is_valid is True

    def test_features_populated(self, valid_license_path) -> None:
        validator = LicenseValidator(valid_license_path)
        assert "predictions" in validator.features
        assert "correlation" in validator.features
        assert "otel" in validator.features

    def test_not_in_grace_period(self, valid_license_path) -> None:
        validator = LicenseValidator(valid_license_path)
        assert validator.in_grace_period is False

    def test_expires_at_set(self, valid_license_path) -> None:
        validator = LicenseValidator(valid_license_path)
        assert validator.expires_at is not None
        assert validator.expires_at > datetime.now(UTC)


class TestExpiredLicense:
    def test_is_invalid(self, expired_license_path) -> None:
        validator = LicenseValidator(expired_license_path)
        assert validator.is_valid is False

    def test_features_empty_when_expired(self, expired_license_path) -> None:
        validator = LicenseValidator(expired_license_path)
        assert validator.features == []


class TestGracePeriod:
    def test_is_valid_during_grace(self, grace_period_license_path) -> None:
        validator = LicenseValidator(grace_period_license_path)
        assert validator.is_valid is True

    def test_in_grace_period_flag(self, grace_period_license_path) -> None:
        validator = LicenseValidator(grace_period_license_path)
        assert validator.in_grace_period is True


class TestInvalidSignature:
    def test_tampered_license_rejected(self, tmp_path) -> None:
        data = generate_license(
            customer_email="tamper@example.com",
            features=["predictions"],
            max_servers=1,
        )
        # Tamper with the data
        data["max_servers"] = 9999
        path = tmp_path / "tampered.json"
        path.write_text(json.dumps(data))

        validator = LicenseValidator(str(path))
        assert validator.is_valid is False

    def test_missing_signature_rejected(self, tmp_path) -> None:
        data = generate_license(
            customer_email="nosig@example.com",
            features=["predictions"],
            max_servers=1,
        )
        del data["signature"]
        path = tmp_path / "nosig.json"
        path.write_text(json.dumps(data))

        validator = LicenseValidator(str(path))
        assert validator.is_valid is False


class TestMissingFile:
    def test_nonexistent_path(self) -> None:
        validator = LicenseValidator("/tmp/nonexistent_license_file.json")
        assert validator.is_valid is False

    def test_none_path(self) -> None:
        validator = LicenseValidator(None)
        assert validator.is_valid is False


class TestFeatureCheck:
    def test_check_enabled_feature(self, valid_license_path) -> None:
        validator = LicenseValidator(valid_license_path)
        assert validator.check_feature("predictions") is True

    def test_check_disabled_feature(self, valid_license_path) -> None:
        validator = LicenseValidator(valid_license_path)
        assert validator.check_feature("nonexistent_feature") is False

    def test_check_feature_when_invalid(self, expired_license_path) -> None:
        validator = LicenseValidator(expired_license_path)
        assert validator.check_feature("predictions") is False


class TestGenerateLicense:
    def test_round_trip(self, tmp_path) -> None:
        """Generate a license and validate it successfully."""
        data = generate_license(
            customer_email="roundtrip@example.com",
            features=["predictions", "otel"],
            max_servers=10,
            days_valid=30,
        )

        path = tmp_path / "roundtrip.json"
        path.write_text(json.dumps(data))

        validator = LicenseValidator(str(path))
        assert validator.is_valid is True
        assert "predictions" in validator.features
        assert "otel" in validator.features

    def test_generated_license_has_expected_fields(self) -> None:
        data = generate_license(
            customer_email="fields@example.com",
            features=["predictions"],
            max_servers=3,
            days_valid=90,
        )
        assert "license_id" in data
        assert "customer_email" in data
        assert "plan" in data
        assert data["plan"] == "pro"
        assert "max_servers" in data
        assert data["max_servers"] == 3
        assert "issued_at" in data
        assert "expires_at" in data
        assert "features" in data
        assert "signature" in data

    def test_different_emails_different_ids(self) -> None:
        d1 = generate_license("a@example.com", ["predictions"], 1)
        d2 = generate_license("b@example.com", ["predictions"], 1)
        assert d1["license_id"] != d2["license_id"]
