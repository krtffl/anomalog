"""Data retention policy enforcement."""

from __future__ import annotations

import structlog

from anomalog.storage.duckdb import DuckDBStorage

logger = structlog.get_logger(__name__)


async def enforce_retention(duck: DuckDBStorage, retention_days: int) -> None:
    """Delete data older than retention period."""
    deleted = duck.delete_old_logs(retention_days)
    if deleted > 0:
        logger.info("retention_enforced", logs_deleted=deleted, retention_days=retention_days)
