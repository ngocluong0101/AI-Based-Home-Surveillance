"""SIGHUP on the running engine: the settings of the cameras change without a restart."""

import pytest

pytestmark = pytest.mark.gpu_engine

WINDOW = 12   # seconds; about one pass over the sample video, so counts are comparable between windows


def test_settings_are_applied_to_the_running_engine(rig):
    r = rig(slots=3, cameras={1: [], 2: [], 3: []})
    r.wait_ready([1, 2, 3])
    base = r.window(WINDOW)
    assert all(base.get(c, 0) > 20 for c in (1, 2, 3)), f"no usable baseline: {base}"

    # camera 1 repeats a person sooner, camera 2 keeps only confident detections, camera 3 detects nobody
    r.reload({1: ["absence_timeout=0.2"], 2: ["conf_threshold=0.9"], 3: ["ai_enabled=false"]})
    changed = r.window(WINDOW)
    assert changed[1] >= 1.4 * base[1], (base, changed)
    assert changed[2] <= 0.2 * base[2], (base, changed)
    assert changed.get(3, 0) == 0, (base, changed)
    beat = r.last_heartbeat()
    assert "people" not in beat[3], "a camera with AI off must report 'people: unknown', not 0"
    assert "people" in beat[1]
    log = r.log()
    assert "reload: camera_id=3 conf_threshold=0.00 absence_timeout=10.0 ai=off" in log
    assert r.running()


def test_a_broken_file_changes_nothing(rig):
    r = rig(slots=3, cameras={1: [], 2: ["conf_threshold=0.9"], 3: ["ai_enabled=false"]})
    r.wait_ready([1, 2])
    before = r.window(WINDOW)
    assert before.get(3, 0) == 0

    r.reload(raw=f"[camera.1]\nuri=file://{r.video}\nconf_threshold=banana\n")
    assert "the cameras file could not be used, nothing changed" in r.log()
    after = r.window(WINDOW)
    assert after.get(3, 0) == 0                      # AI is still off
    assert after[2] <= 0.2 * max(after[1], 1)        # camera 2 still filters
    assert r.heartbeat_ids() == [1, 2, 3]
    assert r.running()


def test_settings_return_to_the_defaults(rig):
    r = rig(slots=3, cameras={1: [], 2: [], 3: []})
    r.wait_ready([1, 2, 3])
    base = r.window(WINDOW)
    r.reload({1: ["absence_timeout=0.2"], 2: ["conf_threshold=0.9"], 3: ["ai_enabled=false"]})
    r.window(WINDOW)
    r.reload({1: [], 2: [], 3: []})
    back = r.window(WINDOW)
    for cam in (1, 2, 3):
        assert 0.6 * base[cam] <= back[cam] <= 1.5 * base[cam], (cam, base, back)
    assert "people" in r.last_heartbeat()[3]
