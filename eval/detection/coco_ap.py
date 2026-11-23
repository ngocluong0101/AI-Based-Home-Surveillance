"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

V2-1: AP of the person detector, same network, different pipelines, on the same 1000 COCO val2017 images.

Pipelines (all see the SAME decoded pixels, except the first):
  ultra_original   Ultralytics PyTorch FP32 on the original image files (the "official" number; reference only)
  ultra_default    Ultralytics FP32 on the decoded video frames, NMS IoU 0.7
  ultra_iou045     the same with NMS IoU 0.45 (the engine config)
  ort_nearest      Python replica of the engine (nearest resize, zero padding, parser logic, NMS 0.45), ONNX Runtime FP32
  ort_linear       the same with bilinear resize (what Ultralytics uses)
  engine_trt       the engine itself: TensorRT FP16 in DeepStream (dump of ds_engine --dump-detections)

Prepare the inputs first:
  python eval/detection/make_coco_video.py --n 1000
  eval/tools/in_container.sh eval/tools/encode_h264.sh png eval/work/coco/frames/%06d.png eval/work/coco/coco1000.mp4
  python eval/tools/make_pgie_config.py --batch 1 --threshold 0.001 --out eval/work/pgie_b1_t0001.txt
  eval/tools/in_container.sh engine/build/ds_engine --config /work/eval/work/pgie_b1_t0001.txt \
      --tracker-config /work/engine/configs/tracker_nvdcf.yml --camera 1=file:///work/eval/work/coco/coco1000.mp4 \
      --dump-detections /work/eval/work/coco/ds_dump.csv --fps-interval 0

AP uses pycocotools (bbox, person, all 1000 images). "at 0.25" columns are the engine's operating point: score >= 0.25 matched to
non-crowd ground truth at IoU 0.5 (crowd regions are not ignored, so precision is a little pessimistic: compare pipelines with
each other, not with a paper).
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

from eval.common import boxes as B, ds_dump, replica  # noqa: E402

PERSON = 1  # COCO category id


def clip_to_image(xywh: np.ndarray, w: int, h: int) -> np.ndarray:
    x1 = np.clip(xywh[:, 0], 0, w)
    y1 = np.clip(xywh[:, 1], 0, h)
    x2 = np.clip(xywh[:, 0] + xywh[:, 2], 0, w)
    y2 = np.clip(xywh[:, 1] + xywh[:, 3], 0, h)
    return np.stack([x1, y1, x2 - x1, y2 - y1], axis=1)


def canvas_to_image(xywh: np.ndarray, fm: dict) -> np.ndarray:
    """Boxes in the 1920x1080 canvas -> pixels of the original COCO image (undo scale + padding)."""
    out = xywh.copy().astype(float)
    out[:, 0] = (out[:, 0] - fm["pad_x"]) / fm["scale"]
    out[:, 1] = (out[:, 1] - fm["pad_y"]) / fm["scale"]
    out[:, 2] /= fm["scale"]
    out[:, 3] /= fm["scale"]
    return clip_to_image(out, fm["width"], fm["height"])


def coco_eval(gt, preds: list, img_ids: list) -> dict:
    from pycocotools.cocoeval import COCOeval

    if not preds:
        return {"AP": 0.0, "AP50": 0.0, "AR100": 0.0}
    with contextlib.redirect_stdout(io.StringIO()):
        dt = gt.loadRes(preds)
        ev = COCOeval(gt, dt, "bbox")
        ev.params.imgIds = img_ids
        ev.params.catIds = [PERSON]
        ev.evaluate()
        ev.accumulate()
        ev.summarize()
    s = ev.stats
    return {"AP": float(s[0]), "AP50": float(s[1]), "APs": float(s[3]), "APm": float(s[4]), "APl": float(s[5]),
            "AR100": float(s[8])}


def operating_point(gt, preds: list, img_ids: list, score_min: float = 0.25) -> dict:
    """Detections >= score_min matched to non-crowd ground truth at IoU 0.5."""
    by_img = {}
    for p in preds:
        if p["score"] >= score_min:
            by_img.setdefault(p["image_id"], []).append(p)
    tp = fp = fn = 0
    for iid in img_ids:
        anns = [a for a in gt.loadAnns(gt.getAnnIds(imgIds=[iid], catIds=[PERSON], iscrowd=False))]
        gtb = np.array([a["bbox"] for a in anns], dtype=float).reshape(-1, 4)
        dets = by_img.get(iid, [])
        db = np.array([d["bbox"] for d in dets], dtype=float).reshape(-1, 4)
        ds = np.array([d["score"] for d in dets], dtype=float)
        pairs, only_det, only_gt = B.greedy_match(db, ds, gtb, np.ones(len(gtb)), 0.5)
        tp += len(pairs)
        fp += len(only_det)
        fn += len(only_gt)
    return {"detections": tp + fp, "tp": tp, "fp": fp, "fn": fn,
            "precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=REPO / "eval" / "work" / "coco")
    ap.add_argument("--coco", type=Path, default=REPO / "datasets" / "coco")
    ap.add_argument("--video", type=Path, default=None)
    ap.add_argument("--dump", type=Path, default=None)
    ap.add_argument("--weights", type=Path, default=REPO / "yolov8n.pt")
    ap.add_argument("--onnx", type=Path, default=REPO / "engine" / "models" / "yolov8n.onnx")
    ap.add_argument("--out", type=Path, default=None, help="JSON result file (default: eval/results/<date>_v2-1_coco_ap.json)")
    args = ap.parse_args()
    video = args.video or args.work / "coco1000.mp4"
    dump_path = args.dump or args.work / "ds_dump.csv"

    from pycocotools.coco import COCO

    with contextlib.redirect_stdout(io.StringIO()):
        gt = COCO(str(args.coco / "annotations" / "instances_val2017.json"))
    meta = json.loads((args.work / "frames.json").read_text())["frames"]
    img_ids = [m["image_id"] for m in meta]

    def iter_frames():
        """The decoded frames, one at a time (1000 frames of 1080p do not fit in memory twice over)."""
        cap = cv2.VideoCapture(str(video))
        while True:
            ok, f = cap.read()
            if not ok:
                break
            yield f

    n_frames = sum(1 for _ in iter_frames())
    if n_frames != len(meta):
        raise SystemExit(f"video has {n_frames} frames, mapping has {len(meta)}")

    results = {}

    def add(name: str, per_frame_boxes_scores, to_image) -> None:
        preds = []
        for k, (xywh, sc) in enumerate(per_frame_boxes_scores):
            if len(xywh) == 0:
                continue
            xywh = to_image(xywh, meta[k])
            for b, s in zip(xywh, sc):
                preds.append({"image_id": meta[k]["image_id"], "category_id": PERSON,
                              "bbox": [round(float(v), 3) for v in b], "score": round(float(s), 6)})
        r = coco_eval(gt, preds, img_ids)
        r["at_0.25"] = operating_point(gt, preds, img_ids)
        r["detections_total"] = len(preds)
        results[name] = r
        print(f"{name:16s} AP={r['AP']:.4f} AP50={r['AP50']:.4f} AR100={r['AR100']:.4f} | "
              f"@0.25: n={r['at_0.25']['detections']} P={r['at_0.25']['precision']:.3f} R={r['at_0.25']['recall']:.3f}")

    from ultralytics import YOLO

    model = YOLO(str(args.weights))

    def run_ultra(source, iou):
        """source: a list of file names, or an iterator of frames (processed 16 at a time)."""
        def chunks():
            if isinstance(source, list):
                for i in range(0, len(source), 16):
                    yield source[i:i + 16]
                return
            buf = []
            for f in source:
                buf.append(f)
                if len(buf) == 16:
                    yield buf
                    buf = []
            if buf:
                yield buf

        out = []
        for chunk in chunks():
            for res in model.predict(chunk, imgsz=640, conf=0.001, iou=iou, classes=[0], max_det=300,
                                     half=False, verbose=False):
                b = res.boxes
                xyxy = b.xyxy.cpu().numpy().astype(float)
                xywh = np.stack([xyxy[:, 0], xyxy[:, 1], xyxy[:, 2] - xyxy[:, 0], xyxy[:, 3] - xyxy[:, 1]], axis=1)
                out.append((xywh, b.conf.cpu().numpy()))
        return out

    ident = lambda xywh, fm: clip_to_image(xywh, fm["width"], fm["height"])  # noqa: E731
    originals = [str(args.coco / "val2017" / m["file_name"]) for m in meta]
    add("ultra_original", run_ultra(originals, 0.7), ident)
    add("ultra_default", run_ultra(iter_frames(), 0.7), canvas_to_image)
    add("ultra_iou045", run_ultra(iter_frames(), 0.45), canvas_to_image)

    import onnxruntime as ort

    sess = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])
    for name, interp in (("ort_nearest", cv2.INTER_NEAREST), ("ort_linear", cv2.INTER_LINEAR)):
        per_frame = []
        for f in iter_frames():
            x, s, px, py = replica.letterbox(f, interp, 0)
            out = sess.run(None, {"images": x[None]})[0][0]
            per_frame.append(replica.decode(out, s, px, py, 0.001, 0.45))
        add(name, per_frame, canvas_to_image)

    if dump_path.is_file():
        d = ds_dump.load_dump(dump_path)
        per_frame = [ds_dump.boxes_of(d["det"], k) for k in range(len(meta))]
        add("engine_trt", per_frame, canvas_to_image)
    else:
        print(f"no engine dump at {dump_path}; skipping engine_trt")

    out = args.out or REPO / "eval" / "results" / "2026-11-19_v2-1_coco_ap.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"images": len(meta), "category": "person", "pipelines": results}, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
