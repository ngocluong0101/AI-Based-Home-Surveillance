"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

M4 on the real path: a clip plays in real time from a fake RTSP camera -> the real ds_engine (pose, rules) in the DeepStream container ->
HTTP -> the real backend -> a throwaway PostgreSQL -> WebSocket. M4 = (time the alert arrives at a WebSocket client) - (time the body
touches the floor in the video), on one machine's clock.

    eval/.venv/bin/python eval/e2e/latency.py --clips fall-01,fall-02,fall-03,fall-04,fall-05 --repeats 3 --set fall_still_s=1.0

All times are CLOCK_REALTIME of this host (containers share it). P = wall time of clip time 0 on the camera (fitted from
rtsp_test_server's printed wall/PTS pairs, on the paced ones); t_impact = P + impact_s of the label. The delay is cut into pieces:
  label_vs_rule     rule impact - label impact: not a delay, the rule's idea of the impact differs from the person who labelled it
  rule              timestamp - occurred_at: waiting for the person to stay down (fall_still_s + settle)
  camera_to_backend received_at - (P + clip time of the report): RTSP, jitter buffer, decode, mux, pose, rule, snapshot, HTTP, DB commit
  backend_to_ws     ws - received_at
Not measured: Telegram (needs the real bot), the VLM (V2-6), more than one camera.
"""
import argparse
import asyncio
import datetime
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from eval.e2e import labels, replay  # noqa: E402

IMAGE = "nvcr.io/nvidia/deepstream:7.1-triton-multiarch"
PG = "m4-latency-pg"
PG_PORT, API_PORT, RTSP_PORT = 55433, 18090, 8556
WORK = REPO / "eval/work/m4"
DB_URL = f"postgresql://postgres:test@127.0.0.1:{PG_PORT}/m4"
PGIE = "/work/eval/work/pgie_pose_b1.txt"


def sh(*args, check=True, **kw):
    return subprocess.run(args, capture_output=True, text=True, check=check, **kw)


def api(method: str, path: str, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{API_PORT}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read() or "null")


def to_ns(d: datetime.datetime) -> int:
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return int(d.timestamp()) * 1_000_000_000 + d.microsecond * 1000


class WsListener(threading.Thread):
    """Collects (wall_ns at receipt, message) of every WebSocket message."""

    def __init__(self):
        super().__init__(daemon=True)
        self.messages: list[tuple[int, dict]] = []
        self.ready = threading.Event()
        self.stop = threading.Event()

    def run(self):
        import websockets

        async def go():
            async with websockets.connect(f"ws://127.0.0.1:{API_PORT}/api/ws") as ws:
                self.ready.set()
                while not self.stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), 0.2)
                    except asyncio.TimeoutError:
                        continue
                    self.messages.append((time.time_ns(), json.loads(raw)))
        asyncio.run(go())

    def falls(self, camera_id: int):
        return [(t, m["data"]) for t, m in self.messages if m.get("type") == "event.created" and m["data"]["camera_id"] == camera_id
                and m["data"]["type"] == "FALL"]


class Server:
    """rtsp_test_server in the container; reads first_packet_ns from its stdout."""

    def __init__(self, clip: Path):
        self.name = f"m4-rtsp-{os.getpid()}"
        self.pkts: list[tuple[int, int]] = []        # (pts_ns, wall_ns) of the first RTP buffers
        self.p = subprocess.Popen(["docker", "run", "--rm", "--name", self.name, "--network", "host", "-e", "RTSP_TEST_UNSHARED=1",
                                   "-v", f"{REPO}:/work:ro", "--entrypoint", "/work/engine/build/rtsp_test_server", IMAGE,
                                   f"/work/{clip.relative_to(REPO)}", str(RTSP_PORT), "/cam"], stdout=subprocess.PIPE, text=True)
        self.serving = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()
        if not self.serving.wait(30):
            raise RuntimeError("rtsp_test_server did not start")

    def _read(self):
        for line in self.p.stdout:
            if line.startswith("serving"):
                self.serving.set()
            elif line.startswith("pkt "):
                _, pts, wall = line.split()
                self.pkts.append((int(pts), int(wall)))

    def on_air_offset_ns(self) -> int | None:
        """P such that the frame at clip time t leaves at P + t: the median of wall - pts over the paced buffers (the first few may be
        produced while the media prerolls, before the client's PLAY)."""
        paced = self.pkts[15:]
        return int(statistics.median(w - p for p, w in paced)) if paced else None

    def stop(self):
        sh("docker", "rm", "-f", self.name, check=False)


def start_backend() -> subprocess.Popen:
    sh("docker", "rm", "-f", PG, check=False)
    sh("docker", "run", "-d", "--rm", "--name", PG, "-p", f"127.0.0.1:{PG_PORT}:5432", "-e", "POSTGRES_PASSWORD=test", "-e", "POSTGRES_DB=m4",
       "--tmpfs", "/var/lib/postgresql/data", "postgres:16-alpine")
    for _ in range(60):
        if sh("docker", "exec", PG, "pg_isready", "-U", "postgres", "-d", "m4", check=False).returncode == 0:
            time.sleep(1)
            break
        time.sleep(1)
    snaps = WORK / "snaps"
    snaps.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(REPO), DATABASE_URL=DB_URL, SNAPSHOT_DIR=str(snaps), INTERNAL_TOKEN="",
               ENABLE_TELEGRAM="false", TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="", ENGINE_MANAGER_URL="")
    sh(sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head", cwd=REPO, env=env)
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:create_app", "--factory", "--host", "127.0.0.1", "--port", str(API_PORT)],
                         cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            api("GET", "/api/health")
            return p
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("backend did not start")


def received_at(event_id: str) -> int:
    import psycopg
    with psycopg.connect(DB_URL) as c:
        row = c.execute("select received_at from events where event_id = %s", (event_id,)).fetchone()
    return to_ns(row[0])


def trial(clip_name: str, repeat: int, overrides: dict, ws: WsListener, fall_model: str = "") -> dict:
    label = next(c for c in labels.load_dir() if Path(c.clip).stem == clip_name)
    fall = next(e for e in label.events if e.type == "fall" and e.label == "positive")
    clip = REPO / "eval/work/urfall" / label.clip
    tdir = WORK / f"{clip_name}_{repeat}"
    tdir.mkdir(parents=True, exist_ok=True)
    for f in tdir.glob("*"):
        f.unlink()
    cam = api("POST", "/api/cameras", {"name": f"{clip_name}-{repeat}", "source_type": "rtsp", "source_uri": f"rtsp://127.0.0.1:{RTSP_PORT}/cam",
                                       "behaviors": ["fall"]})
    cid = cam["id"]
    ini = tdir / "cameras.ini"
    ini.write_text(f"[camera.{cid}]\nuri=rtsp://127.0.0.1:{RTSP_PORT}/cam\nbehaviors=fall,fight\n" + "".join(f"{k}={v}\n" for k, v in overrides.items()))
    ini.chmod(0o600)
    srv = Server(clip)
    name = f"m4-engine-{os.getpid()}"
    w = f"/work/{tdir.relative_to(REPO)}"
    sh("docker", "run", "-d", "--name", name, "--gpus", "all", "-u", "1000:1000", "--network", "host", "-v", f"{REPO}:/work", "-w", "/work/engine",
       "--entrypoint", "./build/ds_engine", IMAGE, "--config", PGIE, "--tracker-config", "/work/engine/configs/tracker_nvdcf.yml",
       "--cameras-file", f"{w}/cameras.ini", *(["--fall-model", fall_model] if fall_model else []), "--backend-url", f"http://127.0.0.1:{API_PORT}/internal/events", "--spool-file", f"{w}/spool.jsonl",
       "--snapshot-dir", f"/work/{(WORK / 'snaps').relative_to(REPO)}", "--dump-detections", f"{w}/dump.csv", "--fps-interval", "0")
    try:
        end = time.monotonic() + label.duration_s + 60
        while time.monotonic() < end and not ws.falls(cid):
            time.sleep(0.1)
        time.sleep(1.0)
        got = ws.falls(cid)
    finally:
        sh("docker", "kill", "--signal=TERM", name, check=False)
        sh("docker", "wait", name, check=False)
        log = sh("docker", "logs", name, check=False)
        (tdir / "engine.log").write_text(log.stdout + log.stderr)
        sh("docker", "rm", "-f", name, check=False)
        srv.stop()
    out = {"clip": clip_name, "repeat": repeat, "camera_id": cid, "falls_received": len(got)}
    P = srv.on_air_offset_ns()
    if not got or P is None:
        out["missed"] = True
        return out
    t_ws, ev = got[0]
    occurred = to_ns(datetime.datetime.fromisoformat(ev["occurred_at"]))
    reported = to_ns(datetime.datetime.fromisoformat(ev["timestamp"]))
    recv = received_at(ev["event_id"])
    # the rule's impact on the clip's timeline, from the dump of this very run (frame times are the engine's PTS)
    preds = [p for p in replay.replay(tdir / "dump.csv", label, overrides) if p.type == "fall"]
    import pandas as pd
    pts0 = pd.read_csv(tdir / "dump.csv", usecols=["stage", "pts_ns"]).query("stage == 'fdet'").pts_ns.min() / 1e9
    sys_clip_replay = preds[0].t_pred - pts0 if preds else None
    # The same from the events themselves, which also works when the network (not the rules) reported: the first PERSON_DETECTED of a track is
    # stamped stream_start + PTS of the first frame the track is in (the dump has that PTS), which gives the engine's stream_start, and
    # `occurred_at` minus it is the engine's impact on the clip's timeline.
    sys_clip = None
    first = next((m["data"] for _, m in ws.messages if m.get("type") == "event.created" and m["data"]["camera_id"] == cid
                  and m["data"]["type"] == "PERSON_DETECTED"), None)
    if first is not None:
        df = pd.read_csv(tdir / "dump.csv", usecols=["stage", "object_id", "pts_ns"])
        pts_first = df[(df.stage == "trk") & (df.object_id == first["track_id"])].pts_ns.min() / 1e9
        stream_start = to_ns(datetime.datetime.fromisoformat(first["timestamp"])) - int(pts_first * 1e9)
        sys_clip = (occurred - stream_start) / 1e9 - pts0
    if sys_clip is None:
        sys_clip = sys_clip_replay
    t0 = P + int(fall.impact_s * 1e9)
    total = (t_ws - t0) / 1e9
    rule = (reported - occurred) / 1e9
    # camera -> DB: from the moment the frame that completed the rule left the camera until the backend stored the event. The engine's own
    # clock (stream_start + PTS) is anchored on the first frame, whose lag is not the steady one, so splitting this at `timestamp` is not reliable.
    pipeline = (recv - (P + int((sys_clip + rule) * 1e9))) / 1e9 if sys_clip is not None else None
    stages = {"label_vs_rule": None if sys_clip is None else sys_clip - fall.impact_s, "rule": rule,
              "camera_to_backend": pipeline, "backend_to_ws": (t_ws - recv) / 1e9}
    engine_clock = {"occurred_minus_P_minus_rule_impact": None if sys_clip is None else (occurred - (P + int(sys_clip * 1e9))) / 1e9}
    out.update(total_s=total, stages_s=stages, engine_clock_s=engine_clock, packets_fitted=len(srv.pkts), snapshot=bool(ev.get("snapshot_url")), severity=ev["severity"],
               other_events=sorted({m["data"]["type"] for _, m in ws.messages if m.get("type") == "event.created" and m["data"]["camera_id"] == cid}))
    out["impact_replay_vs_events_s"] = None if sys_clip_replay is None or sys_clip is None else sys_clip_replay - sys_clip
    if sys_clip is not None:
        out["check_sum_s"] = sum(stages.values()) - total          # should be ~0 (the pieces add up to the total)
    return out


def quantiles(xs):
    s = sorted(xs)
    q = lambda f: s[min(len(s) - 1, int(round((len(s) - 1) * f)))]     # noqa: E731
    return {"median": statistics.median(s), "p95": q(0.95), "max": s[-1], "min": s[0]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", default="fall-01,fall-02,fall-03,fall-04,fall-05")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="behaviour threshold, written to the camera's group (e.g. fall_still_s=1.0)")
    ap.add_argument("--fall-model", default="", help="path INSIDE the container (repo is /work) of the skeleton network's ONNX: runs with fall_net=2 "
                    "(network or rules; put fall_net_thr/fall_net_k in --set)")
    ap.add_argument("--tag", default="m4_urfall")
    a = ap.parse_args()
    overrides = dict(s.split("=", 1) for s in a.set)
    if a.fall_model:
        overrides.setdefault("fall_net", "2")
    WORK.mkdir(parents=True, exist_ok=True)
    backend = start_backend()
    ws = WsListener()
    ws.start()
    ws.ready.wait(10)
    trials = []
    try:
        for clip in a.clips.split(","):
            for r in range(a.repeats):
                t = trial(clip, r, overrides, ws, a.fall_model)
                trials.append(t)
                print(f"{clip}#{r}: " + ("MISSED" if t.get("missed") else f"M4 {t['total_s']:.2f}s  " +
                      " ".join(f"{k}={v:+.2f}" for k, v in t["stages_s"].items() if v is not None)), flush=True)
    finally:
        ws.stop.set()
        ws.join(3)
        backend.terminate()
        sh("docker", "rm", "-f", PG, check=False)
    ok = [t for t in trials if not t.get("missed")]
    summary = {"trials": len(trials), "alerts": len(ok)}
    if ok:
        summary["total_s"] = quantiles([t["total_s"] for t in ok])
        for k in ("label_vs_rule", "rule", "camera_to_backend", "backend_to_ws"):
            xs = [t["stages_s"][k] for t in ok if t["stages_s"].get(k) is not None]
            if xs:
                summary[k] = quantiles(xs)
    print(json.dumps(summary, indent=1))
    out = REPO / "eval/results" / f"{datetime.date.today()}_v2-4_{a.tag}.json"
    out.write_text(json.dumps({"overrides": overrides, "note": "UR Fall dev clips (640x480, rules tuned on them), N=1 camera, fake RTSP camera in real time, "
                               "real engine + backend + PostgreSQL, alert = WebSocket event.created; impact = UR Fall annotation (coarse); Telegram/VLM not included",
                               "summary": summary, "trials": trials}, indent=1))
    print(f"wrote {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
