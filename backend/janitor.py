"""Removes old snapshot files (ROADMAP 3.8), with `SnapshotCleaner` from src/snapshot_management.py.

A pass runs once at start-up and then whenever `every_n` snapshots were stored since the last one
(SNAPSHOT_CLEANUP_INTERVAL: it avoids scanning the disk after every event), or `max_idle` seconds have
passed, so a quiet system is cleaned too. After the files are gone the events stop pointing at them:
`snapshot_file` is cleared for events older than the retention, and `snapshot_url` disappears from the
API instead of leading to a 404.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from backend.models import Event
from src.snapshot_management import SnapshotCleaner

log = logging.getLogger(__name__)


class SnapshotJanitor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        snapshot_dir: str | Path,
        retention_days: int,
        every_n: int,
        check_interval: float,
        max_idle: float = 3600.0,
        retention_source: Callable[[], int] | None = None,
    ) -> None:
        # The Settings page can change the number of days at any time: when given, it is asked at each pass.
        self._retention_source = retention_source
        self._session_factory = session_factory
        self._snapshot_dir = Path(snapshot_dir)
        self._cleaner = SnapshotCleaner(retention_days)
        self._every_n = max(1, every_n)
        self.check_interval = check_interval
        self._max_idle = max_idle
        self._lock = threading.Lock()
        self._new_snapshots = 0
        self._last_run = float("-inf")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def note_snapshot(self) -> None:
        """An event with a snapshot was stored."""
        with self._lock:
            self._new_snapshots += 1

    def nudge(self) -> None:
        """Something changed (the retention): run at the next look instead of waiting for the hourly pass."""
        with self._lock:
            self._last_run = float("-inf")

    def due(self) -> bool:
        with self._lock:
            return (self._new_snapshots >= self._every_n
                    or time.monotonic() - self._last_run >= self._max_idle)

    def run_once(self) -> tuple[int, int]:
        """One pass. Returns (files deleted, deletion errors)."""
        with self._lock:
            self._new_snapshots = 0
            self._last_run = time.monotonic()
        if self._retention_source is not None:
            try:
                self._cleaner = SnapshotCleaner(self._retention_source())
            except Exception:   # keep cleaning with the days we had rather than not at all
                log.exception("could not read the snapshot retention setting")
        deleted, errors = self._cleaner.cleanup(self._snapshot_dir)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._cleaner.retention_seconds)
        with self._session_factory() as db:
            cleared = db.execute(
                update(Event).where(Event.snapshot_file.is_not(None), Event.received_at < cutoff)
                .values(snapshot_file=None)
            ).rowcount
            db.commit()
        if cleared:
            log.info("snapshot cleanup: %d event(s) no longer point at an image", cleared)
        return deleted, errors

    def start(self) -> None:
        if self.check_interval <= 0:
            return
        self._thread = threading.Thread(target=self._loop, name="snapshot-janitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(self.check_interval + 10)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.due():
                try:
                    self.run_once()
                except Exception:
                    log.exception("snapshot cleanup failed")
            self._stop.wait(self.check_interval)
