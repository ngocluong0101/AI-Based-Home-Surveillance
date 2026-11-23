"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Behaviour rules (V2-3): falls, fights and people in restricted areas, from the skeletons and boxes of tracked people.

This module is the SPECIFICATION; engine/src/core/behavior.c is its C port and both run the same vectors
(tests/vectors/behavior_*.json). No I/O and no clock: the caller passes `now_ns`. One BehaviorEngine per camera.
Every threshold lives in BehaviorConfig and can be changed per camera.

FALL   An upright person becomes "down" (torso >= fall_down_deg from vertical) within fall_drop_window_s, the hips drop
       fast, and the person then stays down and still for fall_still_s. The event is emitted after that stillness;
       occurred_ns is the first frame the person was down (M4 latency is measured from it). Not a fall: sitting, bending
       over, lying down slowly, standing back up quickly, someone already lying when first seen, hips in a "lie_ok" zone
       (bed, sofa). One event per fall until the person stands up and fall_cooldown_s passes.
FIGHT  A hand or foot moving fast (>= fight_speed torso lengths/s) inside another person's box counts as contact. Contact
       time fills a leaky bucket per pair; reaching fight_threshold_s raises one event, then fight_cooldown_s. Hugs and
       handshakes are slow, so they are no contact.
RESTRICTED_AREA   The feet stay inside a "restricted" zone for zone_dwell_s (occurred_ns = moment of entry). Leaving for
       zone_exit_s restarts the count; a new event needs zone_rearm_s outside.
Zones are polygons in relative coordinates (0..1); someone standing in an "ignore" zone (TV, mirror) is skipped by all rules.
Skeletons use the COCO order of YOLOv8-pose, x and y in pixels (y down).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

# COCO keypoint indices
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST = 5, 6, 7, 8, 9, 10
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE = 11, 12, 13, 14, 15, 16
HITTERS = (L_WRIST, R_WRIST, L_ANKLE, R_ANKLE)   # what can hit

S = 1_000_000_000
MAX_PEOPLE_FOR_PAIRS = 16   # a frame with more people than this still gets every other rule, but pairs are checked for the first 16

FALL, FIGHT, RESTRICTED_AREA = "FALL", "FIGHT", "RESTRICTED_AREA"


@dataclass
class BehaviorConfig:
    fall: bool = False
    fight: bool = False
    zones: bool = False            # restricted-area events (the lie_ok and ignore zones act whenever they exist)
    vis_min: float = 0.5           # a keypoint with a lower visibility is not seen
    forget_s: float = 5.0          # a track or a pair not seen for this long is forgotten
    # fall
    fall_down_deg: float = 60.0
    fall_up_deg: float = 40.0
    fall_drop_window_s: float = 1.5
    fall_hip_drop_min: float = 0.5
    fall_still_s: float = 2.0
    fall_settle_s: float = 1.0     # how long after the impact the hips may still be falling / settling
    fall_hip_window_s: float = 0.8   # the hips must have come down by fall_hip_drop_min this soon after the person was last upright
                                     # (a fall is fast; lying down on purpose is not)
    fall_move_max: float = 1.0
    fall_lying_aspect: float = 0.9   # box width / height at or above which a person without a skeleton counts as lying
    fall_stand_back_s: float = 0.5
    fall_lost_s: float = 1.0
    fall_cooldown_s: float = 10.0
    fall_min_torso_px: float = 20.0
    # fall, skeleton network (V2-5): a classifier scores the last second or so of a person's skeleton; the rules above need the person to stay
    # down, the network reacts to the fall itself. 0 = rules only, 1 = the network decides (the rules are off), 2 = either of them
    fall_net: int = 0
    fall_net_thr: float = 0.9      # a score at or above this counts
    fall_net_k: int = 3            # this many scores in a row make a fall (the cooldown is fall_cooldown_s, shared with the rules)
    # fight
    fight_speed: float = 3.0
    fight_decay_s: float = 2.0
    fight_threshold_s: float = 0.4
    fight_rearm: float = 0.1
    fight_cooldown_s: float = 10.0
    fight_max_dt_s: float = 0.5
    # zones
    zone_dwell_s: float = 3.0
    zone_exit_s: float = 1.0
    zone_rearm_s: float = 10.0


@dataclass
class Zone:
    kind: str                       # "restricted" | "lie_ok" | "ignore"
    points: Sequence[tuple]         # polygon, (x, y) relative to the frame
    zone_id: int = 0                # reported in events of a restricted zone


@dataclass
class Person:
    track_id: int
    box: tuple                      # x, y, w, h in pixels
    keypoints: Optional[Sequence[tuple]] = None   # 17 x (x, y, visibility), pixels; None = the model gave no pose


@dataclass
class BehaviorEvent:
    type: str
    track_ids: tuple
    score: float                    # 0..1
    zone_id: int                    # -1 when the event has no zone
    occurred_ns: int                # when the behaviour began (fall: the impact)
    now_ns: int                     # when it was emitted


def point_in_polygon(px: float, py: float, poly: Sequence[tuple]) -> bool:
    """Ray casting; a point exactly on an edge may count either way (a real foot never is)."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _centre(kp, ids, vis_min):
    pts = [kp[i] for i in ids if kp[i][2] >= vis_min]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _torso(kp, vis_min):
    """(angle from the vertical in degrees, torso length, shoulder centre, hip centre), or None when shoulders or hips are
    not both seen. 0 = upright, 90 = horizontal, above 90 = head lower than the hips."""
    s = _centre(kp, (L_SHOULDER, R_SHOULDER), vis_min)
    h = _centre(kp, (L_HIP, R_HIP), vis_min)
    if s is None or h is None:
        return None
    length = math.hypot(s[0] - h[0], s[1] - h[1])
    if length <= 0.0:
        return None
    angle = math.degrees(math.atan2(abs(s[0] - h[0]), h[1] - s[1]))
    return angle, length, s, h


@dataclass
class _FallState:
    mode: str = "unknown"           # unknown | upright | down | lying | fired
    last_ns: int = 0                # last frame in which the person was measured
    t_up: int = 0
    sy_ref: float = 0.0
    hx_ref: float = 0.0
    hy_ref: float = 0.0
    torso_ref: float = 1.0
    t_down: int = 0
    hx_down: float = 0.0          # where the hips settled (they keep following until fall_settle_s after the impact)
    hy_down: float = 0.0
    max_moved: float = 0.0        # how far the hips went from where they were upright, in torso lengths
    t_moved: Optional[int] = None   # when they first went fall_hip_drop_min
    last_hx: float = 0.0          # the last hips seen (used while the pose is missing)
    last_hy: float = 0.0
    max_angle: float = 0.0
    up_since: Optional[int] = None
    cooldown_until: int = 0


@dataclass
class _NetState:
    run: int = 0                    # scores in a row at or above the threshold
    first_ns: int = 0               # the end of the window of the first of them


@dataclass
class _ZoneState:
    inside_since: Optional[int] = None
    out_since: Optional[int] = None
    fired: bool = False


@dataclass
class _Track:
    last_seen: int = 0
    fall: _FallState = field(default_factory=_FallState)
    net: _NetState = field(default_factory=_NetState)
    zones: dict = field(default_factory=dict)         # zone_id -> _ZoneState
    prev_kp: Optional[list] = None
    prev_ns: int = 0
    speeds: Optional[list] = None                     # speed of the 4 hitters in this frame, torso lengths per second


@dataclass
class _Pair:
    last_ns: int = 0
    last_seen: int = 0
    bucket: float = 0.0
    since_ns: int = 0
    fired: bool = False
    cooldown_until: int = 0


class BehaviorEngine:
    def __init__(self, config: BehaviorConfig, zones: Sequence[Zone] = (), frame_w: float = 1920.0, frame_h: float = 1080.0):
        self.cfg = config
        self.zones = list(zones)
        self.w = float(frame_w)
        self.h = float(frame_h)
        self.tracks: dict[int, _Track] = {}
        self.pairs: dict[tuple, _Pair] = {}

    # ---- helpers ----
    def _in_zone(self, zone: Zone, x: float, y: float) -> bool:
        return point_in_polygon(x, y, [(px * self.w, py * self.h) for px, py in zone.points])

    def _in_any(self, kind: str, x: float, y: float) -> bool:
        return any(z.kind == kind and self._in_zone(z, x, y) for z in self.zones)

    @staticmethod
    def _foot(p: Person, vis_min: float):
        if p.keypoints is not None:
            c = _centre(p.keypoints, (L_ANKLE, R_ANKLE), vis_min)
            if c is not None:
                return c
        return (p.box[0] + p.box[2] / 2.0, p.box[1] + p.box[3])

    def process(self, people: Sequence[Person], now_ns: int, scores: Sequence[tuple] = ()) -> list[BehaviorEvent]:
        """`scores`: (track_id, probability of a fall, end_ns of the window it was computed on) that arrived since the last call, oldest first."""
        cfg = self.cfg
        events: list[BehaviorEvent] = []

        visible = []
        for p in people:
            fx, fy = self._foot(p, cfg.vis_min)
            if self._in_any("ignore", fx, fy):
                continue
            visible.append((p, fx, fy))

        for p, fx, fy in visible:
            tr = self.tracks.get(p.track_id)
            if tr is None:
                tr = self.tracks[p.track_id] = _Track()
            tr.last_seen = now_ns
            if cfg.zones:
                self._zone_rule(tr, p, fx, fy, now_ns, events)
            if cfg.fall:
                if cfg.fall_net != 1:
                    self._fall_rule(tr, p, now_ns, events)
                if cfg.fall_net != 0:
                    self._net_rule(tr, p, [sc for sc in scores if sc[0] == p.track_id], now_ns, events)
            self._speeds(tr, p, now_ns)

        if cfg.fight:
            self._fight_rule(visible, now_ns, events)

        self._forget(now_ns)
        self._fall_lost(now_ns)
        return events

    # ---- restricted areas ----
    def _zone_rule(self, tr: _Track, p: Person, fx: float, fy: float, now: int, events: list) -> None:
        cfg = self.cfg
        for z in self.zones:
            if z.kind != "restricted":
                continue
            st = tr.zones.setdefault(z.zone_id, _ZoneState())
            if self._in_zone(z, fx, fy):
                st.out_since = None
                if st.inside_since is None:
                    st.inside_since = now
                if not st.fired and now - st.inside_since >= int(cfg.zone_dwell_s * S):
                    st.fired = True
                    events.append(BehaviorEvent(RESTRICTED_AREA, (p.track_id,), 1.0, z.zone_id, st.inside_since, now))
            else:
                if st.inside_since is None:
                    continue
                if st.out_since is None:
                    st.out_since = now
                out = now - st.out_since
                if out >= int(cfg.zone_exit_s * S) and not st.fired:
                    st.inside_since = None
                    st.out_since = None
                elif st.fired and out >= int(cfg.zone_rearm_s * S):
                    st.inside_since = None
                    st.out_since = None
                    st.fired = False

    # ---- falls ----
    def _fall_rule(self, tr: _Track, p: Person, now: int, events: list) -> None:
        cfg = self.cfg
        st = tr.fall
        m = _torso(p.keypoints, cfg.vis_min) if p.keypoints is not None else None
        if m is not None and m[1] < cfg.fall_min_torso_px:
            return
        if m is None:
            # No usable skeleton this frame. A person who is down stays down while the box is wide; the last hips stand in.
            if st.mode == "down" and p.box[2] >= cfg.fall_lying_aspect * p.box[3]:
                st.last_ns = now
                self._fall_down_step(st, p, now, st.last_hx, st.last_hy, st.max_angle, False, events)
            return
        angle, length, s, h = m
        st.last_ns = now
        st.last_hx, st.last_hy = h
        up = angle < cfg.fall_up_deg
        stand_back = int(cfg.fall_stand_back_s * S)

        def set_refs():
            st.t_up, st.sy_ref, st.hx_ref, st.hy_ref, st.torso_ref = now, s[1], h[0], h[1], length

        if st.mode == "unknown":
            if up:
                st.mode = "upright"
                set_refs()
            elif angle >= cfg.fall_down_deg:
                st.mode = "lying"          # first seen lying: not a fall
        elif st.mode == "upright":
            if up:
                set_refs()
            elif angle >= cfg.fall_down_deg:
                if now - st.t_up <= int(cfg.fall_drop_window_s * S):
                    st.mode = "down"
                    st.t_down, st.hx_down, st.hy_down = now, h[0], h[1]
                    st.max_angle = angle
                    st.max_moved = math.hypot(h[0] - st.hx_ref, h[1] - st.hy_ref) / st.torso_ref
                    st.t_moved = now if st.max_moved >= cfg.fall_hip_drop_min else None
                    st.up_since = None
                else:
                    st.mode = "lying"
                    st.up_since = None
        elif st.mode in ("lying", "fired"):
            if up:
                if st.up_since is None:
                    st.up_since = now
                if now - st.up_since >= stand_back and now >= st.cooldown_until:
                    st.mode = "upright"
                    st.up_since = None
                    set_refs()
            else:
                st.up_since = None
        elif st.mode == "down":
            st.max_angle = max(st.max_angle, angle)
            if up:
                if st.up_since is None:
                    st.up_since = now
                if now - st.up_since >= stand_back:
                    st.mode = "upright"
                    st.up_since = None
                    set_refs()
                return
            st.up_since = None
            self._fall_down_step(st, p, now, h[0], h[1], st.max_angle, True, events)

    def _net_rule(self, tr: _Track, p: Person, scores: list, now: int, events: list) -> None:
        """The skeleton network says fall_net_k scores in a row at or above fall_net_thr: that is a fall, reported now, with the end of the first window
        as the moment it began. Shares the cooldown with the rules; a person whose hips are in a lie_ok zone (bed, sofa) is never reported."""
        cfg = self.cfg
        st, net = tr.fall, tr.net
        for _, prob, end_ns in scores:
            if prob < cfg.fall_net_thr:
                net.run = 0
                continue
            net.run += 1
            if net.run == 1:
                net.first_ns = end_ns
            if net.run < cfg.fall_net_k or now < st.cooldown_until:
                continue
            m = _torso(p.keypoints, cfg.vis_min) if p.keypoints is not None else None
            hx, hy = m[3] if m is not None else (p.box[0] + p.box[2] / 2.0, p.box[1] + p.box[3] / 2.0)
            net.run = 0
            if self._in_any("lie_ok", hx, hy):
                continue
            events.append(BehaviorEvent(FALL, (p.track_id,), min(1.0, max(0.0, prob)), -1, net.first_ns, now))
            st.mode = "fired"
            st.cooldown_until = now + int(cfg.fall_cooldown_s * S)

    def _fall_down_step(self, st: _FallState, p: Person, now: int, hx: float, hy: float, max_angle: float, measured: bool,
                        events: list) -> None:
        """The person is down: follow the hips while they settle, and report the fall once they have been still long enough."""
        cfg = self.cfg
        since = now - st.t_down
        if since < int(cfg.fall_settle_s * S):
            if measured:
                st.hx_down, st.hy_down = hx, hy
                moved = math.hypot(hx - st.hx_ref, hy - st.hy_ref) / st.torso_ref
                st.max_moved = max(st.max_moved, moved)
                if st.t_moved is None and moved >= cfg.fall_hip_drop_min:
                    st.t_moved = now
            return
        if since < int(cfg.fall_still_s * S):
            return
        still = math.hypot(hx - st.hx_down, hy - st.hy_down) / st.torso_ref <= cfg.fall_move_max
        came_down = st.t_moved is not None and st.t_moved - st.t_up <= int(cfg.fall_hip_window_s * S)
        if not came_down or not still:
            st.mode = "lying"          # the hips never came down (bending over), came down slowly (lying down), or the person is still moving about
        elif self._in_any("lie_ok", hx, hy):
            st.mode = "fired"          # a bed or a sofa: lying there is not a fall, and nothing to repeat
            st.cooldown_until = now
        else:
            score = 0.5 + 0.5 * min(1.0, max(0.0, (max_angle - cfg.fall_down_deg) / (90.0 - cfg.fall_down_deg)))
            events.append(BehaviorEvent(FALL, (p.track_id,), score, -1, st.t_down, now))
            st.mode = "fired"
            st.cooldown_until = now + int(cfg.fall_cooldown_s * S)

    def _fall_lost(self, now: int) -> None:
        lost = int(self.cfg.fall_lost_s * S)
        for tr in self.tracks.values():
            st = tr.fall
            if st.mode == "down" and now - st.last_ns > lost:
                st.mode = "unknown"        # out of sight (or no skeleton and no wide box) while down: nothing is claimed

    # ---- fights ----
    def _speeds(self, tr: _Track, p: Person, now: int) -> None:
        tr.speeds = None
        kp = p.keypoints
        if kp is None:
            tr.prev_kp = None
            return
        m = _torso(kp, self.cfg.vis_min)
        if tr.prev_kp is not None and m is not None:
            dt = (now - tr.prev_ns) / S
            if 0.0 < dt <= self.cfg.fight_max_dt_s:
                out = []
                for k in HITTERS:
                    a, b = kp[k], tr.prev_kp[k]
                    if a[2] >= self.cfg.vis_min and b[2] >= self.cfg.vis_min:
                        out.append(math.hypot(a[0] - b[0], a[1] - b[1]) / (m[1] * dt))
                    else:
                        out.append(None)
                tr.speeds = out
        tr.prev_kp = [tuple(v) for v in kp]
        tr.prev_ns = now

    def _contact(self, a: Person, ta: _Track, b: Person) -> bool:
        if ta.speeds is None or a.keypoints is None:
            return False
        bx, by, bw, bh = b.box
        for k, v in zip(HITTERS, ta.speeds):
            if v is None or v < self.cfg.fight_speed:
                continue
            x, y, _ = a.keypoints[k]
            if bx < x < bx + bw and by < y < by + bh:
                return True
        return False

    def _fight_rule(self, visible, now: int, events: list) -> None:
        cfg = self.cfg
        people = [(p, self.tracks[p.track_id]) for p, _, _ in visible if p.keypoints is not None][:MAX_PEOPLE_FOR_PAIRS]
        for i in range(len(people)):
            for j in range(i + 1, len(people)):
                (a, ta), (b, tb) = people[i], people[j]
                key = (min(a.track_id, b.track_id), max(a.track_id, b.track_id))
                pr = self.pairs.get(key)
                if pr is None:
                    pr = self.pairs[key] = _Pair(last_ns=now, last_seen=now)
                dt = (now - pr.last_ns) / S
                contact = self._contact(a, ta, b) or self._contact(b, tb, a)
                if dt > 0.0:
                    pr.bucket = pr.bucket * math.exp(-dt / cfg.fight_decay_s)
                    if contact:
                        if pr.bucket < cfg.fight_rearm:
                            pr.since_ns = now - int(min(dt, cfg.fight_max_dt_s) * S)
                        pr.bucket += min(dt, cfg.fight_max_dt_s)
                pr.last_ns = now
                pr.last_seen = now
                if not pr.fired and pr.bucket >= cfg.fight_threshold_s:
                    score = 0.5 + 0.5 * min(1.0, pr.bucket / (2.0 * cfg.fight_threshold_s))
                    events.append(BehaviorEvent(FIGHT, (a.track_id, b.track_id), min(1.0, score), -1, pr.since_ns, now))
                    pr.fired = True
                    pr.cooldown_until = now + int(cfg.fight_cooldown_s * S)
                elif pr.fired and pr.bucket < cfg.fight_rearm and now >= pr.cooldown_until:
                    pr.fired = False

    # ---- housekeeping ----
    def _forget(self, now: int) -> None:
        limit = int(self.cfg.forget_s * S)
        for tid in [t for t, tr in self.tracks.items() if now - tr.last_seen > limit]:
            del self.tracks[tid]
        for key in [k for k, pr in self.pairs.items() if now - pr.last_seen > limit]:
            del self.pairs[key]

    def track_count(self) -> int:
        return len(self.tracks)
