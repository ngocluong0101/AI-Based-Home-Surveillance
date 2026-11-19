"""V2-1: why does the engine find fewer people than Ultralytics? Same network, same decoded frames, different pipelines.

For each MOT17 sequence (video made by eval/tools/encode_h264.sh, engine run with --dump-detections):
  engine          the dump of ds_engine (stage "det": what nvinfer reports, threshold 0.25, NMS IoU 0.45)
  ultra_default   Ultralytics PyTorch with its default NMS IoU 0.7
  ultra_iou045    Ultralytics with the engine's NMS IoU 0.45
  ort_nearest     Python replica of the engine (nearest-neighbour resize, zero padding), ONNX Runtime FP32
  ort_linear      the same with bilinear resize
  ort_rect114_iou045 / _iou07   replica with Ultralytics' preprocessing (rectangular 640x384 letterbox, pad 114, bilinear)
                  and NMS IoU 0.45 / 0.7: separates "how the picture is prepared" from "how the boxes are chosen"
  engine_<tag>    another run of the engine, e.g. engine_rect384 (input 640x384 instead of 640x640), from its dump
  ultra_1280      Ultralytics at 1280 input (NMS IoU 0.45): how much does a larger network input help small people?
It prints, per pipeline: detections, recall/precision against the MOT ground truth, recall by pedestrian height,
and the agreement with the engine (common / only in the pipeline / only in the engine, with the share of those
that sit just above the threshold, i.e. boxes that could flip with a tiny numeric difference).

    python eval/detection/compare_pipelines.py --seqs 02 04 09
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.common import boxes as B, ds_dump, refdet  # noqa: E402

MOT_ROOT = REPO / "datasets" / "mot17" / "MOT17" / "train"
CONF = 0.25
HEIGHT_BINS = [(0, 50), (50, 100), (100, 200), (200, 10000)]
NEAR = 0.35   # "just above the threshold" = confidence below this


def gt_boxes(seq: str) -> dict:
    cols = ["frame", "id", "x", "y", "w", "h", "flag", "cls", "vis"]
    gt = pd.read_csv(MOT_ROOT / f"MOT17-{seq}-FRCNN" / "gt" / "gt.txt", header=None, names=cols)
    gt = gt[(gt.cls == 1) & (gt.flag != 0)]
    return {int(f): g[["x", "y", "w", "h"]].to_numpy(float) for f, g in gt.groupby("frame")}


def run_seq(seq: str, work: Path, model, session, only=None) -> dict:
    video = work / f"MOT17-{seq}.mp4"
    d = ds_dump.load_dump(work / f"MOT17-{seq}.nvdcf.csv")
    n = len(d["frames"])
    pipes = {"engine": [ds_dump.boxes_of(d["det"], k) for k in range(n)]}
    builders = {
        "ultra_default": lambda v: refdet.ultralytics_detect(model, refdet.iter_frames(v), CONF, 0.7),
        "ultra_iou045": lambda v: refdet.ultralytics_detect(model, refdet.iter_frames(v), CONF, 0.45),
        "ort_nearest": lambda v: refdet.ort_detect(session, refdet.iter_frames(v), cv2.INTER_NEAREST, CONF, 0.45),
        "ort_linear": lambda v: refdet.ort_detect(session, refdet.iter_frames(v), cv2.INTER_LINEAR, CONF, 0.45),
        "ort_rect114_iou045": lambda v: refdet.ort_detect_rect(session, refdet.iter_frames(v), CONF, 0.45),
        "ort_rect114_iou07": lambda v: refdet.ort_detect_rect(session, refdet.iter_frames(v), CONF, 0.7),
        "ultra_1280": lambda v: refdet.ultralytics_detect(model, refdet.iter_frames(v), CONF, 0.45, imgsz=1280),
    }
    for name, build in builders.items():
        if only is None or name in only:
            pipes[name] = build(video)
    # other engine runs (other config), named engine_<tag>: the dump MOT17-<seq>.<tag>.csv
    for name in (only or []):
        if name.startswith("engine_"):
            dd = ds_dump.load_dump(work / f"MOT17-{seq}.{name[len('engine_'):]}.csv")
            pipes[name] = [ds_dump.boxes_of(dd["det"], k) for k in range(len(dd["frames"]))]
    gt = gt_boxes(seq)

    result = {}
    for name, per_frame in pipes.items():
        tp = fp = fn = 0
        by_h = {b: [0, 0] for b in HEIGHT_BINS}   # [found, total]
        common = only_pipe = only_eng = 0
        near_pipe = near_eng = 0
        total = 0
        for k in range(n):
            pb, ps = per_frame[k]
            keep = ps >= CONF
            pb, ps = pb[keep], ps[keep]
            total += len(pb)
            g = gt.get(k + 1, np.zeros((0, 4)))
            pairs, only_det, only_gt = B.greedy_match(pb, ps, g, np.ones(len(g)), 0.5)
            tp += len(pairs); fp += len(only_det); fn += len(only_gt)
            found = {j for _, j in pairs}
            for j, box in enumerate(g):
                for lo, hi in HEIGHT_BINS:
                    if lo <= box[3] < hi:
                        by_h[(lo, hi)][1] += 1
                        by_h[(lo, hi)][0] += int(j in found)
            if name != "engine":
                eb, es = pipes["engine"][k]
                m = es >= CONF
                eb, es = eb[m], es[m]
                pr, only_p, only_e = B.greedy_match(pb, ps, eb, es, 0.5)
                common += len(pr); only_pipe += len(only_p); only_eng += len(only_e)
                near_pipe += sum(1 for i in only_p if ps[i] < NEAR)
                near_eng += sum(1 for j in only_e if es[j] < NEAR)
        r = {"detections": total, "tp": tp, "fp": fp, "fn": fn, "recall": tp / max(tp + fn, 1),
             "precision": tp / max(tp + fp, 1),
             "recall_by_height_px": {f"{lo}-{hi if hi < 10000 else 'inf'}": [v[0], v[1]] for (lo, hi), v in by_h.items()}}
        if name != "engine":
            r["vs_engine"] = {"common": common, "only_pipeline": only_pipe, "only_engine": only_eng,
                              "only_pipeline_near_threshold": near_pipe, "only_engine_near_threshold": near_eng}
        result[name] = r
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seqs", nargs="+", default=["02", "04", "09"])
    ap.add_argument("--work", type=Path, default=REPO / "eval" / "work" / "mot17")
    ap.add_argument("--only", default=None, help="comma-separated pipelines to (re)compute; the others are kept from --out")
    ap.add_argument("--threads", type=int, default=4, help="CPU threads for ONNX Runtime (this runs on a laptop)")
    ap.add_argument("--out", type=Path, default=REPO / "eval" / "results" / "2026-11-19_v2-1_pipeline_gap.json")
    args = ap.parse_args()

    from ultralytics import YOLO
    import onnxruntime as ort

    model = YOLO(str(REPO / "yolov8n.pt"))
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = args.threads
    session = ort.InferenceSession(str(REPO / "engine" / "models" / "yolov8n.onnx"), opts, providers=["CPUExecutionProvider"])

    only = set(args.only.split(",")) if args.only else None
    per_seq = {}
    if only and args.out.is_file():          # keep what an earlier run computed, add the new pipelines
        per_seq = json.loads(args.out.read_text())["per_sequence"]
    for s in args.seqs:
        per_seq.setdefault(s, {}).update(run_seq(s, args.work, model, session, only))
    # sum over sequences
    total = {}
    for s, res in per_seq.items():
        for name, r in res.items():
            t = total.setdefault(name, {"detections": 0, "tp": 0, "fp": 0, "fn": 0, "vs_engine": {}, "recall_by_height_px": {}})
            for key in ("detections", "tp", "fp", "fn"):
                t[key] += r[key]
            for k, v in r.get("vs_engine", {}).items():
                t["vs_engine"][k] = t["vs_engine"].get(k, 0) + v
            for k, (a, b) in r["recall_by_height_px"].items():
                cur = t["recall_by_height_px"].setdefault(k, [0, 0])
                cur[0] += a; cur[1] += b
    for name, t in total.items():
        t["recall"] = t["tp"] / max(t["tp"] + t["fn"], 1)
        t["precision"] = t["tp"] / max(t["tp"] + t["fp"], 1)

    eng = total["engine"]["detections"]
    print(f"sequences {args.seqs}; detections with score >= {CONF}; GT = MOT17 pedestrians (flag 1)")
    print(f"{'pipeline':14s} {'dets':>7s} {'vs engine':>10s} {'recall':>7s} {'prec':>6s}  recall by pedestrian height (px)  | common/only_pipe/only_engine (near thr.)")
    for name, t in total.items():
        hs = "  ".join(f"{k}:{v[0] / max(v[1], 1):.2f}" for k, v in t["recall_by_height_px"].items())
        ve = t["vs_engine"]
        extra = (f"{ve['common']}/{ve['only_pipeline']}/{ve['only_engine']} "
                 f"({ve['only_pipeline_near_threshold']}/{ve['only_engine_near_threshold']})") if ve else "-"
        print(f"{name:14s} {t['detections']:7d} {t['detections'] / eng - 1:+9.1%} {t['recall']:7.3f} {t['precision']:6.3f}  {hs}  | {extra}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"sequences": args.seqs, "conf": CONF, "total": total, "per_sequence": per_seq}, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
