"""V2-2: accuracy of the pose model (COCO keypoint AP, OKS) in the engine against the same network run outside it.

Same 1000 COCO val2017 images as V2-1 (eval/detection/make_coco_video.py), decoded from the same H.264 video, so every
pipeline sees the same pixels (except the first one). Pipelines:
  ultra_pose_original   Ultralytics PyTorch FP32 on the original image files (reference)
  ultra_pose_video      the same on the decoded video frames, NMS IoU 0.7 (Ultralytics default)
  ultra_pose_iou045     the same with NMS IoU 0.45 (what the engine's pose parser uses)
  ort_pose              Python replica of the engine (nvinfer preprocessing + the parser logic), ONNX Runtime FP32
  engine_trt_pose       the engine itself: TensorRT FP16 in DeepStream, keypoints read from the dump
Each is scored with pycocotools twice: keypoints (OKS) and boxes, so that the pose model can also be compared with the
detection-only model (V2-1: engine box AP 0.457).

Prepare the engine dump first:
  eval/.venv/bin/python eval/tools/make_pgie_config.py --base pgie_yolov8n_pose.txt --batch 1 --threshold 0.001 --out eval/work/pgie_pose_b1_t0001.txt
  eval/tools/in_container.sh engine/build/ds_engine --config /work/eval/work/pgie_pose_b1_t0001.txt --tracker-config /work/engine/configs/tracker_nvdcf.yml \
      --camera 1=file:///work/eval/work/coco/coco1000.mp4 --dump-detections /work/eval/work/coco/pose_dump.csv --fps-interval 0
    nice -n 19 eval/.venv/bin/python eval/pose/coco_kpt_ap.py
"""
import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from eval.common import ds_dump, refdet  # noqa: E402
from eval.detection.coco_ap import canvas_to_image, clip_to_image  # noqa: E402

PERSON = 1
NET_W, NET_H = 640, 384


def kp_to_image(kp: np.ndarray, fm: dict) -> np.ndarray:
    """Keypoints (n,17,3) in canvas pixels -> original image pixels."""
    out = kp.copy()
    out[:, :, 0] = (kp[:, :, 0] - fm["pad_x"]) / fm["scale"]
    out[:, :, 1] = (kp[:, :, 1] - fm["pad_y"]) / fm["scale"]
    return out


def score(gt, iou_type: str, preds: list, img_ids: list) -> dict:
    from pycocotools.cocoeval import COCOeval

    if not preds:
        return {"AP": 0.0}
    with contextlib.redirect_stdout(io.StringIO()):
        dt = gt.loadRes(preds)
        ev = COCOeval(gt, dt, iou_type)
        ev.params.imgIds = img_ids
        ev.params.catIds = [PERSON]
        ev.evaluate()
        ev.accumulate()
        ev.summarize()
    s = ev.stats
    if iou_type == "keypoints":
        return {"AP": float(s[0]), "AP50": float(s[1]), "AP75": float(s[2]), "AP_medium": float(s[3]),
                "AP_large": float(s[4]), "AR": float(s[5])}
    return {"AP": float(s[0]), "AP50": float(s[1]), "AR100": float(s[8])}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=REPO / "eval" / "work" / "coco")
    ap.add_argument("--coco", type=Path, default=REPO / "datasets" / "coco")
    ap.add_argument("--dump", type=Path, default=None)
    ap.add_argument("--threads", type=int, default=4, help="CPU threads for ONNX Runtime (this runs on a laptop)")
    ap.add_argument("--only-engine", action="store_true", help="recompute only engine_trt_pose; keep the rest of --out")
    ap.add_argument("--out", type=Path, default=REPO / "eval" / "results" / "2026-11-19_v2-2_coco_keypoints.json")
    args = ap.parse_args()
    video = args.work / "coco1000.mp4"
    dump_path = args.dump or args.work / "pose_dump.csv"

    from pycocotools.coco import COCO

    with contextlib.redirect_stdout(io.StringIO()):
        gt = COCO(str(args.coco / "annotations" / "person_keypoints_val2017.json"))
    meta = json.loads((args.work / "frames.json").read_text())["frames"]
    img_ids = [m["image_id"] for m in meta]
    results = {}

    def add(name: str, per_frame, to_image) -> None:
        kp_preds, box_preds = [], []
        for k, (xywh, sc, kp) in enumerate(per_frame):
            if len(xywh) == 0:
                continue
            xywh, kp = to_image(xywh, kp, meta[k])
            for b, s, p in zip(xywh, sc, kp):
                kp_preds.append({"image_id": meta[k]["image_id"], "category_id": PERSON, "score": round(float(s), 6),
                                 "keypoints": [round(float(v), 3) for v in p.reshape(-1)]})
                box_preds.append({"image_id": meta[k]["image_id"], "category_id": PERSON, "score": round(float(s), 6),
                                  "bbox": [round(float(v), 3) for v in b]})
        r = {"keypoints": score(gt, "keypoints", kp_preds, img_ids), "boxes": score(gt, "bbox", box_preds, img_ids),
             "detections": len(kp_preds), "detections_at_0.25": sum(1 for p in kp_preds if p["score"] >= 0.25)}
        results[name] = r
        print(f"{name:20s} keypoint AP={r['keypoints']['AP']:.4f} AP50={r['keypoints']['AP50']:.4f} "
              f"APm={r['keypoints']['AP_medium']:.4f} APl={r['keypoints']['AP_large']:.4f} | box AP={r['boxes']['AP']:.4f} "
              f"| dets>=0.25: {r['detections_at_0.25']}", flush=True)

    def from_original(xywh, kp, fm):
        return clip_to_image(xywh, fm["width"], fm["height"]), kp

    def from_canvas(xywh, kp, fm):
        return canvas_to_image(xywh, fm), kp_to_image(kp, fm)

    if args.only_engine and args.out.is_file():
        results.update(json.loads(args.out.read_text())["pipelines"])
    full = not args.only_engine

    from ultralytics import YOLO
    import onnxruntime as ort

    model = YOLO(str(REPO / "yolov8n-pose.pt"))
    if full:
        add("ultra_pose_original", refdet.ultralytics_pose(model, [str(args.coco / "val2017" / m["file_name"]) for m in meta],
                                                           0.001, 0.7), from_original)
        add("ultra_pose_video", refdet.ultralytics_pose(model, refdet.iter_frames(video), 0.001, 0.7), from_canvas)
        add("ultra_pose_iou045", refdet.ultralytics_pose(model, refdet.iter_frames(video), 0.001, 0.45), from_canvas)

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = args.threads
        sess = ort.InferenceSession(str(REPO / "engine" / "models" / "yolov8n-pose.onnx"), opts, providers=["CPUExecutionProvider"])
        # the replica already returns original-image pixels only after undoing the letterbox, which maps the 1920x1080 canvas
        # back; the canvas -> original step is still needed
        add("ort_pose", refdet.ort_pose(sess, refdet.iter_frames(video), NET_W, NET_H, cv2.INTER_NEAREST, 0.001, 0.45), from_canvas)


    if dump_path.is_file():
        d = ds_dump.load_dump(dump_path)
        per_frame = []
        for k in range(len(meta)):
            per_frame.append(ds_dump.keypoints_of(d["det"], k))
        add("engine_trt_pose", per_frame, from_canvas)
    else:
        print(f"no engine dump at {dump_path}: engine_trt_pose skipped")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"images": len(meta), "pipelines": results}, indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
