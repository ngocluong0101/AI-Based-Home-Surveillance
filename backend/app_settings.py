"""Settings kept in the database and changed from the web app (the Settings page).

They are what a person decides at run time (alerts on or off, quiet hours, how long snapshots are kept, what a new
camera starts with), as opposed to what the installation is made of (addresses, tokens, ports), which stays in `.env`.
Nothing here is secret. A setting nobody changed has the value the environment gave it (`defaults`), so an existing
installation behaves as before until the page is used.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, time
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from backend.models import AppSetting

log = logging.getLogger(__name__)


def parse_hhmm(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


@dataclass(frozen=True)
class AppSettings:
    alerts_enabled: bool = True            # off: events are stored and shown, but no Telegram message is sent
    quiet_hours_enabled: bool = False
    quiet_start: str = "22:00"             # server's local time
    quiet_end: str = "06:00"               # earlier than the start = the hours run past midnight
    snapshot_retention_days: int = 7
    default_conf_threshold: float = 0.25   # a new camera starts with these two (the form is filled with them)
    default_absence_timeout: float = 10.0

    def in_quiet_hours(self, local: datetime) -> bool:
        """Is this (local) moment inside the quiet hours? `quiet_start` is included, `quiet_end` is not."""
        if not self.quiet_hours_enabled:
            return False
        now, start, end = local.time().replace(second=0, microsecond=0), parse_hhmm(self.quiet_start), parse_hhmm(self.quiet_end)
        if start == end:
            return False
        return start <= now < end if start < end else (now >= start or now < end)

    def mutes(self, received_at: datetime) -> bool:
        """Should the alert of an event received at this moment (timezone-aware) not be sent?"""
        return not self.alerts_enabled or self.in_quiet_hours(received_at.astimezone())


class AppSettingsStore:
    """Reads and writes the settings (one row per setting in `app_settings`)."""

    def __init__(self, session_factory: sessionmaker[Session], defaults: AppSettings | None = None) -> None:
        self._session_factory = session_factory
        self.defaults = defaults or AppSettings()
        self._names = {f.name for f in fields(AppSettings)}

    def get(self) -> AppSettings:
        with self._session_factory() as db:
            stored = {row.key: row.value for row in db.execute(select(AppSetting.key, AppSetting.value))}
        known: dict[str, Any] = {}
        for key, value in stored.items():
            if key in self._names:
                known[key] = value
            else:
                log.warning("ignoring the unknown stored setting %r", key)
        return replace(self.defaults, **known)

    def update(self, changes: dict[str, Any]) -> AppSettings:
        """Stores the given settings (already checked by the caller) and returns all of them."""
        unknown = set(changes) - self._names
        if unknown:
            raise KeyError(f"unknown setting(s): {', '.join(sorted(unknown))}")
        with self._session_factory() as db:
            for key, value in changes.items():
                stmt = insert(AppSetting).values(key=key, value=value)
                db.execute(stmt.on_conflict_do_update(index_elements=[AppSetting.key],
                                                      set_={"value": stmt.excluded.value, "updated_at": func.now()}))
            db.commit()
        return self.get()


def snapshot_usage(snapshot_dir: str | Path) -> tuple[int, int]:
    """(number of snapshot files, their total size in bytes), as the cleaner sees them."""
    count = size = 0
    try:
        for path in Path(snapshot_dir).glob("PERSON_DETECTED_*.jpg"):
            try:
                size += path.stat().st_size
                count += 1
            except OSError:   # removed while we count
                continue
    except OSError:
        return 0, 0
    return count, size


def as_dict(settings: AppSettings) -> dict[str, Any]:
    return asdict(settings)
