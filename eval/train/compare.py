"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Event-level comparison on UR Fall: the rules (replay of src/events/behavior.py on the same dumps) against the cross-validated skeleton model.

    eval/.venv/bin/python eval/train/compare.py eval/work/train/cv_stgcn_T48.json --k 3 --thr 0.5,0.7,0.9

Same footing for both: an event is a TIME at which the system reports (ST-GCN: the frame where its score has been >= thr for k
evaluations; rule: the frame of its report). A fall sequence is detected if a report falls in [f0, f1 + 150] frames (f0/f1: UR Fall
start of the fall / of lying); any other report in a fall sequence, and any report in an ADL sequence, is a false alarm. Reports
closer than 3 s are one report. 30 fps.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.e2e import labels, replay  # noqa: E402
from eval.train import data  # noqa: E402

FPS, REFRACTORY = 30.0, 90


def fires(end, p, thr, k):
    out, run, last = [], 0, -10**9
    for e, v in zip(end, p):
        run = run + 1 if v >= thr else 0
        if run >= k and e - last > REFRACTORY:
            out.append(int(e))
            last = e
    return out


def judge(seq, frames):
    ff = data.fall_frames(seq)
    if ff is None:
        return {"tp": 0, "fn": 0, "fp": len(frames), "delay": []}
    f0, f1, _ = ff
    hit = [f for f in frames if f0 <= f <= f1 + 150]
    return {"tp": int(bool(hit)), "fn": int(not hit), "fp": len(frames) - len(hit[:1]) if hit else len(frames), "delay": [(hit[0] - f1) / FPS] if hit else []}


def table(per_seq: dict, name: str):
    falls = [s for s in per_seq if s.startswith("fall")]
    adls = [s for s in per_seq if s.startswith("adl")]
    tp = sum(per_seq[s]["tp"] for s in falls)
    fp_adl = sum(per_seq[s]["fp"] for s in adls)
    fp_fall = sum(per_seq[s]["fp"] for s in falls)
    delay = [d for s in falls for d in per_seq[s]["delay"]]
    print(f"{name:<34} falls {tp}/{len(falls)}  FP in {len(adls)} ADL sequences {fp_adl}  FP elsewhere in fall sequences {fp_fall}"
          + (f"  report - lying frame: median {np.median(delay):+.2f}s max {max(delay):+.2f}s" if delay else ""))
    return {"name": name, "falls": len(falls), "tp": tp, "adl": len(adls), "fp_adl": fp_adl, "fp_fall_seq": fp_fall,
            "delay_median_s": float(np.median(delay)) if delay else None}


def rule_frames(seq, overrides):
    lab = labels.parse(dict(clip=f"{seq}.mp4", fps=30, width=640, height=480, scene="s", person="p", split="dev", duration_s=60.0, has_incident=False, events=[]), seq)
    preds = replay.replay(data.URFALL / f"{seq}.csv", lab, overrides)
    return [int(round(p.t_reported * FPS)) for p in preds if p.type == "fall"]


def window_auc(run: dict, labels: dict) -> float:
    from scipy.stats import rankdata
    p = np.array([v for sq in run for v in run[sq]["p"]])
    y = np.array([v for sq in run for v in labels[sq]])
    r = rankdata(p)
    pos = y == 1
    return float((r[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * (~pos).sum()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cv", nargs="*", type=Path)
    ap.add_argument("--thr", default="0.5,0.8,0.9,0.95")
    ap.add_argument("--k", type=int, default=3, help="evaluations in a row above the threshold (1 evaluation per frame)")
    ap.add_argument("--no-rule", action="store_true")
    ap.add_argument("--write", default="")
    a = ap.parse_args()
    out = []
    seqs = data.sequences()
    n_fall = sum(s.startswith("fall") for s in seqs)
    n_adl = len(seqs) - n_fall
    if not a.no_rule:
        for name, ov in (("rule, fall_still_s=2.0 (default)", {}), ("rule, fall_still_s=1.0 (tuned on 5 of these)", {"fall_still_s": 1.0})):
            out.append(table({s: judge(s, rule_frames(s, ov)) for s in seqs}, name))
    print(f"\n{'model':<26}{'seeds':>6}{'AUC':>7}   thr  falls of {n_fall} (min-max)   false alarms in {n_adl} ADL (min-max)   outside the fall (min-max)   delay median s")
    for cv in a.cv:
        d = json.loads(cv.read_text())
        aucs = [window_auc(run, d["labels"]) for run in d["runs"]]
        for thr in (float(t) for t in a.thr.split(",")):
            rows = []
            for run in d["runs"]:
                per = {s: judge(s, fires(run[s]["end"], run[s]["p"], thr, a.k) if s in run else []) for s in seqs}
                rows.append(table_quiet(per))
            f = [r["tp"] for r in rows]
            fa = [r["fp_adl"] for r in rows]
            fe = [r["fp_fall_seq"] for r in rows]
            dl = [r["delay_median_s"] for r in rows if r["delay_median_s"] is not None]
            print(f"{cv.stem[3:]:<26}{len(rows):>6}{np.mean(aucs):7.3f}  {thr:4.2f}   {np.mean(f):5.1f} ({min(f)}-{max(f)})"
                  f"             {np.mean(fa):5.1f} ({min(fa)}-{max(fa)})                  {np.mean(fe):4.1f} ({min(fe)}-{max(fe)})"
                  f"          {np.median(dl):+.2f}" if dl else "")
            out.append({"model": cv.stem[3:], "thr": thr, "k": a.k, "seeds": len(rows), "window_auc": aucs, "falls": f, "false_alarms_adl": fa,
                        "false_alarms_in_fall_sequences": fe, "delay_median_s": dl})
    if a.write:
        Path(a.write).write_text(json.dumps(out, indent=1))


def table_quiet(per_seq: dict) -> dict:
    falls = [s for s in per_seq if s.startswith("fall")]
    adls = [s for s in per_seq if s.startswith("adl")]
    delay = [d for s in falls for d in per_seq[s]["delay"]]
    return {"tp": sum(per_seq[s]["tp"] for s in falls), "fp_adl": sum(per_seq[s]["fp"] for s in adls), "fp_fall_seq": sum(per_seq[s]["fp"] for s in falls),
            "delay_median_s": float(np.median(delay)) if delay else None}


if __name__ == "__main__":
    main()
