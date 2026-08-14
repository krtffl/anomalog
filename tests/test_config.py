"""Tests for YAML configuration loading and validation."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from anomalog.config import AnomalogConfig, load_config


class TestConfigParsing:
    def test_valid_config(self, tmp_path: Path) -> None:
        config_data = {
            "sources": [
                {"name": "app", "method": "file", "path": "/var/log/app.log"},
                {"name": "api", "method": "loki", "loki_labels": {"app": "api"}},
            ],
            "alerts": [
                {
                    "type": "telegram",
                    "telegram_bot_token": "123:abc",
                    "telegram_chat_id": "456",
                }
            ],
            "storage": {"duckdb_path": "/tmp/test.duckdb", "retention_days": 14},
            "alert_cooldown_minutes": 15,
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)

        assert len(config.sources) == 2
        assert config.sources[0].name == "app"
        assert config.sources[0].method == "file"
        assert config.sources[0].path == "/var/log/app.log"
        assert config.sources[1].name == "api"
        assert config.sources[1].loki_labels == {"app": "api"}
        assert len(config.alerts) == 1
        assert config.alerts[0].type == "telegram"
        assert config.storage.retention_days == 14
        assert config.alert_cooldown_minutes == 15

    def test_missing_sources_raises(self) -> None:
        with pytest.raises(ValidationError, match="sources"):
            AnomalogConfig.model_validate({"log_level": "debug"})

    def test_empty_sources_raises(self) -> None:
        with pytest.raises(ValidationError):
            AnomalogConfig.model_validate({"sources": []})


class TestConfigDefaults:
    def test_default_values_applied(self) -> None:
        config = AnomalogConfig.model_validate(
            {"sources": [{"name": "test", "method": "file", "path": "/tmp/test.log"}]}
        )
        assert config.storage.duckdb_path == "./data/anomalog.duckdb"
        assert config.storage.sqlite_path == "./data/anomalog.sqlite"
        assert config.storage.retention_days == 30
        assert config.dashboard.enabled is True
        assert config.dashboard.listen_host == "127.0.0.1"
        assert config.dashboard.listen_port == 8701
        assert config.alert_cooldown_minutes == 30
        assert config.log_level == "info"
        assert config.alerts == []

    def test_source_defaults(self) -> None:
        config = AnomalogConfig.model_validate({"sources": [{"name": "test", "method": "file"}]})
        source = config.sources[0]
        assert source.sensitivity == 0.5
        assert source.training_window_hours == 168
        assert len(source.anomaly_types) == 4
        assert source.path is None
        assert source.loki_labels is None


class TestSensitivityBounds:
    def test_sensitivity_at_zero(self) -> None:
        config = AnomalogConfig.model_validate(
            {"sources": [{"name": "test", "method": "file", "sensitivity": 0.0}]}
        )
        assert config.sources[0].sensitivity == 0.0

    def test_sensitivity_at_one(self) -> None:
        config = AnomalogConfig.model_validate(
            {"sources": [{"name": "test", "method": "file", "sensitivity": 1.0}]}
        )
        assert config.sources[0].sensitivity == 1.0

    def test_sensitivity_below_zero_raises(self) -> None:
        with pytest.raises(ValidationError, match="sensitivity"):
            AnomalogConfig.model_validate(
                {"sources": [{"name": "test", "method": "file", "sensitivity": -0.1}]}
            )

    def test_sensitivity_above_one_raises(self) -> None:
        with pytest.raises(ValidationError, match="sensitivity"):
            AnomalogConfig.model_validate(
                {"sources": [{"name": "test", "method": "file", "sensitivity": 1.1}]}
            )
