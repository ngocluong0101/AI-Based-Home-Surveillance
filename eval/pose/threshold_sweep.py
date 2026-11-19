"""V2-2: how many people does the pose model find compared with the detection model, and what does the score threshold do?

The pose model (YOLOv8n-pose) is trained on people with annotated keypoints, so its scores for small, far or half-hidden
people are lower than the detection model's. This runs both networks (ONNX Runtime FP32, the engine's preprocessing, input
640x384, NMS 0.45) on a MOT17 video and prints, for several thresholds, the detections, the precision and the recall by
pedestrian height against the MOT ground truth.

    nice -n 19 eval/.venv/bin/python eval/pose/threshold_sweep.py --seq 09 --threads 4
Needs the video eval/work/mot17/MOT17-<seq>.mp4 (see eval/README.md) and engine/models/yolov8n_h384.onnx (copy of yolov8n.onnx).
It uses the CPU only; do not run it while a benchmark is running.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.common import boxes as B, refdet, replica  # noqa: E402
from eval.detection.compare_pipelines import HEIGHT_BINS, gt_boxes  # noqa: E402

THRESHOLDS = (0.25, 0.15, 0.10, 0.05, 0.02)


def evaluate(per_frame, gt, thr):
    found = {b: [0, 0] for b in HEIGHT_BINS}
    tp = fp = 0
    for k, (bx, sc) in enumerate(per_frame):
        keep = sc >= thr
        bx, sc = bx[keep], sc[keep]
        g = gt.get(k + 1, np.zeros((0, 4)))
        pairs, only_det, _ = B.greedy_match(bx, sc, g, np.ones(len(g)), 0.5)
        tp += len(pairs)
        fp += len(only_det)
        hit = {j for _, j in pairs}
        for j, box in enumerate(g):
            for lo, hi in HEIGHT_BINS:
                if lo <= box[3] < hi:
                    found[(lo, hi)][1] += 1
                    found[(lo, hi)][0] += int(j in hit)
    return {"detections": tp + fp, "precision": tp / max(tp + fp, 1),
            "recall_by_height_px": {f"{lo}-{hi if hi < 10000 else 'inf'}": a / max(b, 1) for (lo, hi), (a, b) in found.items()}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seq", default="09")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", type=Path, default=REPO / "eval" / "results" / "2026-11-19_v2-2_pose_vs_detection_threshold.json")
    args = ap.parse_args()

    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = args.threads
    pose = ort.InferenceSession(str(REPO / "engine/models/yolov8n-pose.onnx"), opts, providers=["CPUExecutionProvider"])
    det = ort.InferenceSession(str(REPO / "engine/models/yolov8n_h384.onnx"), opts, providers=["CPUExecutionProvider"])
    P, D = [], []
    for f in refdet.iter_frames(REPO / "eval" / "work" / "mot17" / f"MOT17-{args.seq}.mp4"):
        x, s, px, py = replica.letterbox_net(f, 640, 384, cv2.INTER_NEAREST)
        P.append(replica.decode_pose(pose.run(None, {"images": x[None]})[0][0], s, px, py, 640, 384, min(THRESHOLDS), 0.45)[:2])
        D.append(replica.decode(det.run(None, {"images": x[None]})[0][0], s, px, py, min(THRESHOLDS), 0.45))
    gt = gt_boxes(args.seq)
    result = {"sequence": args.seq, "pose": {}, "detection": {}}
    for name, per in (("pose", P), ("detection", D)):
        for thr in THRESHOLDS:
            r = evaluate(per, gt, thr)
            result[name][str(thr)] = r
            rec = " ".join(f"{k}:{v:.2f}" for k, v in r["recall_by_height_px"].items())
            print(f"{name:9s} thr={thr:<5} dets={r['detections']:5d} prec={r['precision']:.3f}  recall by height (px) {rec}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
