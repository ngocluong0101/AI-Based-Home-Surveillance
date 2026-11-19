"""The pictures the VLM is shown for an event (V2-6): a few frames from just before to just after it.

The engine keeps a ring of recent frames per camera (`--frame-ring-dir`, files `ring_cam<id>_<epoch ms>.jpg`, a few per second, the oldest
removed after ~15 s). Here the ones around the event are picked: from `occurred_at - before` to `timestamp + after`, `n` of them spread evenly.
The `after` matters: a skeleton-network alert comes DURING the fall, and a VLM that sees only the first half of it says "nobody fell" (12 of 30 real
falls lost at threshold 0.5 with 0.5 s after, none with 2 s: eval/vlm, ROADMAP_V2_NHAT_KY 10.3). So the verifier waits `after` seconds.
Without a ring (not configured, or the engine did not write one) the event's own snapshot is the only frame; the VLM can still look at
it, but cannot tell a fall from lying down on purpose as well as with a few frames.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

RING_NAME = re.compile(r"^ring_cam(\d+)_(\d+)\.jpg$")


@dataclass
class Frames:
    jpegs: list[bytes]
    seconds_apart: float
    source: str            # "ring" | "snapshot" | "none"


class FrameSource:
    after_default = 2.0

    def __init__(self, snapshot_dir: str | Path, ring_dir: str | Path | None = None, n: int = 6,
                 before: float = 2.5, after: float = after_default) -> None:
        self.snapshot_dir = Path(snapshot_dir)
        self.ring_dir = Path(ring_dir) if ring_dir else None
        self.n, self.before, self.after = n, before, after

    def ring_files(self, camera_id: int) -> list[tuple[float, Path]]:
        if self.ring_dir is None or not self.ring_dir.is_dir():
            return []
        out = []
        for p in self.ring_dir.iterdir():
            m = RING_NAME.match(p.name)
            if m and int(m.group(1)) == camera_id:
                out.append((int(m.group(2)) / 1000.0, p))
        return sorted(out)

    def for_event(self, camera_id: int, occurred_ts: float, reported_ts: float, snapshot_file: str | None) -> Frames:
        """occurred_ts / reported_ts: epoch seconds"""
        lo, hi = occurred_ts - self.before, reported_ts + self.after
        inside = [(t, p) for t, p in self.ring_files(camera_id) if lo <= t <= hi]
        if len(inside) >= 2:
            if len(inside) > self.n:
                step = (len(inside) - 1) / (self.n - 1)
                inside = [inside[round(i * step)] for i in range(self.n)]
            try:
                jpegs = [p.read_bytes() for _, p in inside]
                return Frames(jpegs, (inside[-1][0] - inside[0][0]) / (len(inside) - 1), "ring")
            except OSError:
                pass   # a ring file removed while we read: use what the snapshot gives
        if snapshot_file:
            path = self.snapshot_dir / snapshot_file
            try:
                return Frames([path.read_bytes()], 0.0, "snapshot")
            except OSError:
                pass
        return Frames([], 0.0, "none")
