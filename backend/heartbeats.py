"""What the engine last said about its cameras (ROADMAP 3.6).

The engine POSTs one heartbeat per process every few seconds. Only the latest one matters and it is
useless after a restart of the backend (the engine sends the next within seconds), so it is kept in
memory, not in the database.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class CameraBeat:
    state: str
    fps: float
    last_frame_age: float
    people: int | None = None   # persons in the latest frame; None = not reported


@dataclass(frozen=True)
class CameraHealth:
    """What is known about one camera right now."""

    reporting: bool                # a recent heartbeat names this camera
    state: str | None = None       # RUNNING | RECONNECTING | ENDED (a file played to the end), from that heartbeat
    fps: float | None = None
    last_frame_age: float | None = None   # seconds since the engine last saw a frame, as of the heartbeat
    heartbeat_age: float | None = None    # seconds since the heartbeat arrived; None = none yet
    people: int | None = None             # persons the camera showed in its latest frame; None = not known


class HeartbeatStore:
    """Latest heartbeat of the engine. A heartbeat replaces the one before as a whole: it lists every
    camera of the process, so a camera missing from it is not in the engine any more."""

    def __init__(self, stale_after: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._stale_after = stale_after
        self._clock = clock
        self._lock = threading.Lock()
        self._run_id: uuid.UUID | None = None
        self._received_at: float | None = None
        self._cameras: dict[int, CameraBeat] = {}

    def update(self, run_id: uuid.UUID, cameras: dict[int, CameraBeat]) -> None:
        with self._lock:
            self._run_id = run_id
            self._received_at = self._clock()
            self._cameras = dict(cameras)

    def run_id(self) -> uuid.UUID | None:
        with self._lock:
            return self._run_id

    def health(self, camera_id: int) -> CameraHealth:
        with self._lock:
            if self._received_at is None:
                return CameraHealth(reporting=False)
            age = self._clock() - self._received_at
            beat = self._cameras.get(camera_id)
        if age > self._stale_after or beat is None:
            return CameraHealth(reporting=False, heartbeat_age=round(age, 3))
        return CameraHealth(reporting=True, state=beat.state, fps=beat.fps,
                            last_frame_age=beat.last_frame_age, heartbeat_age=round(age, 3), people=beat.people)
