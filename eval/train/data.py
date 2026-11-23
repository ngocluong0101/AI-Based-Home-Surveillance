"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

UR Fall windows for the ST-GCN: sequences, frame labels, window labels, windowing (skeleton.py defines a window).

Window label (a window is identified by its LAST frame e; f0 = first frame annotated "falling", f1 = first "lying"; 0-based):
    fall      f0 + 8 <= e <= f1 + 30     the window holds the drop and the impact
    ignored   f1 + 30 < e <= f1 + 90     already down: neither "the fall" nor "not a fall"
    normal    everything else of a fall sequence, and every window of an ADL sequence (including lying down on purpose, crouching,
              picking things up: the hard negatives)
Only windows whose last frame has a skeleton are used (as in the engine).
"""
import re
from pathlib import Path

import numpy as np

from . import skeleton as sk

REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "eval/work"
URFALL = WORK / "urfall"
FALL_CSV, ADL_CSV = WORK / "urfall_falls.csv", WORK / "urfall_adls.csv"


def sequences() -> list[str]:
    return sorted(p.stem for p in URFALL.glob("*.csv") if re.fullmatch(r"(fall|adl)-\d+", p.stem))


def fall_frames(seq: str) -> tuple[int, int, int] | None:
    """(f0, f1, n_frames) 0-based for a fall sequence, from the UR Fall annotation file; None for ADL."""
    if not seq.startswith("fall"):
        return None
    import pandas as pd
    d = pd.read_csv(FALL_CSV, header=None)
    g = d[d[0] == seq]
    lab, fr = g[2].to_numpy(), g[1].to_numpy()
    return int(fr[(lab == 0).argmax()]) - 1, int(fr[(lab == 1).argmax()]) - 1, len(g)


def window_label(e: int, ff: tuple | None) -> int:
    """1 fall, 0 normal, -1 ignored"""
    if ff is None:
        return 0
    f0, f1, _ = ff
    if f0 + 8 <= e <= f1 + 30:
        return 1
    if f1 + 30 < e <= f1 + 90:
        return -1
    return 0


def build(seqs: list[str], T: int = 48, stride: int = 3, speeds=(1.0,)) -> dict:
    X, y, meta = [], [], []
    for s in seqs:
        ff = fall_frames(s)
        for oid, tr in sk.load_tracks(URFALL / f"{s}.csv").items():
            for sp in speeds:
                for e in range(int(tr.frames[0]) + sk.MIN_FRAMES - 1, int(tr.frames[-1]) + 1, stride):
                    lab = window_label(e, ff)
                    if lab < 0:
                        continue
                    w = sk.window(tr, e, T, sp)
                    if w is None:
                        continue
                    X.append(w)
                    y.append(lab)
                    meta.append((s, oid, e, sp))
    return {"X": np.stack(X), "y": np.array(y, np.int64), "seq": np.array([m[0] for m in meta]), "end": np.array([m[2] for m in meta]),
            "speed": np.array([m[3] for m in meta])}
