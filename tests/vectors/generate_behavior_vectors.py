"""Generate tests/vectors/behavior_*.json from BehaviorEngine (src/events/behavior.py).

The Python engine is the specification. Each scenario below only describes what the cameras see: a skeleton and a box per
person per frame (made by the small body model at the top of this file, a rigid stick figure that stands, falls, lies, waves
and punches). The events the engine raises are recorded by running it. The C port (engine/src/core/behavior.c) must give the
same events for the same files (`make test-core`), and so must the engine itself (tests/test_behavior_vectors.py), plus
hand-written assertions for the scenarios in tests/test_behavior.py, so a vector can never silently disagree with the rules.

Regenerate after a deliberate change of the rules:
    PYTHONPATH=. python tests/vectors/generate_behavior_vectors.py
and review the diff of the JSON files.

File format:
    name, description
    frame                [width, height] in pixels
    config               fields of BehaviorConfig that differ from the defaults
    zones[]              {kind, zone_id, points: [[x, y], ...]}, relative to the frame
    steps[]              processed in order, on one engine
      now_ns             clock, integer nanoseconds
      people[]           {track_id, box: [x, y, w, h], keypoints: [[x, y, visibility] x 17] or null}
      expected_events[]  {type, track_ids, zone_id, occurred_ns, score}, each emitted at now_ns
      tracks_after       number of tracks the engine still remembers
Coordinates are rounded to 0.001 px before the engine sees them, so both languages read exactly the same numbers.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict
from pathlib import Path

from src.events import BehaviorConfig, BehaviorEngine, Person, Zone

OUT_DIR = Path(__file__).resolve().parent
S = 1_000_000_000
DT = 0.1                      # 10 frames per second keeps the files small
W, H = 1920, 1080
GROUND = 900.0                # y of the floor where people stand
L = 100.0                     # torso length in pixels (a person is about 3.6 torso lengths tall)


# ---------------------------------------------------------------- a rigid stick figure
def skeleton(hip, angle_deg, wrists=None, length=L):
    """17 keypoints (x, y, visibility). The body is rotated about the hip: angle_deg is the angle of the torso from the
    vertical (0 upright, 90 lying, positive = leaning to the person's right). wrists = {L_WRIST/R_WRIST index: (x, y)} puts
    a hand somewhere else than hanging down."""
    a = math.radians(angle_deg)
    u = (math.sin(a), -math.cos(a))          # from the hips to the shoulders
    n = (math.cos(a), math.sin(a))           # to the person's right

    def at(base, du=0.0, dn=0.0):
        return (base[0] + u[0] * du * length + n[0] * dn * length, base[1] + u[1] * du * length + n[1] * dn * length)

    s = at(hip, 1.0)
    kp = [None] * 17
    kp[0] = at(s, 0.45)
    kp[1], kp[2] = at(s, 0.5, -0.08), at(s, 0.5, 0.08)
    kp[3], kp[4] = at(s, 0.45, -0.15), at(s, 0.45, 0.15)
    kp[5], kp[6] = at(s, 0, -0.25), at(s, 0, 0.25)
    kp[7], kp[8] = at(s, -0.5, -0.3), at(s, -0.5, 0.3)
    kp[9], kp[10] = at(s, -1.0, -0.3), at(s, -1.0, 0.3)
    kp[11], kp[12] = at(hip, 0, -0.15), at(hip, 0, 0.15)
    kp[13], kp[14] = at(hip, -0.6, -0.15), at(hip, -0.6, 0.15)
    kp[15], kp[16] = at(hip, -1.2, -0.15), at(hip, -1.2, 0.15)
    for k, xy in (wrists or {}).items():
        kp[k] = xy
    return [(round(x, 3), round(y, 3), 0.9) for x, y in kp]


def box_of(kp, margin=15.0):
    xs = [p[0] for p in kp]
    ys = [p[1] for p in kp]
    return (round(min(xs) - margin, 3), round(min(ys) - margin, 3), round(max(xs) - min(xs) + 2 * margin, 3),
            round(max(ys) - min(ys) + 2 * margin, 3))


def person(tid, kp):
    return {"track_id": tid, "box": list(box_of(kp)), "keypoints": [list(p) for p in kp]}


def standing(x, tid=1, length=L):
    return person(tid, skeleton((x, GROUND - 1.2 * length), 0.0, length=length))


def smooth(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def falling(x, t, t0, duration, tid=1, to_deg=90.0, length=L):
    """Standing until t0, then rotating to `to_deg` over `duration` seconds while the hips come down, then lying."""
    k = smooth((t - t0) / duration)
    hip = (x + 0.9 * length * k * math.sin(math.radians(to_deg)), GROUND - 1.2 * length * (1 - k) - 0.15 * length * k)
    return person(tid, skeleton(hip, to_deg * k, length=length))


def seq(t_end, make, t_start=0.0):
    n = int(round((t_end - t_start) / DT))
    return [(round(t_start + i * DT, 6), make(round(t_start + i * DT, 6))) for i in range(n + 1)]


# ---------------------------------------------------------------- scenarios: (description, config, zones, frames)
def lie_zone():
    return {"kind": "lie_ok", "zone_id": 0, "points": [[0.0, 0.5], [1.0, 0.5], [1.0, 1.0], [0.0, 1.0]]}


def scenarios():
    out = {}

    out["fall_basic"] = ("Upright for 2 s, falls over in 0.6 s, lies still for 5 s: one FALL, emitted 2 s after the impact.",
                         {"fall": True}, [], seq(7.6, lambda t: [falling(800, t, 2.0, 0.6)]))

    # sitting: the hips come down by a torso length, the torso stays upright
    def sit(t):
        k = smooth((t - 2.0) / 0.8)
        return [person(1, skeleton((800, GROUND - 1.2 * L + 0.9 * L * k), 0.0))]
    out["fall_sit_down"] = ("Sits down quickly (torso stays upright): no fall.", {"fall": True}, [], seq(8.0, sit))

    # bending over: the torso goes to 80 degrees but the hips do not move
    def bend(t):
        k = smooth((t - 2.0) / 0.6)
        return [person(1, skeleton((800, GROUND - 1.2 * L), 80.0 * k))]
    out["fall_bend_over"] = ("Bends over to 80 degrees for 5 s, hips stay: no fall.", {"fall": True}, [], seq(8.0, bend))

    out["fall_slow_lie_down"] = ("Lies down over 4 s (longer than the 1.5 s window): no fall.", {"fall": True}, [],
                                 seq(11.0, lambda t: [falling(800, t, 2.0, 4.0)]))

    def get_up(t):
        if t < 4.0:
            return [falling(800, t, 2.0, 0.6)]
        k = smooth((t - 4.0) / 0.6)                     # up again after 1 s on the floor
        return [person(1, skeleton((800 + 90 * (1 - k), GROUND - 1.2 * L * k - 0.15 * L * (1 - k)), 90.0 * (1 - k)))]
    out["fall_get_up_quickly"] = ("Falls and is back on the feet after 1 s: no event.", {"fall": True}, [], seq(9.0, get_up))

    out["fall_on_the_sofa_zone"] = ("Falls onto a lie_ok zone (sofa): no event.", {"fall": True}, [lie_zone()],
                                    seq(8.0, lambda t: [falling(800, t, 2.0, 0.6)]))

    out["fall_lying_20s_one_event"] = ("Lies still for 20 s after the fall: exactly one event, no repeat.", {"fall": True}, [],
                                       seq(24.0, lambda t: [falling(800, t, 2.0, 0.6)]))

    def lost_pose(t):
        p = falling(800, t, 2.0, 0.6)
        if round(t * 10) % 3 == 0 and t > 2.0:          # every third frame the model gives no pose
            p = {"track_id": 1, "box": p["box"], "keypoints": None}
        return [p]
    out["fall_pose_missing_some_frames"] = ("Falls; in every third frame the pose model gave no skeleton: still one FALL.",
                                            {"fall": True}, [], seq(7.6, lost_pose))

    def two(t):
        return [falling(500, t, 2.0, 0.6, tid=1), standing(1300 + 20 * t, tid=2)]
    out["fall_two_people_one_falls"] = ("One person falls, another walks by: one FALL for track 1 only.", {"fall": True}, [],
                                        seq(7.6, two))

    def reappear(t):
        if 2.0 <= t < 8.0:
            return []                                    # out of sight for 6 s, longer than forget_s
        return [falling(800, t, 0.0, 0.6)] if t < 2.0 else [standing(800)]
    out["fall_track_forgotten_after_absence"] = ("Falls, leaves the picture for 6 s, comes back upright: nothing is claimed.",
                                                 {"fall": True}, [], seq(10.0, reappear))

    # what real clips taught (eval/work/urfall): the hips keep falling after the torso has turned; the pose model often loses a
    # person who lies on the floor; a person who rolls about after the fall is not "still"
    def hips_lag(t):
        k_angle, k_hip = smooth((t - 2.0) / 0.3), smooth((t - 2.0) / 0.8)
        hip = (800 + 90 * k_hip, GROUND - 1.2 * L * (1 - k_hip) - 0.15 * L * k_hip)
        return [person(1, skeleton(hip, 90.0 * k_angle))]
    out["fall_hips_keep_falling_after_the_torso_turned"] = (
        "The torso turns in 0.3 s but the hips take 0.8 s to reach the floor (as in a real clip): one FALL.", {"fall": True}, [],
        seq(7.0, hips_lag))

    def pose_lost_on_the_floor(t):
        p = falling(800, t, 2.0, 0.6)
        if t >= 3.2:                                     # from half a second after the impact the model sees no skeleton
            p = {"track_id": 1, "box": p["box"], "keypoints": None}
        return [p]
    out["fall_pose_lost_while_lying_box_stays_wide"] = (
        "After the fall the pose model gives no skeleton at all but the box stays wide: still one FALL (last hips + wide box).",
        {"fall": True}, [], seq(7.6, pose_lost_on_the_floor))

    def pose_lost_box_tall(t):
        p = falling(800, t, 2.0, 0.6)
        if t >= 3.2:
            p = {"track_id": 1, "box": [780.0, 450.0, 120.0, 450.0], "keypoints": None}   # a tall box: the person got up
        return [p]
    out["fall_pose_lost_and_box_tall_claims_nothing"] = (
        "After the fall there is no skeleton and the box is tall (the person is up): nothing is claimed.", {"fall": True}, [],
        seq(7.6, pose_lost_box_tall))

    def rolls(t):
        k = smooth((t - 2.0) / 0.6)
        extra = max(0.0, t - 3.2) * 250.0                 # keeps crawling about after the hips have settled
        hip = (800 + 90 * k + extra, GROUND - 1.2 * L * (1 - k) - 0.15 * L * k)
        return [person(1, skeleton(hip, 90.0 * k))]
    out["fall_crawling_about_is_not_still"] = ("After the fall the person drags themself across the floor: not still, no event.",
                                               {"fall": True}, [], seq(7.6, rolls))

    out["fall_disabled_by_config"] = ("A fall with the fall rule off: no event.", {}, [], seq(7.6, lambda t: [falling(800, t, 2.0, 0.6)]))

    # fights: A is at x=600, B at x=700 (their boxes overlap); A's right hand shoots into B's box every 0.4 s
    def punches(t, speed_hand=True):
        a_hip, b_hip = (600, GROUND - 1.2 * L), (700, GROUND - 1.2 * L)
        phase = (t % 0.4)
        reach = 0.0
        if speed_hand and 1.0 <= t < 3.5:
            reach = 1.0 if phase < 0.2 else 0.0         # alternately in B's chest and back at A's side
        wrist = (a_hip[0] + 25 + reach * 90, a_hip[1] - 80)       # 90 px forward = inside B's box
        a = person(1, skeleton(a_hip, 0.0, wrists={10: wrist}))
        b = person(2, skeleton(b_hip, 0.0))
        return [a, b]
    out["fight_punches"] = ("A punches into B's body every 0.4 s for 2.5 s: one FIGHT for the pair.", {"fight": True}, [],
                            seq(6.0, punches))

    def hug(t):
        a_hip, b_hip = (600, GROUND - 1.2 * L), (700, GROUND - 1.2 * L)
        k = smooth((t - 1.0) / 2.0)                     # the hand travels 90 px in 2 s: far under the speed limit
        a = person(1, skeleton(a_hip, 0.0, wrists={10: (a_hip[0] + 25 + 90 * k, a_hip[1] - 80)}))
        return [a, person(2, skeleton(b_hip, 0.0))]
    out["fight_hug_slow_hand"] = ("A hand slowly goes into the other person (a hug): no event.", {"fight": True}, [], seq(6.0, hug))

    out["fight_walk_side_by_side"] = ("Two people walk side by side, hands at the sides: no event.", {"fight": True}, [],
                                      seq(6.0, lambda t: [standing(500 + 30 * t, 1), standing(620 + 30 * t, 2)]))

    out["fight_second_burst_after_cooldown"] = ("Two bursts of punches 20 s apart (cooldown 10 s): two FIGHT events.",
                                                {"fight": True}, [], seq(26.0, lambda t: punches(t if t < 6.0 else (t - 20.0 if t >= 20.0 else -1.0))))

    out["fight_disabled_by_config"] = ("Punches with the fight rule off: no event.", {}, [], seq(6.0, punches))

    # restricted zone: the right half of the picture
    zone = {"kind": "restricted", "zone_id": 3, "points": [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]]}

    def at_x(t, enter=2.0, leave=None):
        x = 1400 if t >= enter and (leave is None or t < leave) else 400
        return [standing(x)]
    out["zone_dwell"] = ("Walks into the restricted zone and stays 5 s: one RESTRICTED_AREA (zone 3) after 3 s, occurred at the entry.",
                         {"zones": True}, [zone], seq(8.0, at_x))
    out["zone_pass_through"] = ("Inside the restricted zone for 1.5 s only: no event.", {"zones": True}, [zone],
                                seq(6.0, lambda t: at_x(t, 2.0, 3.5)))
    out["zone_rearm_after_leaving"] = ("In, 4 s, out 12 s, in again 4 s: two events.", {"zones": True}, [zone],
                                       seq(26.0, lambda t: [standing(1400 if (2.0 <= t < 6.0 or 18.0 <= t < 22.0) else 400)]))
    out["zone_without_pose_uses_the_box"] = ("A person without a skeleton (detection-only model) stands in the zone: the box decides.",
                                             {"zones": True}, [zone],
                                             seq(8.0, lambda t: [{"track_id": 1, "box": [1300.0, 540.0, 150.0, 360.0], "keypoints": None}]))
    ignore = {"kind": "ignore", "zone_id": 9, "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]}
    out["ignore_zone_hides_everything"] = ("A fall inside an ignore zone (a TV): no event.", {"fall": True}, [ignore],
                                           seq(7.6, lambda t: [falling(800, t, 2.0, 0.6)]))
    # ---- the skeleton network (V2-5): scores are (track, probability, end of its window) that arrive with the frame
    def net_frames(prob_of, t_end=9.0, tid=1, fall_at=2.0):
        """a person falling at fall_at, and one score per frame (the network scores every frame): prob_of(t) -> probability or None"""
        frames = []
        for t, people in seq(t_end, lambda t: [falling(800, t, fall_at, 0.6, tid=tid)]):
            pr = prob_of(t)
            frames.append((t, people, [] if pr is None else [[tid, round(pr, 4), int(round(t * S))]]))
        return frames

    out["net_fall_reported_when_the_scores_arrive_not_after_the_person_stays_down"] = (
        "The network sees the fall: scores 0.95 from t = 2.4 s. fall_net = 1, k = 3: one FALL on the third score, 0.2 s after the first; occurred = the first window's end.",
        {"fall": True, "fall_net": 1}, [], net_frames(lambda t: 0.95 if t >= 2.4 else 0.05))
    out["net_either_reports_once"] = (
        "fall_net = 2: the network reports at ~2.5 s; the rules would report at ~4 s but the cooldown they share stops the second event.",
        {"fall": True, "fall_net": 2}, [], net_frames(lambda t: 0.95 if t >= 2.4 else 0.05))
    out["net_either_rules_still_work_when_the_network_is_silent"] = (
        "fall_net = 2 and no scores at all (the model is down): the rules report the fall as before.",
        {"fall": True, "fall_net": 2}, [], net_frames(lambda t: None))
    out["net_only_with_no_scores_reports_nothing"] = (
        "fall_net = 1 and no scores: the rules are off, so nothing is reported (the engine falls back to fall_net = 0 itself when the model cannot load).",
        {"fall": True, "fall_net": 1}, [], net_frames(lambda t: None))
    out["net_run_must_be_unbroken"] = (
        "Scores 0.95 0.95 0.2 0.95 0.95 (k = 3): the dip resets the count, no event.",
        {"fall": True, "fall_net": 1}, [],
        net_frames(lambda t: {2.4: 0.95, 2.4333: 0.95, 2.4667: 0.2, 2.5: 0.95, 2.5333: 0.95}.get(round(t, 4)), t_end=4.0))
    out["net_threshold_is_inclusive_and_configurable"] = (
        "fall_net_thr = 0.7, k = 2: scores of exactly 0.7 count.", {"fall": True, "fall_net": 1, "fall_net_thr": 0.7, "fall_net_k": 2}, [],
        net_frames(lambda t: 0.7 if t >= 2.4 else 0.0, t_end=4.0))
    out["net_in_a_lie_ok_zone_is_not_a_fall"] = (
        "The network fires while the hips are in a sofa zone: nothing is reported.", {"fall": True, "fall_net": 1}, [lie_zone()],
        net_frames(lambda t: 0.95 if t >= 2.4 else 0.05))
    out["net_cooldown_then_second_fall"] = (
        "Scores stay high for 3 s (one event), a second burst inside the 10 s cooldown is ignored, a third one after it is reported.",
        {"fall": True, "fall_net": 1},
        [], net_frames(lambda t: 0.95 if (2.4 <= t < 5.4 or 8.0 <= t < 9.0 or 14.0 <= t < 16.0) else 0.0, t_end=17.0))
    out["net_scores_of_another_track_are_not_ours"] = (
        "Scores addressed to track 7 while only track 1 is in view: nothing.", {"fall": True, "fall_net": 1}, [],
        [(t, people, [[7, 0.99, int(round(t * S))]]) for t, people, _ in net_frames(lambda t: 0.0, t_end=4.0)])
    return out


# ---------------------------------------------------------------- recording
def to_person(d):
    return Person(d["track_id"], tuple(d["box"]), None if d["keypoints"] is None else [tuple(p) for p in d["keypoints"]])


def record(name, description, overrides, zones, frames):
    cfg = BehaviorConfig(**overrides)
    engine = BehaviorEngine(cfg, [Zone(z["kind"], [tuple(p) for p in z["points"]], z["zone_id"]) for z in zones], W, H)
    steps = []
    for t, people, *rest in frames:
        now = int(round(t * S))
        scores = rest[0] if rest else []
        events = engine.process([to_person(d) for d in people], now, [tuple(x) for x in scores])
        steps.append({"now_ns": now, "people": people, **({"scores": scores} if scores else {}),
                      "expected_events": [{"type": e.type, "track_ids": list(e.track_ids), "zone_id": e.zone_id,
                                           "occurred_ns": e.occurred_ns, "score": round(e.score, 6)} for e in events],
                      "tracks_after": engine.track_count()})
    return {"name": name, "description": description, "frame": [W, H], "config": overrides, "zones": zones, "steps": steps}


def main() -> None:
    for old in OUT_DIR.glob("behavior_*.json"):
        old.unlink()
    for name, (description, overrides, zones, frames) in scenarios().items():
        doc = record(name, description, overrides, zones, frames)
        (OUT_DIR / f"behavior_{name}.json").write_text(json.dumps(doc, separators=(",", ":")) + "\n")
        n_events = sum(len(s["expected_events"]) for s in doc["steps"])
        print(f"{name:42s} {len(doc['steps']):4d} steps, {n_events} events")


if __name__ == "__main__":
    main()
