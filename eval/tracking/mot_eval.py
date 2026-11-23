"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

V2-1: MOTA / IDF1 / ID switches of the engine's tracker on MOT17 sequences.

Input: one dump per sequence from `ds_engine --dump-detections` (stage "trk" rows) plus the MOT17 ground truth, with the MOT16/17
protocol (as TrackEval): only "pedestrian" with flag 1 is ground truth; tracker boxes matching a "distractor" box are removed; a match
needs IoU >= 0.5. These are PRIVATE detections (this project's YOLOv8n), so the numbers are not comparable with the MOT17 leaderboard.

    python eval/tracking/mot_eval.py --seqs 02 04 09 --tag nvdcf
(dumps are eval/work/mot17/MOT17-<seq>.<tag>.csv; see eval/README.md)

motmetrics 1.4.0 still calls np.asfarray, which NumPy 2 removed; it is put back below. Checked on a known case (20 objects, one ID
switch -> MOTA 0.95, IDF1 0.75).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

if not hasattr(np, "asfarray"):
    np.asfarray = lambda a, dtype=float: np.asarray(a, dtype=dtype)  # noqa: E731

import motmetrics as mm  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.optimize import linear_sum_assignment  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.common import boxes as B, ds_dump  # noqa: E402

MOT_ROOT = REPO / "datasets" / "mot17" / "MOT17" / "train"
DISTRACTORS = {2, 7, 8, 12}   # person on vehicle, static person, distractor, reflection
PEDESTRIAN = 1
IOU_MIN = 0.5

METRICS = ["mota", "motp", "idf1", "idp", "idr", "recall", "precision", "num_objects", "num_unique_objects",
           "mostly_tracked", "partially_tracked", "mostly_lost", "num_false_positives", "num_misses",
           "num_switches", "num_fragmentations"]


def load_gt(seq: str) -> pd.DataFrame:
    cols = ["frame", "id", "x", "y", "w", "h", "flag", "cls", "vis"]
    return pd.read_csv(MOT_ROOT / f"MOT17-{seq}-FRCNN" / "gt" / "gt.txt", header=None, names=cols)


def evaluate_sequence(seq: str, dump_path: Path) -> mm.MOTAccumulator:
    gt = load_gt(seq)
    trk = ds_dump.load_dump(dump_path)["trk"]
    n_frames = int(gt.frame.max())
    acc = mm.MOTAccumulator(auto_id=False)
    removed = 0
    for frame in range(1, n_frames + 1):
        g = gt[gt.frame == frame]
        keep = g[(g.cls == PEDESTRIAN) & (g.flag != 0)]
        dis = g[g.cls.isin(DISTRACTORS)]
        h = trk[trk.frame_num == frame - 1]     # the engine counts frames from 0, MOT from 1
        hb = h[["left", "top", "width", "height"]].to_numpy(float)
        hid = h.object_id.to_numpy(dtype=np.int64)

        # tracker boxes that match a distractor ground-truth box are not evaluated
        if len(hb) and len(dis):
            allgt = pd.concat([keep, dis])
            iou = B.iou_matrix(hb, allgt[["x", "y", "w", "h"]].to_numpy(float))
            r, c = linear_sum_assignment(-iou)
            is_dis = np.zeros(len(hb), bool)
            n_keep = len(keep)
            for i, j in zip(r, c):
                if iou[i, j] >= IOU_MIN and j >= n_keep:
                    is_dis[i] = True
            removed += int(is_dis.sum())
            hb, hid = hb[~is_dis], hid[~is_dis]

        gb = keep[["x", "y", "w", "h"]].to_numpy(float)
        dist = mm.distances.iou_matrix(gb, hb, max_iou=1 - IOU_MIN) if len(gb) and len(hb) else np.empty((len(gb), len(hb)))
        acc.update(keep.id.to_numpy(np.int64), hid, dist, frameid=frame)
    return acc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seqs", nargs="+", default=["02", "04", "09"])
    ap.add_argument("--tag", default="nvdcf", help="dump name: eval/work/mot17/MOT17-<seq>.<tag>.csv")
    ap.add_argument("--dump-dir", type=Path, default=REPO / "eval" / "work" / "mot17")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    accs, names = [], []
    for s in args.seqs:
        accs.append(evaluate_sequence(s, args.dump_dir / f"MOT17-{s}.{args.tag}.csv"))
        names.append(f"MOT17-{s}")
    mh = mm.metrics.create()
    summary = mh.compute_many(accs, metrics=METRICS, names=names, generate_overall=True)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    show = summary.copy()
    for c in ("mota", "motp", "idf1", "idp", "idr", "recall", "precision"):
        show[c] = show[c].map(lambda v: f"{v:.3f}")
    print(f"tracker: {args.tag}")
    print(show.rename(columns={"num_objects": "gt_boxes", "num_unique_objects": "gt_ids", "mostly_tracked": "MT",
                               "partially_tracked": "PT", "mostly_lost": "ML", "num_false_positives": "FP",
                               "num_misses": "FN", "num_switches": "IDSW", "num_fragmentations": "FM"}).to_string())
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({"tracker": args.tag, "summary": json.loads(summary.to_json(orient="index"))}, indent=2))
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
