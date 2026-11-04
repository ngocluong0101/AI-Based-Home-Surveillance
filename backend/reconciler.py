"""Keeps the engine manager's camera list equal to what the database says should run.

The database is the truth: a camera runs when it is enabled, not deleted and its desired_state is
'running'. A background thread compares that list (as a revision hash) with the revision the manager
reports and pushes the list when they differ. This also repairs everything by itself: a manager that
was down when a camera was started, or that was restarted and forgot its list, gets the list on the
next round, so the API calls never have to wait for the manager.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.engine_client import EngineClient, EngineManagerError
from backend.models import Camera

log = logging.getLogger(__name__)


def desired_cameras(db: Session) -> list[dict]:
    rows = db.scalars(
        select(Camera)
        .where(Camera.deleted_at.is_(None), Camera.enabled.is_(True), Camera.desired_state == "running")
        .order_by(Camera.id)
    )
    # The settings travel with the address: a change in any of them changes the revision, and the manager
    # tells the running engine to read them again (or restarts it, if the list does not fit its places).
    return [
        {
            "id": c.id,
            "uri": c.source_uri,
            "conf_threshold": c.conf_threshold,
            "absence_timeout": c.absence_timeout,
            "ai_enabled": c.ai_enabled,
        }
        for c in rows
    ]


def revision_of(cameras: list[dict]) -> str:
    """Identifies a camera list; it is sent to the manager and compared with what it reports back."""
    canonical = json.dumps(cameras, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


class Reconciler:
    def __init__(self, session_factory: sessionmaker[Session], client: EngineClient, interval: float) -> None:
        self.client = client
        self.interval = interval
        self._session_factory = session_factory
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._reachable = True   # only changes are logged, not every failed round

    def run_once(self) -> bool:
        """One comparison; pushes the list if the manager does not have it. True when they agree."""
        with self._session_factory() as db:
            cameras = desired_cameras(db)
        revision = revision_of(cameras)
        try:
            if self.client.status().get("revision") != revision:
                self.client.put_cameras(cameras, revision)
                log.info("engine manager told to run cameras %s", [c["id"] for c in cameras])
        except EngineManagerError as exc:
            if self._reachable:
                log.warning("%s; will try again", exc)
            self._reachable = False
            return False
        if not self._reachable:
            log.info("engine manager is reachable again")
        self._reachable = True
        return True

    def trigger(self) -> None:
        """Look now instead of at the next interval (after a camera changed)."""
        self._wake.set()

    def start(self) -> None:
        if self.interval <= 0:
            return
        self._thread = threading.Thread(target=self._loop, name="engine-reconciler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(self.interval + 5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("engine reconcile round failed")
            self._wake.wait(self.interval)
            self._wake.clear()
