"""Python replica of what the engine does around the YOLOv8n network, to compare pipelines on identical pixels.

preprocess  = nvinfer: scale to fit 640x640 keeping the aspect ratio, centred (symmetric padding), RGB, /255
postprocess = engine/parser/nvdsinfer_yolov8_parser.cpp + nvinfer clustering: person score >= threshold and person
              the best class of the anchor, then greedy NMS (IoU threshold), at most `topk` boxes.
The resize filter is a parameter, because which one nvinfer really uses is one of the things V2-1 measures.
"""
import cv2
import numpy as np

NET = 640
PAD_VALUE = 0   # nvinfer pads with zeros (black); Ultralytics pads with 114


def letterbox(img_bgr: np.ndarray, interpolation: int, pad_value: int = PAD_VALUE):
    """-> (net input 3x640x640 float32 RGB 0..1, scale, pad_x, pad_y)."""
    h, w = img_bgr.shape[:2]
    scale = min(NET / w, NET / h)
    nw, nh = round(w * scale), round(h * scale)
    resized = cv2.resize(img_bgr, (nw, nh), interpolation=interpolation)
    pad_x, pad_y = (NET - nw) // 2, (NET - nh) // 2
    canvas = np.full((NET, NET, 3), pad_value, np.uint8)
    canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
    rgb = canvas[:, :, ::-1]
    return np.ascontiguousarray(rgb.transpose(2, 0, 1), dtype=np.float32) / 255.0, scale, pad_x, pad_y


def letterbox_rect(img_bgr: np.ndarray, interpolation: int = cv2.INTER_LINEAR, pad_value: int = 114):
    """Ultralytics' predict-time letterbox: scale to fit 640, then pad only up to a multiple of 32 (a 16:9 frame
    becomes 640x384, not 640x640), pad value 114. -> (tensor, scale, pad_x, pad_y, net_w, net_h)."""
    h, w = img_bgr.shape[:2]
    scale = min(NET / w, NET / h)
    nw, nh = round(w * scale), round(h * scale)
    resized = cv2.resize(img_bgr, (nw, nh), interpolation=interpolation)
    net_w = NET - ((NET - nw) // 32) * 32
    net_h = NET - ((NET - nh) // 32) * 32
    pad_x, pad_y = (net_w - nw) // 2, (net_h - nh) // 2
    canvas = np.full((net_h, net_w, 3), pad_value, np.uint8)
    canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
    rgb = canvas[:, :, ::-1]
    return np.ascontiguousarray(rgb.transpose(2, 0, 1), dtype=np.float32) / 255.0, scale, pad_x, pad_y, net_w, net_h


def nms(boxes_xyxy: np.ndarray, scores: np.ndarray, iou_thr: float) -> list:
    order = np.argsort(-scores)
    keep = []
    while len(order):
        i = order[0]
        keep.append(int(i))
        if len(order) == 1:
            break
        rest = order[1:]
        lt = np.maximum(boxes_xyxy[i, :2], boxes_xyxy[rest, :2])
        rb = np.minimum(boxes_xyxy[i, 2:], boxes_xyxy[rest, 2:])
        inter = np.clip(rb - lt, 0, None).prod(axis=1)
        area_i = (boxes_xyxy[i, 2] - boxes_xyxy[i, 0]) * (boxes_xyxy[i, 3] - boxes_xyxy[i, 1])
        area_r = (boxes_xyxy[rest, 2] - boxes_xyxy[rest, 0]) * (boxes_xyxy[rest, 3] - boxes_xyxy[rest, 1])
        iou = inter / np.maximum(area_i + area_r - inter, 1e-12)
        order = rest[iou <= iou_thr]
    return keep


def decode(output0: np.ndarray, scale: float, pad_x: int, pad_y: int, threshold: float, nms_iou: float,
           topk: int = 300, person_must_be_best: bool = True, net_w: int = NET, net_h: int = NET):
    """output0: (84, anchors). Returns (boxes xywh in the ORIGINAL image pixels, scores)."""
    scores = output0[4]
    mask = scores >= threshold
    if person_must_be_best:
        mask &= scores >= output0[4:].max(axis=0)   # parser: skip when another class is strictly higher
    idx = np.nonzero(mask)[0]
    if len(idx) == 0:
        return np.zeros((0, 4)), np.zeros(0)
    cx, cy, w, h = (output0[k, idx] for k in range(4))
    x1 = np.clip(cx - w / 2, 0, net_w)
    y1 = np.clip(cy - h / 2, 0, net_h)
    x2 = np.clip(cx + w / 2, 0, net_w)
    y2 = np.clip(cy + h / 2, 0, net_h)
    ok = (x2 > x1) & (y2 > y1)
    boxes = np.stack([x1, y1, x2, y2], axis=1)[ok]
    sc = scores[idx][ok]
    keep = nms(boxes, sc, nms_iou)[:topk]
    boxes, sc = boxes[keep], sc[keep]
    # back to the original image
    boxes = (boxes - np.array([pad_x, pad_y, pad_x, pad_y])) / scale
    xywh = np.stack([boxes[:, 0], boxes[:, 1], boxes[:, 2] - boxes[:, 0], boxes[:, 3] - boxes[:, 1]], axis=1)
    return xywh, sc


def letterbox_net(img_bgr: np.ndarray, net_w: int, net_h: int, interpolation: int, pad_value: int = PAD_VALUE):
    """nvinfer's preprocessing for any network size (maintain-aspect-ratio=1, symmetric-padding=1).
    -> (input 3 x net_h x net_w float32 RGB 0..1, scale, pad_x, pad_y)."""
    h, w = img_bgr.shape[:2]
    scale = min(net_w / w, net_h / h)
    nw, nh = round(w * scale), round(h * scale)
    resized = cv2.resize(img_bgr, (nw, nh), interpolation=interpolation)
    pad_x, pad_y = (net_w - nw) // 2, (net_h - nh) // 2
    canvas = np.full((net_h, net_w, 3), pad_value, np.uint8)
    canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
    rgb = canvas[:, :, ::-1]
    return np.ascontiguousarray(rgb.transpose(2, 0, 1), dtype=np.float32) / 255.0, scale, pad_x, pad_y


def decode_pose(output0: np.ndarray, scale: float, pad_x: int, pad_y: int, net_w: int, net_h: int, threshold: float,
                nms_iou: float, max_people: int = 300):
    """output0: (56, anchors) of the YOLOv8-pose ONNX. Same logic as engine/parser/yolov8_pose_decode.h, but returns
    everything in pixels of the ORIGINAL image: (boxes xywh (n,4), scores (n,), keypoints (n,17,3) = x, y, visibility)."""
    scores = output0[4]
    idx = np.nonzero(scores >= threshold)[0]
    idx = idx[np.argsort(-scores[idx], kind="stable")]
    boxes, kept = [], []
    for i in idx:
        if len(kept) >= max_people:
            break
        cx, cy, w, h = (float(output0[k, i]) for k in range(4))
        x1, y1 = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
        x2, y2 = min(float(net_w), cx + w / 2), min(float(net_h), cy + h / 2)
        if x2 <= x1 or y2 <= y1:
            continue
        b = np.array([x1, y1, x2, y2])
        if boxes:
            bb = np.array(boxes)
            lt, rb = np.maximum(bb[:, :2], b[:2]), np.minimum(bb[:, 2:], b[2:])
            inter = np.clip(rb - lt, 0, None).prod(axis=1)
            union = (bb[:, 2] - bb[:, 0]) * (bb[:, 3] - bb[:, 1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
            if (inter / np.maximum(union, 1e-12) > nms_iou).any():
                continue
        boxes.append(b)
        kept.append(i)
    if not kept:
        return np.zeros((0, 4)), np.zeros(0), np.zeros((0, 17, 3))
    kept = np.array(kept)
    b = np.array(boxes)
    kp = output0[5:, kept].reshape(17, 3, -1).transpose(2, 0, 1).copy()      # (n, 17, 3)
    kp[:, :, 0] = (kp[:, :, 0] - pad_x) / scale
    kp[:, :, 1] = (kp[:, :, 1] - pad_y) / scale
    b = (b - np.array([pad_x, pad_y, pad_x, pad_y])) / scale
    xywh = np.stack([b[:, 0], b[:, 1], b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]], axis=1)
    return xywh, scores[kept], kp
