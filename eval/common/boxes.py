"""Small box helpers shared by the evaluation scripts. Boxes are numpy arrays of [left, top, width, height]."""
import numpy as np


def xywh_to_xyxy(b: np.ndarray) -> np.ndarray:
    b = np.asarray(b, dtype=np.float64).reshape(-1, 4)
    return np.stack([b[:, 0], b[:, 1], b[:, 0] + b[:, 2], b[:, 1] + b[:, 3]], axis=1)


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU of every box of a (N,4 xywh) with every box of b (M,4 xywh) -> (N, M)."""
    a = xywh_to_xyxy(a)
    b = xywh_to_xyxy(b)
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.clip(rb - lt, 0, None).prod(axis=2)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / np.maximum(union, 1e-12), 0.0)


def greedy_match(boxes_a: np.ndarray, conf_a: np.ndarray, boxes_b: np.ndarray, conf_b: np.ndarray,
                 iou_min: float = 0.5):
    """Pair boxes of A with boxes of B, most confident first, each box used once, IoU >= iou_min.
    Returns (pairs, only_a, only_b): pairs = list of (i, j); only_* = index lists."""
    iou = iou_matrix(boxes_a, boxes_b)
    used_b = set()
    pairs = []
    only_a = []
    for i in np.argsort(-np.asarray(conf_a)):
        if iou.shape[1] == 0:
            only_a.append(int(i))
            continue
        order = np.argsort(-iou[i])
        hit = next((int(j) for j in order if iou[i, j] >= iou_min and int(j) not in used_b), None)
        if hit is None:
            only_a.append(int(i))
        else:
            used_b.add(hit)
            pairs.append((int(i), hit))
    only_b = [j for j in range(len(boxes_b)) if j not in used_b]
    return pairs, only_a, only_b
