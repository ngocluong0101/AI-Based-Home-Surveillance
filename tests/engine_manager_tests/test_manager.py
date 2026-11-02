import json
import logging
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from engine_manager.manager import CameraSpec, EngineManager, ManagerConfig, write_cameras_file

FAKE = str(Path(__file__).with_name("fake_engine.py"))


def cam(i, uri=None):
    return CameraSpec(i, uri or f"rtsp://user:pw{i}@10.0.0.{i}/stream")


@pytest.fixture
def make_manager(tmp_path, monkeypatch):
    rec = tmp_path / "rec"
    rec.mkdir()
    monkeypatch.setenv("FAKE_ENGINE_DIR", str(rec))
    managers = []

    def factory(mode="run", token=None, **cfg):
        monkeypatch.setenv("FAKE_MODE", mode)
        defaults = dict(command=[sys.executable, FAKE], engine_args=["--config", "x.txt"],
                        runtime_dir=tmp_path / "run", token=token, stop_timeout=3.0,
                        backoff_base=0.2, backoff_max=0.8, stable_after=30.0, poll_interval=0.05)
        defaults.update(cfg)
        m = EngineManager(ManagerConfig(**defaults))
        m.start()
        managers.append(m)
        return m

    factory.rec = rec
    yield factory
    for m in managers:
        m.stop()


def wait_for(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = pred()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def runs(rec):
    return [json.loads(p.read_text()) for p in sorted(rec.glob("run-*.json"), key=lambda p: int(p.stem[4:]))]


def test_nothing_runs_without_cameras(make_manager):
    m = make_manager()
    time.sleep(0.3)
    st = m.status()
    assert st["state"] == "stopped" and st["pid"] is None and st["starts"] == 0


def test_starts_with_the_cameras_and_keeps_secrets_out_of_argv(make_manager):
    m = make_manager(token="s3cret")
    m.set_cameras([cam(2), cam(1)], revision="r1")
    wait_for(lambda: len(runs(make_manager.rec)) == 1 and m.status()["state"] == "running")

    run = runs(make_manager.rec)[0]
    argv = run["argv"]
    assert argv[argv.index("--config") + 1] == "x.txt"
    assert "--cameras-file" in argv
    assert not any("pw1" in a or "pw2" in a or "s3cret" in a for a in argv)   # not visible in `ps`
    assert run["token"] == "s3cret"                                           # through the environment
    assert run["mode_bits"] == "0o600"                                        # the engine insists on this
    assert "[camera.1]\nuri=rtsp://user:pw1@10.0.0.1/stream" in run["cameras"]
    assert run["cameras"].index("[camera.1]") < run["cameras"].index("[camera.2]")   # sorted by id

    st = m.status()
    assert st["running_cameras"] == [1, 2] and st["desired_cameras"] == [1, 2]
    assert st["revision"] == "r1" and st["pid"] and st["starts"] == 1 and st["started_at"]


def test_no_token_means_the_variable_is_not_set(make_manager, monkeypatch):
    monkeypatch.setenv("DS_ENGINE_TOKEN", "left-over-from-the-parent")
    m = make_manager(token=None)
    m.set_cameras([cam(1)])
    wait_for(lambda: len(runs(make_manager.rec)) == 1)
    assert runs(make_manager.rec)[0]["token"] is None


def test_the_same_list_does_not_restart(make_manager):
    m = make_manager()
    m.set_cameras([cam(1)], revision="a")
    wait_for(lambda: m.status()["state"] == "running")
    pid = m.status()["pid"]
    m.set_cameras([cam(1)], revision="b")   # same cameras, new revision only
    time.sleep(0.5)
    assert m.status()["pid"] == pid and m.status()["starts"] == 1 and m.status()["revision"] == "b"


def test_a_changed_list_restarts_gracefully(make_manager):
    m = make_manager()
    m.set_cameras([cam(1)])
    wait_for(lambda: m.status()["state"] == "running")
    first = m.status()["pid"]

    m.set_cameras([cam(1), cam(2)])
    wait_for(lambda: m.status()["starts"] == 2 and m.status()["state"] == "running")
    wait_for(lambda: len(runs(make_manager.rec)) == 2)
    st = m.status()
    assert st["pid"] != first and st["running_cameras"] == [1, 2]
    assert st["last_exit_code"] == 0   # it was stopped with SIGTERM and exited by itself


def test_a_changed_uri_restarts_too(make_manager):
    m = make_manager()
    m.set_cameras([cam(1, "rtsp://a/1")])
    wait_for(lambda: m.status()["state"] == "running")
    m.set_cameras([cam(1, "rtsp://b/1")])
    wait_for(lambda: m.status()["starts"] == 2 and m.status()["state"] == "running")
    wait_for(lambda: len(runs(make_manager.rec)) == 2)
    assert "uri=rtsp://b/1" in runs(make_manager.rec)[1]["cameras"]


def test_empty_list_stops_the_engine(make_manager):
    m = make_manager()
    m.set_cameras([cam(1)])
    wait_for(lambda: m.status()["state"] == "running")
    m.set_cameras([])
    wait_for(lambda: m.status()["state"] == "stopped")
    st = m.status()
    assert st["pid"] is None and st["running_cameras"] == [] and st["last_exit_code"] == 0
    m.set_cameras([cam(1)])   # and it can be started again
    wait_for(lambda: m.status()["starts"] == 2 and m.status()["state"] == "running")


def test_a_crash_is_restarted_with_growing_delays(make_manager):
    m = make_manager(mode="crash")
    m.set_cameras([cam(1)])
    wait_for(lambda: m.status()["starts"] >= 3)
    st = m.status()
    assert st["last_exit_code"] == 3
    stamps = [p.stat().st_mtime for p in sorted(make_manager.rec.glob("run-*.json"))]
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert gaps[1] > gaps[0] + 0.1   # 0.2 s, then 0.4 s (plus the 0.2 s it ran)


def test_a_program_that_cannot_start_is_retried_not_fatal(make_manager):
    m = make_manager(command=["/nonexistent/ds_engine"])
    m.set_cameras([cam(1)])
    wait_for(lambda: m.status()["state"] == "backoff" and m.status()["last_error"])
    assert "could not start" in m.status()["last_error"]
    assert "pw1" not in m.status()["last_error"]


def test_a_finished_engine_is_not_restarted(make_manager):
    m = make_manager(mode="finish")
    m.set_cameras([cam(1)])
    wait_for(lambda: m.status()["state"] == "exited")
    time.sleep(0.6)
    assert m.status()["starts"] == 1 and m.status()["last_exit_code"] == 0
    # a different list does start it again
    m.set_cameras([cam(1), cam(2)])
    wait_for(lambda: m.status()["starts"] == 2)


def test_an_engine_that_ignores_sigterm_is_killed(make_manager):
    m = make_manager(mode="stubborn", stop_timeout=0.5)
    m.set_cameras([cam(1)])
    wait_for(lambda: m.status()["state"] == "running")
    pid = m.status()["pid"]
    t0 = time.monotonic()
    m.set_cameras([])
    wait_for(lambda: m.status()["state"] == "stopped")
    assert time.monotonic() - t0 < 4
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert m.status()["last_exit_code"] == -9


def test_stop_stops_the_engine(make_manager):
    m = make_manager()
    m.set_cameras([cam(1)])
    wait_for(lambda: m.status()["state"] == "running")
    pid = m.status()["pid"]
    m.stop()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_engine_output_is_logged_without_passwords(make_manager, caplog):
    caplog.set_level(logging.INFO, logger="engine")
    m = make_manager()
    m.set_cameras([cam(1)])
    wait_for(lambda: any("source_id=0" in r.message for r in caplog.records))
    m.set_cameras([])
    wait_for(lambda: any("Stopping" in r.message for r in caplog.records))
    text = "\n".join(r.message for r in caplog.records)
    assert "hunter2" not in text and "rtsp://***@10.0.0.5/stream" in text


def test_cameras_file_format(tmp_path):
    path = tmp_path / "d" / "cameras.ini"
    write_cameras_file(path, [CameraSpec(3, "rtsp://u:p@h/a;b#c"), CameraSpec(4, r"file:///C:\v.mp4")])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    assert "[camera.3]\nuri=rtsp://u:p@h/a;b#c\n" in text
    assert r"uri=file:///C:\\v.mp4" in text   # a backslash is doubled in the key-file format
    assert list(path.parent.iterdir()) == [path]   # no temp file left


def test_bad_uris_are_refused_before_anything_changes(make_manager):
    m = make_manager()
    with pytest.raises(ValueError):
        m.set_cameras([cam(1, "rtsp://h/x\ny")])
    assert m.status()["desired_cameras"] == []
