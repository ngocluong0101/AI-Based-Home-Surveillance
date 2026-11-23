"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

The fight rule on real (acted) fights: AIRTLab, 350 clips, replayed from the dumps of eval/crossset/make_dumps.py.

    eval/.venv/bin/python eval/crossset/make_dumps.py --set airtlab
    eval/.venv/bin/python eval/crossset/fight_airtlab.py [--write eval/results/<date>_v2-9_fight_airtlab.json]

Nothing is tuned: the defaults of BehaviorConfig (written from eval/DEFINITIONS.md 1.2). A clip is "reported" when the rule raises at
least one FIGHT event in it: a CLIP-level number (no start/end times), not a timing.
Limits, printed with the numbers: one room, 2-4 staged actors; the "violent" class includes things a skeleton rule is not made to see
(gunshots, stabbing with a prop, clubbing); the two cameras of one action are not independent samples.
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402

from eval.crossset import datasets  # noqa: E402
from eval.e2e import replay  # noqa: E402
from eval.e2e.labels import ClipLabel  # noqa: E402


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fmt(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {100 * k / n:.0f}% (95% CI {100 * lo:.0f}-{100 * hi:.0f}%)" if n else "0/0"


def people_seen(dump: Path) -> tuple[int, int]:
    """(most people tracked at once in a frame, frames with two or more people): a fight needs two."""
    df = pd.read_csv(dump, usecols=["stage", "frame_num", "object_id"], dtype=str, keep_default_na=False)
    trk = df[df.stage == "trk"]
    if trk.empty:
        return 0, 0
    per = trk.groupby("frame_num").object_id.nunique()
    return int(per.max()), int((per >= 2).sum())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", default="")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="a BehaviorConfig field to override (the default is: none, nothing tuned)")
    a = ap.parse_args()
    overrides = dict(kv.split("=", 1) for kv in a.set)
    actions = datasets.airtlab_actions()
    rows = []
    for c in datasets.airtlab_clips():
        dump = datasets.WORK / "airtlab" / f"{c.name}.csv"
        if not dump.exists():
            continue
        label = ClipLabel(c.name, 30.0, 1920, 1080, "airtlab", "dev", "", c.label == "violent", 0.0)
        preds = [p for p in replay.replay(dump, label, overrides) if p.type == "fight"]
        max_people, frames2 = people_seen(dump)
        rows.append({"clip": c.name, "label": c.label, "camera": c.camera, "number": c.number, "reported": len(preds) > 0, "events": len(preds),
                     "max_people": max_people, "frames_with_2plus": frames2, "actions": actions.get((c.label, c.number), [])})
    if not rows:
        sys.exit("no dumps: run eval/crossset/make_dumps.py --set airtlab first")
    df = pd.DataFrame(rows)
    out = {"dataset": "AIRTLab (Bianculli et al. 2020)", "clips": len(df), "config": asdict(replay.config_from(overrides)), "overrides": overrides}
    print(f"{len(df)} clips analysed ({(df.label == 'violent').sum()} violent, {(df.label == 'non-violent').sum()} non-violent); rule defaults, overrides {overrides or 'none'}\n")
    for scope, sub in [("both cameras", df), ("cam1 (phone, front)", df[df.camera == "cam1"]), ("cam2 (fisheye action camera)", df[df.camera == "cam2"])]:
        v, nv = sub[sub.label == "violent"], sub[sub.label == "non-violent"]
        print(f"{scope}")
        print(f"  violent clips reported (recall, clip level):        {fmt(int(v.reported.sum()), len(v))}")
        print(f"  non-violent clips reported (false alarms):          {fmt(int(nv.reported.sum()), len(nv))}")
        out[scope] = {"violent_reported": int(v.reported.sum()), "violent": len(v), "nonviolent_reported": int(nv.reported.sum()), "nonviolent": len(nv)}
    # can the rule even fire? it needs two people tracked at the same time
    v = df[df.label == "violent"]
    cant = int((v.frames_with_2plus < 10).sum())
    print(f"\nviolent clips where fewer than 10 frames had two tracked people at once (the rule has little to work with): {cant}/{len(v)}")
    out["violent_with_under_10_frames_of_two_people"] = cant
    # per action: a clip with several actions counts once for each of them
    per = defaultdict(lambda: [0, 0])
    for r in df.itertuples():
        for act in r.actions:
            per[(r.label, act)][1] += 1
            per[(r.label, act)][0] += int(r.reported)
    print("\nper action (a clip with several actions counts for each; both cameras):")
    out["per_action"] = {}
    for (label, act), (k, n) in sorted(per.items(), key=lambda kv: (kv[0][0], -kv[1][1])):
        print(f"  {label:12} {act:12} reported {k:3}/{n:3} = {100 * k / n:3.0f}%")
        out["per_action"][f"{label}/{act}"] = [k, n]
    if a.write:
        Path(a.write).write_text(json.dumps(out, indent=1, default=str))
        print(f"\nwrote {a.write}")


if __name__ == "__main__":
    main()
