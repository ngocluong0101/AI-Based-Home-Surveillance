"""Run the behaviour rules (the Python specification, bit-for-bit what the C port does, proven by the shared vectors) over the dump that
`ds_engine --dump-detections` wrote for a clip. The slow part (decode + pose on the GPU) happens once per clip; replaying the rules takes
seconds, so thresholds can be tried on train/val without touching the engine again.

Events come out in the clip's own time (PTS), which is what the labels use; there is no wall clock to line up.
"""
from pathlib import Path

import numpy as np

from eval.common import ds_dump
from src.events import BehaviorConfig, BehaviorEngine, Person, Zone

from .labels import ClipLabel
from .score import Pred

FRAME_W, FRAME_H = 1920.0, 1080.0      # the engine works in the nvstreammux frame whatever the camera's size


def _zones(label: ClipLabel) -> list[Zone]:
    zs = []
    for poly in label.lie_zones:
        zs.append(Zone("lie_ok", [(x / label.width, y / label.height) for x, y in poly]))
    for i, poly in enumerate(label.restricted_zones, 1):
        zs.append(Zone("restricted", [(x / label.width, y / label.height) for x, y in poly], i))
    return zs


def config_from(overrides: dict | None = None, zones: bool = False) -> BehaviorConfig:
    cfg = BehaviorConfig(fall=True, fight=True, zones=zones)
    for k, v in (overrides or {}).items():
        if not hasattr(cfg, k):
            raise KeyError(f"BehaviorConfig has no field '{k}'")
        setattr(cfg, k, type(getattr(cfg, k))(v))
    return cfg


def replay(dump_csv: Path, label: ClipLabel, overrides: dict | None = None) -> list[Pred]:
    d = ds_dump.load_dump(dump_csv)
    trk = d["trk"]
    pts = {}
    import pandas as pd
    raw = pd.read_csv(dump_csv, usecols=["stage", "frame_num", "pts_ns"])
    for f, t in raw[raw.stage == "fdet"][["frame_num", "pts_ns"]].itertuples(index=False):
        pts[int(f)] = int(t)
    by_frame = {f: g for f, g in trk.groupby("frame_num")}
    engine = BehaviorEngine(config_from(overrides, zones=bool(label.restricted_zones)), _zones(label), FRAME_W, FRAME_H)
    preds = []
    for f in d["frames"]:
        people = []
        g = by_frame.get(f)
        if g is not None:
            for r in g.itertuples(index=False):
                kp = None
                if isinstance(r.kpts, str):
                    v = [float(x) for x in r.kpts.split()]
                    kp = [tuple(v[3 * k:3 * k + 3]) for k in range(17)]
                people.append(Person(int(r.object_id), (r.left, r.top, r.width, r.height), kp))
        for e in engine.process(people, pts[f]):
            kind = {"FALL": "fall", "FIGHT": "fight"}.get(e.type)
            if kind:
                preds.append(Pred(kind, e.occurred_ns / 1e9, e.now_ns / 1e9))
    return preds
