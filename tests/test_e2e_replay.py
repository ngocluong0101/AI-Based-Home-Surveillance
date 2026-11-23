"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

The replay (eval/e2e/replay.py) turns a dump of the engine into events on the clip's timeline. Built from a hand-made dump of
a person who stands, drops to the floor in 0.2 s and stays there: nothing is read back from the engine.
"""
import pytest

pd = pytest.importorskip("pandas")

from eval.e2e import labels, replay, score  # noqa: E402

FPS = 30


def skeleton(lying: float) -> str:
    """lying 0 = upright, 1 = on the floor. 17 COCO keypoints as 'x y visibility' (pixels, y grows downwards)."""
    def pt(up, flat):
        return (up[0] + (flat[0] - up[0]) * lying, up[1] + (flat[1] - up[1]) * lying)
    kp = {i: pt((900, 250), (600, 900)) for i in range(5)}                    # head
    for i in (5, 6):
        kp[i] = pt((900, 300), (700, 900))                                     # shoulders
    for i in (7, 8, 9, 10):
        kp[i] = pt((900, 450), (800, 900))                                     # arms
    for i in (11, 12):
        kp[i] = pt((900, 550), (900, 900))                                     # hips
    for i in (13, 14):
        kp[i] = pt((900, 700), (1000, 900))                                    # knees
    for i in (15, 16):
        kp[i] = pt((900, 850), (1100, 900))                                    # ankles
    return " ".join(f"{kp[i][0]:.1f} {kp[i][1]:.1f} 0.95" for i in range(17))


def make_dump(path, standing_s, fall_s, lying_s):
    rows = []
    n = int((standing_s + fall_s + lying_s) * FPS)
    for f in range(n):
        t = f / FPS
        lying = min(1.0, max(0.0, (t - standing_s) / fall_s))
        pts = int(t * 1e9)
        rows.append(dict(stage="fdet", source_id=0, frame_num=f, pts_ns=pts))
        box = (600 if lying else 800, 250 if not lying else 700, 400 if lying else 200, 650 if not lying else 250)
        rows.append(dict(stage="trk", source_id=0, frame_num=f, pts_ns=pts, object_id=1, class_id=0,
                         left=box[0], top=box[1], width=box[2], height=box[3], confidence=0.9, tracker_confidence=0.9, kpts=skeleton(lying)))
    pd.DataFrame(rows).to_csv(path, index=False)
    return n / FPS


def label(duration, impact=None):
    ev = [] if impact is None else [dict(type="fall", start_s=impact - 0.3, impact_s=impact, end_s=duration, label="positive")]
    return labels.parse(dict(clip="x.mp4", fps=FPS, width=1920, height=1080, scene="s", person="p", split="val", duration_s=duration,
                             has_incident=bool(ev), events=ev), "x")


def test_a_fall_in_a_dump_comes_out_on_the_clip_timeline_and_matches_its_label(tmp_path):
    dur = make_dump(tmp_path / "x.csv", 3.0, 0.2, 5.0)
    preds = replay.replay(tmp_path / "x.csv", label(dur, impact=3.2))
    assert len(preds) == 1 and preds[0].type == "fall"
    assert 3.0 <= preds[0].t_pred <= 3.5                      # the moment of the drop, not the moment of reporting
    assert preds[0].t_reported - preds[0].t_pred >= 1.5       # the rule waits for the person to stay down
    res = score.match_clip(label(dur, impact=3.2), preds)
    assert len(res.tp) == 1 and not res.fp and not res.fn


def test_a_person_who_only_stands_raises_nothing(tmp_path):
    dur = make_dump(tmp_path / "x.csv", 8.0, 0.2, 0.0)
    assert replay.replay(tmp_path / "x.csv", label(dur)) == []


def test_a_threshold_override_changes_what_the_rule_does(tmp_path):
    dur = make_dump(tmp_path / "x.csv", 3.0, 0.2, 2.5)        # stays down only 2.5 s: reported at the default 2 s + settle, not at 5 s
    assert len(replay.replay(tmp_path / "x.csv", label(dur, impact=3.2))) == 1
    assert replay.replay(tmp_path / "x.csv", label(dur, impact=3.2), {"fall_still_s": 5.0}) == []
    with pytest.raises(KeyError):
        replay.replay(tmp_path / "x.csv", label(dur), {"no_such_field": 1})
