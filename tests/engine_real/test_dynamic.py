"""Cameras are added to and taken out of the running engine; the others do not notice."""

import pytest

from .rig import wait_until

pytestmark = pytest.mark.gpu_engine

WINDOW = 10


def heartbeat_is(r, ids):
    wait_until(lambda: r.heartbeat_ids() == sorted(ids), 20, f"the heartbeat to list cameras {sorted(ids)}")


def keeps_running(r, ids):
    """Every camera in `ids` produced events during the next window."""
    w = r.window(WINDOW)
    assert all(w.get(c, 0) > 0 for c in ids), f"a camera stopped producing events: {w}"


def no_stall(r, ignore_first=3):
    """No camera was ever slow or not RUNNING between its first few readings and its last one. (The first
    readings of a new camera and the last of a removed one are partial intervals.)"""
    for cam, readings in r.fps_readings().items():
        for fps, state in readings[ignore_first:-1]:
            assert fps >= 5, f"camera {cam} dropped to {fps} fps"
            assert state == "RUNNING", f"camera {cam} was {state}"


@pytest.mark.parametrize("stream", [False, True], ids=["detection", "with_video"])
def test_cameras_come_and_go_without_a_restart(rig, stream):
    r = rig(slots=4, cameras={1: [], 2: []}, stream=stream)
    r.wait_ready([1, 2])
    heartbeat_is(r, [1, 2])
    started = r.log().count("scheme=file")   # printed once per camera when the engine starts
    assert started == 2

    r.reload({1: [], 2: [], 3: []})                       # a camera comes
    heartbeat_is(r, [1, 2, 3])
    wait_until(lambda: r.events()[3] > 0, 40, "events from the new camera 3")
    keeps_running(r, [1, 2, 3])

    r.reload({1: [], 2: [], 3: [], 4: []})                # the engine is full
    heartbeat_is(r, [1, 2, 3, 4])
    wait_until(lambda: r.events()[4] > 0, 40, "events from the new camera 4")
    keeps_running(r, [1, 2, 3, 4])

    r.reload({2: [], 3: [], 4: []})                       # one goes
    heartbeat_is(r, [2, 3, 4])
    gone = r.events()[1]
    r.reload({2: [], 3: [], 4: [], 5: []})                # and its place is taken by another
    heartbeat_is(r, [2, 3, 4, 5])
    wait_until(lambda: r.events()[5] > 0, 40, "events from the new camera 5")
    assert r.events()[1] - gone <= 2, "camera 1 went on producing events after it was removed"   # a frame in flight
    assert "camera_id=5 added in slot 0" in r.log(), "the freed place was not used"
    keeps_running(r, [2, 3, 4, 5])

    r.reload({2: [], 3: [], 4: [], 5: [], 6: []})         # one too many: refused, nothing changes
    assert "nothing applied, there is no free place" in r.log()
    assert r.heartbeat_ids() == [2, 3, 4, 5]
    keeps_running(r, [2, 3, 4, 5])
    assert r.events()[6] == 0

    assert r.log().count("scheme=file") == started, "the engine started again"
    no_stall(r)
    assert r.running()
    assert r.stop() == 0
    log = r.log()
    assert "CRITICAL" not in log.replace("dconf-CRITICAL", "")
    assert "ERROR" not in log
