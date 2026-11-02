"""Push to browsers over the WebSocket (ROADMAP 3.7).

The endpoints that learn something (a new event, a heartbeat) are plain `def` functions that run in
FastAPI's thread pool, while the WebSocket handlers run on the event loop. `Hub.publish` is the bridge:
callable from any thread, it hands the message to the loop, which puts it into a small queue per
connected client. A client that cannot keep up is dropped, so a stuck browser never holds memory or
slows down `POST /internal/events`.

Messages are `{"type": "event.created", "data": {...EventOut...}}` and
`{"type": "camera.status", "data": {"camera_id", "reporting", "state", "fps", "last_frame_age",
"heartbeat_age"}}`. Nothing is replayed: a client that connects later reads the current state with
the REST API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import asdict
from typing import Any

from backend.heartbeats import HeartbeatStore

log = logging.getLogger(__name__)

CLIENT_QUEUE_SIZE = 100   # messages a client may be behind before it is dropped


class Connection:
    """One WebSocket client: the queue its sender task reads. Touched on the event loop only."""

    def __init__(self, size: int) -> None:
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=size)

    def offer(self, text: str) -> bool:
        """Queue a message; False (and the queue holds only the close marker) when the client is too slow."""
        try:
            self.queue.put_nowait(text)
            return True
        except asyncio.QueueFull:
            while not self.queue.empty():
                self.queue.get_nowait()
            self.queue.put_nowait(None)   # tells the sender task to close the socket
            return False


class Hub:
    def __init__(self, queue_size: int = CLIENT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connections: set[Connection] = set()   # event loop only

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def detach(self) -> None:
        self._loop = None

    def connect(self) -> Connection:
        conn = Connection(self._queue_size)
        self._connections.add(conn)
        return conn

    def disconnect(self, conn: Connection) -> None:
        self._connections.discard(conn)

    @property
    def clients(self) -> int:
        return len(self._connections)

    def publish(self, type_: str, data: dict[str, Any]) -> None:
        """From any thread. A no-op while the loop is not running (before start-up, after shutdown)."""
        loop = self._loop
        if loop is None:
            return
        text = json.dumps({"type": type_, "data": data}, separators=(",", ":"))
        try:
            loop.call_soon_threadsafe(self._deliver, text)
        except RuntimeError:   # loop closed between the check and the call
            pass

    def _deliver(self, text: str) -> None:
        for conn in list(self._connections):
            if not conn.offer(text):
                self._connections.discard(conn)
                log.warning("dropped a WebSocket client that was %d messages behind", self._queue_size)


class CameraStatusPublisher:
    """Turns heartbeats into `camera.status` messages.

    A message goes out for every camera in each heartbeat (so a screen gets the frame rate every few
    seconds) and once more, with `reporting: false`, when a camera that was reporting stops: it left
    the heartbeat, or the heartbeats themselves stopped (found by `check_stale`, run on a timer).
    """

    def __init__(self, store: HeartbeatStore, hub: Hub) -> None:
        self._store = store
        self._hub = hub
        self._lock = threading.Lock()
        self._reporting: set[int] = set()

    def _publish(self, camera_id: int) -> None:
        health = self._store.health(camera_id)
        self._hub.publish("camera.status", {"camera_id": camera_id, **asdict(health)})

    def on_heartbeat(self, camera_ids: set[int]) -> None:
        with self._lock:
            gone = self._reporting - camera_ids
            self._reporting = set(camera_ids)
        for camera_id in sorted(camera_ids | gone):
            self._publish(camera_id)

    def check_stale(self) -> None:
        with self._lock:
            stale = {c for c in self._reporting if not self._store.health(c).reporting}
            self._reporting -= stale
        for camera_id in sorted(stale):
            self._publish(camera_id)
