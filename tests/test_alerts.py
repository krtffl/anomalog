"""Tests for the alerting system: channels, dispatcher, cooldown, severity filtering."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from anomalog.alert import alertmanager, slack, telegram
from anomalog.alert.dispatcher import (
    _severity_meets_minimum,
    dispatch_alert,
)
from anomalog.config import AlertChannelConfig
from anomalog.storage.sqlite import SQLiteStorage
from anomalog.types import Anomaly, AnomalyType, Severity

# --- Fixtures ---


def _make_anomaly(
    severity: Severity = Severity.HIGH,
    anomaly_type: AnomalyType = AnomalyType.ERROR_RATE_SPIKE,
    source: str = "test-source",
    sample_lines: list[str] | None = None,
    context: dict | None = None,
) -> Anomaly:
    return Anomaly(
        id=str(uuid4()),
        anomaly_type=anomaly_type,
        severity=severity,
        score=0.92,
        source=source,
        detected_at=datetime(2026, 3, 23, 12, 0, 0, tzinfo=UTC),
        description="Error rate spiked to 15% (baseline: 2%)",
        context=context or {"template_id": "tpl_001"},
        sample_lines=sample_lines or ["ERROR disk full on /dev/sda1", "ERROR OOM kill"],
    )


@pytest.fixture()
def anomaly() -> Anomaly:
    return _make_anomaly()


@pytest.fixture()
def sqlite(tmp_path) -> SQLiteStorage:
    db_path = str(tmp_path / "test.sqlite")
    return SQLiteStorage(db_path)


# --- Telegram channel tests ---


class TestTelegramChannel:
    async def test_send_success(self) -> None:
        """Mock httpx to return 200, verify returns True."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert "sendMessage" in str(request.url)
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        anomaly = _make_anomaly()

        # Monkeypatch httpx.AsyncClient to use mock transport
        original_init = httpx.AsyncClient.__init__

        def patched_init(self, **kwargs):
            kwargs["transport"] = transport
            original_init(self, **kwargs)

        httpx.AsyncClient.__init__ = patched_init  # type: ignore[method-assign]
        try:
            result = await telegram.send_alert(anomaly, "fake-token", "12345")
            assert result is True
        finally:
            httpx.AsyncClient.__init__ = original_init  # type: ignore[method-assign]

    async def test_send_failure(self) -> None:
        """Mock httpx to return 400, verify returns False."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"ok": False, "description": "Bad Request"})

        transport = httpx.MockTransport(handler)
        anomaly = _make_anomaly()

        original_init = httpx.AsyncClient.__init__

        def patched_init(self, **kwargs):
            kwargs["transport"] = transport
            original_init(self, **kwargs)

        httpx.AsyncClient.__init__ = patched_init  # type: ignore[method-assign]
        try:
            result = await telegram.send_alert(anomaly, "fake-token", "12345")
            assert result is False
        finally:
            httpx.AsyncClient.__init__ = original_init  # type: ignore[method-assign]


# --- Slack channel tests ---


class TestSlackChannel:
    async def test_send_success(self) -> None:
        """Mock httpx to return 200."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="ok")

        transport = httpx.MockTransport(handler)
        anomaly = _make_anomaly()

        original_init = httpx.AsyncClient.__init__

        def patched_init(self, **kwargs):
            kwargs["transport"] = transport
            original_init(self, **kwargs)

        httpx.AsyncClient.__init__ = patched_init  # type: ignore[method-assign]
        try:
            result = await slack.send_alert(anomaly, "https://hooks.slack.com/test")
            assert result is True
        finally:
            httpx.AsyncClient.__init__ = original_init  # type: ignore[method-assign]


# --- Alertmanager channel tests ---


class TestAlertmanagerChannel:
    async def test_send_success(self) -> None:
        """Mock httpx to return 200."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert "/api/v2/alerts" in str(request.url)
            return httpx.Response(200)

        transport = httpx.MockTransport(handler)
        anomaly = _make_anomaly()

        original_init = httpx.AsyncClient.__init__

        def patched_init(self, **kwargs):
            kwargs["transport"] = transport
            original_init(self, **kwargs)

        httpx.AsyncClient.__init__ = patched_init  # type: ignore[method-assign]
        try:
            result = await alertmanager.send_alert(anomaly, "http://alertmanager:9093")
            assert result is True
        finally:
            httpx.AsyncClient.__init__ = original_init  # type: ignore[method-assign]


# --- Severity filtering tests ---


class TestSeverityFiltering:
    def test_high_meets_medium(self) -> None:
        assert _severity_meets_minimum(Severity.HIGH, "medium") is True

    def test_low_does_not_meet_medium(self) -> None:
        assert _severity_meets_minimum(Severity.LOW, "medium") is False

    def test_critical_meets_critical(self) -> None:
        assert _severity_meets_minimum(Severity.CRITICAL, "critical") is True

    def test_medium_does_not_meet_high(self) -> None:
        assert _severity_meets_minimum(Severity.MEDIUM, "high") is False

    def test_low_meets_low(self) -> None:
        assert _severity_meets_minimum(Severity.LOW, "low") is True


# --- Dispatcher tests ---


class TestDispatcher:
    async def test_routes_to_matching_channels(
        self, anomaly: Anomaly, sqlite: SQLiteStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HIGH anomaly: medium-min channel fires, critical-min channel does not."""
        medium_channel = AlertChannelConfig(
            type="telegram",
            telegram_bot_token="tok",
            telegram_chat_id="123",
            min_severity="medium",
        )
        critical_channel = AlertChannelConfig(
            type="slack",
            slack_webhook_url="https://hooks.slack.com/x",
            min_severity="critical",
        )

        telegram_calls: list[Anomaly] = []
        slack_calls: list[Anomaly] = []

        async def mock_telegram(a: Anomaly, token: str, chat_id: str) -> bool:
            telegram_calls.append(a)
            return True

        async def mock_slack(a: Anomaly, url: str) -> bool:
            slack_calls.append(a)
            return True

        monkeypatch.setattr("anomalog.alert.dispatcher.telegram.send_alert", mock_telegram)
        monkeypatch.setattr("anomalog.alert.dispatcher.slack.send_alert", mock_slack)

        result = await dispatch_alert(
            anomaly, [medium_channel, critical_channel], sqlite, cooldown_minutes=30
        )
        assert result is True
        assert len(telegram_calls) == 1
        assert len(slack_calls) == 0

    async def test_cooldown_prevents_duplicate(
        self, anomaly: Anomaly, sqlite: SQLiteStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dispatch same anomaly twice; second should be blocked by cooldown."""
        channel = AlertChannelConfig(
            type="telegram",
            telegram_bot_token="tok",
            telegram_chat_id="123",
            min_severity="low",
        )

        call_count = 0

        async def mock_send(a: Anomaly, token: str, chat_id: str) -> bool:
            nonlocal call_count
            call_count += 1
            return True

        monkeypatch.setattr("anomalog.alert.dispatcher.telegram.send_alert", mock_send)

        result1 = await dispatch_alert(anomaly, [channel], sqlite, cooldown_minutes=30)
        assert result1 is True
        assert call_count == 1

        result2 = await dispatch_alert(anomaly, [channel], sqlite, cooldown_minutes=30)
        assert result2 is False
        assert call_count == 1  # No additional calls

    async def test_parallel_dispatch(
        self, sqlite: SQLiteStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple channels all get called."""
        anomaly = _make_anomaly(severity=Severity.CRITICAL)

        ch_telegram = AlertChannelConfig(
            type="telegram",
            telegram_bot_token="tok",
            telegram_chat_id="123",
            min_severity="low",
        )
        ch_slack = AlertChannelConfig(
            type="slack",
            slack_webhook_url="https://hooks.slack.com/x",
            min_severity="low",
        )
        ch_alertmanager = AlertChannelConfig(
            type="alertmanager",
            alertmanager_url="http://am:9093",
            min_severity="low",
        )

        called: dict[str, bool] = {}

        async def mock_telegram(a: Anomaly, token: str, chat_id: str) -> bool:
            called["telegram"] = True
            return True

        async def mock_slack(a: Anomaly, url: str) -> bool:
            called["slack"] = True
            return True

        async def mock_am(a: Anomaly, url: str) -> bool:
            called["alertmanager"] = True
            return True

        monkeypatch.setattr("anomalog.alert.dispatcher.telegram.send_alert", mock_telegram)
        monkeypatch.setattr("anomalog.alert.dispatcher.slack.send_alert", mock_slack)
        monkeypatch.setattr("anomalog.alert.dispatcher.alertmanager.send_alert", mock_am)

        result = await dispatch_alert(
            anomaly, [ch_telegram, ch_slack, ch_alertmanager], sqlite, cooldown_minutes=30
        )
        assert result is True
        assert called == {"telegram": True, "slack": True, "alertmanager": True}

    async def test_severity_filtering_low_anomaly(
        self, sqlite: SQLiteStorage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """LOW anomaly doesn't trigger MEDIUM channel."""
        anomaly = _make_anomaly(severity=Severity.LOW)
        channel = AlertChannelConfig(
            type="telegram",
            telegram_bot_token="tok",
            telegram_chat_id="123",
            min_severity="medium",
        )

        call_count = 0

        async def mock_send(a: Anomaly, token: str, chat_id: str) -> bool:
            nonlocal call_count
            call_count += 1
            return True

        monkeypatch.setattr("anomalog.alert.dispatcher.telegram.send_alert", mock_send)

        result = await dispatch_alert(anomaly, [channel], sqlite, cooldown_minutes=30)
        assert result is False
        assert call_count == 0
