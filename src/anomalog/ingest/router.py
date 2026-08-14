"""Ingestion router: receives raw log lines, parses, and stores them."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from anomalog.parser.bridge import create_drain_tree, parse_line

if TYPE_CHECKING:
    from anomalog.storage.duckdb import DuckDBStorage

logger = structlog.get_logger(__name__)

BATCH_SIZE = 100
MAX_QUEUE_SIZE = 10_000


class IngestionRouter:
    """Async queue-based ingestion pipeline."""

    def __init__(self, duck: DuckDBStorage) -> None:
        self.duck = duck
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self.drain_trees: dict[str, object] = {}  # per-source DrainTree
        self._running = False
        self._batch: list[dict] = []

    async def submit(self, raw_line: str, source: str, line_number: int = 0) -> None:
        """Submit a raw log line for processing."""
        try:
            self.queue.put_nowait(
                {
                    "raw": raw_line,
                    "source": source,
                    "line_number": line_number,
                }
            )
        except asyncio.QueueFull:
            logger.warning("ingestion_queue_full", source=source, dropped=True)

    async def run(self) -> None:
        """Main processing loop. Consumes from queue, parses, batches, stores."""
        self._running = True
        logger.info("ingestion_router_started")

        while self._running:
            try:
                item = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except TimeoutError:
                # Flush any partial batch on idle
                if self._batch:
                    await self._flush()
                continue

            parsed = parse_line(item["raw"])
            if parsed is None:
                continue

            # Get or create DrainTree for this source
            source = item["source"]
            drain = self.drain_trees.get(source)
            if drain is None:
                drain = create_drain_tree()
                if drain is not None:
                    self.drain_trees[source] = drain

            template_id = None
            if drain is not None and parsed.get("message"):
                template_id = str(drain.process(parsed["message"]))  # type: ignore[union-attr]

            log_entry = {
                "timestamp": parsed.get("timestamp") or datetime.now(UTC).isoformat(),
                "source": source,
                "level": parsed.get("level"),
                "message": parsed.get("message"),
                "template_id": template_id,
                "fields": parsed.get("fields", {}),
                "line_number": item["line_number"],
            }

            self._batch.append(log_entry)

            if len(self._batch) >= BATCH_SIZE:
                await self._flush()

    async def _flush(self) -> None:
        """Flush buffered entries to DuckDB."""
        if not self._batch:
            return
        batch = self._batch
        self._batch = []
        try:
            self.duck.insert_logs(batch)
            logger.debug("batch_flushed", count=len(batch))
        except Exception:
            logger.exception("batch_flush_failed", count=len(batch))

    async def stop(self) -> None:
        """Stop the router and flush remaining entries."""
        self._running = False
        await self._flush()
        logger.info("ingestion_router_stopped")
