"""Snapshot storage management (cleanup old files to prevent disk exhaustion)."""

from __future__ import annotations

import logging
from pathlib import Path
from time import time


class SnapshotCleaner:
    """Clean up old snapshot files from a directory."""

    def __init__(self, retention_days: int) -> None:
        """
        Initialize the cleaner.

        Args:
            retention_days: Keep snapshots newer than this many days; delete older ones.
        """
        self.retention_days = max(1, retention_days)
        self.retention_seconds = self.retention_days * 86400

    def list_expired_files(self, snapshot_dir: Path) -> list[Path]:
        """
        Return list of snapshot files older than retention period.

        Args:
            snapshot_dir: Directory to scan.

        Returns:
            List of Path objects for files to delete (sorted oldest first).
        """
        if not snapshot_dir.exists():
            return []

        now = time()
        expired: list[tuple[float, Path]] = []

        for file_path in snapshot_dir.glob("PERSON_DETECTED_*.jpg"):
            if not file_path.is_file():
                continue
            try:
                mtime = file_path.stat().st_mtime
                age_seconds = now - mtime
                if age_seconds > self.retention_seconds:
                    expired.append((mtime, file_path))
            except OSError:
                pass

        expired.sort()
        return [p for _, p in expired]

    def cleanup(self, snapshot_dir: Path) -> tuple[int, int]:
        """
        Delete expired snapshot files.

        Args:
            snapshot_dir: Directory to clean.

        Returns:
            Tuple of (deleted_count, error_count).
        """
        expired = self.list_expired_files(snapshot_dir)
        if not expired:
            return 0, 0

        logger = logging.getLogger(__name__)
        deleted = 0
        errors = 0

        for file_path in expired:
            try:
                file_path.unlink()
                deleted += 1
                logger.debug("Deleted expired snapshot: %s", file_path.name)
            except OSError as exc:
                errors += 1
                logger.warning("Failed to delete %s: %s", file_path.name, exc)

        if deleted > 0:
            logger.info("Snapshot cleanup: deleted %d old file(s)", deleted)
        if errors > 0:
            logger.warning("Snapshot cleanup: %d deletion error(s)", errors)

        return deleted, errors
