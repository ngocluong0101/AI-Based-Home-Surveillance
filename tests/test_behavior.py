"""Hand-written checks of the behaviour rules (src/events/behavior.py).

The vectors (test_behavior_vectors.py) are generated from the engine, so they prove that C and Python agree, not that the
rules are right. These tests state the expected behaviour in words, on the same scenarios, and do not depend on the
generated numbers except through the timing the definitions of eval/DEFINITIONS.md call for.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from src.events import BehaviorConfig, BehaviorEngine, Person, Zone
from src.events.behavior import point_in_polygon

S = 1_000_000_000
VECTOR_DIR = Path(__file__).resolve().parent / "vectors"


def run(name):
    """All events of a vector scenario, with the time they were emitted."""
    v = json.loads((VECTOR_DIR / f"behavior_{name}.json").read_text())
    return [(s["now_ns"], e) for s in v["steps"] for e in s["expected_events"]]


def test_fall_is_reported_two_seconds_after_the_impact_with_the_impact_as_its_time():
    (now, e), = run("fall_basic")
    assert e["type"] == "FALL" and e["track_ids"] == [1] and e["zone_id"] == -1
    assert 2.0 * S < e["occurred_ns"] < 3.0 * S                        # the fall starts at 2.0 s and takes 0.6 s
    assert now - e["occurred_ns"] == pytest.approx(2.0 * S, abs=0.11 * S)   # fall_still_s
    assert 0.5 <= e["score"] <= 1.0


def test_what_real_clips_taught():
    """The hips fall after the torso has turned; the pose model loses a person on the floor but the box says lying."""
    assert [e["type"] for _, e in run("fall_hips_keep_falling_after_the_torso_turned")] == ["FALL"]
    assert [e["type"] for _, e in run("fall_pose_lost_while_lying_box_stays_wide")] == ["FALL"]
    assert run("fall_pose_lost_and_box_tall_claims_nothing") == []
    assert run("fall_crawling_about_is_not_still") == []


@pytest.mark.parametrize("name", ["fall_sit_down", "fall_bend_over", "fall_slow_lie_down", "fall_get_up_quickly",
                                  "fall_on_the_sofa_zone", "fall_track_forgotten_after_absence", "fall_disabled_by_config",
                                  "ignore_zone_hides_everything"])
def test_things_that_are_not_falls_raise_nothing(name):
    assert run(name) == []


def test_a_person_lying_for_a_long_time_raises_one_event_not_many():
    assert len(run("fall_lying_20s_one_event")) == 1


def test_a_fall_is_found_when_some_frames_have_no_pose_and_blames_the_right_track():
    assert [e["track_ids"] for _, e in run("fall_pose_missing_some_frames")] == [[1]]
    assert [e["track_ids"] for _, e in run("fall_two_people_one_falls")] == [[1]]


def test_punches_make_one_fight_event_for_the_pair_and_a_second_one_after_the_cooldown():
    (_, e), = run("fight_punches")
    assert e["type"] == "FIGHT" and sorted(e["track_ids"]) == [1, 2]
    two = run("fight_second_burst_after_cooldown")
    assert len(two) == 2 and two[1][0] - two[0][0] > 10 * S


@pytest.mark.parametrize("name", ["fight_hug_slow_hand", "fight_walk_side_by_side", "fight_disabled_by_config"])
def test_hugs_walking_and_a_disabled_rule_are_not_fights(name):
    assert run(name) == []


def test_restricted_area_event_carries_the_zone_and_the_moment_of_entry():
    (now, e), = run("zone_dwell")
    assert e["type"] == "RESTRICTED_AREA" and e["zone_id"] == 3 and e["track_ids"] == [1]
    assert e["occurred_ns"] == pytest.approx(2.0 * S, abs=0.11 * S)
    assert now - e["occurred_ns"] == pytest.approx(3.0 * S, abs=0.11 * S)    # zone_dwell_s


def test_zone_pass_through_rearm_and_boxes_without_pose():
    assert run("zone_pass_through") == []
    assert len(run("zone_rearm_after_leaving")) == 2
    assert len(run("zone_without_pose_uses_the_box")) == 1


def test_point_in_polygon():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert point_in_polygon(5, 5, square) and not point_in_polygon(15, 5, square) and not point_in_polygon(5, -1, square)
    concave = [(0, 0), (10, 0), (10, 10), (5, 4), (0, 10)]            # a notch at the bottom
    assert point_in_polygon(2, 2, concave) and not point_in_polygon(5, 8, concave)
    assert not point_in_polygon(1, 1, [(0, 0), (1, 1)])                # fewer than three points: nothing is inside


def test_a_clock_that_goes_backwards_does_not_crash_or_fire():
    engine = BehaviorEngine(BehaviorConfig(fall=True, fight=True, zones=True))
    p = Person(1, (0, 0, 10, 10), None)
    engine.process([p], 10 * S)
    assert engine.process([p], 5 * S) == []


def test_tracks_are_forgotten():
    engine = BehaviorEngine(BehaviorConfig(zones=True))
    engine.process([Person(1, (0, 0, 10, 10), None)], 0)
    assert engine.track_count() == 1
    engine.process([], 6 * S)
    assert engine.track_count() == 0
