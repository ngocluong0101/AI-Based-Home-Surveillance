"""Fixtures for the backend tests: a PostgreSQL server of their own.

The tests never touch an existing server. They start a throwaway cluster (initdb + pg_ctl from the
installed PostgreSQL, listening on a unix socket in a temp directory) and remove it afterwards. To use
another server set TEST_DATABASE_URL to a database whose name ends with _test: its tables are emptied
before every test.
"""

from __future__ import annotations

import glob
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend.db import normalize_url
from backend.main import create_app
from backend.settings import Settings

REPO = Path(__file__).resolve().parents[2]


def _pg_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    candidates = sorted(glob.glob(f"/usr/lib/postgresql/*/bin/{name}"), reverse=True)
    return candidates[0] if candidates else None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def migrate(url: str, revision: str = "head", downgrade: bool = False) -> None:
    cfg = Config(str(REPO / "backend" / "alembic.ini"))
    cfg.attributes["url"] = url
    (command.downgrade if downgrade else command.upgrade)(cfg, revision)


@pytest.fixture(scope="session")
def database_url():
    given = os.environ.get("TEST_DATABASE_URL")
    if given:
        name = make_url(normalize_url(given)).database or ""
        if not name.endswith("_test"):
            pytest.fail("TEST_DATABASE_URL must name a database ending with _test (its tables are emptied)")
        migrate(given)
        yield given
        return

    initdb, pg_ctl = _pg_bin("initdb"), _pg_bin("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.skip("PostgreSQL server programs (initdb, pg_ctl) not found; set TEST_DATABASE_URL")

    root = tempfile.mkdtemp(prefix="pgt")   # short path: unix socket paths are limited to ~100 characters
    data = os.path.join(root, "data")
    port = _free_port()
    try:
        subprocess.run([initdb, "-D", data, "-U", "test", "-A", "trust", "--no-sync", "-E", "UTF8"],
                       check=True, capture_output=True)
        subprocess.run(
            [pg_ctl, "-D", data, "-w", "-l", os.path.join(root, "log"), "-o",
             f"-c listen_addresses='' -c unix_socket_directories='{root}' -c port={port} "
             "-c fsync=off -c synchronous_commit=off", "start"],
            check=True, capture_output=True)
        url = f"postgresql+psycopg://test@/postgres?host={root}&port={port}"
        migrate(url)
        yield url
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
def db_engine(database_url):
    engine = create_engine(normalize_url(database_url))
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE events, cameras RESTART IDENTITY CASCADE"))
    yield engine
    engine.dispose()


@pytest.fixture
def snapshot_dir(tmp_path):
    d = tmp_path / "snapshots"
    d.mkdir()
    return d


class NoTelegram:
    enabled = False

    def send_person_detected(self, event):
        raise AssertionError("a test reached Telegram")

    send_person_detected_photo = send_person_detected


@pytest.fixture
def make_client(database_url, db_engine, snapshot_dir):
    clients = []

    def factory(engine_client=None, telegram=None, **overrides) -> TestClient:
        overrides.setdefault("reconcile_interval", 0)   # no background thread unless a test asks for it
        overrides.setdefault("notify_interval", 0)
        overrides.setdefault("cleanup_check_interval", 0)
        telegram = telegram or NoTelegram()   # never the real one: .env of the developer may hold a token
        settings = Settings(database_url=database_url, snapshot_dir=str(snapshot_dir),
                            configure_logging=False, **overrides)
        client = TestClient(create_app(settings, engine_client=engine_client, telegram=telegram))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()
