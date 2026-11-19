"""Read the CSV written by `ds_engine --dump-detections` (engine/src/dump.c)."""
from pathlib import Path

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
