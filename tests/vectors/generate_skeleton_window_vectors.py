"""Writes tests/vectors/skeleton_windows.json: raw skeleton histories and the window eval/train/skeleton.py builds from them. The C function
pose_history_skeleton_window() (engine/src/core/pose.c) must give the same numbers (engine/tests/test_skel_window.c), so the network is fed
the same input at run time as in training.

    eval/.venv/bin/python tests/vectors/generate_skeleton_window_vectors.py

Cases are made from a walking-then-falling skeleton with holes and missing frames added on purpose, plus real windows from UR Fall if the dumps exist.
"""
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.train import skeleton as sk  # noqa: E402

T = 24


def person(lying, cx=300.0, cy=400.0, torso=100.0):
    """17 x (x, y, v): lying 0 = upright, 1 = on the floor; hips at (cx, cy) when upright."""
    def p(up, flat):
        return (cx + (up[0] + (flat[0] - up[0]) * lying) * torso / 100.0, cy + (up[1] + (flat[1] - up[1]) * lying) * torso / 100.0)
    kp = {}
    for i in range(5):
        kp[i] = p((0, -140), (-140, 100))
    for i, dx in ((5, -10), (6, 10)):
        kp[i] = p((dx, -100), (-100, 100 + dx))
    for i, dx in ((7, -15), (8, 15), (9, -15), (10, 15)):
        kp[i] = p((dx, -50), (-50, 100 + dx))
    for i, dx in ((11, -10), (12, 10)):
        kp[i] = p((dx, 0), (0, 100 + dx))
    for i, dx in ((13, -10), (14, 10)):
        kp[i] = p((dx, 60), (60, 100 + dx))
    for i, dx in ((15, -10), (16, 10)):
        kp[i] = p((dx, 120), (120, 100 + dx))
    return np.array([[*kp[i], 0.9] for i in range(17)])


def case(name, n_frames, end, lying_of, drop=(), hide=(), hidden_frames=(), vis_low=(), size=300.0):
    frames = []
    for f in range(n_frames):
        if f in hidden_frames:
            continue
        kp = person(lying_of(f))
        for j in hide:
            kp[j, 2] = 0.1
        if f in drop:
            for j in vis_low:
                kp[j, 2] = 0.2
        frames.append({"frame": f, "size": round(size + f % 3, 2), "kp": [round(float(v), 3) for v in kp.reshape(-1)]})
    return {"name": name, "T": T, "end": end, "frames": frames}


def expected(c):
    tr = sk.Track(np.array([f["frame"] for f in c["frames"]]), np.array([f["kp"] for f in c["frames"]]).reshape(-1, 17, 3),
                  np.array([f["size"] for f in c["frames"]], float))
    w = sk.window(tr, c["end"], c["T"])
    c["expected"] = None if w is None else [round(float(v), 5) for v in w.reshape(-1)]
    return c


cases = [
    case("standing", 40, 39, lambda f: 0.0),
    case("fall", 60, 59, lambda f: min(1.0, max(0.0, (f - 40) / 6))),
    case("hole_of_3_frames_is_filled", 40, 39, lambda f: min(1.0, max(0.0, (f - 25) / 6)), hidden_frames=(30, 31, 32)),
    case("hole_of_7_frames_stays_open", 40, 39, lambda f: min(1.0, max(0.0, (f - 25) / 6)), hidden_frames=tuple(range(26, 33))),
    case("left_wrist_missing_a_few_frames", 40, 39, lambda f: 0.0, drop=(20, 21, 22), vis_low=(9,)),
    case("hips_never_seen_origin_from_shoulders", 40, 39, lambda f: 0.0, hide=(11, 12)),
    case("hips_and_shoulders_never_seen_gives_no_window", 40, 39, lambda f: 0.0, hide=(5, 6, 11, 12)),
    case("shoulders_never_seen_scale_from_box", 40, 39, lambda f: 0.0, hide=(5, 6)),
    case("newest_frame_without_a_skeleton_gives_no_window", 40, 39, lambda f: 0.0, hidden_frames=(39,)),
    case("only_10_frames_of_history_gives_no_window", 10, 9, lambda f: 0.0),
    case("20_frames_of_history_gives_a_partial_window", 20, 19, lambda f: min(1.0, max(0.0, (f - 12) / 5))),
    case("15_frames_is_one_short", 15, 14, lambda f: 0.0),
    case("16_frames_is_enough", 16, 15, lambda f: 0.0),
    case("odd_number_of_scale_samples", 40, 38, lambda f: min(1.0, max(0.0, (f - 25) / 6))),
]
# real windows: a fall and an ADL of UR Fall when the dumps are there (ids and frames are kept as in the dump; rounded to 3 decimals)
for seq, ends in (("fall-02", (60, 75)), ("adl-02", (100,))):
    dump = REPO / "eval/work/urfall" / f"{seq}.csv"
    if dump.exists():
        for oid, tr in sk.load_tracks(dump).items():
            for e in ends:
                sel = (tr.frames >= e - T + 1) & (tr.frames <= e)
                if sel.any():
                    cases.append({"name": f"urfall_{seq}_{e}", "T": T, "end": e, "frames": [
                        {"frame": int(f), "size": round(float(s), 2), "kp": [round(float(v), 3) for v in k.reshape(-1)]}
                        for f, s, k in zip(tr.frames[sel], tr.size[sel], tr.kp[sel])]})
out = [expected(c) for c in cases]
(REPO / "tests/vectors/skeleton_windows.json").write_text(json.dumps(out))
print(f"{len(out)} cases, {sum(c['expected'] is None for c in out)} with no window, {(REPO / 'tests/vectors/skeleton_windows.json').stat().st_size // 1000} kB")
