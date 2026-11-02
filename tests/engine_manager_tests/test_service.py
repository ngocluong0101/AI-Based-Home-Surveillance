import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from engine_manager.manager import EngineManager, ManagerConfig
from engine_manager.service import create_app

from .test_manager import FAKE, wait_for


@pytest.fixture
def api(tmp_path, monkeypatch):
    rec = tmp_path / "rec"
    rec.mkdir()
    monkeypatch.setenv("FAKE_ENGINE_DIR", str(rec))
    monkeypatch.setenv("FAKE_MODE", "run")
    manager = EngineManager(ManagerConfig(command=[sys.executable, FAKE], engine_args=[], runtime_dir=tmp_path / "run",
                                          stop_timeout=3, poll_interval=0.05))
    with TestClient(create_app(manager, configure_logging=False)) as client:
        yield client, manager


def test_status_before_anything(api):
    client, _ = api
    body = client.get("/status").json()
    assert body["state"] == "stopped" and body["desired_cameras"] == []


def test_put_cameras_starts_and_empty_list_stops(api):
    client, manager = api
    resp = client.put("/cameras", json={"cameras": [{"id": 1, "uri": "rtsp://u:p@h/x"}], "revision": "abc"})
    assert resp.status_code == 200 and resp.json()["revision"] == "abc"
    wait_for(lambda: client.get("/status").json()["state"] == "running")
    assert client.get("/status").json()["running_cameras"] == [1]

    client.put("/cameras", json={"cameras": []})
    wait_for(lambda: client.get("/status").json()["state"] == "stopped")


def test_validation(api):
    client, _ = api
    bad = [
        {"cameras": [{"id": 1, "uri": "a"}, {"id": 1, "uri": "b"}]},   # duplicate id
        {"cameras": [{"id": -1, "uri": "a"}]},
        {"cameras": [{"id": 1, "uri": ""}]},
        {"cameras": [{"id": 1, "uri": "a\nb"}]},
        {"cameras": [{"id": 1, "uri": "a", "extra": 1}]},
        {"cameras": "nope"},
        {},
    ]
    for body in bad:
        assert client.put("/cameras", json=body).status_code == 422, body
    assert client.get("/status").json()["desired_cameras"] == []


def test_leaving_the_app_stops_the_engine(tmp_path, monkeypatch):
    rec = tmp_path / "rec"
    rec.mkdir()
    monkeypatch.setenv("FAKE_ENGINE_DIR", str(rec))
    manager = EngineManager(ManagerConfig(command=[sys.executable, FAKE], engine_args=[], runtime_dir=tmp_path / "run",
                                          stop_timeout=3, poll_interval=0.05))
    with TestClient(create_app(manager, configure_logging=False)) as client:
        client.put("/cameras", json={"cameras": [{"id": 1, "uri": "rtsp://h/x"}]})
        wait_for(lambda: manager.status()["state"] == "running")
        pid = manager.status()["pid"]
    import os
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
