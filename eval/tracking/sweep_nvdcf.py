"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

V2-1: change one NvDCF parameter at a time and see what it does to MOTA / IDF1 / ID switches on MOT17.

    python eval/tracking/sweep_nvdcf.py maxShadowTrackingAge=15 maxShadowTrackingAge=60 probationAge=5
Each KEY=VALUE makes a copy of engine/configs/tracker_nvdcf.yml with that line changed, runs the engine on the sequences (in the
container, with the 640x384 detector config eval/work/pgie_b1_384.txt so the detector is the same every run) and evaluates it. The
repo's config is not touched: values found on a street scene must not become the defaults of a hallway camera (V2-4 decides).
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.tracking import mot_eval  # noqa: E402

import motmetrics as mm  # noqa: E402


def variant(base: str, key: str, value: str) -> str:
    out, n = re.subn(rf"^(\s*{re.escape(key)}:\s*)\S+", rf"\g<1>{value}", base, flags=re.M)
    if n != 1:
        raise SystemExit(f"{key}: found {n} lines in the tracker config, expected exactly 1")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("changes", nargs="*", help="KEY=VALUE, one per run; the unchanged config is always run first")
    ap.add_argument("--seqs", nargs="+", default=["02", "04", "09"])
    ap.add_argument("--pgie", default="/work/eval/work/pgie_b1_384.txt")
    ap.add_argument("--out", type=Path, default=REPO / "eval" / "results" / "2026-11-19_v2-1_nvdcf_sweep.json")
    args = ap.parse_args()

    base = (REPO / "engine" / "configs" / "tracker_nvdcf.yml").read_text()
    runs = [("baseline", base)] + [(c, variant(base, *c.split("=", 1))) for c in args.changes]
    tdir = REPO / "eval" / "work" / "trackers"
    tdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, text in runs:
        tag = "sweep_" + re.sub(r"[^A-Za-z0-9.]+", "_", name)
        cfg = tdir / f"{tag}.yml"
        cfg.write_text(text)
        for s in args.seqs:
            subprocess.run(
                ["nice", "-n", "10", str(REPO / "eval/tools/in_container.sh"), "engine/build/ds_engine",
                 "--config", args.pgie, "--tracker-config", f"/work/eval/work/trackers/{cfg.name}",
                 "--camera", f"1=file:///work/eval/work/mot17/MOT17-{s}.mp4",
                 "--dump-detections", f"/work/eval/work/mot17/MOT17-{s}.{tag}.csv", "--fps-interval", "0"],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        accs = [mot_eval.evaluate_sequence(s, REPO / "eval/work/mot17" / f"MOT17-{s}.{tag}.csv") for s in args.seqs]
        summ = mm.metrics.create().compute_many(accs, metrics=mot_eval.METRICS, names=args.seqs, generate_overall=True)
        o = summ.loc["OVERALL"]
        row = {"change": name, "mota": float(o.mota), "idf1": float(o.idf1), "recall": float(o.recall),
               "idsw": int(o.num_switches), "fm": int(o.num_fragmentations), "fp": int(o.num_false_positives),
               "mt": int(o.mostly_tracked), "ml": int(o.mostly_lost)}
        rows.append(row)
        print(f"{name:28s} MOTA={row['mota']:.3f} IDF1={row['idf1']:.3f} recall={row['recall']:.3f} "
              f"IDSW={row['idsw']:4d} FM={row['fm']:4d} FP={row['fp']:5d} MT={row['mt']} ML={row['ml']}", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"sequences": args.seqs, "detector": "640x384 input (pgie_b1_384)", "runs": rows}, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
