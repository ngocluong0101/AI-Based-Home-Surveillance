"""EngineManager: keeps one ds_engine process running for the cameras it was told about.

One process serves all cameras (one batched pipeline), so the camera list is the process's command
line: when the list changes the process is stopped (SIGTERM, so it prints its summary and flushes its
spool) and started again with the new list; when the process dies it is started again after a growing
delay. The manager is told *what* should run (set_cameras); it does not know about the database.

RTSP URLs hold passwords, so they never go into the command line (`ps` would show them): they are
written to a file readable by the owner only and given to the engine with --cameras-file.
The backend token reaches the engine through the environment variable DS_ENGINE_TOKEN, for the same reason.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
engine_log = logging.getLogger("engine")   # what the engine itself prints

_SECRET_IN_URL = re.compile(r"(\w+://)[^/\s@]+@")


@dataclass(frozen=True)
class CameraSpec:
    id: int
    uri: str


@dataclass
class ManagerConfig:
    command: list[str]                       # the program (or `docker run ... ds_engine`) to start
    engine_args: list[str]                   # everything but the cameras file, e.g. --tracker-config ...
    runtime_dir: Path                        # holds the cameras file
    # nvinfer config to start from. When set, every start gets a copy of it made for the number of
    # cameras (see write_engine_config) as --config; when None, engine_args carries its own --config.
    engine_config: Path | None = None
    token: str | None = None                 # becomes DS_ENGINE_TOKEN
    stop_timeout: float = 25.0               # SIGTERM -> SIGKILL (the engine needs up to ~12 s to flush)
    backoff_base: float = 1.0                # delay before the first restart after a crash, then doubled
    backoff_max: float = 60.0
    stable_after: float = 30.0               # a run this long makes the next crash start from backoff_base again
    poll_interval: float = 0.2

    @classmethod
    def from_env(cls) -> "ManagerConfig":
        from src import config

        runtime = Path(config.ENGINE_RUNTIME_DIR).resolve()
        # Absolute paths: when the engine runs in a container they are the paths inside it too.
        args = [
            "--tracker-config", str(Path(config.ENGINE_TRACKER_CONFIG).resolve()),
            "--backend-url", config.ENGINE_BACKEND_URL,
            "--spool-file", str(runtime / "events.spool"),
            "--absence-timeout", str(config.ABSENCE_TIMEOUT),
            "--fps-interval", "0",
        ]
        if config.ENGINE_STREAM_URL:
            args += ["--stream-url", config.ENGINE_STREAM_URL]
        if config.ENGINE_HEARTBEAT_URL:
            args += ["--heartbeat-url", config.ENGINE_HEARTBEAT_URL]
        if config.ENABLE_SNAPSHOT:
            args += ["--snapshot-dir", str(Path(config.SNAPSHOT_DIR).resolve())]
        args += shlex.split(config.ENGINE_EXTRA_ARGS)
        return cls(command=shlex.split(config.ENGINE_COMMAND), engine_args=args,
                   runtime_dir=runtime, token=config.INTERNAL_TOKEN,
                   engine_config=Path(config.ENGINE_CONFIG).resolve())


# Keys of an nvinfer config that hold a file path; relative ones are relative to the config's directory.
_PATH_KEYS = ("onnx-file", "model-file", "proto-file", "labelfile-path", "custom-lib-path",
              "int8-calib-file", "mean-file", "tlt-encoded-model", "custom-network-config",
              "model-engine-file")


def write_engine_config(base: Path, out_dir: Path, batch: int) -> tuple[Path, Path | None]:
    """Write a copy of the nvinfer config `base` for `batch` cameras; return (copy, TensorRT engine file).

    A TensorRT engine is built for one maximum batch size and nvinfer keeps one file per size, named
    <onnx file>_b<N>_gpu0_fp16.engine, next to the ONNX file. The config names a single engine file, and
    when it does not match the number of cameras nvinfer builds a new engine at EVERY start (~150 s).
    So the copy points model-engine-file at the file of this batch size: the first start with N cameras
    builds it (nvinfer saves it there), later ones just load it. Paths are made absolute because the copy
    lives elsewhere. A config without onnx-file is copied as it is (no way to name the engine).
    Delete the *_b*_*.engine files when the ONNX model is exported again: nvinfer does not check them.
    """
    lines = base.read_text(encoding="utf-8").splitlines()
    onnx = None
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key.strip() == "onnx-file":
            onnx = (base.parent / value.strip()).resolve()
    engine = None
    if onnx is not None:
        engine = onnx.with_name(f"{onnx.name}_b{batch}_gpu0_fp16.engine")

    out, seen_engine = [], False
    for line in lines:
        key, sep, value = line.partition("=")
        name = key.strip()
        if sep and not line.lstrip().startswith("#") and name in _PATH_KEYS:
            if name == "model-engine-file" and engine is not None:
                line, seen_engine = f"model-engine-file={engine}", True
            else:
                line = f"{name}={(base.parent / value.strip()).resolve()}"
                seen_engine = seen_engine or name == "model-engine-file"
        out.append(line)
    if engine is not None and not seen_engine:   # no model-engine-file line: add one to [property]
        at = next((i + 1 for i, l in enumerate(out) if l.strip() == "[property]"), 0)
        out.insert(at, f"model-engine-file={engine}")

    path = out_dir / f"pgie_b{batch}.txt"
    out_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".pgie-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.chmod(tmp, 0o644)   # mkstemp makes it 600; a container running as another user must read it
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path, engine


def _keyfile_value(text: str) -> str:
    """Escape a value for the GKeyFile format the engine reads."""
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ValueError("control characters are not allowed in a camera URI")
    return text.replace("\\", "\\\\")


def write_cameras_file(path: Path, cameras: list[CameraSpec]) -> None:
    """[camera.<id>] uri=... for each camera, mode 600 (the engine refuses anything wider)."""
    lines: list[str] = []
    for cam in cameras:
        lines += [f"[camera.{cam.id}]", f"uri={_keyfile_value(cam.uri)}", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".cameras-")   # mkstemp creates it with mode 600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@dataclass
class _Run:
    proc: subprocess.Popen
    started: float
    reader: threading.Thread


@dataclass
class _State:
    name: str = "stopped"           # stopped | running | stopping | backoff | exited
    starts: int = 0
    last_exit_code: int | None = None
    last_error: str | None = None
    started_at: datetime | None = None
    running_ids: tuple[int, ...] = ()
    revision: str | None = None
    desired: tuple[CameraSpec, ...] = ()
    backoff: float = 0.0
    next_start: float = 0.0
    run: _Run | None = field(default=None, repr=False)


class EngineManager:
    def __init__(self, config: ManagerConfig) -> None:
        self.cfg = config
        self._lock = threading.Lock()
        self._st = _State()
        self._wake = threading.Event()
        self._shutdown = False
        self._thread: threading.Thread | None = None
        self._ran_for: tuple[CameraSpec, ...] = ()   # the list the current/last process got

    # ---- public ----

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="engine-manager", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the engine (gracefully) and the supervisor."""
        self._shutdown = True
        self._wake.set()
        if self._thread is not None:
            self._thread.join(self.cfg.stop_timeout + 10)

    def set_cameras(self, cameras: list[CameraSpec], revision: str | None = None) -> None:
        """The cameras that should be running now. The process is restarted only if the list changed."""
        wanted = tuple(sorted(cameras, key=lambda c: c.id))
        for cam in wanted:
            _keyfile_value(cam.uri)   # reject what cannot be written, before anything changes
        with self._lock:
            self._st.desired = wanted
            self._st.revision = revision
        self._wake.set()

    def status(self) -> dict:
        with self._lock:
            st = self._st
            run = st.run
            uptime = round(time.monotonic() - run.started, 1) if run else None
            return {
                "state": st.name,
                "pid": run.proc.pid if run else None,
                "started_at": st.started_at.isoformat() if st.started_at else None,
                "uptime_s": uptime,
                "starts": st.starts,
                "last_exit_code": st.last_exit_code,
                "last_error": st.last_error,
                "running_cameras": list(st.running_ids),
                "desired_cameras": [c.id for c in st.desired],
                "revision": st.revision,
            }

    # ---- supervisor thread ----

    def _loop(self) -> None:
        while not self._shutdown:
            try:
                self._tick()
            except Exception:   # the supervisor must not die
                log.exception("engine manager tick failed")
            self._wake.wait(self.cfg.poll_interval)
            self._wake.clear()
        self._terminate()

    def _tick(self) -> None:
        with self._lock:
            want = self._st.desired
            run = self._st.run
        now = time.monotonic()

        if run is not None:
            rc = run.proc.poll()
            if rc is not None:
                self._on_exit(rc)
                return
            if want == self._ran_for:
                if self._st.backoff and now - run.started >= self.cfg.stable_after:
                    with self._lock:
                        self._st.backoff = 0.0   # it has been fine for long enough
                return
            log.info("camera list changed, restarting the engine")
            self._terminate()   # then fall through and start it with the new list

        with self._lock:
            st = self._st
            if want != self._ran_for:
                st.backoff = 0.0   # a new list is not a retry of the failed one
                st.next_start = 0.0
            if not want:
                st.name = "stopped"
                return
            if st.name == "exited" and want == self._ran_for:
                return   # it finished by itself (every source ended); only a new list starts it again
            if now < st.next_start:
                st.name = "backoff"
                return
        self._spawn(want)

    def _spawn(self, cameras: tuple[CameraSpec, ...]) -> None:
        cfg = self.cfg
        cameras_file = cfg.runtime_dir / "cameras.ini"
        args = [*cfg.command, "--cameras-file", str(cameras_file), *cfg.engine_args]
        env = dict(os.environ)
        env.pop("DS_ENGINE_TOKEN", None)
        if cfg.token:
            env["DS_ENGINE_TOKEN"] = cfg.token
        self._ran_for = cameras
        try:
            if cfg.engine_config is not None:
                pgie, trt_engine = write_engine_config(cfg.engine_config, cfg.runtime_dir, len(cameras))
                args += ["--config", str(pgie)]
                if trt_engine is not None and not trt_engine.exists():
                    log.warning("no TensorRT engine for %d camera(s) yet (%s): the engine builds it at "
                                "this start, which takes about 2-3 minutes, then keeps it",
                                len(cameras), trt_engine.name)
            write_cameras_file(cameras_file, list(cameras))
            proc = subprocess.Popen(args, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        except (OSError, ValueError) as exc:
            # The message of an OSError names a file, never a URI.
            log.error("could not start the engine: %s", exc)
            self._failed(f"could not start: {exc}")
            return
        reader = threading.Thread(target=self._pump, args=(proc,), name="engine-output", daemon=True)
        reader.start()
        with self._lock:
            st = self._st
            st.run = _Run(proc, time.monotonic(), reader)
            st.name = "running"
            st.starts += 1
            st.started_at = datetime.now(timezone.utc)
            st.running_ids = tuple(c.id for c in cameras)
            st.last_error = None
        log.info("engine started (pid %d) for cameras %s", proc.pid, [c.id for c in cameras])

    @staticmethod
    def _pump(proc: subprocess.Popen) -> None:
        """Copy the engine's output into the log, with passwords in URLs removed."""
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = _SECRET_IN_URL.sub(r"\1***@", raw.decode("utf-8", errors="replace").rstrip())
            if line:
                engine_log.info(line)

    def _failed(self, why: str) -> None:
        with self._lock:
            st = self._st
            st.last_error = why
            st.backoff = min(max(st.backoff * 2, self.cfg.backoff_base), self.cfg.backoff_max)
            st.next_start = time.monotonic() + st.backoff
            st.name = "backoff"
            st.running_ids = ()

    def _on_exit(self, rc: int) -> None:
        with self._lock:
            run = self._st.run
            self._st.run = None
            self._st.last_exit_code = rc
            self._st.running_ids = ()
            ran = time.monotonic() - run.started if run else 0.0
        if run:
            run.reader.join(2)
        if rc == 0:
            # Exit 0 = every source reached its end (video files); nothing to restart.
            log.info("engine finished (exit 0)")
            with self._lock:
                self._st.name = "exited"
                self._st.backoff = 0.0
            return
        log.error("engine exited with code %d after %.0f s", rc, ran)
        if ran >= self.cfg.stable_after:
            with self._lock:
                self._st.backoff = 0.0
        self._failed(f"exited with code {rc}")

    def _terminate(self) -> None:
        with self._lock:
            run = self._st.run
            if run is None:
                return
            self._st.name = "stopping"
        proc = run.proc
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(self.cfg.stop_timeout)
            except subprocess.TimeoutExpired:
                log.error("engine ignored SIGTERM for %.0f s, killing it", self.cfg.stop_timeout)
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(5)
        run.reader.join(2)
        with self._lock:
            self._st.last_exit_code = proc.returncode
            self._st.run = None
            self._st.running_ids = ()
            self._st.name = "stopped"
