"""Storage backends for anomalog."""

from anomalog.storage.duckdb import DuckDBStorage
from anomalog.storage.sqlite import SQLiteStorage

__all__ = ["DuckDBStorage", "SQLiteStorage"]
