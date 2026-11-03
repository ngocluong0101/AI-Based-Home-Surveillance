#!/usr/bin/env python3
"""soak.py - ROADMAP Phase 8 "Soak test": run the whole Docker stack for hours with fake cameras and check that
nothing grows or breaks over time (RAM, VRAM, process count, disk, FPS, restarts).

An offline test tool, run from the repository root on the Docker host (needs Docker, the NVIDIA toolkit, the
DeepStream image and engine/models/yolov8n.onnx; the DeepStream image is also used for the fake camera):

    python3 deploy/soak.py video  --hours 3                  # once: a long video, the sample repeated (no re-encoding)
    python3 deploy/soak.py run    --hours 3 --cameras 4      # starts a stack of its own, writes soak-<time>/, tears it down
    python3 deploy/soak.py report soak-<time>                # the verdict again, from the CSV

`run` uses its own compose project (surveillance-soak), its own web port (18081) and Telegram off, so it does not
touch a stack you run for real; start it with `setsid nohup ... &` to keep it running after the terminal closes.
Every --sample seconds it writes a row to samples.csv; it stops early when the host disk or memory gets too low. The
files in the output directory: samples.csv, events.log (anything unusual, with times), report.txt, logs-*.txt.
"""
import argparse
import csv
import json
import math
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMPOSE = REPO / "deploy" / "compose.yml"
PROJECT = "surveillance-soak"
PORT = 18081
IMAGE = "nvcr.io/nvidia/deepstream:7.1-triton-multiarch"
SAMPLE_VIDEO = REPO / "data" / "videos" / "sample_1080p_h264.mp4"
SAMPLE_SECONDS = 48.1
MIN_FREE_DISK_GB = 8.0
MIN_AVAILABLE_RAM_GB = 1.0
SERVICES = ("db", "mediamtx", "backend", "engine", "frontend", "fakecam")
WARMUP_S = 600           # ignored by the drift checks: engines, caches and pools settle
UNITS = {"b": 1, "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sh(cmd, **kw) -> str:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    return subprocess.run(cmd, **kw).stdout.strip()


def mib(text: str) -> float:
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text)
    return float(m.group(1)) * UNITS.get(m.group(2).lower(), 1) / 1024 ** 2 if m else float("nan")


# ---------------------------------------------------------------- video

def make_video(hours: float, out: Path) -> None:
    copies = math.ceil(hours * 3600 / SAMPLE_SECONDS) + 5
    out.parent.mkdir(parents=True, exist_ok=True)
    branches = " ".join("filesrc location=/in/sample.mp4 ! qtdemux ! h264parse ! c." for _ in range(copies))
    script = f"gst-launch-1.0 -q concat name=c ! h264parse ! mp4mux ! filesink location=/out/{out.name} {branches}"
    print(f"building {out} from {copies} copies of the sample ({copies * SAMPLE_SECONDS / 3600:.1f} h) ...")
    subprocess.run(["docker", "run", "--rm", "-u", "1000:1000", "-e", "HOME=/tmp",
                    "-v", f"{SAMPLE_VIDEO}:/in/sample.mp4:ro", "-v", f"{out.parent}:/out",
                    "--entrypoint", "/bin/bash", IMAGE, "-c", script], check=True)
    print(f"done: {out} ({out.stat().st_size / 1024 ** 3:.1f} GiB)")


# ---------------------------------------------------------------- the stack

class Stack:
    def __init__(self, out: Path, video: Path, cameras: int):
        self.out, self.video, self.cameras = out, video, cameras
        self.env_file = out / "soak.env"
        self.override = out / "soak.override.yml"
        self.base = f"http://127.0.0.1:{PORT}"

    def write_files(self) -> None:
        fd = os.open(self.env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(f"POSTGRES_PASSWORD={secrets.token_hex(8)}\nINTERNAL_TOKEN={secrets.token_hex(16)}\n"
                    f"ENABLE_TELEGRAM=false\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\nHTTP_PORT={PORT}\n"
                    "PUBLIC_HOST=localhost\n")
        # the fake camera: the repository's rtsp_test_server (built by `make rtsp-test-server`), reachable from the
        # engine container as rtsp://fakecam:8554/cam; every client gets its own copy of the video from the start
        # restart "no" everywhere: after a reboot the soak run is dead (it is a process on the host), and a stack that
        # Docker brought back by itself would run on with nobody watching the disk
        no_restart = "".join(f"  {svc}:\n    restart: 'no'\n" for svc in ("db", "mediamtx", "backend", "engine", "frontend"))
        self.override.write_text(
            "services:\n" + no_restart + "  fakecam:\n"
            f"    image: {IMAGE}\n    user: '1000:1000'\n    restart: 'no'\n"
            "    entrypoint: ['/repo/engine/build/rtsp_test_server']\n"
            f"    command: ['/videos/{self.video.name}', '8554', '/cam']\n"
            "    environment: {RTSP_TEST_UNSHARED: '1', HOME: /tmp}\n"
            f"    volumes:\n      - {REPO}/engine/build:/repo/engine/build:ro\n      - {self.video.parent}:/videos:ro\n")

    def compose(self, *args, **kw) -> str:
        cmd = ["docker", "compose", "--env-file", str(REPO / ".env"), "--env-file", str(self.env_file),
               "-p", PROJECT, "-f", str(COMPOSE), "-f", str(self.override), *args]
        return sh(cmd, cwd=REPO, **kw)

    def secrets_(self) -> list[str]:
        vals = dict(l.split("=", 1) for l in self.env_file.read_text().splitlines() if "=" in l)
        return [vals["POSTGRES_PASSWORD"], vals["INTERNAL_TOKEN"]]

    def api(self, method: str, path: str, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def wait_healthy(self, timeout: float = 900) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if self.api("GET", "/api/health").get("status") == "ok":
                    return
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(3)
        raise RuntimeError("the stack did not become healthy")

    def add_cameras(self) -> list[int]:
        ids = []
        for i in range(self.cameras):
            cam = self.api("POST", "/api/cameras", {"name": f"Soak {i + 1}", "source_type": "rtsp",
                                                    "source_uri": "rtsp://fakecam:8554/cam"})
            ids.append(cam["id"])
        for cid in ids:
            self.api("POST", f"/api/cameras/{cid}/start")
        return ids


# ---------------------------------------------------------------- sampling

def container(service: str) -> str:
    return f"{PROJECT}-{service}-1"


def sample(stack: Stack, cam_ids: list[int], baseline_vram: int) -> dict:
    row = {"t": now(), "epoch": round(time.time(), 1)}
    stats = {}
    for line in sh(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.MemUsage}}|{{.CPUPerc}}"]).splitlines():
        name, mem, cpu = line.split("|")
        stats[name] = (mib(mem.split("/")[0]), float(cpu.strip("%") or 0))
    for svc in SERVICES:
        mem, cpu = stats.get(container(svc), (float("nan"), float("nan")))
        row[f"rss_{svc}_mib"], row[f"cpu_{svc}_pct"] = round(mem, 1), round(cpu, 1)
    g = [x.strip() for x in sh(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu,utilization.encoder,utilization.decoder",
                                 "--format=csv,noheader,nounits"]).split(",")]
    row.update(vram_mib=int(g[0]), vram_over_baseline_mib=int(g[0]) - baseline_vram, gpu_pct=g[1], nvenc_pct=g[2], nvdec_pct=g[3])
    row["engine_procs"] = max(0, len(sh(["docker", "top", container("engine"), "-eo", "pid"]).splitlines()) - 1)
    restarts, health_bad = 0, 0
    for svc in SERVICES:
        info = sh(["docker", "inspect", "-f", "{{.RestartCount}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                   container(svc)]).split()
        if info:
            restarts += int(info[0])
            health_bad += 0 if (info[1] == "running" and (len(info) < 3 or info[2] == "healthy")) else 1
    row.update(container_restarts=restarts, containers_not_healthy=health_bad)
    fps, bad, starts, people = [], 0, 0, 0
    for cid in cam_ids:
        try:
            st = stack.api("GET", f"/api/cameras/{cid}/status")
            s = st["stream"]
            fps.append(s["fps"] or 0.0)
            bad += 0 if (s["reporting"] and s["state"] == "RUNNING") else 1
            starts = st["engine"]["starts"] or starts
            people += s.get("people") or 0
        except (urllib.error.URLError, OSError, KeyError, ValueError, TypeError):
            fps.append(0.0)
            bad += 1
    row.update(fps_min=min(fps) if fps else 0, fps_mean=round(sum(fps) / len(fps), 1) if fps else 0,
               cameras_not_running=bad, engine_starts=starts, people_now=people)
    try:
        row["events_total"] = stack.api("GET", "/api/events?limit=1")["total"]
    except (urllib.error.URLError, OSError, KeyError, ValueError, TypeError):
        row["events_total"] = ""
    du = sh(["docker", "exec", container("backend"), "du", "-sk", "/var/lib/surveillance/snapshots"]).split()
    row["snapshots_mib"] = round(int(du[0]) / 1024, 1) if du else ""
    dbs = sh(["docker", "exec", container("db"), "psql", "-U", "surveillance", "-d", "surveillance", "-Atc",
              "select pg_database_size('surveillance')"])
    row["db_mib"] = round(int(dbs) / 1024 ** 2, 1) if dbs.isdigit() else ""
    row["host_free_disk_gb"] = round(shutil.disk_usage("/").free / 1024 ** 3, 1)
    mem = {k: int(v) for k, v in re.findall(r"(\w+):\s+(\d+) kB", Path("/proc/meminfo").read_text())}
    row["host_available_ram_gb"] = round(mem.get("MemAvailable", 0) / 1024 ** 2, 1)
    return row


# ---------------------------------------------------------------- report

def column(rows, name):
    out = []
    for r in rows:
        try:
            out.append((float(r["epoch"]), float(r[name])))
        except (ValueError, KeyError):
            pass
    return out


def slope_per_hour(points) -> float:
    n = len(points)
    if n < 3:
        return 0.0
    mt, mv = sum(t for t, _ in points) / n, sum(v for _, v in points) / n
    den = sum((t - mt) ** 2 for t, _ in points)
    return 3600 * sum((t - mt) * (v - mv) for t, v in points) / den if den else 0.0


def report(out: Path, leaks: list[str] | None = None, notes: list[str] | None = None) -> str:
    rows = list(csv.DictReader((out / "samples.csv").open()))
    if len(rows) < 4:
        return "too few samples for a report"
    t0 = float(rows[0]["epoch"])
    lines = [f"Soak report {out.name}: {len(rows)} samples over {(float(rows[-1]['epoch']) - t0) / 3600:.2f} h "
             f"({rows[0]['t']} -> {rows[-1]['t']})", ""]
    verdicts = []

    def check(ok, text, warn=False):
        verdicts.append("OK" if ok else ("WARN" if warn else "FAIL"))
        lines.append(f"  [{verdicts[-1]:4}] {text}")

    lines.append("Drift (mean of the first third vs the last third of the run after a 10 min warm-up; slope per hour):")
    steady = [r for r in rows if float(r["epoch"]) - t0 >= WARMUP_S] or rows
    for name, label in [("rss_engine_mib", "engine RAM (MiB)"), ("rss_backend_mib", "backend RAM (MiB)"),
                        ("rss_db_mib", "PostgreSQL RAM (MiB)"), ("rss_mediamtx_mib", "MediaMTX RAM (MiB)"),
                        ("vram_mib", "VRAM, whole GPU (MiB)")]:
        pts = column(steady, name)
        if len(pts) < 6:
            continue
        third = len(pts) // 3
        a, b = sum(v for _, v in pts[:third]) / third, sum(v for _, v in pts[-third:]) / third
        ratio = b / a if a else 1.0
        check(ratio <= 1.10, f"{label}: {a:.0f} -> {b:.0f} ({(ratio - 1) * 100:+.1f}%, {slope_per_hour(pts):+.1f}/h)",
              warn=ratio <= 1.20)
    procs = [v for _, v in column(steady, "engine_procs")]
    if procs:
        check(max(procs) - min(procs) <= 5, f"processes in the engine container: min {min(procs):.0f}, max {max(procs):.0f}")
    lines += ["", "Stability:"]
    fps = sorted(v for _, v in column(steady, "fps_min"))
    if fps:
        p5 = fps[int(len(fps) * 0.05)]
        check(p5 >= 25, f"slowest camera: 5th percentile {p5:.1f} fps, worst {fps[0]:.1f} fps (target 30)")
    nr = [v for _, v in column(steady, "cameras_not_running")]
    check(sum(1 for v in nr if v > 0) <= len(nr) * 0.01, f"samples with a camera not RUNNING (after warm-up): {sum(1 for v in nr if v > 0)} of {len(nr)}")
    rs = [v for _, v in column(rows, "container_restarts")]
    check(not rs or rs[-1] == rs[0], f"container restarts during the run: {int(rs[-1] - rs[0]) if rs else 0}")
    es = [v for _, v in column(steady, "engine_starts")]
    check(not es or max(es) == min(es), f"engine (ds_engine) restarts after warm-up: {int(max(es) - min(es)) if es else 0}")
    unhealthy = [v for _, v in column(steady, "containers_not_healthy")]
    check(sum(1 for v in unhealthy if v > 0) <= len(unhealthy) * 0.01, f"samples with an unhealthy container (after warm-up): {sum(1 for v in unhealthy if v > 0)} of {len(unhealthy)}")
    lines += ["", "Disk and data (expected to grow: one snapshot per event; the janitor keeps SNAPSHOT_RETENTION_DAYS):"]
    hours = (float(rows[-1]["epoch"]) - t0) / 3600
    for name, label in [("snapshots_mib", "snapshots"), ("db_mib", "database")]:
        pts = column(rows, name)
        if pts and hours > 0:
            lines.append(f"  {label}: {pts[0][1]:.0f} -> {pts[-1][1]:.0f} MiB, {slope_per_hour(pts):+.0f} MiB/h")
    ev = column(rows, "events_total")
    if ev and hours > 0:
        lines.append(f"  events: {ev[-1][1]:.0f} in total, {(ev[-1][1] - ev[0][1]) / hours:.0f}/h")
    fd = column(rows, "host_free_disk_gb")
    if fd:
        lines.append(f"  host free disk: {fd[0][1]:.1f} -> {fd[-1][1]:.1f} GB")
    for key, label in [("gpu_pct", "GPU"), ("nvenc_pct", "NVENC"), ("nvdec_pct", "NVDEC")]:
        pts = [v for _, v in column(steady, key)]
        if pts:
            lines.append(f"  {label} utilization: mean {sum(pts) / len(pts):.0f}%, max {max(pts):.0f}%")
    if leaks is not None:
        lines += ["", "Secrets in the container logs:"]
        check(not leaks, "the database password and INTERNAL_TOKEN do not appear in any log" if not leaks
              else "found in: " + ", ".join(leaks))
    for n in notes or []:
        lines.append(f"  note: {n}")
    verdict = "FAIL" if "FAIL" in verdicts else ("WARN" if "WARN" in verdicts else "PASS")
    lines += ["", f"VERDICT: {verdict}"]
    text = "\n".join(lines)
    (out / "report.txt").write_text(text + "\n")
    return text


# ---------------------------------------------------------------- run

def run(args) -> int:
    video = Path(args.video).resolve()
    if not video.exists():
        sys.exit(f"{video} does not exist: run `python3 deploy/soak.py video --hours {args.hours}` first")
    if not (REPO / "engine" / "build" / "rtsp_test_server").exists():
        sys.exit("build the fake camera first: make -C engine rtsp-test-server (inside the DeepStream container)")
    if not (REPO / "engine" / "models" / "yolov8n.onnx").exists():
        sys.exit("engine/models/yolov8n.onnx is missing (python engine/tools/export_yolov8_onnx.py)")
    out = Path(args.out or f"soak-{datetime.now().strftime('%Y%m%d-%H%M')}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    stack = Stack(out, video, args.cameras)
    stack.write_files()
    events = (out / "events.log").open("a", buffering=1)

    def log(msg):
        line = f"{now()} {msg}"
        print(line, flush=True)
        events.write(line + "\n")

    stop = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.append(sig))
    notes: list[str] = []
    try:
        baseline = int(sh(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]))
        log(f"starting the stack (project {PROJECT}, port {PORT}, {args.cameras} cameras, {args.hours} h, output {out})")
        # named on purpose: not the "backup" service (it writes into the real data/backups/ and a soak run has no
        # data worth keeping)
        stack.compose("up", "-d", "--build", "db", "mediamtx", "backend", "engine", "frontend", "fakecam")
        stack.wait_healthy()
        cam_ids = stack.add_cameras()
        log(f"cameras {cam_ids} added and started")
        deadline = time.monotonic() + args.hours * 3600
        with (out / "samples.csv").open("w", newline="") as f:
            writer = None
            while time.monotonic() < deadline and not stop:
                started = time.monotonic()
                row = sample(stack, cam_ids, baseline)
                if writer is None:
                    writer = csv.DictWriter(f, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                f.flush()
                if row["cameras_not_running"] or row["containers_not_healthy"]:
                    log(f"unusual: cameras not running={row['cameras_not_running']} unhealthy containers={row['containers_not_healthy']} fps_min={row['fps_min']}")
                if row["host_free_disk_gb"] < MIN_FREE_DISK_GB or row["host_available_ram_gb"] < MIN_AVAILABLE_RAM_GB:
                    notes.append(f"stopped early: host disk {row['host_free_disk_gb']} GB free, RAM {row['host_available_ram_gb']} GB available")
                    log(notes[-1])
                    break
                time.sleep(max(0.0, args.sample - (time.monotonic() - started)))
        if stop:
            notes.append("stopped by a signal before the planned time")
    except Exception as exc:   # report what we have, then clean up
        notes.append(f"aborted: {type(exc).__name__}: {exc}")
        log(notes[-1])
    leaks = []
    try:
        for svc in SERVICES[:5]:
            text = stack.compose("logs", "--no-color", "--tail", "20000", svc)
            (out / f"logs-{svc}.txt").write_text(text)
            for secret in stack.secrets_():
                if secret in text:
                    leaks.append(svc)
        errors = sum(len(re.findall(r"\b(ERROR|Traceback|CRITICAL)\b", (out / f"logs-{s}.txt").read_text()))
                     for s in ("backend", "engine"))
        notes.append(f"ERROR/Traceback/CRITICAL lines in the backend and engine logs: {errors} (see logs-*.txt)")
    except Exception as exc:
        notes.append(f"could not collect the logs: {exc}")
    if not args.keep_up:
        stack.compose("down", "-v", "--rmi", "local")
    try:
        text = report(out, leaks, notes)
    except Exception as exc:
        text = f"no report: {type(exc).__name__}: {exc}"
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("video", help="build the long test video")
    v.add_argument("--hours", type=float, default=3.0)
    v.add_argument("--out", default=str(REPO / "data" / "videos" / "soak_loop.mp4"))
    r = sub.add_parser("run", help="run the soak test")
    r.add_argument("--hours", type=float, default=3.0)
    r.add_argument("--cameras", type=int, default=4)
    r.add_argument("--sample", type=float, default=30.0, help="seconds between samples")
    r.add_argument("--video", default=str(REPO / "data" / "videos" / "soak_loop.mp4"))
    r.add_argument("--out", help="output directory (default soak-<date>-<time> in the current directory)")
    r.add_argument("--keep-up", action="store_true", help="leave the stack running at the end")
    p = sub.add_parser("report", help="print the verdict of a finished run again")
    p.add_argument("dir")
    args = ap.parse_args()
    if args.cmd == "video":
        make_video(args.hours, Path(args.out).resolve())
    elif args.cmd == "run":
        return run(args)
    else:
        print(report(Path(args.dir).resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
