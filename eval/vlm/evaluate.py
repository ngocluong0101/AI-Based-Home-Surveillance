"""How good is the VLM at telling a real fall from a false alarm? (V2-6.5) On UR Fall (30 falls, 40 daily-activity sequences, 640x480, one room).

Two kinds of window, each made of N frames spread over a few seconds of the sequence:
  annotated   falls: [f0 - 0.5 s, f1 + 1.5 s] (the fall itself), label yes. ADL: three 3-second windows per sequence (start, middle, end), label no.
              This is the VLM's accuracy on the question, independent of any detector.
  alerts      what the detectors really reported: the rules (fall_still_s = 1.0) and the skeleton network (CV scores, thr 0.95, k 3, seed 0).
              Frames [t - 2.5 s, t + 0.5 s] around the report time t. Label = whether the report was a true detection. This is the number the
              system cares about: of the false alarms, how many does the VLM remove, and how many real falls does it take away with them.

    nice -n 10 eval/.venv/bin/python eval/vlm/evaluate.py --frames 6                 # results are cached in eval/work/vlm/, a rerun costs nothing
    nice -n 10 eval/.venv/bin/python eval/vlm/evaluate.py --frames 6 --summary-only
Only 40 daily-activity sequences (~5 minutes): this cannot say anything per hour, and one room is one room.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.train import compare as C, data  # noqa: E402

FPS = 30.0
CACHE = REPO / "eval/work/vlm"


def windows(seqs):
    """(id, seq, kind, label, t0, t1) in seconds"""
    out = []
    for s in seqs:
        ff = data.fall_frames(s)
        n = data_len(s)
        if ff is not None:
            f0, f1, _ = ff
            out.append((f"{s}:fall", s, "annotated", 1, max(0.0, f0 / FPS - 0.5), min(n / FPS, f1 / FPS + 1.5)))
        else:
            dur = n / FPS
            for name, a in (("start", 0.0), ("mid", max(0.0, dur / 2 - 1.5)), ("end", max(0.0, dur - 3.0))):
                out.append((f"{s}:{name}", s, "annotated", 0, a, min(dur, a + 3.0)))
    return out


def alert_windows(seqs, cv_json, after=0.5):
    """The reports of the rules and of the network, each with its true/false label"""
    out = []
    cv = json.loads(Path(cv_json).read_text())["runs"][0]
    for source, get in (("rule", lambda s: C.rule_frames(s, {"fall_still_s": 1.0})),
                        ("net", lambda s: C.fires(cv[s]["end"], cv[s]["p"], 0.95, 3) if s in cv else [])):
        for s in seqs:
            for fr in get(s):
                ff = data.fall_frames(s)
                true = ff is not None and ff[0] <= fr <= ff[1] + 150
                t = fr / FPS
                n = data_len(s) / FPS
                tag = "" if after == 0.5 else f"+a{after}"
                out.append((f"{s}:{source}@{fr}{tag}", s, f"alert_{source}", int(true), max(0.0, t - 2.5), min(n, t + after)))
    return out


_LEN = {}


def data_len(seq):
    if seq not in _LEN:
        import cv2
        _LEN[seq] = int(cv2.VideoCapture(str(data.URFALL / f"{seq}.mp4")).get(cv2.CAP_PROP_FRAME_COUNT))
    return _LEN[seq]


def grab(seq, t0, t1, n):
    import cv2
    from PIL import Image
    cap = cv2.VideoCapture(str(data.URFALL / f"{seq}.mp4"))
    frames = []
    ok, f = cap.read()
    while ok:
        frames.append(f)
        ok, f = cap.read()
    idx = np.clip(np.round(np.linspace(t0, t1, n) * FPS).astype(int), 0, len(frames) - 1)
    return [Image.fromarray(cv2.cvtColor(frames[i], cv2.COLOR_BGR2RGB)) for i in idx], (t1 - t0) / max(1, n - 1)


def auc(score, y):
    from scipy.stats import rankdata
    r = rankdata(score)
    pos = np.array(y) == 1
    return float((r[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * (~pos).sum()))


def summarise(rows, no_thrs=(0.5, 0.7, 0.9, 0.95, 0.99), yes_thr=0.5):
    out = {}
    ann = [r for r in rows if r["kind"] == "annotated"]
    if ann:
        py = [r["p_yes"] for r in ann]
        y = [r["label"] for r in ann]
        out["annotated"] = {"windows": len(ann), "falls": sum(y), "auc_p_yes": auc(py, y),
                            "yes_at_0.5": {"falls_yes": sum(1 for r in ann if r["label"] and r["p_yes"] >= yes_thr), "non_falls_yes": sum(1 for r in ann if not r["label"] and r["p_yes"] >= yes_thr)},
                            "median_latency_s": float(np.median([r["latency_s"] for r in ann]))}
    for kind in ("alert_rule", "alert_net"):
        al = [r for r in rows if r["kind"] == kind]
        if not al:
            continue
        true, false = [r for r in al if r["label"]], [r for r in al if not r["label"]]
        res = {"alerts": len(al), "true": len(true), "false": len(false), "by_no_threshold": {}}
        for thr in no_thrs:
            res["by_no_threshold"][str(thr)] = {"false_removed": sum(1 for r in false if r["p_no"] >= thr), "true_lost": sum(1 for r in true if r["p_no"] >= thr)}
        out[kind] = res
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=6)
    ap.add_argument("--cv", default=str(REPO / "eval/work/train/cv_p_stgcn_T48.json"))
    ap.add_argument("--only", default="", help="comma list of sequences")
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--tag", default="")
    ap.add_argument("--no-reason", action="store_true")
    ap.add_argument("--after", type=float, default=0.5, help="seconds of video after the report that the VLM may look at (a skeleton-network report comes DURING the fall)")
    ap.add_argument("--style", default="options", choices=["options", "yn"])
    a = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / f"results_f{a.frames}{a.tag}.jsonl" if a.style == "yn" else CACHE / f"results_{a.style}_f{a.frames}{a.tag}.jsonl"
    done = {}
    if cache_file.exists():
        for line in cache_file.read_text().splitlines():
            r = json.loads(line)
            done[r["id"]] = r
    seqs = [s for s in data.sequences() if not a.only or s in a.only.split(",")]
    todo = windows(seqs) + alert_windows(seqs, a.cv, a.after)
    print(f"{len(todo)} windows, {sum(1 for w in todo if w[0] in done)} cached", flush=True)
    if not a.summary_only:
        from vlm_service.verifier import QwenVL
        m = QwenVL(style=a.style)
        with cache_file.open("a") as f:
            for i, (wid, s, kind, label, t0, t1) in enumerate(todo):
                if wid in done:
                    continue
                frames, dt = grab(s, t0, t1, a.frames)
                v = m.verify("FALL", frames, dt, reason=not a.no_reason)
                r = {"id": wid, "seq": s, "kind": kind, "label": label, "t0": t0, "t1": t1, "p_yes": v.p_yes, "p_no": v.p_no, "p_unsure": v.p_unsure, "letters": v.extra.get("letters"),
                     "latency_s": v.latency_s, "reason": v.reason}
                done[wid] = r
                f.write(json.dumps(r) + "\n")
                f.flush()
                if i % 20 == 0:
                    print(f"{i}/{len(todo)}", flush=True)
    rows = [done[w[0]] for w in todo if w[0] in done]
    print(json.dumps(summarise(rows), indent=1))


if __name__ == "__main__":
    main()
