"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Asks the VLM whether a FALL or FIGHT really happened (V2-6), from a background thread.

`POST /internal/events` stores a FALL / FIGHT as `verification = pending` and wakes this thread. For each pending event it picks
the frames around the moment (backend/frames.py), asks the VLM service and records the answer:

  yes     -> confirmed
  no      -> rejected      the VLM was sure (the service only says "no" above its own threshold)
  unsure  -> unverified    the alert goes out as without a VLM
  error / no frames / too old -> unverified   FAIL-OPEN: nothing here may swallow a real fall

The Notifier holds the Telegram message of a pending event for at most `timeout` seconds. A verdict that arrives later is still
recorded. One event at a time: the VLM has one GPU.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from backend.frames import FrameSource
from backend.models import Event

log = logging.getLogger(__name__)

BATCH = 20


class Verifier:
    def __init__(self, session_factory: sessionmaker[Session], client: Any, frames: FrameSource, timeout: float, interval: float,
                 on_update: Callable[[uuid.UUID], None] | None = None, on_done: Callable[[], None] | None = None,
                 settle: float = 0.0) -> None:
        self._session_factory = session_factory
        self._client = client
        self._frames = frames
        self.timeout = timeout
        self.settle = settle       # an event is looked at only this long after it arrived: the frames AFTER it must exist (the ring)
        self.interval = interval
        self._on_update = on_update
        self._on_done = on_done            # wakes the notifier: a held message may go now
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> int:
        """Check the pending events, oldest first. Returns how many were settled."""
        with self._session_factory() as db:
            rows = db.execute(
                select(Event.event_id, Event.camera_id, Event.type, Event.timestamp, Event.occurred_at, Event.received_at,
                       Event.snapshot_file)
                .where(Event.verification == "pending").order_by(Event.received_at, Event.event_id).limit(BATCH)
            ).all()
        settled = 0
        for row in rows:
            if self._stop.is_set():
                break
            age = (datetime.now(timezone.utc) - row.received_at).total_seconds()
            if age > self.timeout:
                settled += self._settle(row.event_id, "unverified", None, f"not checked: the VLM had not answered in {self.timeout:.0f} s")
                continue
            if age < self.settle:
                continue
            frames = self._frames.for_event(row.camera_id, (row.occurred_at or row.timestamp).timestamp(), row.timestamp.timestamp(),
                                            row.snapshot_file)
            if not frames.jpegs:
                settled += self._settle(row.event_id, "unverified", None, "not checked: no picture of the event")
                continue
            try:
                answer = self._client.verify(row.type, frames.jpegs, frames.seconds_apart)
            except Exception as exc:   # the network, the service, the model: whatever it is, the alert must go out
                log.warning("VLM check of event %s failed: %s", row.event_id, type(exc).__name__)
                settled += self._settle(row.event_id, "unverified", None, f"not checked: VLM error ({type(exc).__name__})")
                continue
            note = f"{answer.reason} [{frames.source}, {len(frames.jpegs)} frame(s), {answer.latency_s:.1f} s]".strip()
            state = {"yes": "confirmed", "no": "rejected"}.get(answer.verdict, "unverified")
            if answer.verdict == "unsure":
                note = f"VLM unsure. {note}"
            settled += self._settle(row.event_id, state, answer.p_yes, note)
        return settled

    def _settle(self, event_id: uuid.UUID, state: str, score: float | None, reason: str) -> int:
        with self._session_factory() as db:
            result = db.execute(
                update(Event).where(Event.event_id == event_id, Event.verification == "pending")
                .values(verification=state, verification_score=score, verification_reason=reason[:500], verified_at=datetime.now(timezone.utc))
            )
            db.commit()
        if result.rowcount:
            for hook, args in ((self._on_update, (event_id,)), (self._on_done, ())):
                if hook is None:
                    continue
                try:
                    hook(*args)
                except Exception:
                    log.exception("hook after a VLM verdict failed")
        return int(result.rowcount)

    # -- thread ---------------------------------------------------------------------------------------

    def trigger(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self.interval <= 0:
            return
        self._thread = threading.Thread(target=self._loop, name="vlm-verifier", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(self.timeout + 5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("verification round failed")
            self._wake.wait(self.interval)
            self._wake.clear()
