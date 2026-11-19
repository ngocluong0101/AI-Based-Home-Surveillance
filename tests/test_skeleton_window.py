"""The skeleton window the ST-GCN is trained on (eval/train/skeleton.py), by hand: expected values worked out from the definition, not read back."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from eval.train import skeleton as sk

REPO = Path(__file__).resolve().parents[1]


def track(frames, kps, size=300.0):
    return sk.Track(np.array(frames), np.array(kps, float).reshape(-1, 17, 3), np.full(len(frames), size))


def upright(cx=500.0, cy=600.0, torso=100.0):
    kp = np.zeros((17, 3))
    kp[:, 2] = 0.9
    kp[:, 0], kp[:, 1] = cx, cy - torso / 2
    half = 0.1 * torso                                  # a body keeps its proportions when it is smaller
    kp[5], kp[6] = (cx - half, cy - torso, 0.9), (cx + half, cy - torso, 0.9)
    kp[11], kp[12] = (cx - half, cy, 0.9), (cx + half, cy, 0.9)
    return kp


def test_origin_is_the_hip_centre_and_scale_is_the_torso():
    w = sk.window(track(list(range(16)), [upright()] * 16), 15, 16)
    assert w.shape == (3, 16, 17)
    assert w[0, 0, 11] == pytest.approx(-0.1) and w[1, 0, 11] == pytest.approx(0.0)        # left hip: 10 px left of the centre, torso 100 px
    assert w[1, 0, 5] == pytest.approx(-1.0)                                               # shoulder a torso above the hips (y down)
    assert (w[2] == 1).all()


def test_moving_hips_are_motion_not_absorbed_by_the_normalisation():
    """a person whose hips drop by half a torso inside the window: y of the hips must END 0.5 below where it started (the origin is fixed)"""
    frames = [upright(cy=600 + 5 * f) for f in range(11)]
    w = sk.window(track(list(range(11)), frames), 10, 11)
    assert w[1, 0, 11] == pytest.approx(0.0) and w[1, 10, 11] == pytest.approx(0.5)


def test_a_person_far_away_and_a_person_close_give_the_same_window():
    a = sk.window(track(list(range(16)), [upright(torso=100)] * 16), 15, 16)
    b = sk.window(track(list(range(16)), [upright(cx=100, cy=300, torso=40)] * 16), 15, 16)
    assert np.allclose(a, b, atol=1e-5)


def test_short_holes_are_filled_and_long_ones_are_not():
    kps = [upright(cy=600 + 4 * f) for f in range(30)]
    frames = list(range(30))
    short = [f for f in frames if f not in (8, 9, 10)]                  # 3 missing frames
    w = sk.window(track(short, [kps[f] for f in short]), 29, 30)
    assert (w[2, 8:11] == 1).all()
    assert w[1, 9, 11] == pytest.approx((600 + 4 * 9 - 600) / 100, abs=1e-6)       # straight line between the neighbours = the true value here
    long_ = [f for f in frames if f not in range(5, 12)]                # 7 missing frames
    w = sk.window(track(long_, [kps[f] for f in long_]), 29, 30)
    assert (w[2, 5:12] == 0).all() and (w[0, 5:12] == 0).all()


def test_no_window_without_a_skeleton_in_the_newest_frame_or_without_anything_to_scale_by():
    t = track(list(range(16)), [upright()] * 16)
    assert sk.window(t, 16, 16) is None                       # frame 16 has no skeleton
    kp = upright()
    kp[[5, 6, 11, 12], 2] = 0.1
    assert sk.window(track(list(range(16)), [kp] * 16), 15, 16) is None


def test_a_window_needs_sixteen_frames_with_a_skeleton_the_rest_may_be_missing():
    kps = [upright(cy=600 + 3 * f) for f in range(20)]
    assert sk.window(track(list(range(15)), kps[:15]), 14, 48) is None             # 15 frames: one short
    w = sk.window(track(list(range(16)), kps[:16]), 15, 48)                          # 16 frames in a window of 48: the other 32 are missing
    assert w is not None and (w[2, :32] == 0).all() and (w[2, 32:] == 1).all()
    assert w[1, 47, 11] == pytest.approx(3 * 15 / 100, abs=1e-6)                     # origin = the first frame that has a skeleton, and it is frame 0


def test_speed_picks_frames_of_a_longer_stretch():
    frames = [upright(cy=600 + 2 * f) for f in range(40)]
    w = sk.window(track(list(range(40)), frames), 39, 20, speed=2.0)     # 40 frames subsampled to 20: the hips cover 2 px x 39 frames
    assert w[1, 19, 11] == pytest.approx(78 / 100, abs=0.02)


def test_the_flip_table_swaps_left_and_right_only():
    assert sorted(sk.FLIP) == list(range(17))
    assert all(sk.FLIP[sk.FLIP[i]] == i for i in range(17)) and sk.FLIP[0] == 0 and sk.FLIP[5] == 6 and sk.FLIP[15] == 16


def test_the_shared_vectors_are_current():
    """tests/vectors/skeleton_windows.json is what the C test compares with; it must be what this code produces"""
    path = REPO / "tests/vectors/skeleton_windows.json"
    before = path.read_text()
    subprocess.run([sys.executable, str(REPO / "tests/vectors/generate_skeleton_window_vectors.py")], check=True, capture_output=True)
    try:
        assert path.read_text() == before or "urfall_" in before        # the real-clip cases exist only where the dumps do
    finally:
        path.write_text(before)
    assert any(c["expected"] is None for c in json.loads(before))
