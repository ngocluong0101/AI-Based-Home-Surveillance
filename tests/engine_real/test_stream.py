"""The video the engine publishes: every camera arrives at the media server, as H.264, full size, and leaves
when the camera is taken out."""

import pytest

from backend.source_probe import probe_source

from .rig import wait_until

pytestmark = pytest.mark.gpu_engine


def path_is_live(cam_id):
    res = probe_source("rtsp", f"rtsp://127.0.0.1:8554/live/cam{cam_id}", 3.0)
    return res if res.ok else None


def test_every_camera_is_published_and_a_removed_one_stops(rig):
    r = rig(slots=3, cameras={1: [], 2: []}, stream=True)
    r.wait_ready([1, 2])
    for cam in (1, 2):
        res = wait_until(lambda cam=cam: path_is_live(cam), 30, f"live/cam{cam} at the media server")
        assert res.codec == "H.264"
        assert (res.width, res.height) == (1920, 1080)

    r.reload({1: [], 2: [], 3: []})                     # a camera added later is published too
    res = wait_until(lambda: path_is_live(3), 40, "live/cam3 at the media server")
    assert res.codec == "H.264" and (res.width, res.height) == (1920, 1080)

    r.reload({2: [], 3: []})                            # camera 1 leaves: its stream ends, the others go on
    wait_until(lambda: path_is_live(1) is None, 30, "live/cam1 to disappear")
    assert path_is_live(2) is not None and path_is_live(3) is not None

    r.reload({1: [], 2: [], 3: []})                     # and comes back (into the place it left, with its branch)
    wait_until(lambda: path_is_live(1), 40, "live/cam1 to come back")
    assert "stream failed" not in r.log()
