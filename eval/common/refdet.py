"""Reference detectors for V2-1: the same network run outside the engine, on the frames of a video.

All functions return one (boxes xywh, scores) pair per frame, in pixels of the video frame, and read the video
one frame at a time (a 1080p video does not fit in memory).
"""
from pathlib import Path

import cv2
import numpy as np

from eval.common import replica


def iter_frames(video: Path):
    cap = cv2.VideoCapture(str(video))
    while True:
        ok, f = cap.read()
        if not ok:
            return
        yield f


def _xyxy_to_xywh(xyxy: np.ndarray) -> np.ndarray:
    xyxy = np.asarray(xyxy, dtype=float).reshape(-1, 4)
    return np.stack([xyxy[:, 0], xyxy[:, 1], xyxy[:, 2] - xyxy[:, 0], xyxy[:, 3] - xyxy[:, 1]], axis=1)


def ultralytics_detect(model, frames, conf: float, iou: float, imgsz: int = 640, batch: int = 16):
    """Ultralytics PyTorch FP32, person only, its own letterbox (pad 114, bilinear)."""
    out, buf = [], []

    def flush():
        for res in model.predict(buf, imgsz=imgsz, conf=conf, iou=iou, classes=[0], max_det=300, verbose=False):
            b = res.boxes
            out.append((_xyxy_to_xywh(b.xyxy.cpu().numpy()), b.conf.cpu().numpy().astype(float)))
        buf.clear()

    for f in frames:
        buf.append(f)
        if len(buf) == batch:
            flush()
    if buf:
        flush()
    return out


def ort_detect(session, frames, interpolation: int, conf: float, iou: float):
    """The Python replica of the engine (see replica.py) on ONNX Runtime FP32."""
    out = []
    for f in frames:
        x, s, px, py = replica.letterbox(f, interpolation, 0)
        raw = session.run(None, {"images": x[None]})[0][0]
        out.append(replica.decode(raw, s, px, py, conf, iou))
    return out


def ort_detect_rect(session, frames, conf: float, iou: float):
    """Replica with Ultralytics' own preprocessing (rectangular letterbox, pad 114, bilinear) but ONNX Runtime and the
    engine's parser logic: it separates 'how the picture is prepared' from 'how the boxes are chosen'."""
    out = []
    for f in frames:
        x, s, px, py, nw, nh = replica.letterbox_rect(f)
        raw = session.run(None, {"images": x[None]})[0][0]
        out.append(replica.decode(raw, s, px, py, conf, iou, net_w=nw, net_h=nh))
    return out
