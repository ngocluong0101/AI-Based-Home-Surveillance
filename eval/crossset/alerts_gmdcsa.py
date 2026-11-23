"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

The ALERTS (with times) that the detectors raise on GMDCSA-24, for the VLM check of eval/crossset/vlm_gmdcsa.py. One alert list per clip, from:
  rule      the hand-written rules, fall_still_s 1.0 (tuned on UR Fall)
  net_ur    the network trained on UR Fall only (eval/work/train/ur_only_models/stgcn_T48_ur_only.pt), threshold 0.9, 3 scores in a row
  net_loso  the network now in use (UR Fall + GMDCSA-24), threshold 0.95, retrained leaving one actor out and asked about that actor

Output: eval/work/crossset/gmdcsa_alerts.json {"fps": {clip: fps}, "frames": {clip: n}, "alerts": {source: {clip: [end frame, ...]}}}.

    eval/.venv/bin/python eval/crossset/alerts_gmdcsa.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import torch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import cv2  # noqa: E402

from eval.crossset import datasets, train_mixed as tm  # noqa: E402
from eval.crossset.fall_gmdcsa import fps_of  # noqa: E402
from eval.e2e import replay  # noqa: E402
from eval.e2e.labels import ClipLabel  # noqa: E402
from eval.train import data, model as M  # noqa: E402
from eval.train.compare import fires  # noqa: E402

UR_ONLY = REPO / "eval/work/train/ur_only_models/stgcn_T48_ur_only.pt"
REFRACTORY = 90


def clip_fires(net, d: dict, thr: float) -> list[int]:
    """Every alert frame of the clip (all tracks), nearer than 3 s to the previous one counted once."""
    frames = []
    for tr in d["tracks"]:
        p = tm.score_windows(net, tr["X"])
        frames += fires(tr["end"], p, thr, 3)
    frames.sort()
    out = []
    for f in frames:
        if not out or f - out[-1] > REFRACTORY:
            out.append(int(f))
    return out


def load_ur_only():
    ck = torch.load(UR_ONLY, map_location="cpu")
    M.VELOCITY = ck.get("velocity", True)
    net = M.build(ck["model"], ck["T"])
    net.load_state_dict(ck["state"])
    return net.eval().cuda()


def main() -> None:
    gm = tm.build_gmdcsa()
    names = sorted(gm)
    clips = {c.name: c for c in datasets.gmdcsa_clips()}
    out = {"fps": {}, "frames": {}, "alerts": {"rule": {}, "net_ur": {}, "net_loso": {}}}
    for n in names:
        cap = cv2.VideoCapture(str(clips[n].path))
        out["frames"][n] = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        out["fps"][n] = fps_of(clips[n].path)
        label = ClipLabel(n, out["fps"][n], 1280, 720, "gmdcsa24", "dev", "", gm[n]["label"] == "fall", 0.0)
        preds = [p for p in replay.replay(datasets.WORK / "gmdcsa24" / f"{n}.csv", label, {"fall_still_s": 1.0}) if p.type == "fall"]
        out["alerts"]["rule"][n] = [int(round(p.t_reported * out["fps"][n])) for p in preds]
    net = load_ur_only()
    for n in names:
        out["alerts"]["net_ur"][n] = clip_fires(net, gm[n], 0.9)
    print("rule and net_ur done", flush=True)
    seqs = data.sequences()
    ur = data.build(seqs, tm.T, 2, (0.8, 1.0, 1.25))
    subjects = sorted({gm[n]["subject"] for n in names})
    for i, held in enumerate(subjects):
        g = tm.pack_training(gm, [n for n in names if gm[n]["subject"] != held])
        net = tm.fit_mil(ur["X"], ur["y"], g, 30, 10, i)
        for n in names:
            if gm[n]["subject"] == held:
                out["alerts"]["net_loso"][n] = clip_fires(net, gm[n], 0.95)
        print(f"net_loso: {held} done", flush=True)
    (datasets.WORK / "gmdcsa_alerts.json").write_text(json.dumps(out))
    for src, d in out["alerts"].items():
        f = [n for n in names if gm[n]["label"] == "fall"]
        a = [n for n in names if gm[n]["label"] == "adl"]
        print(f"{src:9} alerts in fall clips {sum(len(d[n]) for n in f)} (clips with one: {sum(bool(d[n]) for n in f)}/{len(f)}); in ADL clips {sum(len(d[n]) for n in a)} "
              f"(clips with one: {sum(bool(d[n]) for n in a)}/{len(a)})")


if __name__ == "__main__":
    main()
