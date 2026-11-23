"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

One real run of the whole V2-6 chain: ds_engine (pose, rules, snapshot, frame ring) -> backend -> PostgreSQL -> the real VLM service (Qwen3-VL on the GPU).
For each clip: what the event looked like after the VLM answered. Telegram is off. Uses the throwaway PostgreSQL container of the tests (v2-test-pg).

    eval/.venv/bin/python eval/vlm/chain.py fall-09 adl-10 adl-34
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "eval/work/m6"
API, VLM = 18091, 8200
IMAGE = "nvcr.io/nvidia/deepstream:7.1-triton-multiarch"
DB = "postgresql://postgres:test@127.0.0.1:55432/m6"


def call(method, path, body=None, port=API):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read() or "null")


def wait(port, path, secs=120):
    end = time.time() + secs
    while time.time() < end:
        try:
            call("GET", path, port=port)
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"nothing on :{port}{path}")


def main():
    clips = sys.argv[1:] or ["fall-09", "adl-10", "adl-34"]
    for d in ("snaps", "ring"):
        (WORK / d).mkdir(parents=True, exist_ok=True)
    subprocess.run(["docker", "exec", "v2-test-pg", "psql", "-U", "postgres", "-c", "DROP DATABASE IF EXISTS m6"], capture_output=True)
    subprocess.run(["docker", "exec", "v2-test-pg", "psql", "-U", "postgres", "-c", "CREATE DATABASE m6"], check=True, capture_output=True)
    env = dict(os.environ, PYTHONPATH=str(REPO), DATABASE_URL=DB, SNAPSHOT_DIR=str(WORK / "snaps"), FRAME_RING_DIR=str(WORK / "ring"),
               VLM_URL=f"http://127.0.0.1:{VLM}", VERIFY_TIMEOUT="10", ENABLE_TELEGRAM="false", TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="",
               INTERNAL_TOKEN="", ENGINE_MANAGER_URL="")
    subprocess.run([sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"], cwd=REPO, env=env, check=True, capture_output=True)
    procs = [subprocess.Popen([sys.executable, "-m", "uvicorn", "vlm_service.app:create_app", "--factory", "--port", str(VLM)], cwd=REPO, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
             subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:create_app", "--factory", "--port", str(API)], cwd=REPO, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    try:
        wait(API, "/api/health")
        wait(VLM, "/health")
        print("VLM service up", call("GET", "/health", port=VLM))
        for clip in clips:
            cam = call("POST", "/api/cameras", {"name": clip, "source_type": "file", "source_uri": f"file:///work/eval/work/urfall/{clip}.mp4", "behaviors": ["fall"]})
            ini = WORK / f"{clip}.ini"
            ini.write_text(f"[camera.{cam['id']}]\nuri=file:///work/eval/work/urfall/{clip}.mp4\nbehaviors=fall\nfall_still_s=1.0\n")
            ini.chmod(0o600)
            subprocess.run(["docker", "run", "--rm", "--gpus", "all", "-u", "1000:1000", "--network", "host", "-v", f"{REPO}:/work", "-w", "/work/engine",
                            "--entrypoint", "./build/ds_engine", IMAGE, "--config", "/work/eval/work/pgie_pose_b1.txt", "--tracker-config",
                            "/work/engine/configs/tracker_nvdcf.yml", "--cameras-file", f"/work/eval/work/m6/{clip}.ini",
                            "--backend-url", f"http://127.0.0.1:{API}/internal/events", "--spool-file", f"/work/eval/work/m6/{clip}.spool",
                            "--snapshot-dir", "/work/eval/work/m6/snaps", "--frame-ring-dir", "/work/eval/work/m6/ring", "--fps-interval", "0"],
                           capture_output=True, text=True, timeout=300)
            time.sleep(6)       # the verifier waits for the frames after the event, then the VLM answers
            evs = call("GET", f"/api/events?camera_id={cam['id']}&type=FALL,FIGHT")["items"]
            print(f"--- {clip}: {len(evs)} alert(s)")
            for e in evs:
                print(f"    {e['type']} severity={e['severity']} verification={e['verification']} score={e['verification_score']} notify={e['notify_status']}\n"
                      f"      reason: {e['verification_reason']}")
        print("ring files left:", len(list((WORK / "ring").glob("ring_cam*.jpg"))))
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
