"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Read the CSV written by `ds_engine --dump-detections` (engine/src/dump.c).
"""
from pathlib import Path

import numpy as np
import pandas as pd


def load_dump(path: Path) -> dict:
    """Returns {"det": DataFrame, "trk": DataFrame, "frames": sorted list of frame numbers seen by nvinfer}."""
    df = pd.read_csv(path, dtype={"object_id": "Int64"})
    det = df[df.stage == "det"].reset_index(drop=True)
    trk = df[df.stage == "trk"].reset_index(drop=True)
    frames = sorted(df[df.stage == "fdet"].frame_num.unique().tolist())
    return {"det": det, "trk": trk, "frames": frames}


def boxes_of(df: pd.DataFrame, frame: int):
    """(boxes Nx4 xywh, confidences N) of one frame of a det/trk table."""
    rows = df[df.frame_num == frame]
    return rows[["left", "top", "width", "height"]].to_numpy(dtype=float), rows.confidence.to_numpy(dtype=float)


def keypoints_of(df: pd.DataFrame, frame: int):
    """(boxes xywh (n,4), confidences (n,), keypoints (n,17,3)) of one frame of a det/trk table written by the pose model.
    Keypoints are x, y (pixels of the nvstreammux frame), visibility."""
    rows = df[(df.frame_num == frame) & df.kpts.notna()]
    boxes = rows[["left", "top", "width", "height"]].to_numpy(dtype=float)
    kp = np.array([[float(v) for v in k.split()] for k in rows.kpts], dtype=float).reshape(-1, 17, 3)
    return boxes, rows.confidence.to_numpy(dtype=float), kp
