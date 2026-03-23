"""Shared test fixtures for anomalog tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from anomalog.config import AnomalogConfig, DashboardConfig, SourceConfig, StorageConfig


@pytest.fixture
def test_config(tmp_path: Path) -> AnomalogConfig:
    """Minimal config for testing: HTTP source, no alerts, no file tailers."""
    return AnomalogConfig(
        sources=[SourceConfig(name="test", method="http")],
        storage=StorageConfig(
            duckdb_path=str(tmp_path / "test.duckdb"),
            sqlite_path=str(tmp_path / "test.sqlite"),
        ),
        dashboard=DashboardConfig(enabled=True),
    )
