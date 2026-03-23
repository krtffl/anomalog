"""Tests for DuckDB and SQLite storage backends."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from anomalog.storage.duckdb import DuckDBStorage
from anomalog.storage.sqlite import SQLiteStorage


class TestDuckDBInsertAndQuery:
    def test_insert_logs_and_get_recent(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.duckdb")
        duck = DuckDBStorage(db_path)
        try:
            now = datetime.now(timezone.utc)
            logs = [
                {
                    "timestamp": now,
                    "source": "app",
                    "level": "info",
                    "message": "started",
                    "template_id": "tpl_001",
                    "fields": {"host": "srv1"},
                    "line_number": 1,
                },
                {
                    "timestamp": now + timedelta(seconds=1),
                    "source": "app",
                    "level": "error",
                    "message": "disk full",
                    "template_id": "tpl_002",
                    "fields": {},
                    "line_number": 2,
                },
            ]
            duck.insert_logs(logs)
            results = duck.get_recent_logs("app", since=now - timedelta(minutes=1))

            assert len(results) == 2
            assert results[0]["source"] == "app"
            assert results[0]["level"] == "info"
            assert results[1]["level"] == "error"
        finally:
            duck.close()

    def test_insert_empty_logs(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.duckdb")
        duck = DuckDBStorage(db_path)
        try:
            duck.insert_logs([])  # Should not raise
            results = duck.get_recent_logs("app", since=datetime.now(timezone.utc))
            assert results == []
        finally:
            duck.close()


class TestDuckDBAnomalies:
    def test_insert_and_get_anomalies(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.duckdb")
        duck = DuckDBStorage(db_path)
        try:
            now = datetime.now(timezone.utc)
            anomaly = {
                "id": "anom_001",
                "anomaly_type": "error_rate_spike",
                "severity": "high",
                "score": 0.95,
                "source": "app",
                "detected_at": now,
                "description": "Error rate exceeded 50%",
                "context": {"baseline_rate": 0.05, "current_rate": 0.55},
                "sample_lines": ["ERROR disk full", "ERROR connection refused"],
                "alerted": False,
            }
            duck.insert_anomaly(anomaly)

            results = duck.get_recent_anomalies(source="app")
            assert len(results) == 1
            assert results[0]["id"] == "anom_001"
            assert results[0]["anomaly_type"] == "error_rate_spike"
            assert results[0]["score"] == 0.95

            all_results = duck.get_recent_anomalies()
            assert len(all_results) == 1
        finally:
            duck.close()


class TestDuckDBRetention:
    def test_delete_old_logs(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "test.duckdb")
        duck = DuckDBStorage(db_path)
        try:
            now = datetime.now(timezone.utc)
            logs = [
                {
                    "timestamp": now,
                    "source": "app",
                    "level": "info",
                    "message": "recent",
                    "line_number": 1,
                },
            ]
            duck.insert_logs(logs)

            # Retention of 30 days should not delete anything just inserted
            deleted = duck.delete_old_logs(30)
            assert deleted == 0

            remaining = duck.get_recent_logs("app", since=now - timedelta(hours=1))
            assert len(remaining) == 1
        finally:
            duck.close()


class TestSQLiteFileOffsets:
    def test_get_set_offset(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "state.sqlite")
        store = SQLiteStorage(db_path)
        try:
            assert store.get_file_offset("app", "/var/log/app.log") == 0

            store.set_file_offset("app", "/var/log/app.log", 1024, inode=12345)
            assert store.get_file_offset("app", "/var/log/app.log") == 1024

            store.set_file_offset("app", "/var/log/app.log", 2048, inode=12345)
            assert store.get_file_offset("app", "/var/log/app.log") == 2048
        finally:
            store.close()

    def test_independent_sources(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "state.sqlite")
        store = SQLiteStorage(db_path)
        try:
            store.set_file_offset("app", "/var/log/app.log", 100)
            store.set_file_offset("api", "/var/log/api.log", 200)

            assert store.get_file_offset("app", "/var/log/app.log") == 100
            assert store.get_file_offset("api", "/var/log/api.log") == 200
        finally:
            store.close()


class TestSQLiteAlertCooldowns:
    def test_no_cooldown_by_default(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "state.sqlite")
        store = SQLiteStorage(db_path)
        try:
            assert store.is_in_cooldown("app", "error_rate_spike") is False
        finally:
            store.close()

    def test_set_and_check_cooldown(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "state.sqlite")
        store = SQLiteStorage(db_path)
        try:
            future = datetime.now(timezone.utc) + timedelta(hours=1)
            store.set_cooldown("app", "error_rate_spike", expires_at=future)

            assert store.is_in_cooldown("app", "error_rate_spike") is True

        finally:
            store.close()

    def test_expired_cooldown(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "state.sqlite")
        store = SQLiteStorage(db_path)
        try:
            past = datetime.now(timezone.utc) - timedelta(hours=1)
            store.set_cooldown("app", "error_rate_spike", expires_at=past)

            assert store.is_in_cooldown("app", "error_rate_spike") is False
        finally:
            store.close()

    def test_cleanup_expired_cooldowns(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "state.sqlite")
        store = SQLiteStorage(db_path)
        try:
            past = datetime.now(timezone.utc) - timedelta(hours=1)
            future = datetime.now(timezone.utc) + timedelta(hours=1)

            store.set_cooldown("app", "error_rate_spike", expires_at=past)
            store.set_cooldown("app", "novel_pattern", expires_at=future)

            store.cleanup_expired_cooldowns()

            assert store.is_in_cooldown("app", "error_rate_spike") is False
            assert store.is_in_cooldown("app", "novel_pattern") is True
        finally:
            store.close()


class TestSQLiteModelMetadata:
    def test_get_nonexistent(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "state.sqlite")
        store = SQLiteStorage(db_path)
        try:
            assert store.get_model_metadata("app") is None
        finally:
            store.close()

    def test_set_and_get(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "state.sqlite")
        store = SQLiteStorage(db_path)
        try:
            now = datetime.now(timezone.utc)
            store.set_model_metadata("app", trained_at=now, lines_trained=5000, model_path="/models/app_v1")

            meta = store.get_model_metadata("app")
            assert meta is not None
            assert meta["source"] == "app"
            assert meta["version"] == 1
            assert meta["lines_trained"] == 5000
            assert meta["model_path"] == "/models/app_v1"
        finally:
            store.close()

    def test_version_increments(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "state.sqlite")
        store = SQLiteStorage(db_path)
        try:
            now = datetime.now(timezone.utc)
            store.set_model_metadata("app", trained_at=now, lines_trained=5000, model_path="/models/v1")
            store.set_model_metadata("app", trained_at=now, lines_trained=10000, model_path="/models/v2")

            meta = store.get_model_metadata("app")
            assert meta is not None
            assert meta["version"] == 2
            assert meta["lines_trained"] == 10000
            assert meta["model_path"] == "/models/v2"
        finally:
            store.close()
