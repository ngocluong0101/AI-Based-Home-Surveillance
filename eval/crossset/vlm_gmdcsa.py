"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Does the VLM check remove the false alarms on GMDCSA-24? (V2-9c) The measurement of eval/vlm/evaluate.py + analyse.py, on a dataset the system
has not seen.

    eval/.venv/bin/python eval/crossset/alerts_gmdcsa.py                 # the alerts with their times (GPU: retrains leaving one actor out)
    eval/.venv/bin/python eval/crossset/vlm_gmdcsa.py --write eval/results/<date>_v2-9_vlm_gmdcsa24.json

For every alert: 6 frames from 2.5 s before the report to 2.0 s after it (what the deployed verifier shows), asked as vlm_service's
multiple-choice question. An alert is TRUE in a fall clip and FALSE in a daily-activity clip (clip-level label, so "real falls lost" is
optimistic). The VLM would silence an alert when p_yes < 0.1 (the deployed value). Frames are shrunk to 640 px like the service does.
Results are cached in eval/work/crossset/vlm_gmdcsa.jsonl.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import cv2  # noqa: E402
from PIL import Image  # noqa: E402

from eval.crossset import datasets  # noqa: E402

CACHE = datasets.WORK / "vlm_gmdcsa.jsonl"
BEFORE, AFTER, N_FRAMES = 2.5, 2.0, 6
THRS = (0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9)


def grab(path: Path, fps: float, n_frames: int, windows: list[tuple[float, float]]) -> list[list[Image.Image]]:
    """Frames of several windows of one clip, decoded in one pass (only the needed ones are kept)."""
    need = []
    for t0, t1 in windows:
        need.append(np.clip(np.round(np.linspace(max(0.0, t0), min(n_frames / fps, t1), N_FRAMES) * fps).astype(int), 0, n_frames - 1))
    wanted = set(int(i) for a in need for i in a)
    got = {}
    cap = cv2.VideoCapture(str(path))
    i = 0
    ok, f = cap.read()
    while ok and i <= max(wanted):
        if i in wanted:
            im = Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
            im.thumbnail((640, 640))
            got[i] = im
        i += 1
        ok, f = cap.read()
    cap.release()
    return [[got[min(int(j), max(got))] for j in a] for a in need]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", default="")
    ap.add_argument("--sources", default="rule,net_ur,net_loso")
    a = ap.parse_args()
    al = json.loads((datasets.WORK / "gmdcsa_alerts.json").read_text())
    clips = {c.name: c for c in datasets.gmdcsa_clips()}
    todo = []                                   # (id, source, clip, label, t0, t1)
    for src in a.sources.split(","):
        for name, frames in al["alerts"][src].items():
            for fr in frames:
                t = fr / al["fps"][name]
                todo.append((f"{src}:{name}@{fr}", src, name, int(clips[name].label == "fall"), t - BEFORE, t + AFTER))
    done = {}
    if CACHE.exists():
        for line in CACHE.read_text().splitlines():
            r = json.loads(line)
            done[r["id"]] = r
    print(f"{len(todo)} alert windows, {sum(1 for w in todo if w[0] in done)} cached", flush=True)
    pending = [w for w in todo if w[0] not in done]
    if pending:
        from vlm_service.verifier import QwenVL
        m = QwenVL(style="options")
        by_clip = {}
        for w in pending:
            by_clip.setdefault(w[2], []).append(w)
        with CACHE.open("a") as f:
            for k, (name, ws) in enumerate(sorted(by_clip.items())):
                imgs = grab(clips[name].path, al["fps"][name], al["frames"][name], [(w[4], w[5]) for w in ws])
                for w, frames in zip(ws, imgs):
                    v = m.verify("FALL", frames, (w[5] - w[4]) / (N_FRAMES - 1), reason=False)
                    r = {"id": w[0], "source": w[1], "clip": name, "label": w[3], "p_yes": v.p_yes, "p_no": v.p_no, "latency_s": v.latency_s}
                    done[w[0]] = r
                    f.write(json.dumps(r) + "\n")
                    f.flush()
                if k % 10 == 0:
                    print(f"{k}/{len(by_clip)} clips", flush=True)
    rows = [done[w[0]] for w in todo if w[0] in done]
    out = {}
    print("\nsource      alerts (true/false) | VLM silences the alert when p_yes < thr: false alarms removed / real falls lost")
    for src in a.sources.split(","):
        r = [x for x in rows if x["source"] == src]
        true, false = [x for x in r if x["label"]], [x for x in r if not x["label"]]
        out[src] = {"alerts": len(r), "true": len(true), "false": len(false), "by_thr": {}}
        line = f"{src:9} {len(r):4} ({len(true)}/{len(false)}) |"
        for t in THRS:
            fr_, tl = sum(x["p_yes"] < t for x in false), sum(x["p_yes"] < t for x in true)
            out[src]["by_thr"][str(t)] = {"false_removed": fr_, "true_lost": tl}
            line += f" {t}: {fr_}/{len(false)} {tl}/{len(true)} |"
        print(line)
        # clip level: a fall clip is still caught when at least one of its alerts survives the VLM, an ADL clip is a false alarm when one survives
        names_f = sorted({x["clip"] for x in r if x["label"]})
        names_a = sorted({x["clip"] for x in r if not x["label"]})
        for t in (0.1, 0.5):
            kept_f = sum(any(x["p_yes"] >= t for x in r if x["clip"] == n) for n in names_f)
            kept_a = sum(any(x["p_yes"] >= t for x in r if x["clip"] == n) for n in names_a)
            out[src][f"clips_after_vlm_{t}"] = {"fall_clips_with_a_surviving_alert": [kept_f, len(names_f)], "adl_clips_with_a_surviving_alert": [kept_a, len(names_a)]}
            print(f"          clip level, VLM at {t}: fall clips still reported {kept_f}/{len(names_f)}, ADL clips still reported (false alarms) {kept_a}/{len(names_a)}")
    lat = [x["latency_s"] for x in rows]
    out["median_latency_s"] = float(np.median(lat)) if lat else None
    if a.write:
        Path(a.write).write_text(json.dumps(out, indent=1))
        print(f"wrote {a.write}")


if __name__ == "__main__":
    main()
