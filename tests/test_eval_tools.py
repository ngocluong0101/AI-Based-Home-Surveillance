"""Tests of the evaluation helpers in eval/ (V2-1). They need no GPU, no dataset and no network; the MOT metric
test needs the `motmetrics` package, which lives in eval/.venv, so it is skipped elsewhere."""
import numpy as np
import pytest

from eval.common import boxes as B
from eval.common import replica


def test_iou_known_values():
    a = np.array([[0, 0, 10, 10]], float)
    b = np.array([[0, 0, 10, 10], [5, 0, 10, 10], [20, 20, 5, 5]], float)
    iou = B.iou_matrix(a, b)[0]
    assert iou[0] == pytest.approx(1.0)
    assert iou[1] == pytest.approx(50 / 150)        # overlap 5x10 = 50, union 100 + 100 - 50
    assert iou[2] == 0.0


def test_greedy_match_pairs_each_box_once_and_most_confident_first():
    det = np.array([[0, 0, 10, 10], [1, 0, 10, 10]], float)   # two boxes on the same object
    gt = np.array([[0, 0, 10, 10]], float)
    pairs, only_det, only_gt = B.greedy_match(det, np.array([0.9, 0.8]), gt, np.ones(1), 0.5)
    assert pairs == [(0, 0)] and only_det == [1] and only_gt == []


def test_greedy_match_empty_sides():
    pairs, oa, ob = B.greedy_match(np.zeros((0, 4)), np.zeros(0), np.array([[0, 0, 5, 5]], float), np.ones(1))
    assert pairs == [] and oa == [] and ob == [0]


def test_letterbox_square_is_centred_with_black_padding():
    img = np.full((1080, 1920, 3), 200, np.uint8)
    x, scale, pad_x, pad_y = replica.letterbox(img, replica.cv2.INTER_NEAREST)
    assert x.shape == (3, 640, 640) and scale == pytest.approx(1 / 3)
    assert (pad_x, pad_y) == (0, 140)
    assert x[:, 0, 0].max() == 0.0                  # padding is zero in nvinfer
    assert x[:, 320, 320] == pytest.approx(200 / 255)


@pytest.mark.parametrize("h,w,expected", [(1080, 1920, (384, 640)), (480, 640, (480, 640)), (720, 1280, (384, 640))])
def test_letterbox_rect_matches_ultralytics_shapes(h, w, expected):
    _, _, _, _, net_w, net_h = replica.letterbox_rect(np.zeros((h, w, 3), np.uint8))
    assert (net_h, net_w) == expected


def test_decode_applies_person_must_be_best_class_nms_and_unletterbox():
    out = np.zeros((84, 8400), np.float32)
    # anchor 0: person 0.9 at the centre of a 100x200 box; anchor 1: same object, 0.8 (removed by NMS)
    for i, (sc, dx) in enumerate([(0.9, 0), (0.8, 2)]):
        out[0:4, i] = [320 + dx, 320, 100, 200]
        out[4, i] = sc
    out[0:4, 2] = [100, 100, 40, 40]; out[4, 2] = 0.7; out[5, 2] = 0.9   # class 1 is better: not a person
    out[0:4, 3] = [500, 500, 40, 40]; out[4, 3] = 0.1                    # under the threshold
    boxes, scores = replica.decode(out, scale=0.5, pad_x=0, pad_y=0, threshold=0.25, nms_iou=0.45)
    assert len(boxes) == 1 and scores[0] == pytest.approx(0.9)
    # network box (270,220)-(370,420) -> original pixels (scale 0.5): left 540, top 440, width 200, height 400
    assert boxes[0] == pytest.approx([540, 440, 200, 400])


def test_decode_topk_and_empty():
    out = np.zeros((84, 8400), np.float32)
    boxes, scores = replica.decode(out, 1.0, 0, 0, 0.25, 0.45)
    assert boxes.shape == (0, 4) and scores.shape == (0,)


def test_mot_metrics_known_answer():
    mm = pytest.importorskip("motmetrics")
    if not hasattr(np, "asfarray"):
        np.asfarray = lambda a, dtype=float: np.asarray(a, dtype=dtype)   # same shim as eval/tracking/mot_eval.py
    acc = mm.MOTAccumulator(auto_id=True)
    box = lambda x: [x, 0, 10, 10]   # noqa: E731
    for f in range(10):
        hyp = [1, 2] if f < 5 else [1, 3]           # object 2 changes its ID at frame 5: exactly one ID switch
        d = mm.distances.iou_matrix(np.array([box(0), box(50)], float), np.array([box(0), box(50)], float), max_iou=0.5)
        acc.update([1, 2], hyp, d)
    r = mm.metrics.create().compute(acc, metrics=["mota", "idf1", "num_switches"], name="t").iloc[0]
    assert r.num_switches == 1
    assert r.mota == pytest.approx(0.95)            # 1 - 1/20
    assert r.idf1 == pytest.approx(0.75)            # 15 correctly identified boxes of 20 + 20


def _pose_tensor(anchors, person):
    """A (56, anchors) tensor holding one person: cx, cy, w, h, score and 17 keypoints given as (x, y, v)."""
    out = np.zeros((56, anchors), np.float32)
    cx, cy, w, h, score, kpts = person
    out[0:5, 0] = [cx, cy, w, h, score]
    for k, (x, y, v) in enumerate(kpts):
        out[5 + 3 * k:8 + 3 * k, 0] = [x, y, v]
    return out


def test_decode_pose_gives_original_pixels_and_matches_the_cpp_parser_logic():
    # the same numbers as engine/parser/tests/test_pose_decode.cpp, plus a letterbox of scale 0.5 and a 12 px bar
    kpts = [(320, 92, 0.95)] + [(320, 192, 0.9)] * 15 + [(370, 292, 0.5)]
    out = _pose_tensor(8, (320, 192, 100, 200, 0.9, kpts))
    out[0:5, 1] = [322, 192, 100, 200, 0.8]            # a duplicate of the same person: removed by NMS
    boxes, scores, kp = replica.decode_pose(out, scale=0.5, pad_x=0, pad_y=12, net_w=640, net_h=384, threshold=0.25, nms_iou=0.45)
    assert len(boxes) == 1 and scores[0] == pytest.approx(0.9)
    assert boxes[0] == pytest.approx([540, 160, 200, 400])            # (270, 92-12)/0.5 ... width 100/0.5
    assert kp.shape == (1, 17, 3)
    assert kp[0, 0] == pytest.approx([640, 160, 0.95])                # the nose: ((320-0)/0.5, (92-12)/0.5)
    assert kp[0, 16, 2] == pytest.approx(0.5)


def test_decode_pose_empty_and_threshold():
    out = np.zeros((56, 4), np.float32)
    boxes, scores, kp = replica.decode_pose(out, 1.0, 0, 0, 640, 384, 0.25, 0.45)
    assert boxes.shape == (0, 4) and scores.shape == (0,) and kp.shape == (0, 17, 3)


def test_keypoints_of_reads_the_dump_column(tmp_path):
    pytest.importorskip("pandas")   # lives in eval/.venv
    from eval.common import ds_dump

    kp = " ".join(f"{v}" for k in range(17) for v in (100 + k, 200 + k, 0.9))
    csv = tmp_path / "d.csv"
    csv.write_text("stage,source_id,frame_num,pts_ns,object_id,class_id,left,top,width,height,confidence,tracker_confidence,kpts\n"
                   "fdet,0,0,0,,,,,,,,,\n"
                   f"det,0,0,0,,0,10,20,30,40,0.9,0,{kp}\n"
                   "det,0,0,0,,0,50,60,30,40,0.8,0,\n")                 # an object without keypoints is skipped
    boxes, conf, kpts = ds_dump.keypoints_of(ds_dump.load_dump(csv)["det"], 0)
    assert boxes.shape == (1, 4) and conf[0] == pytest.approx(0.9) and kpts.shape == (1, 17, 3)
    assert kpts[0, 3] == pytest.approx([103, 203, 0.9])
