"""A test rig around the REAL ds_engine: the DeepStream container, the GPU, a fake backend that records what the
engine sends, and (for the video tests) a MediaMTX container.

Nothing here is a mock of the engine. The tests change the cameras file while it runs, send it SIGHUP, and look at
what it did through the same channels the product uses: the events and heartbeats it POSTs, its own output, and
the streams that arrive at the media server.
"""

from __future__ import annotations

import collections
import itertools
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from engine_manager.manager import write_engine_config

REPO = Path(__file__).resolve().parents[2]
IMAGE = "nvcr.io/nvidia/deepstream:7.1-triton-multiarch"
MEDIAMTX_IMAGE = "bluenviron/mediamtx:latest"
FPS_LINE = re.compile(r"^fps source_id=\d+ camera_id=(\d+) ([\d.]+) (\w+)")
_names = itertools.count(1)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_until(pred, timeout: float, what: str, interval: float = 0.5):
    """Poll until pred() is truthy; on timeout fail with `what`."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = pred()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout:.0f} s waiting for: {what}")


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


class MediaServer:
    """MediaMTX on the host network (RTMP 1935 in, RTSP 8554 out)."""

    def __init__(self) -> None:
        self.name = f"engine-test-mtx-{os.getpid()}-{next(_names)}"
        docker("run", "-d", "--rm", "--name", self.name, "--network", "host", MEDIAMTX_IMAGE)
        time.sleep(2)

    def stop(self) -> None:
        docker("rm", "-f", self.name, check=False)


class EngineRig:
    """`slots` places for cameras (--max-cameras); `cameras` is {camera_id: extra lines for its [camera.N] group}."""

    def __init__(self, video: Path, slots: int, cameras: dict[int, list[str]], stream: bool = False) -> None:
        self.video = video
        self.slots = slots
        self.work = Path(tempfile.mkdtemp(prefix="engine-test-", dir=REPO / "logs"))
        self.name = f"engine-test-{os.getpid()}-{next(_names)}"
        self.stream = stream
        self.media: MediaServer | None = None
        self.port = free_port()
        self.events_log = self.work / "events.jsonl"
        self.hb_log = self.work / "heartbeats.jsonl"
        self.write_cameras(cameras)
        self.receiver = subprocess.Popen(
            [sys.executable, str(REPO / "engine/tools/fake_receiver.py"), "--port", str(self.port),
             "--log", str(self.events_log), "--heartbeat-log", str(self.hb_log)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        pgie, _ = write_engine_config(REPO / "engine/configs/pgie_yolov8n.txt", self.work, slots)
        args = ["--config", str(pgie), "--tracker-config", "configs/tracker_nvdcf.yml",
                "--cameras-file", str(self.work / "cameras.ini"), "--max-cameras", str(slots), "--fps-interval", "1",
                "--backend-url", f"http://127.0.0.1:{self.port}/internal/events",
                "--spool-file", str(self.work / "spool.jsonl"),
                "--heartbeat-url", f"http://127.0.0.1:{self.port}/internal/heartbeat", "--heartbeat-interval", "1"]
        if stream:
            self.media = MediaServer()
            args += ["--stream-url", "rtmp://127.0.0.1:1935/live"]
        docker("run", "-d", "--name", self.name, "--gpus", "all", "-u", "1000:1000", "--network", "host",
               "-v", f"{REPO}:{REPO}", "-v", f"{self.work}:{self.work}",
               "-v", f"{video.parent}:{video.parent}:ro", "-w", f"{REPO}/engine",
               "--entrypoint", "./build/ds_engine", IMAGE, *args)

    # ---- the cameras file and the signal

    def write_cameras(self, cameras: dict[int, list[str]], raw: str | None = None) -> None:
        """Replace the cameras file in one step, as the engine manager does. `raw` writes this text instead."""
        text = raw if raw is not None else "".join(
            f"[camera.{cid}]\nuri=file://{self.video}\n" + "".join(f"{line}\n" for line in extra) + "\n"
            for cid, extra in cameras.items())
        tmp = self.work / "cameras.new"
        tmp.write_text(text)
        tmp.chmod(0o600)
        tmp.replace(self.work / "cameras.ini")

    def hup(self) -> None:
        docker("kill", "--signal=HUP", self.name)

    def _reload_lines(self) -> int:
        return sum(1 for line in self.log().splitlines() if line.startswith("reload: "))

    def reload(self, cameras: dict[int, list[str]] | None = None, raw: str | None = None, settle: float = 3.0) -> None:
        """Write the file, tell the engine, wait until it has answered, and give it a moment."""
        before = self._reload_lines()
        self.write_cameras(cameras or {}, raw)
        self.hup()
        wait_until(lambda: self._reload_lines() > before, 30, "the engine to answer the SIGHUP")
        time.sleep(settle)

    # ---- what the engine did

    def log(self) -> str:
        r = docker("logs", self.name, check=False)
        return r.stdout + r.stderr

    def running(self) -> bool:
        return docker("inspect", "-f", "{{.State.Running}}", self.name, check=False).stdout.strip() == "true"

    def events(self) -> collections.Counter:
        if not self.events_log.exists():
            return collections.Counter()
        return collections.Counter(
            json.loads(line)["camera_id"] for line in self.events_log.read_text().splitlines() if line.strip())

    def last_heartbeat(self) -> dict[int, dict]:
        if not self.hb_log.exists():
            return {}
        lines = [x for x in self.hb_log.read_text().splitlines() if x.strip()]
        return {c["camera_id"]: c for c in json.loads(lines[-1])["cameras"]} if lines else {}

    def heartbeat_ids(self) -> list[int]:
        return sorted(self.last_heartbeat())

    def window(self, seconds: float) -> dict[int, int]:
        """Events that arrived from each camera during the next `seconds`."""
        before = self.events()
        time.sleep(seconds)
        after = self.events()
        return {cid: after[cid] - before[cid] for cid in set(after) | set(before)}

    def fps_readings(self) -> dict[int, list[tuple[float, str]]]:
        out: dict[int, list[tuple[float, str]]] = collections.defaultdict(list)
        for line in self.log().splitlines():
            m = FPS_LINE.match(line)
            if m:
                out[int(m.group(1))].append((float(m.group(2)), m.group(3)))
        return out

    def wait_ready(self, camera_ids: list[int], timeout: float = 300) -> None:
        wait_until(lambda: all(self.events()[c] > 0 for c in camera_ids), timeout, f"events from cameras {camera_ids}")

    # ---- the end

    def stop(self) -> int | None:
        """SIGTERM and the engine's exit code (None if it was already gone)."""
        if not self.running():
            return None
        docker("kill", "--signal=TERM", self.name, check=False)
        r = docker("wait", self.name, check=False)
        return int(r.stdout.strip()) if r.stdout.strip().lstrip("-").isdigit() else None

    def close(self) -> None:
        self.receiver.terminate()
        docker("rm", "-f", self.name, check=False)
        if self.media is not None:
            self.media.stop()
