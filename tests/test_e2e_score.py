"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Hand-written cases for the V2-4 scoring (eval/e2e): what counts as a hit, a duplicate, a false alarm, an ambiguous event;
the Wilson interval; the leak check on splits. Expected values are worked out by hand from eval/DEFINITIONS.md sections 3-4,
not read back from the code.
"""
import pytest

from eval.e2e import labels, score
from eval.e2e.score import Pred


def clip(events, **kw):
    d = dict(clip="c.mp4", fps=30, width=640, height=480, scene="room", person="p", split="val", duration_s=60.0, events=events,
             has_incident=any(e["label"] == "positive" for e in events))
    d.update(kw)
    return labels.parse(d, "c")


def fall(start, impact, end, label="positive", case=""):
    return dict(type="fall", start_s=start, impact_s=impact, end_s=end, label=label, case=case)


def test_hit_window_is_start_minus_one_to_end_plus_ten():
    c = clip([fall(10, 11, 13)])
    for t, hit in [(8.9, False), (9.0, True), (13.0, True), (23.0, True), (23.1, False)]:
        r = score.match_clip(c, [Pred("fall", t, t + 1)])
        assert bool(r.tp) == hit, t
        assert bool(r.fp) == (not hit)


def test_wrong_type_does_not_match():
    r = score.match_clip(clip([fall(10, 11, 13)]), [Pred("fight", 11, 12)])
    assert not r.tp and len(r.fp) == 1 and len(r.fn) == 1


def test_second_event_on_the_same_label_is_a_duplicate_not_a_false_alarm():
    r = score.match_clip(clip([fall(10, 11, 13)]), [Pred("fall", 11, 12), Pred("fall", 12, 13)])
    assert len(r.tp) == 1 and len(r.duplicates) == 1 and not r.fp and not r.fn


def test_event_in_an_ambiguous_window_is_neither_hit_nor_false_alarm_and_does_not_hide_a_miss():
    c = clip([fall(10, 11, 13), fall(40, 40.4, 40.9, label="ambiguous")])
    r = score.match_clip(c, [Pred("fall", 40.5, 41.5)])
    assert not r.tp and not r.fp and len(r.ignored) == 1 and len(r.fn) == 1


def test_two_labels_need_two_events():
    c = clip([fall(10, 11, 12), fall(30, 31, 32)])
    r = score.match_clip(c, [Pred("fall", 31, 32)])
    assert len(r.tp) == 1 and len(r.fn) == 1 and r.fn[0].start_s == 10


def test_wilson_matches_known_values():
    lo, hi = score.wilson(5, 5)
    assert lo == pytest.approx(0.5655, abs=1e-3) and hi == pytest.approx(1.0, abs=1e-9)
    lo, hi = score.wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.2775, abs=1e-3)
    assert score.wilson(0, 0) == (0.0, 1.0)


def test_m2_counts_only_clips_without_an_incident_and_divides_by_hours():
    quiet = clip([], duration_s=1800.0, clip="q.mp4", tags=["sit_hard"])
    busy = clip([fall(10, 11, 13)], clip="b.mp4", duration_s=60.0)
    results = [score.match_clip(quiet, [Pred("fall", 100, 102), Pred("fall", 900, 902)]),
               score.match_clip(busy, [Pred("fall", 11, 12), Pred("fall", 50, 51)])]    # the 50 s one is a FP inside an incident clip
    t = score.summarise(results, "fall", [quiet, busy])
    assert t["m2"]["fp_raw"] == 2 and t["m2"]["camera_hours"] == pytest.approx(0.5)
    assert t["m2"]["fp_per_camera_hour"] == pytest.approx(4.0)
    assert t["fp_in_incident_clips"] == 1
    assert t["recall"] == 1.0 and t["precision"] == pytest.approx(1 / 4)
    assert t["by_case"]["sit_hard"]["fp"] == 2


def test_latency_is_report_time_minus_impact_not_minus_start():
    c = clip([fall(10, 12, 14)])
    t = score.summarise([score.match_clip(c, [Pred("fall", 12, 14.5)])], "fall", [c])
    assert t["rule_latency_s"]["median"] == pytest.approx(2.5)
    assert t["recall_within_5s"] == 1.0


def test_hard_case_tally_follows_the_label_case():
    a = clip([fall(10, 11, 13, case="occluded")], clip="a.mp4")
    b = clip([fall(10, 11, 13, case="occluded")], clip="b.mp4", person="q", scene="hall")
    t = score.summarise([score.match_clip(a, [Pred("fall", 11, 12)]), score.match_clip(b, [])], "fall", [a, b])
    assert t["by_case"]["occluded"] == {"tp": 1, "fn": 1, "fp": 0}
    assert t["people"] == 2 and t["scenes"] == 2


@pytest.mark.parametrize("patch,msg", [
    ({"split": "holdout"}, "split"),
    ({"events": [dict(type="fall", start_s=1, end_s=2, label="positive")], "has_incident": True}, "impact_s"),
    ({"events": [fall(1, 5, 2)], "has_incident": True}, "impact_s"),
    ({"events": [fall(1, 1.5, 2)], "has_incident": False}, "has_incident"),
    ({"events": [dict(type="slip", start_s=1, end_s=2, label="positive")]}, "type"),
    ({"events": [fall(1, 1.5, 99)], "has_incident": True}, "ends after"),
])
def test_bad_labels_are_rejected(patch, msg):
    with pytest.raises(labels.LabelError, match=msg):
        patch = dict(patch)
        clip(patch.pop("events", []), **patch)


def test_a_person_or_scene_in_two_splits_is_a_leak_but_dev_is_exempt():
    a = clip([], clip="a.mp4", split="train", person="ann", scene="room1")
    b = clip([], clip="b.mp4", split="test", person="ann", scene="room2")
    with pytest.raises(labels.LabelError, match="person 'ann'"):
        labels.check_split_leaks([a, b])
    c = clip([], clip="c.mp4", split="test", person="bob", scene="room1")
    with pytest.raises(labels.LabelError, match="scene 'room1'"):
        labels.check_split_leaks([a, c])
    d = clip([], clip="d.mp4", split="dev", person="ann", scene="room1")
    labels.check_split_leaks([a, d])
    e = clip([], clip="e.mp4", split="val", person="bob+cat", scene="room3")
    f = clip([], clip="f.mp4", split="test", person="cat", scene="room4")
    with pytest.raises(labels.LabelError, match="'cat'"):
        labels.check_split_leaks([e, f])


def test_shipped_labels_are_valid():
    assert labels.load_dir()      # the UR Fall dev labels in eval/labels/
