"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Can ANY thresholds of the fight rule tell AIRTLab's fights from its hugs, high fives and greetings? (V2-9)

The two thresholds that matter: fight_speed (torso lengths per second) and fight_threshold_s (contact time). Every clip is loaded once and
the rule replayed for each pair on a grid. Clips are split by NUMBER (the two cameras of one performance go together): the operating
point is chosen on odd numbers and judged on even ones. Same room and actors in both halves, so even that is optimistic.

    eval/.venv/bin/python eval/crossset/fight_sweep.py --write eval/results/<date>_v2-9_fight_sweep.json
"""
import argparse
import itertools
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402

from eval.crossset import datasets  # noqa: E402
from eval.crossset.fight_airtlab import wilson  # noqa: E402
from eval.e2e import replay  # noqa: E402
from src.events import BehaviorEngine, Person  # noqa: E402

SPEEDS = [3.0, 4.0, 5.0, 6.0, 8.0, 10.0]
THRESHOLDS = [0.4, 0.8, 1.5, 2.5, 4.0]


def load(dump: Path):
    """[(pts_ns, [Person])] for the frames of one dump."""
    raw = pd.read_csv(dump, dtype={"object_id": "Int64"})
    pts = {int(f): int(t) for f, t in raw[raw.stage == "fdet"][["frame_num", "pts_ns"]].itertuples(index=False)}
    by_frame = {f: g for f, g in raw[raw.stage == "trk"].groupby("frame_num")}
    frames = []
    for f in sorted(pts):
        people = []
        g = by_frame.get(f)
        if g is not None:
            for r in g.itertuples(index=False):
                kp = None
                if isinstance(r.kpts, str):
                    v = [float(x) for x in r.kpts.split()]
                    kp = [tuple(v[3 * k:3 * k + 3]) for k in range(17)]
                people.append(Person(int(r.object_id), (r.left, r.top, r.width, r.height), kp))
        frames.append((pts[f], people))
    return frames


def reported(frames, speed: float, threshold: float) -> bool:
    cfg = replay.config_from({"fight_speed": speed, "fight_threshold_s": threshold})
    cfg.fall = False
    eng = BehaviorEngine(cfg, [], replay.FRAME_W, replay.FRAME_H)
    for t, people in frames:
        if any(e.type == "FIGHT" for e in eng.process(people, t)):
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", default="")
    a = ap.parse_args()
    clips = [c for c in datasets.airtlab_clips() if (datasets.WORK / "airtlab" / f"{c.name}.csv").exists()]
    print(f"loading {len(clips)} dumps ...", flush=True)
    data = {c.name: load(datasets.WORK / "airtlab" / f"{c.name}.csv") for c in clips}
    rows = []
    for speed, thr in itertools.product(SPEEDS, THRESHOLDS):
        res = {c.name: reported(data[c.name], speed, thr) for c in clips}
        for half in ("dev", "test"):
            sel = [c for c in clips if (c.number % 2 == 1) == (half == "dev")]
            v = [c for c in sel if c.label == "violent"]
            nv = [c for c in sel if c.label == "non-violent"]
            rows.append({"speed": speed, "thr": thr, "half": half, "tp": sum(res[c.name] for c in v), "v": len(v),
                         "fp": sum(res[c.name] for c in nv), "nv": len(nv)})
        print(f"  speed {speed:4} thr {thr:3}: done", flush=True)
    df = pd.DataFrame(rows)
    dev = df[df.half == "dev"].copy()
    dev["youden"] = dev.tp / dev.v - dev.fp / dev.nv
    best = dev.sort_values(["youden", "speed"], ascending=[False, True]).iloc[0]
    test = df[(df.half == "test") & (df.speed == best.speed) & (df.thr == best.thr)].iloc[0]
    print("\nodd numbers (the half the thresholds are chosen on): recall - false-alarm rate")
    print(dev.pivot(index="speed", columns="thr", values="youden").round(2).to_string())
    print(f"\nbest on the odd half: fight_speed {best.speed}, fight_threshold_s {best.thr}: "
          f"{int(best.tp)}/{int(best.v)} violent reported, {int(best.fp)}/{int(best.nv)} non-violent reported")
    lo, hi = wilson(int(test.tp), int(test.v)), wilson(int(test.fp), int(test.nv))
    print(f"the same thresholds on the even half (never looked at): {int(test.tp)}/{int(test.v)} violent reported "
          f"(CI {100*lo[0]:.0f}-{100*lo[1]:.0f}%), {int(test.fp)}/{int(test.nv)} non-violent reported (CI {100*hi[0]:.0f}-{100*hi[1]:.0f}%)")
    default = df[(df.speed == 3.0) & (df.thr == 0.4) & (df.half == "test")].iloc[0]
    print(f"for reference the defaults (3.0, 0.4) on the even half: {int(default.tp)}/{int(default.v)} violent, {int(default.fp)}/{int(default.nv)} non-violent")
    if a.write:
        Path(a.write).write_text(json.dumps({"grid": rows, "best_on_dev": {"speed": best.speed, "thr": best.thr}, "test_at_best": test.to_dict()}, indent=1, default=float))
        print(f"wrote {a.write}")


if __name__ == "__main__":
    main()
