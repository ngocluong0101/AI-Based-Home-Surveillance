"""Falls on a dataset the system has never seen: GMDCSA-24 (V2-9). Everything was built and tuned on UR Fall (one room, 30 fps, 640x480); this runs it,
unchanged, on 4 other actors in 3 home setups, 720p and 480p, 30 and 15 fps.

    eval/.venv/bin/python eval/crossset/make_dumps.py --set gmdcsa24
    eval/.venv/bin/python eval/crossset/fall_gmdcsa.py --write eval/results/<date>_v2-9_fall_gmdcsa24.json

Two detectors, both used as they were: the hand-written rules (fall_still_s 2.0 = the default, and 1.0 = the value tuned on five UR Fall clips) and the ST-GCN
(the final checkpoint eval/work/train/stgcn_T48.pt, trained on all UR Fall sequences and on nothing else; threshold 0.9 and 3 scores in a row, the deployed
defaults). CLIP level: a clip is "reported" when the detector raises a fall anywhere in it. The dataset has no frame-level times, so this is "did it notice
a fall in these seconds", the same simplification as AIRTLab. A fall clip not reported is a miss; an ADL clip reported is a false alarm.

15 fps: the network learned windows of 48 frames at 30 fps (1.6 s). On a 15 fps clip the same 48 frames last 3.2 s, so it is scored twice: as it is, and with the
window resampled to 1.6 s (the skeleton window with speed 0.5, what a deployed engine would do after reading the camera's frame rate).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import cv2  # noqa: E402
import torch  # noqa: E402

from eval.crossset import datasets  # noqa: E402
from eval.crossset.fight_airtlab import fmt  # noqa: E402
from eval.e2e import replay  # noqa: E402
from eval.e2e.labels import ClipLabel  # noqa: E402
from eval.train import model as M  # noqa: E402
from eval.train import skeleton as sk  # noqa: E402
from eval.train.compare import fires  # noqa: E402

CKPT = REPO / "eval/work/train/stgcn_T48.pt"


def fps_of(path: Path) -> float:
    cap = cv2.VideoCapture(str(path))
    v = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    return float(v)


def load_net():
    ck = torch.load(CKPT, map_location="cpu")
    M.VELOCITY = ck.get("velocity", True)
    net = M.build(ck["model"], ck["T"])
    net.load_state_dict(ck["state"])
    return net.eval().cuda(), ck["T"]


def net_fires(net, T: int, dump: Path, speed: float, thr: float, k: int = 3) -> bool:
    for _, tr in sk.load_tracks(dump).items():
        ends, ws = [], []
        for e in range(int(tr.frames[0]) + sk.MIN_FRAMES - 1, int(tr.frames[-1]) + 1):
            w = sk.window(tr, e, T, speed)
            if w is not None:
                ends.append(e)
                ws.append(w)
        if not ws:
            continue
        X = np.stack(ws).astype(np.float32)
        with torch.no_grad():
            p = torch.cat([torch.softmax(net(torch.tensor(X[i:i + 512]).cuda()), 1)[:, 1].cpu() for i in range(0, len(X), 512)]).numpy()
        if fires(ends, p, thr, k):
            return True
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", default="")
    a = ap.parse_args()
    net, T = load_net()
    desc = datasets.gmdcsa_descriptions()
    rows = []
    for c in datasets.gmdcsa_clips():
        dump = datasets.WORK / "gmdcsa24" / f"{c.name}.csv"
        if not dump.exists():
            continue
        fps = fps_of(c.path)
        label = ClipLabel(c.name, fps, 1280, 720, "gmdcsa24", "dev", c.camera, c.label == "fall", 0.0)
        r = {"clip": c.name, "label": c.label, "subject": c.camera, "fps": round(fps, 1), "desc": desc.get(c.name, ""),
             "slow": fps < 22,
             "rule_default": any(p.type == "fall" for p in replay.replay(dump, label, {"fall_still_s": 2.0})),
             "rule_1s": any(p.type == "fall" for p in replay.replay(dump, label, {"fall_still_s": 1.0}))}
        for thr in (0.9, 0.95):
            r[f"net_{thr}"] = net_fires(net, T, dump, 1.0, thr)
            r[f"net_{thr}_resampled"] = net_fires(net, T, dump, 0.5 if fps < 22 else 1.0, thr)
        rows.append(r)
    print(f"{len(rows)} clips ({sum(r['label'] == 'fall' for r in rows)} falls, {sum(r['label'] == 'adl' for r in rows)} daily activities); "
          f"{sum(r['slow'] for r in rows)} are 15 fps\n")
    detectors = [("rules, fall_still_s 2.0 (default)", "rule_default"), ("rules, fall_still_s 1.0 (tuned on 5 UR Fall clips)", "rule_1s"),
                 ("ST-GCN thr 0.9 (default), as is", "net_0.9"), ("ST-GCN thr 0.9, 15 fps clips resampled", "net_0.9_resampled"),
                 ("ST-GCN thr 0.95, 15 fps clips resampled", "net_0.95_resampled")]
    out = {"dataset": "GMDCSA-24", "clips": len(rows), "results": {}, "per_clip": rows}

    def block(title, sel):
        print(title)
        out["results"][title] = {}
        for name, key in detectors:
            f = [r for r in sel if r["label"] == "fall"]
            d = [r for r in sel if r["label"] == "adl"]
            kf, kd = sum(r[key] for r in f), sum(r[key] for r in d)
            print(f"  {name:50} falls reported {fmt(kf, len(f)):42} ADL reported (false alarms) {fmt(kd, len(d))}")
            out["results"][title][key] = {"falls": [kf, len(f)], "adl": [kd, len(d)]}
        print()

    block("all clips", rows)
    block("30 fps clips", [r for r in rows if not r["slow"]])
    block("15 fps clips", [r for r in rows if r["slow"]])
    for s in sorted({r["subject"] for r in rows}):
        block(f"{s}", [r for r in rows if r["subject"] == s])
    hidden = [r for r in rows if r["label"] == "fall" and "not visible" in r["desc"].lower()]
    full = [r for r in rows if r["label"] == "fall" and "not visible" not in r["desc"].lower()]
    print(f"falls whose description says the full body is not visible: {len(hidden)}; the others: {len(full)}")
    for name, key in detectors:
        kh, kf = sum(r[key] for r in hidden), sum(r[key] for r in full)
        print(f"  {name:50} not-visible {fmt(kh, len(hidden)):40} full-body {fmt(kf, len(full))}")
        out["results"].setdefault("by_visibility", {})[key] = {"not_visible": [kh, len(hidden)], "full_body": [kf, len(full)]}
    if a.write:
        Path(a.write).write_text(json.dumps(out, indent=1))
        print(f"\nwrote {a.write}")


if __name__ == "__main__":
    main()
