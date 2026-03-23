"""DuckDB storage for log analytics and anomaly records."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb
import structlog

logger = structlog.get_logger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS logs (
    timestamp   TIMESTAMPTZ,
    source      VARCHAR NOT NULL,
    level       VARCHAR,
    message     VARCHAR,
    template_id VARCHAR,
    fields      JSON,
    line_number BIGINT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS templates (
    template_id VARCHAR NOT NULL,
    source      VARCHAR NOT NULL,
    template    VARCHAR NOT NULL,
    count       BIGINT NOT NULL DEFAULT 0,
    first_seen  TIMESTAMPTZ NOT NULL,
    last_seen   TIMESTAMPTZ NOT NULL,
    sample_line VARCHAR,
    PRIMARY KEY (template_id, source)
);

CREATE TABLE IF NOT EXISTS anomalies (
    id            VARCHAR NOT NULL PRIMARY KEY,
    anomaly_type  VARCHAR NOT NULL,
    severity      VARCHAR NOT NULL,
    score         DOUBLE NOT NULL,
    source        VARCHAR NOT NULL,
    detected_at   TIMESTAMPTZ NOT NULL,
    description   VARCHAR NOT NULL,
    context       JSON NOT NULL,
    sample_lines  JSON,
    alerted       BOOLEAN NOT NULL DEFAULT false
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_logs_source_ts ON logs (source, timestamp);
CREATE INDEX IF NOT EXISTS idx_anomalies_source_detected ON anomalies (source, detected_at);
CREATE INDEX IF NOT EXISTS idx_anomalies_type ON anomalies (anomaly_type, detected_at);
"""


class DuckDBStorage:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        for sql in SCHEMA_SQL.strip().split(";"):
            stmt = sql.strip()
            if stmt:
                self.conn.execute(stmt)
        for sql in INDEX_SQL.strip().split(";"):
            stmt = sql.strip()
            if stmt:
                self.conn.execute(stmt)
        logger.info("duckdb_schema_initialized")

    def insert_logs(self, logs: list[dict]) -> None:
        """Batch insert parsed log entries."""
        if not logs:
            return
        self.conn.executemany(
            """INSERT INTO logs (timestamp, source, level, message, template_id, fields, line_number)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    log.get("timestamp"),
                    log["source"],
                    log.get("level"),
                    log.get("message"),
                    log.get("template_id"),
                    json.dumps(log.get("fields", {})),
                    log.get("line_number", 0),
                )
                for log in logs
            ],
        )

    def insert_anomaly(self, anomaly: dict) -> None:
        """Insert a detected anomaly."""
        self.conn.execute(
            """INSERT INTO anomalies
               (id, anomaly_type, severity, score, source, detected_at,
                description, context, sample_lines, alerted)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                anomaly["id"],
                anomaly["anomaly_type"],
                anomaly["severity"],
                anomaly["score"],
                anomaly["source"],
                anomaly["detected_at"],
                anomaly["description"],
                json.dumps(anomaly.get("context", {})),
                json.dumps(anomaly.get("sample_lines", [])),
                anomaly.get("alerted", False),
            ],
        )

    def get_recent_logs(
        self, source: str, since: datetime, limit: int = 10000
    ) -> list[dict]:
        """Get recent logs for a source since a given timestamp."""
        result = self.conn.execute(
            """SELECT timestamp, source, level, message, template_id, fields, line_number
               FROM logs WHERE source = ? AND timestamp >= ? ORDER BY timestamp LIMIT ?""",
            [source, since, limit],
        )
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def get_recent_anomalies(
        self, source: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Get recent anomalies, optionally filtered by source."""
        if source:
            result = self.conn.execute(
                "SELECT * FROM anomalies WHERE source = ? ORDER BY detected_at DESC LIMIT ?",
                [source, limit],
            )
        else:
            result = self.conn.execute(
                "SELECT * FROM anomalies ORDER BY detected_at DESC LIMIT ?", [limit]
            )
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def get_error_rate(
        self, source: str, since: datetime, bucket_minutes: int = 5
    ) -> list[dict]:
        """Get error rate per time bucket."""
        interval = f"{int(bucket_minutes)} MINUTE"
        result = self.conn.execute(
            f"""SELECT
                 time_bucket(INTERVAL '{interval}', timestamp) AS bucket,
                 COUNT(*) AS total,
                 COUNT(*) FILTER (WHERE level IN ('error', 'fatal')) AS errors,
                 CAST(COUNT(*) FILTER (WHERE level IN ('error', 'fatal')) AS DOUBLE)
                   / COUNT(*) AS error_rate
               FROM logs
               WHERE source = ? AND timestamp >= ?
               GROUP BY bucket ORDER BY bucket""",
            [source, since],
        )
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def delete_old_logs(self, retention_days: int) -> int:
        """Delete logs older than retention period. Returns count deleted."""
        days = int(retention_days)
        result = self.conn.execute(
            f"DELETE FROM logs WHERE ingested_at < now() - INTERVAL '{days} DAY' RETURNING 1",
        )
        count = len(result.fetchall())
        if count > 0:
            logger.info("retention_cleanup", deleted=count, retention_days=retention_days)
        return count

    def close(self) -> None:
        self.conn.close()
