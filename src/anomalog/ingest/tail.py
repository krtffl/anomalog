"""File tailing ingestion via watchdog filesystem monitoring."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

if TYPE_CHECKING:
    from anomalog.ingest.router import IngestionRouter
    from anomalog.storage.sqlite import SQLiteStorage

logger = structlog.get_logger(__name__)


class LogFileTailer(FileSystemEventHandler):
    """Watches a log file and submits new lines to the ingestion router."""

    def __init__(
        self,
        source_name: str,
        file_path: str,
        router: IngestionRouter,
        sqlite: SQLiteStorage,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.source_name = source_name
        self.file_path = file_path
        self.router = router
        self.sqlite = sqlite
        self.loop = loop
        self.offset = sqlite.get_file_offset(source_name, file_path)
        self._inode = self._get_inode()
        self._line_number = 0

    def _get_inode(self) -> int | None:
        try:
            return os.stat(self.file_path).st_ino
        except OSError:
            return None

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if event.src_path != self.file_path:
            return

        # Detect log rotation (inode changed)
        current_inode = self._get_inode()
        if current_inode != self._inode:
            logger.info("log_rotation_detected", source=self.source_name, path=self.file_path)
            self.offset = 0
            self._inode = current_inode

        try:
            with open(self.file_path, errors="replace") as f:
                f.seek(self.offset)
                new_lines = f.readlines()
                self.offset = f.tell()
        except OSError as e:
            logger.error("file_read_error", source=self.source_name, error=str(e))
            return

        for line in new_lines:
            stripped = line.rstrip("\n")
            if stripped:
                self._line_number += 1
                asyncio.run_coroutine_threadsafe(
                    self.router.submit(stripped, self.source_name, self._line_number),
                    self.loop,
                )

        # Persist offset
        self.sqlite.set_file_offset(self.source_name, self.file_path, self.offset, current_inode)


def start_file_tailer(
    source_name: str,
    file_path: str,
    router: IngestionRouter,
    sqlite: SQLiteStorage,
    loop: asyncio.AbstractEventLoop,
) -> Observer:
    """Start watching a log file. Returns the Observer (call .stop() to halt)."""
    path = Path(file_path)
    if not path.exists():
        logger.warning("file_not_found", source=source_name, path=file_path)

    handler = LogFileTailer(source_name, str(path), router, sqlite, loop)
    observer = Observer()
    observer.schedule(handler, str(path.parent), recursive=False)
    observer.start()
    logger.info("file_tailer_started", source=source_name, path=file_path)
    return observer
