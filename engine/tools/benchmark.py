#!/usr/bin/env python3
"""benchmark.py - ROADMAP 6.4/6.5: how many cameras does one GPU carry, and is "1 process = N cameras"
better than "N processes = 1 camera each"?

An offline test tool, not part of the product. Run it INSIDE the DeepStream container (it needs
ds_engine, nvidia-smi, and for --source rtsp the fake camera from `make rtsp-test-server`):

    docker run --rm --gpus all -u 1000:1000 -v $PWD:/repo -e HOME=/tmp --entrypoint /usr/bin/python3 \\
        nvcr.io/nvidia/deepstream:7.1-triton-multiarch /repo/engine/tools/benchmark.py \\
        --video /repo/data/videos/sample_1080p_h264.mp4 --source rtsp --layout single --cameras 1 2 3 4

Each case starts the engine(s), waits until every camera reports frames, measures for --duration
seconds (rtsp) or until the files end (files), then stops and reports:
  fps/cam   median of the per-interval FPS of each camera (the worst camera is shown)
  GPU       nvidia-smi utilization.gpu / .encoder / .decoder (mean over the window, max for GPU)
  VRAM      memory.used of the whole GPU (other apps included), max over the window; the JSON also
            has vram_mib = that minus what was in use before the case (noisy: a previous case may
            not have released its memory yet)
  CPU, RSS  of the engine process(es): % of ONE core, summed, and resident memory
--source rtsp serves the same file as N RTSP cameras in real time (30 fps each, every camera has its
own NVDEC session, like real cameras); the case passes when every camera holds --target-fps and no
camera reconnects. --source files reads the file as fast as possible, so fps/cam shows the headroom.
The fake camera serves the file once, so keep warm-up + --duration below the length of the video.

A TensorRT engine is needed per batch size (one per N for --layout single, batch 1 for multi); a
missing one is built the first time (~150 s) and kept in engine/models/. Latency is not measured here.
"""
import argparse
import json
import os
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[1]
FPS_LINE = re.compile(r"^fps source_id=(\d+) camera_id=(\d+) ([\d.]+) (\w+)")
FINAL_LINE = re.compile(r"^source_id=\d+ camera_id=(\d+) frames=\d+ .* fps=([\d.]+)$")
CLK_TCK = os.sysconf("SC_CLK_TCK")
READY_TIMEOUT_S = 600   # a missing TensorRT engine is built at startup


def make_config(batch: int, workdir: Path) -> Path:
    """pgie_yolov8n.txt with absolute paths and the engine file of this batch size."""
    out = []
    base = ENGINE_DIR / "configs"
    engine = ENGINE_DIR / "models" / f"yolov8n.onnx_b{batch}_gpu0_fp16.engine"
    for line in (base / "pgie_yolov8n.txt").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("onnx-file", "labelfile-path", "custom-lib-path"):
            line = f"{key}={(base / value).resolve()}"
        elif sep and key == "model-engine-file":
            line = f"{key}={engine}"
        out.append(line)
    path = workdir / f"pgie_b{batch}.txt"
    path.write_text("\n".join(out) + "\n")
    return path


class Engine:
    """One ds_engine process; a thread parses its periodic fps lines."""

    def __init__(self, index: int, cameras: list[int], uris: dict[int, str], config: Path, tracker: Path,
                 interval: int = 2):
        cmd = ["stdbuf", "-oL", "-eL", str(ENGINE_DIR / "build" / "ds_engine"), "--config", str(config),
               "--tracker-config", str(tracker), "--fps-interval", str(interval)]
        for cam in cameras:
            cmd += ["--camera", f"{cam}={uris[cam]}"]
        self.index = index
        self.cameras = cameras
        self.samples: list[tuple[float, int, float, str]] = []   # (monotonic, camera_id, fps, state)
        self.final: dict[int, float] = {}   # camera_id -> fps of the whole run (includes start-up)
        self.tail: list[str] = []
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     bufsize=1, start_new_session=True)
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            m = FPS_LINE.match(line)
            if m:
                self.samples.append((time.monotonic(), int(m.group(2)), float(m.group(3)), m.group(4)))
            elif FINAL_LINE.match(line):
                m = FINAL_LINE.match(line)
                self.final[int(m.group(1))] = float(m.group(2))
            elif not line.startswith("event "):
                self.tail = (self.tail + [line.rstrip()])[-8:]

    def stop(self, wait: float = 20.0) -> int | None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
        try:
            self.proc.wait(wait)
        except subprocess.TimeoutExpired:
            os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait()
        self.thread.join(5)
        return self.proc.returncode


class Sampler(threading.Thread):
    """Every second: nvidia-smi utilization/memory and the CPU/RSS of the given pids."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.rows: list[dict] = []
        self.pids: list[int] = []
        self.stopping = threading.Event()

    @staticmethod
    def _proc(pid: int) -> tuple[int, int]:
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
            ticks = int(fields[11]) + int(fields[12])
            rss = 0
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1]) // 1024
            return ticks, rss
        except (OSError, IndexError, ValueError):
            return 0, 0

    def run(self) -> None:
        query = "utilization.gpu,utilization.encoder,utilization.decoder,memory.used"
        while not self.stopping.is_set():
            out = subprocess.run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True).stdout.strip().split(",")
            try:
                gpu, enc, dec, mem = (int(x) for x in out)
            except ValueError:
                gpu = enc = dec = mem = 0
            ticks = rss = 0
            for pid in list(self.pids):
                t, r = self._proc(pid)
                ticks += t
                rss += r
            self.rows.append({"t": time.monotonic(), "gpu": gpu, "enc": enc, "dec": dec, "mem": mem,
                              "ticks": ticks, "rss": rss})
            self.stopping.wait(1.0)


def window(rows: list[dict], t0: float, t1: float) -> list[dict]:
    return [r for r in rows if t0 <= r["t"] <= t1]


def run_case(args, layout: str, n: int, workdir: Path, tracker: Path, sampler: Sampler) -> dict:
    batch = n if layout == "single" else 1
    config = make_config(batch, workdir)
    uri = (f"file://{args.video}" if args.source == "files" else f"rtsp://127.0.0.1:{args.port}/cam")
    uris = {cam: uri for cam in range(1, n + 1)}
    groups = [list(range(1, n + 1))] if layout == "single" else [[cam] for cam in range(1, n + 1)]
    baseline_mem = sampler.rows[-1]["mem"] if sampler.rows else 0
    row = {"layout": layout, "source": args.source, "cameras": n, "processes": len(groups), "ok": False}

    engines: list[Engine] = []
    launched = time.monotonic()
    try:
        for i, cams in enumerate(groups):
            engines.append(Engine(i, cams, uris, config, tracker, 1 if args.source == "files" else 2))
            if layout == "multi":
                time.sleep(2.0)   # start-up of one process is heavy; do not stack them
        sampler.pids = [e.proc.pid for e in engines]

        # ready = every camera of every process reported a non-zero FPS
        deadline = time.monotonic() + READY_TIMEOUT_S
        ready_at = None
        while time.monotonic() < deadline:
            if all(any(s[1] == cam and s[2] > 0 for s in e.samples) for e in engines for cam in e.cameras):
                ready_at = time.monotonic()
                break
            if any(e.proc.poll() is not None for e in engines):
                break
            time.sleep(0.5)
        if ready_at is None and args.source == "files":
            # a short file can end before every camera printed a line
            ready_at = min((s[0] for e in engines for s in e.samples), default=launched)
            row["includes_startup"] = True
        if ready_at is None:
            row["error"] = "not ready: " + " | ".join(l for e in engines for l in e.tail[-3:])
            return row

        if args.source == "rtsp":
            time.sleep(args.duration)
        else:
            while any(e.proc.poll() is None for e in engines):
                time.sleep(0.5)
        end = time.monotonic()
        codes = [e.stop() for e in engines]
        mem_rows = window(sampler.rows, ready_at, end)

        per_cam = []   # (median fps, min fps, lines that were not RUNNING)
        for e in engines:
            for cam in e.cameras:
                got = [s for s in e.samples if s[1] == cam and ready_at - 2.5 <= s[0] <= end + 2.5]
                if args.source == "files" and len(got) > 2:
                    got = got[1:-1]   # the first interval includes the ramp-up, the last is partial
                fps = [s[2] for s in got if s[2] > 0] or [e.final.get(cam, 0.0)]
                if args.source == "files" and not any(s[2] > 0 for s in got):
                    row["includes_startup"] = True   # only the whole-run fps is known: a lower bound
                per_cam.append((statistics.median(fps), min(fps), sum(1 for s in got if s[3] != "RUNNING")))
        span = max(end - ready_at, 1e-9)
        ticks = (mem_rows[-1]["ticks"] - mem_rows[0]["ticks"]) if len(mem_rows) > 1 else 0
        worst = min(c[0] for c in per_cam)
        row.update({
            "fps_cam_worst": round(worst, 1),
            "fps_cam_best": round(max(c[0] for c in per_cam), 1),
            "fps_dip_min": round(min(c[1] for c in per_cam), 1),
            "fps_total": round(sum(c[0] for c in per_cam), 1),
            "reconnecting_lines": sum(c[2] for c in per_cam),
            "gpu_mean": round(statistics.mean(r["gpu"] for r in mem_rows), 1) if mem_rows else None,
            "gpu_max": max((r["gpu"] for r in mem_rows), default=None),
            "enc_mean": round(statistics.mean(r["enc"] for r in mem_rows), 1) if mem_rows else None,
            "dec_mean": round(statistics.mean(r["dec"] for r in mem_rows), 1) if mem_rows else None,
            "vram_mib": max((r["mem"] for r in mem_rows), default=0) - baseline_mem,
            "vram_total_mib": max((r["mem"] for r in mem_rows), default=0),   # whole GPU, incl. other apps
            "cpu_pct_one_core": round(100.0 * ticks / CLK_TCK / span, 1),
            "rss_mib": max((r["rss"] for r in mem_rows), default=0),
            "exit_codes": codes,
            "window_s": round(span, 1),
        })
        row["ok"] = all(c == 0 for c in codes)
        if args.source == "rtsp":
            row["ok"] = row["ok"] and worst >= args.target_fps * 0.93 and row["reconnecting_lines"] == 0
        return row
    finally:
        for e in engines:
            e.stop(5)
        sampler.pids = []


def ensure_engine(batch: int, args, workdir: Path, tracker: Path) -> None:
    """Build the TensorRT engine of this batch size once (it is built at start-up of any run)."""
    engine = ENGINE_DIR / "models" / f"yolov8n.onnx_b{batch}_gpu0_fp16.engine"
    if engine.exists():
        return
    print(f"building TensorRT engine for batch {batch} (~150 s) ...", flush=True)
    uris = {cam: f"file://{args.video}" for cam in range(1, batch + 1)}
    e = Engine(0, list(range(1, batch + 1)), uris, make_config(batch, workdir), tracker)
    t0 = time.monotonic()
    while time.monotonic() - t0 < READY_TIMEOUT_S and e.proc.poll() is None and not e.samples:
        time.sleep(1)
    e.stop(10)
    if not engine.exists():
        sys.exit(f"could not build {engine.name}: " + " | ".join(e.tail))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True, help="absolute path of the 1080p test video")
    ap.add_argument("--source", choices=["rtsp", "files"], default="rtsp")
    ap.add_argument("--layout", choices=["single", "multi", "both"], default="single",
                    help="single = 1 process with N cameras, multi = N processes with 1 camera each")
    ap.add_argument("--cameras", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--duration", type=float, default=25.0, help="measuring window, rtsp only (s)")
    ap.add_argument("--target-fps", type=float, default=30.0)
    ap.add_argument("--stop-on-fail", action="store_true",
                    help="stop raising N for a layout after a failed case")
    ap.add_argument("--port", type=int, default=8554)
    ap.add_argument("--out", help="write the rows as JSON to this file")
    args = ap.parse_args()
    args.video = str(Path(args.video).resolve())

    workdir = Path(tempfile.mkdtemp(prefix="bench_"))
    tracker = ENGINE_DIR / "configs" / "tracker_nvdcf.yml"
    binary = ENGINE_DIR / "build" / "rtsp_test_server"
    if args.source == "rtsp" and not binary.exists():
        sys.exit("build the fake camera first: make -C engine rtsp-test-server")
    sampler = Sampler()
    sampler.start()
    rows: list[dict] = []
    server = None
    try:
        layouts = ["single", "multi"] if args.layout == "both" else [args.layout]
        for layout in layouts:
            for n in args.cameras:
                ensure_engine(n if layout == "single" else 1, args, workdir, tracker)
                if args.source == "rtsp":
                    server = subprocess.Popen([str(binary), args.video, str(args.port), "/cam"],
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                              start_new_session=True,
                                              env={**os.environ, "RTSP_TEST_UNSHARED": "1"})
                    time.sleep(1.5)
                try:
                    print(f"--- {layout} {args.source} N={n}", flush=True)
                    row = run_case(args, layout, n, workdir, tracker, sampler)
                finally:
                    if server is not None:
                        os.killpg(server.pid, signal.SIGKILL)
                        server.wait()
                        server = None
                rows.append(row)
                print(json.dumps(row), flush=True)
                time.sleep(3.0)   # let the GPU memory and the RTSP server settle
                if args.stop_on_fail and not row["ok"]:
                    break
    finally:
        sampler.stopping.set()
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    fmt = "{:7} {:>3} {:>4} {:>8} {:>6} {:>7} {:>6} {:>8} {:>5} {:>5} {:>9} {:>7} {:>8} {:>6}"
    print(fmt.format("layout", "N", "ok", "fps/cam", "dip", "total", "GPU%", "gpu max", "enc%", "dec%",
                     "VRAM tot", "CPU%", "RSS MiB", "reconn"))
    for r in rows:
        if "fps_cam_worst" not in r:
            print(f"{r['layout']:7} {r['cameras']:>3}  FAIL  {r.get('error', '')[:100]}")
            continue
        print(fmt.format(r["layout"], r["cameras"], "yes" if r["ok"] else "NO", r["fps_cam_worst"],
                         r["fps_dip_min"], r["fps_total"], r["gpu_mean"], r["gpu_max"], r["enc_mean"],
                         r["dec_mean"], r["vram_total_mib"], r["cpu_pct_one_core"], r["rss_mib"],
                         r["reconnecting_lines"]))
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
