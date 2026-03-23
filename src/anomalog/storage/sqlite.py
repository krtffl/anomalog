"""SQLite storage for operational state (drain state, file offsets, alert cooldowns, model metadata)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS file_offsets (
    source TEXT NOT NULL,
    path TEXT NOT NULL,
    offset INTEGER NOT NULL DEFAULT 0,
    inode INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source, path)
);

CREATE TABLE IF NOT EXISTS alert_cooldowns (
    source TEXT NOT NULL,
    anomaly_type TEXT NOT NULL,
    template_id TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL,
    PRIMARY KEY (source, anomaly_type, template_id)
);

CREATE TABLE IF NOT EXISTS model_metadata (
    source TEXT NOT NULL PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 1,
    trained_at TEXT NOT NULL,
    lines_trained INTEGER NOT NULL,
    model_path TEXT NOT NULL
);
"""


class SQLiteStorage:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        logger.info("sqlite_schema_initialized")

    # --- File offsets ---

    def get_file_offset(self, source: str, path: str) -> int:
        row = self.conn.execute(
            "SELECT offset FROM file_offsets WHERE source = ? AND path = ?",
            (source, path),
        ).fetchone()
        return row["offset"] if row else 0

    def set_file_offset(
        self, source: str, path: str, offset: int, inode: int | None = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """INSERT INTO file_offsets (source, path, offset, inode, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source, path) DO UPDATE SET offset=?, inode=?, updated_at=?""",
            (source, path, offset, inode, now, offset, inode, now),
        )
        self.conn.commit()

    # --- Alert cooldowns ---

    def is_in_cooldown(
        self, source: str, anomaly_type: str, template_id: str | None = None
    ) -> bool:
        row = self.conn.execute(
            """SELECT expires_at FROM alert_cooldowns
               WHERE source = ? AND anomaly_type = ? AND template_id = ?""",
            (source, anomaly_type, template_id or ""),
        ).fetchone()
        if row is None:
            return False
        return datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)

    def set_cooldown(
        self,
        source: str,
        anomaly_type: str,
        expires_at: datetime,
        template_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO alert_cooldowns (source, anomaly_type, template_id, expires_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(source, anomaly_type, template_id)
               DO UPDATE SET expires_at=?""",
            (
                source,
                anomaly_type,
                template_id or "",
                expires_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
        self.conn.commit()

    def cleanup_expired_cooldowns(self) -> None:
        self.conn.execute(
            "DELETE FROM alert_cooldowns WHERE expires_at < ?",
            (datetime.now(timezone.utc).isoformat(),),
        )
        self.conn.commit()

    # --- Model metadata ---

    def get_model_metadata(self, source: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM model_metadata WHERE source = ?", (source,)
        ).fetchone()
        return dict(row) if row else None

    def set_model_metadata(
        self, source: str, trained_at: datetime, lines_trained: int, model_path: str
    ) -> None:
        existing = self.get_model_metadata(source)
        version = (existing["version"] + 1) if existing else 1
        self.conn.execute(
            """INSERT INTO model_metadata (source, version, trained_at, lines_trained, model_path)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source) DO UPDATE SET
               version=?, trained_at=?, lines_trained=?, model_path=?""",
            (
                source,
                version,
                trained_at.isoformat(),
                lines_trained,
                model_path,
                version,
                trained_at.isoformat(),
                lines_trained,
                model_path,
            ),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
