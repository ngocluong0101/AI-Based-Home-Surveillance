"""The system the end-to-end tests talk to: a REAL backend on a REAL (throwaway) PostgreSQL, and a REAL engine
manager that runs a stand-in for ds_engine (the one of the manager tests) with room for 3 cameras.

Started by Playwright (see playwright.config.ts) from the repository root, stopped by SIGTERM. Nothing here touches
the developer's database, the real .env settings that send Telegram messages, or the GPU.

    python3 frontend/e2e/stack.py        # backend on 127.0.0.1:18765, engine manager on 127.0.0.1:18766
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tests.backend_api.conftest import _free_port, _pg_bin, migrate  # noqa: E402  (the throwaway PostgreSQL of the backend tests)

BACKEND_PORT = int(os.environ.get("E2E_BACKEND_PORT", "18765"))
MANAGER_PORT = int(os.environ.get("E2E_MANAGER_PORT", "18766"))
PLACES = 3   # the engine's room for cameras: small, so that a test can fill it


def wait_for(url: str, seconds: float = 60) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(url, timeout=2).close()
            return
        except OSError:
            time.sleep(0.3)
    raise SystemExit(f"{url} did not come up")


def main() -> None:
    initdb, pg_ctl = _pg_bin("initdb"), _pg_bin("pg_ctl")
    if not initdb or not pg_ctl:
        raise SystemExit("PostgreSQL server programs (initdb, pg_ctl) not found")
    root = tempfile.mkdtemp(prefix="pge2e")   # short: unix socket paths are limited
    data = os.path.join(root, "data")
    port = _free_port()
    children: list[subprocess.Popen] = []

    def cleanup(*_):
        for child in reversed(children):
            child.terminate()
        for child in children:
            try:
                child.wait(30)
            except subprocess.TimeoutExpired:
                child.kill()
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

    subprocess.run([initdb, "-D", data, "-U", "test", "-A", "trust", "--no-sync", "-E", "UTF8"], check=True, capture_output=True)
    subprocess.run([pg_ctl, "-D", data, "-w", "-l", os.path.join(root, "log"), "-o",
                    f"-c listen_addresses='' -c unix_socket_directories='{root}' -c port={port} -c fsync=off", "start"],
                   check=True, capture_output=True)
    url = f"postgresql+psycopg://test@/postgres?host={root}&port={port}"
    migrate(url)

    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(REPO),
        "DATABASE_URL": url,
        # never the developer's Telegram bot, whatever their .env says
        "ENABLE_TELEGRAM": "false", "TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": "",
        "SNAPSHOT_DIR": os.path.join(root, "snapshots"),
        "ENGINE_MANAGER_URL": f"http://127.0.0.1:{MANAGER_PORT}",
        "ENGINE_COMMAND": f"{sys.executable} {REPO / 'tests/engine_manager_tests/fake_engine.py'}",
        "ENGINE_RUNTIME_DIR": os.path.join(root, "run"),
        "ENGINE_MAX_CAMERAS": str(PLACES),
        "ENGINE_BACKEND_URL": f"http://127.0.0.1:{BACKEND_PORT}/internal/events",
        "ENGINE_STREAM_URL": "",
        "INTERNAL_TOKEN": "",
        "FAKE_ENGINE_DIR": os.path.join(root, "rec"), "FAKE_MODE": "run",
    })
    os.makedirs(env["FAKE_ENGINE_DIR"])
    quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    children.append(subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "engine_manager.service:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(MANAGER_PORT)], env=env, cwd=REPO, **quiet))
    wait_for(f"http://127.0.0.1:{MANAGER_PORT}/status")
    children.append(subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:create_app", "--factory",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT)], env=env, cwd=REPO, **quiet))
    wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health")
    print(f"e2e stack ready: backend :{BACKEND_PORT}, engine manager :{MANAGER_PORT}, {PLACES} places", flush=True)

    while True:   # until SIGTERM
        if any(c.poll() is not None for c in children):
            cleanup()
        time.sleep(1)


if __name__ == "__main__":
    main()
