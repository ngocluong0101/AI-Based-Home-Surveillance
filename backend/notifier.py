"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Telegram alerts, sent from a background thread (ROADMAP 3.8).

`POST /internal/events` only stores the event (notify_status 'pending') and wakes this thread, so a slow Telegram never delays
the engine. Pending events are sent oldest first and `notify_status` records the outcome:

  sent      Telegram accepted it
  skipped   held back on purpose: alerts off, quiet hours, or the VLM rejected it and VLM_SUPPRESS is on
  disabled  Telegram is not configured
  failed    undeliverable for longer than `max_age` seconds (an alert that late is noise)

While Telegram fails the event stays 'pending' and is retried with a growing pause (1 s doubling to 60 s). The event itself is
never lost or changed.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.app_settings import AppSettingsStore
from backend.captions import behaviour_caption
from backend.models import Camera, Event

log = logging.getLogger(__name__)

BATCH = 50
BACKOFF_START = 1.0
BACKOFF_MAX = 60.0


class Notifier:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        telegram: Any,
        snapshot_dir: str | Path,
        interval: float,
        max_age: float,
        on_update: Callable[[uuid.UUID], None] | None = None,
        store: AppSettingsStore | None = None,
        verify_timeout: float = 0.0,
        suppress_rejected: bool = False,
    ) -> None:
        self._suppress_rejected = suppress_rejected   # False: a rejected alert is sent with a note (the VLM can be wrong)
        self._verify_timeout = verify_timeout   # a message waits for the VLM at most this long (0 = never waits)
        self._store = store   # the alert rules of the Settings page; None = every event is sent
        self._session_factory = session_factory
        self._telegram = telegram
        self._snapshot_dir = Path(snapshot_dir)
        self.interval = interval
        self.max_age = max_age
        self._on_update = on_update
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._backoff = 0.0
        self._retry_at = 0.0   # monotonic time before which Telegram is not tried

    # -- one round ------------------------------------------------------------------------------------

    def run_once(self) -> bool:
        """Handle the pending events. False when Telegram failed (the rest waits for the next round)."""
        with self._session_factory() as db:
            rows = db.execute(
                select(Event.event_id, Event.track_id, Event.confidence, Event.timestamp,
                       Event.snapshot_file, Event.received_at, Event.type, Event.track_ids, Event.zone_id,
                       Event.occurred_at, Event.verification, Event.verification_reason, Camera.name.label("camera_name"))
                .join(Camera, Camera.id == Event.camera_id)
                .where(Event.notify_status == "pending")
                .where(or_(Event.verification != "pending",
                           Event.received_at <= datetime.now(timezone.utc) - timedelta(seconds=self._verify_timeout)))
                .order_by(Event.received_at, Event.event_id)
                .limit(BATCH)
            ).all()   # the session ends here: no transaction stays open during a slow Telegram call

        rules = self._store.get() if self._store is not None and rows else None   # one look per round
        now = datetime.now(timezone.utc)
        for row in rows:
            if row.verification == "rejected" and self._suppress_rejected:
                self._mark(row.event_id, "skipped")   # opt-in (VLM_SUPPRESS): stored and shown, not sent
            elif rules is not None and rules.mutes(row.received_at):
                self._mark(row.event_id, "skipped")
            elif not self._telegram.enabled:
                self._mark(row.event_id, "disabled")
            elif (now - row.received_at).total_seconds() > self.max_age:
                log.warning("giving up on the Telegram alert for event %s: undeliverable for over %.0f s",
                            row.event_id, self.max_age)
                self._mark(row.event_id, "failed")
            elif self._send(row):
                self._backoff = 0.0
                self._mark(row.event_id, "sent")
            else:
                self._backoff = min(max(self._backoff * 2, BACKOFF_START), BACKOFF_MAX)
                self._retry_at = time.monotonic() + self._backoff
                return False
            now = datetime.now(timezone.utc)
        return True

    def _send(self, row: Any) -> bool:
        # The notifier prints the time with strftime; show the server's local time, not UTC.
        event = SimpleNamespace(track_id=row.track_id, confidence=row.confidence,
                                timestamp=row.timestamp.astimezone())
        try:
            path = self._snapshot_dir / row.snapshot_file if row.snapshot_file else None
            has_photo = path is not None and path.is_file()   # the cleaner may have removed it: send text only
            if row.type != "PERSON_DETECTED":
                caption = behaviour_caption(row.type, row.camera_name, list(row.track_ids) or [row.track_id], row.zone_id,
                                            row.timestamp, row.occurred_at, row.confidence, row.verification,
                                            row.verification_reason)
                return bool(self._telegram.send_alert(caption, str(path) if has_photo else None))
            if has_photo:
                return bool(self._telegram.send_person_detected_photo(event, str(path)))
            return bool(self._telegram.send_person_detected(event))
        except Exception:   # the notifier promises not to raise; a stand-in or a bug must not kill the thread
            log.exception("Telegram send raised")
            return False

    def _mark(self, event_id: uuid.UUID, status: str) -> None:
        with self._session_factory() as db:
            result = db.execute(
                update(Event).where(Event.event_id == event_id, Event.notify_status == "pending")
                .values(notify_status=status)
            )
            db.commit()
        if result.rowcount and self._on_update is not None:
            try:
                self._on_update(event_id)
            except Exception:
                log.exception("could not announce the notify_status change")

    # -- thread ---------------------------------------------------------------------------------------

    def trigger(self) -> None:
        """A new event was stored: look now instead of at the next interval."""
        self._wake.set()

    def start(self) -> None:
        if self.interval <= 0:
            return
        self._thread = threading.Thread(target=self._loop, name="telegram-notifier", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(15)   # a Telegram call in flight is bounded by its own timeout (10 s)

    def _loop(self) -> None:
        while not self._stop.is_set():
            if time.monotonic() >= self._retry_at:
                try:
                    self.run_once()
                except Exception:
                    log.exception("notification round failed")
            self._wake.wait(self.interval)
            self._wake.clear()
