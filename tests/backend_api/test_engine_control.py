"""Start / stop / status of cameras and the reconciler that keeps the engine manager in step."""

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.engine_client import EngineClient, EngineManagerError
from engine_manager.manager import EngineManager, ManagerConfig
from engine_manager.service import create_app as create_manager_app

from .helpers import make_camera

FAKE_ENGINE = str(Path(__file__).parents[1] / "engine_manager_tests" / "fake_engine.py")
RTSP = "rtsp://admin:secret@192.168.1.10:554/stream"


class FakeManager:
    """Has the interface of EngineClient and the memory of the real manager."""

    def __init__(self):
        self.up = True
        self.cameras: list[dict] = []
        self.revision = None
        self.puts = 0

    def status(self):
        if not self.up:
            raise EngineManagerError("down")
        return {"state": "running" if self.cameras else "stopped", "starts": 1, "last_exit_code": None,
                "running_cameras": [c["id"] for c in self.cameras], "revision": self.revision}

    def put_cameras(self, cameras, revision):
        if not self.up:
            raise EngineManagerError("down")
        self.cameras, self.revision, self.puts = cameras, revision, self.puts + 1
        return self.status()

    def forget(self):   # the manager restarted
        self.cameras, self.revision = [], None

    def close(self):
        pass


@pytest.fixture
def manager():
    return FakeManager()


@pytest.fixture
def api(make_client, manager):
    client = make_client(engine_client=manager)
    return client, client.app.state.reconciler


def status(client, cam_id):
    return client.get(f"/api/cameras/{cam_id}/status").json()


def test_a_new_camera_is_not_running(api, manager):
    client, rec = api
    cam = make_camera(client, source_uri=RTSP)
    assert rec.run_once()
    assert manager.cameras == []
    st = status(client, cam["id"])
    assert st["desired_state"] == "stopped" and st["should_run"] is False
    assert st["engine"]["manager"] == "reachable" and st["engine"]["in_engine"] is False


def test_start_gives_the_manager_the_real_uri_and_stop_takes_it_away(api, manager):
    client, rec = api
    cam = make_camera(client, source_uri=RTSP)

    resp = client.post(f"/api/cameras/{cam['id']}/start")
    assert resp.status_code == 200
    assert resp.json()["desired_state"] == "running" and resp.json()["should_run"] is True
    assert resp.json()["engine"]["applied"] is False   # recorded, not applied yet
    assert client.get(f"/api/cameras/{cam['id']}").json()["desired_state"] == "running"

    assert rec.run_once()
    assert manager.cameras == [{"id": cam["id"], "uri": RTSP,   # the manager gets the password
                                "conf_threshold": 0.25, "absence_timeout": 10.0, "ai_enabled": True}]
    st = status(client, cam["id"])
    assert st["engine"]["applied"] is True and st["engine"]["in_engine"] is True and st["engine"]["state"] == "running"
    assert "secret" not in str(st)

    stopped = client.post(f"/api/cameras/{cam['id']}/stop").json()
    assert stopped["desired_state"] == "stopped"
    assert rec.run_once()
    assert manager.cameras == []
    assert status(client, cam["id"])["engine"]["in_engine"] is False


def test_several_cameras_form_one_list(api, manager):
    client, rec = api
    a = make_camera(client, name="a", source_uri="rtsp://h/a")
    b = make_camera(client, name="b", source_uri="rtsp://h/b")
    make_camera(client, name="c", source_uri="rtsp://h/c")   # never started
    client.post(f"/api/cameras/{b['id']}/start")
    client.post(f"/api/cameras/{a['id']}/start")
    rec.run_once()
    assert [c["id"] for c in manager.cameras] == [a["id"], b["id"]]


def test_nothing_is_pushed_when_nothing_changed(api, manager):
    client, rec = api
    cam = make_camera(client, source_uri=RTSP)
    client.post(f"/api/cameras/{cam['id']}/start")
    rec.run_once()
    rec.run_once()
    rec.run_once()
    assert manager.puts == 1


def test_starting_twice_is_harmless(api, manager):
    client, rec = api
    cam = make_camera(client, source_uri=RTSP)
    for _ in range(2):
        assert client.post(f"/api/cameras/{cam['id']}/start").status_code == 200
        rec.run_once()
    assert manager.puts == 1


def test_a_disabled_camera_cannot_be_started_and_disabling_removes_it(api, manager):
    client, rec = api
    cam = make_camera(client, source_uri=RTSP)
    client.patch(f"/api/cameras/{cam['id']}", json={"enabled": False})
    resp = client.post(f"/api/cameras/{cam['id']}/start")
    assert resp.status_code == 409

    client.patch(f"/api/cameras/{cam['id']}", json={"enabled": True})
    client.post(f"/api/cameras/{cam['id']}/start")
    rec.run_once()
    assert len(manager.cameras) == 1

    client.patch(f"/api/cameras/{cam['id']}", json={"enabled": False})
    rec.run_once()
    assert manager.cameras == []
    st = status(client, cam["id"])
    assert st["desired_state"] == "running" and st["should_run"] is False   # comes back when enabled again
    client.patch(f"/api/cameras/{cam['id']}", json={"enabled": True})
    rec.run_once()
    assert len(manager.cameras) == 1


def test_changing_the_uri_of_a_running_camera_is_pushed(api, manager):
    client, rec = api
    cam = make_camera(client, source_uri="rtsp://h/one")
    client.post(f"/api/cameras/{cam['id']}/start")
    rec.run_once()
    client.patch(f"/api/cameras/{cam['id']}", json={"source_type": "rtsp", "source_uri": "rtsp://h/two"})
    rec.run_once()
    assert manager.cameras[0]["uri"] == "rtsp://h/two" and manager.puts == 2


def test_renaming_does_not_restart_the_engine(api, manager):
    client, rec = api
    cam = make_camera(client, source_uri="rtsp://h/one")
    client.post(f"/api/cameras/{cam['id']}/start")
    rec.run_once()
    client.patch(f"/api/cameras/{cam['id']}", json={"name": "renamed"})
    rec.run_once()
    assert manager.puts == 1


def test_the_settings_of_a_camera_reach_the_manager_with_its_address(api, manager):
    client, rec = api
    cam = make_camera(client, source_uri="rtsp://h/one", conf_threshold=0.6, absence_timeout=30, ai_enabled=False)
    client.post(f"/api/cameras/{cam['id']}/start")
    rec.run_once()
    assert manager.cameras == [{"id": cam["id"], "uri": "rtsp://h/one", "conf_threshold": 0.6,
                                "absence_timeout": 30.0, "ai_enabled": False}]


def test_changing_a_setting_restarts_the_engine_once_with_the_new_value(api, manager):
    """The engine reads its settings at start, so a new value only takes effect through a new revision."""
    client, rec = api
    cam = make_camera(client, source_uri="rtsp://h/one")
    client.post(f"/api/cameras/{cam['id']}/start")
    rec.run_once()
    assert manager.puts == 1 and manager.cameras[0]["conf_threshold"] == 0.25
    for change, key, value in (({"conf_threshold": 0.9}, "conf_threshold", 0.9),
                               ({"absence_timeout": 2}, "absence_timeout", 2.0),
                               ({"ai_enabled": False}, "ai_enabled", False)):
        before = manager.puts
        client.patch(f"/api/cameras/{cam['id']}", json=change)
        rec.run_once()
        rec.run_once()   # a second round with nothing new pushes nothing
        assert manager.puts == before + 1 and manager.cameras[0][key] == value


def test_deleting_a_running_camera_removes_it(api, manager):
    client, rec = api
    cam = make_camera(client, source_uri="rtsp://h/one")
    client.post(f"/api/cameras/{cam['id']}/start")
    rec.run_once()
    client.delete(f"/api/cameras/{cam['id']}")
    rec.run_once()
    assert manager.cameras == []
    assert client.post(f"/api/cameras/{cam['id']}/start").status_code == 404
    assert client.get(f"/api/cameras/{cam['id']}/status").status_code == 404


def test_an_unreachable_manager_does_not_fail_the_api_and_is_caught_up_later(api, manager):
    client, rec = api
    manager.up = False
    cam = make_camera(client, source_uri=RTSP)
    resp = client.post(f"/api/cameras/{cam['id']}/start")
    assert resp.status_code == 200 and resp.json()["engine"]["manager"] == "unreachable"
    assert rec.run_once() is False
    assert status(client, cam["id"])["engine"] == {"manager": "unreachable", "state": None, "in_engine": None,
                                                   "applied": None, "starts": None, "last_exit_code": None}

    manager.up = True
    assert rec.run_once() is True
    assert [c["id"] for c in manager.cameras] == [cam["id"]]


def test_a_restarted_manager_gets_the_list_again(api, manager):
    client, rec = api
    cam = make_camera(client, source_uri=RTSP)
    client.post(f"/api/cameras/{cam['id']}/start")
    rec.run_once()
    manager.forget()
    assert status(client, cam["id"])["engine"]["applied"] is False
    rec.run_once()
    assert manager.cameras and status(client, cam["id"])["engine"]["applied"] is True


def test_without_a_manager_start_and_stop_only_record(make_client):
    client = make_client()
    cam = make_camera(client, source_uri=RTSP)
    assert client.post(f"/api/cameras/{cam['id']}/start").json()["engine"] == {
        "manager": "not_configured", "state": None, "in_engine": None, "applied": None, "starts": None,
        "last_exit_code": None}
    assert client.get(f"/api/cameras/{cam['id']}").json()["desired_state"] == "running"
    assert client.post(f"/api/cameras/{cam['id']}/stop").json()["desired_state"] == "stopped"


def test_the_background_thread_applies_a_change_without_being_asked(make_client, manager):
    client = make_client(engine_client=manager, reconcile_interval=30)   # the interval is long: trigger() must do it
    cam = make_camera(client, source_uri=RTSP)
    client.post(f"/api/cameras/{cam['id']}/start")
    end = time.monotonic() + 5
    while not manager.cameras and time.monotonic() < end:
        time.sleep(0.05)
    assert [c["id"] for c in manager.cameras] == [cam["id"]]


def test_errors_of_the_client_never_show_the_request(manager):
    import httpx

    def refuse(request):
        raise httpx.ConnectError("boom " + request.content.decode())

    client = EngineClient(http=httpx.Client(base_url="http://manager", transport=httpx.MockTransport(refuse)))
    with pytest.raises(EngineManagerError) as err:
        client.put_cameras([{"id": 1, "uri": RTSP}], "r")
    assert "secret" not in str(err.value)


def test_with_the_real_manager_and_a_stand_in_engine(make_client, tmp_path, monkeypatch):
    rec_dir = tmp_path / "rec"
    rec_dir.mkdir()
    monkeypatch.setenv("FAKE_ENGINE_DIR", str(rec_dir))
    monkeypatch.setenv("FAKE_MODE", "run")
    real = EngineManager(ManagerConfig(command=[sys.executable, FAKE_ENGINE], engine_args=[],
                                       runtime_dir=tmp_path / "run", stop_timeout=3, poll_interval=0.05))
    with TestClient(create_manager_app(real, configure_logging=False)) as manager_http:
        client = make_client(engine_client=EngineClient(http=manager_http))
        rec = client.app.state.reconciler
        cam = make_camera(client, source_uri=RTSP)

        client.post(f"/api/cameras/{cam['id']}/start")
        rec.run_once()
        end = time.monotonic() + 8
        while status(client, cam["id"])["engine"]["in_engine"] is not True and time.monotonic() < end:
            time.sleep(0.05)
        st = status(client, cam["id"])
        assert st["engine"]["in_engine"] is True and st["engine"]["applied"] is True and st["engine"]["state"] == "running"
        cams_file = (tmp_path / "run" / "cameras.ini").read_text()
        assert f"[camera.{cam['id']}]\nuri={RTSP}" in cams_file

        client.post(f"/api/cameras/{cam['id']}/stop")
        rec.run_once()
        end = time.monotonic() + 8
        while status(client, cam["id"])["engine"]["state"] != "stopped" and time.monotonic() < end:
            time.sleep(0.05)
        assert status(client, cam["id"])["engine"]["state"] == "stopped"
