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

CREATE TABLE IF NOT EXISTS metric_samples (
    name        VARCHAR NOT NULL,
    labels_hash VARCHAR NOT NULL,
    labels      JSON NOT NULL,
    value       DOUBLE NOT NULL,
    timestamp   TIMESTAMP NOT NULL,
    metric_type VARCHAR NOT NULL,
    source      VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_models (
    metric_name   VARCHAR NOT NULL,
    labels_hash   VARCHAR NOT NULL,
    model_type    VARCHAR NOT NULL,
    model_blob    BLOB,
    trained_at    TIMESTAMP NOT NULL,
    rmse          DOUBLE,
    PRIMARY KEY (metric_name, labels_hash)
);

CREATE TABLE IF NOT EXISTS predictions (
    id               VARCHAR NOT NULL,
    metric_name      VARCHAR NOT NULL,
    labels_hash      VARCHAR NOT NULL,
    horizon_hours    INTEGER NOT NULL,
    predictions_json JSON NOT NULL,
    exhaustion_time  TIMESTAMP,
    threshold        DOUBLE,
    predicted_at     TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS correlated_events (
    id               VARCHAR NOT NULL,
    log_event_id     VARCHAR NOT NULL,
    metric_event_id  VARCHAR NOT NULL,
    time_delta_sec   DOUBLE NOT NULL,
    confidence       DOUBLE NOT NULL,
    detected_at      TIMESTAMP NOT NULL
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_logs_source_ts ON logs (source, timestamp);
CREATE INDEX IF NOT EXISTS idx_anomalies_source_detected ON anomalies (source, detected_at);
CREATE INDEX IF NOT EXISTS idx_anomalies_type ON anomalies (anomaly_type, detected_at);
CREATE INDEX IF NOT EXISTS idx_metric_samples_name_ts ON metric_samples (name, timestamp);
CREATE INDEX IF NOT EXISTS idx_predictions_metric ON predictions (metric_name, predicted_at);
CREATE INDEX IF NOT EXISTS idx_correlated_detected ON correlated_events (detected_at);
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

    # --- Metric samples ---

    def insert_metric_samples(self, samples: list[dict]) -> None:
        """Batch insert metric samples."""
        if not samples:
            return
        self.conn.executemany(
            """INSERT INTO metric_samples (name, labels_hash, labels, value, timestamp,
                                          metric_type, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    s["name"],
                    s["labels_hash"],
                    s["labels"] if isinstance(s["labels"], str) else json.dumps(s["labels"]),
                    s["value"],
                    s["timestamp"],
                    s["metric_type"],
                    s["source"],
                )
                for s in samples
            ],
        )

    def get_recent_metrics(
        self, name: str, since: datetime, limit: int = 10000
    ) -> list[dict]:
        """Get recent metric samples by name since a given timestamp."""
        result = self.conn.execute(
            """SELECT name, labels_hash, labels, value, timestamp, metric_type, source
               FROM metric_samples
               WHERE name = ? AND timestamp >= ?
               ORDER BY timestamp
               LIMIT ?""",
            [name, since, limit],
        )
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def get_metric_names(self) -> list[str]:
        """Return distinct metric names."""
        result = self.conn.execute("SELECT DISTINCT name FROM metric_samples ORDER BY name")
        return [row[0] for row in result.fetchall()]

    # --- Predictions ---

    def insert_prediction(self, prediction: dict) -> None:
        """Insert a capacity prediction."""
        self.conn.execute(
            """INSERT INTO predictions
               (id, metric_name, labels_hash, horizon_hours, predictions_json,
                exhaustion_time, threshold, predicted_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                prediction["id"],
                prediction["metric_name"],
                prediction["labels_hash"],
                prediction["horizon_hours"],
                prediction["predictions_json"]
                if isinstance(prediction["predictions_json"], str)
                else json.dumps(prediction["predictions_json"]),
                prediction.get("exhaustion_time"),
                prediction.get("threshold"),
                prediction["predicted_at"],
            ],
        )

    def get_predictions(
        self, metric_name: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Get predictions, optionally filtered by metric name."""
        if metric_name:
            result = self.conn.execute(
                """SELECT * FROM predictions
                   WHERE metric_name = ?
                   ORDER BY predicted_at DESC LIMIT ?""",
                [metric_name, limit],
            )
        else:
            result = self.conn.execute(
                "SELECT * FROM predictions ORDER BY predicted_at DESC LIMIT ?",
                [limit],
            )
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def save_prediction_model(
        self,
        metric_name: str,
        labels_hash: str,
        model_type: str,
        model_blob: bytes,
        rmse: float,
    ) -> None:
        """Upsert a serialized prediction model."""
        self.conn.execute(
            """INSERT INTO prediction_models (metric_name, labels_hash, model_type,
                                             model_blob, trained_at, rmse)
               VALUES (?, ?, ?, ?, now(), ?)
               ON CONFLICT (metric_name, labels_hash)
               DO UPDATE SET model_type = EXCLUDED.model_type,
                            model_blob = EXCLUDED.model_blob,
                            trained_at = EXCLUDED.trained_at,
                            rmse = EXCLUDED.rmse""",
            [metric_name, labels_hash, model_type, model_blob, rmse],
        )

    def load_prediction_model(self, metric_name: str, labels_hash: str) -> dict | None:
        """Load a prediction model by metric name and labels hash."""
        result = self.conn.execute(
            """SELECT metric_name, labels_hash, model_type, model_blob, trained_at, rmse
               FROM prediction_models
               WHERE metric_name = ? AND labels_hash = ?""",
            [metric_name, labels_hash],
        )
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        if not rows:
            return None
        return dict(zip(columns, rows[0]))

    # --- Correlated events ---

    def insert_correlated_event(self, event: dict) -> None:
        """Insert a correlated log-metric event."""
        self.conn.execute(
            """INSERT INTO correlated_events
               (id, log_event_id, metric_event_id, time_delta_sec, confidence, detected_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                event["id"],
                event["log_event_id"],
                event["metric_event_id"],
                event["time_delta_sec"],
                event["confidence"],
                event["detected_at"],
            ],
        )

    def get_correlated_events(
        self, since: datetime | None = None, limit: int = 100
    ) -> list[dict]:
        """Get correlated events, optionally since a given timestamp."""
        if since:
            result = self.conn.execute(
                """SELECT * FROM correlated_events
                   WHERE detected_at >= ?
                   ORDER BY detected_at DESC LIMIT ?""",
                [since, limit],
            )
        else:
            result = self.conn.execute(
                "SELECT * FROM correlated_events ORDER BY detected_at DESC LIMIT ?",
                [limit],
            )
        columns = [desc[0] for desc in result.description]
        return [dict(zip(columns, row)) for row in result.fetchall()]

    def close(self) -> None:
        self.conn.close()
