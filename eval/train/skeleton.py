"""Skeleton windows for the ST-GCN (V2-5): what goes into the network, in one place, written so that the C++ in the engine can do exactly the same.

A window is the last T frames of one track. Input tensor (3, T, 17): channel 0/1 = x, y of every keypoint relative to the window's origin
and divided by the window's scale, channel 2 = 1 if the keypoint is usable in that frame, else 0 (and x, y are 0 there).

    origin  hip centre (mean of the visible hips, else of the visible shoulders) in the FIRST frame of the window that has one. Fixed for the
            whole window, so the hips falling down during the window show up as motion (the per-frame normalisation of core/pose.c would erase it)
    scale   median over the window of the torso length (shoulder centre -> hip centre) of the frames that have both; none: 0.3 * the median
            larger side of the person's box; none of that: the window is rejected
    start   fewer than MIN_FRAMES frames with a skeleton: no window. Frames before the track began are missing frames (valid 0), so a person who
            falls soon after appearing is still scored
    gaps    a keypoint missing for at most MAX_GAP frames with a visible value on both sides is filled by a straight line; the rest stays 0, valid 0
    speed   a window of T * speed frames is subsampled to T (nearest frame) - training augmentation only; the deployed model uses speed 1

Coordinates are the pixels of the nvstreammux frame (y down), exactly what the dump of `ds_engine --dump-detections` and the engine's PoseHistory hold.
"""
from dataclasses import dataclass

import numpy as np

VIS_MIN = 0.5
MAX_GAP = 5
MIN_FRAMES = 16     # a window with fewer frames that have a skeleton is rejected (a track that was seen only a moment ago); the rest may be missing
L_SHOULDER, R_SHOULDER, L_HIP, R_HIP = 5, 6, 11, 12
# left/right swap for the horizontal flip augmentation (COCO order)
FLIP = [0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15]
EDGES = [(0, 1), (0, 2), (1, 3), (2, 4), (5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (5, 11), (6, 12), (11, 12), (11, 13), (13, 15), (12, 14), (14, 16)]


@dataclass
class Track:
    frames: np.ndarray        # (n,) frame numbers, increasing
    kp: np.ndarray            # (n, 17, 3) x, y, visibility
    size: np.ndarray          # (n,) larger side of the person's box


def load_tracks(dump_csv) -> dict:
    import pandas as pd
    df = pd.read_csv(dump_csv, dtype={"object_id": "Int64"})
    trk = df[(df.stage == "trk") & df.kpts.notna()]
    out = {}
    for oid, g in trk.groupby("object_id"):
        g = g.sort_values("frame_num").drop_duplicates("frame_num")
        kp = np.array([[float(v) for v in k.split()] for k in g.kpts], dtype=np.float64).reshape(-1, 17, 3)
        out[int(oid)] = Track(g.frame_num.to_numpy(dtype=np.int64), kp, np.maximum(g.width.to_numpy(float), g.height.to_numpy(float)))
    return out


def _centre(kp, idx):
    """mean of the visible keypoints among idx, or None"""
    pts = [kp[i, :2] for i in idx if kp[i, 2] >= VIS_MIN]
    return np.mean(pts, axis=0) if pts else None


def window(track: Track, end_frame: int, T: int, speed: float = 1.0):
    """(3, T, 17) float32, or None when the track has no skeleton in the last frame or the window cannot be scaled."""
    span = int(round(T * speed))
    start = end_frame - span + 1
    pos = np.searchsorted(track.frames, np.arange(start, end_frame + 1))
    have = (pos < len(track.frames)) & (track.frames[np.minimum(pos, len(track.frames) - 1)] == np.arange(start, end_frame + 1))
    if not have[-1]:
        return None
    idx = np.round(np.linspace(0, span - 1, T)).astype(int)            # subsample to T frames (identity for speed 1)
    kp = np.zeros((T, 17, 3))
    ok = np.zeros(T, bool)
    size = np.zeros(T)
    for t, i in enumerate(idx):
        if have[i]:
            kp[t] = track.kp[pos[i]]
            size[t] = track.size[pos[i]]
            ok[t] = True
    if int(ok.sum()) < min(MIN_FRAMES, T):
        return None
    seen = (kp[:, :, 2] >= VIS_MIN) & ok[:, None]
    origin = None
    for t in range(T):
        if ok[t]:
            origin = _centre(kp[t], (L_HIP, R_HIP))
            if origin is None:
                origin = _centre(kp[t], (L_SHOULDER, R_SHOULDER))
            if origin is not None:
                break
    if origin is None:
        return None
    torso = []
    for t in range(T):
        if ok[t]:
            s, h = _centre(kp[t], (L_SHOULDER, R_SHOULDER)), _centre(kp[t], (L_HIP, R_HIP))
            if s is not None and h is not None:
                torso.append(np.linalg.norm(s - h))
    scale = float(np.median(torso)) if torso else 0.3 * float(np.median(size[ok]))
    if not scale > 1e-3:
        return None
    xy = (kp[:, :, :2] - origin) / scale
    xy[~seen] = 0.0
    valid = seen.astype(np.float64)
    # repair short gaps, per keypoint
    for j in range(17):
        t = 0
        while t < T:
            if valid[t, j]:
                t += 1
                continue
            u = t
            while u < T and not valid[u, j]:
                u += 1
            if t > 0 and u < T and (u - t) <= MAX_GAP:
                for k in range(t, u):
                    a = (k - t + 1) / (u - t + 1)
                    xy[k, j] = (1 - a) * xy[t - 1, j] + a * xy[u, j]
                    valid[k, j] = 1.0
            t = u
    out = np.zeros((3, T, 17), np.float32)
    out[0], out[1], out[2] = xy[:, :, 0], xy[:, :, 1], valid
    return out
