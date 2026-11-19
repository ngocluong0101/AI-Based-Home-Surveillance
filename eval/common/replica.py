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
