"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Match predicted events to labels and compute M1, M2 (and the rule-side part of M4). Definitions: eval/DEFINITIONS.md sections 3-4.

Pure functions, no I/O. A predicted event is (type, t_pred_s, t_reported_s): t_pred is when the behaviour began by the system's
reckoning (a fall: the impact), t_reported when the event came out. Times are seconds on the clip's own timeline.
"""
import math
from dataclasses import dataclass, field

from .labels import ClipLabel, LabelEvent

EARLY_S = 1.0       # an event may come this much before the label starts
LATE_S = 10.0       # ... and this much after it ends (a late alert is still a detection; latency is M4's job)


@dataclass
class Pred:
    type: str            # "fall" | "fight"
    t_pred: float
    t_reported: float


@dataclass
class ClipResult:
    clip: str
    hours: float
    tp: list = field(default_factory=list)            # (label, pred)
    fn: list = field(default_factory=list)            # labels
    fp: list = field(default_factory=list)            # preds
    duplicates: list = field(default_factory=list)    # preds matching an already-matched label
    ignored: list = field(default_factory=list)       # preds inside an ambiguous window
    tags: list = field(default_factory=list)
    has_incident: bool = False


def _inside(e: LabelEvent, p: Pred) -> bool:
    return e.type == p.type and e.start_s - EARLY_S <= p.t_pred <= e.end_s + LATE_S


def match_clip(label: ClipLabel, preds: list[Pred]) -> ClipResult:
    res = ClipResult(label.clip, label.duration_s / 3600.0, tags=list(label.tags), has_incident=label.has_incident)
    positives = [e for e in label.events if e.label == "positive"]
    ambiguous = [e for e in label.events if e.label == "ambiguous"]
    taken: dict[int, Pred] = {}
    for p in sorted(preds, key=lambda x: x.t_pred):
        hit = next((i for i, e in enumerate(positives) if _inside(e, p)), None)
        if hit is not None:
            if hit in taken:
                res.duplicates.append(p)
            else:
                taken[hit] = p
        elif any(_inside(e, p) for e in ambiguous):
            res.ignored.append(p)
        else:
            res.fp.append(p)
    for i, e in enumerate(positives):
        if i in taken:
            res.tp.append((e, taken[i]))
        else:
            res.fn.append(e)
    return res


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for k successes of n. (0, 1) when n = 0."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def _quantile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * q
    lo = int(math.floor(k))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarise(results: list[ClipResult], kind: str, labels: list[ClipLabel]) -> dict:
    """The table for one behaviour type. `results` are the per-clip matches of the clips being reported."""
    by_clip = {c.clip: c for c in labels}
    tp = [(e, p, r.clip) for r in results for e, p in r.tp if e.type == kind]
    fn = [(e, r.clip) for r in results for e in r.fn if e.type == kind]
    fp = [(p, r) for r in results for p in r.fp if p.type == kind]
    dup = sum(1 for r in results for p in r.duplicates if p.type == kind)
    n_pos = len(tp) + len(fn)
    lo, hi = wilson(len(tp), n_pos)
    quiet = [r for r in results if not r.has_incident]
    quiet_hours = sum(r.hours for r in quiet)
    fp_quiet = sum(1 for p, r in fp if not r.has_incident)
    fp_all = len(fp)
    precision = len(tp) / (len(tp) + fp_all) if tp or fp_all else None
    recall = len(tp) / n_pos if n_pos else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else (0.0 if precision is not None and recall is not None else None)

    out = {
        "type": kind,
        "events": n_pos, "people": len({by_clip[r.clip].person for r in results for e, _ in r.tp if e.type == kind} |
                                       {by_clip[c].person for e, c in fn}), "scenes": len({by_clip[c].scene for _, _, c in tp} | {by_clip[c].scene for _, c in fn}),
        "tp": len(tp), "fn": len(fn), "fp": fp_all, "duplicates": dup,
        "recall": recall, "recall_ci95": [lo, hi] if n_pos else None, "precision": precision, "f1": f1,
        "fp_in_incident_clips": fp_all - fp_quiet,
        "m2": {"fp_raw": fp_quiet, "camera_hours": quiet_hours, "clips": len(quiet),
               "scenes": len({by_clip[r.clip].scene for r in quiet}),
               "fp_per_camera_hour": (fp_quiet / quiet_hours) if quiet_hours > 0 else None},
    }
    # recall with a latency bound, and the rule-side delay (impact -> report). The M4 of the whole chain is measured separately.
    if kind == "fall" and tp:
        lat = [p.t_reported - (e.impact_s if e.impact_s is not None else e.start_s) for e, p, _ in tp]
        out["rule_latency_s"] = {"median": _quantile(lat, 0.5), "p95": _quantile(lat, 0.95), "max": max(lat)}
        for bound in (5.0, 10.0):
            out[f"recall_within_{int(bound)}s"] = sum(1 for x in lat if x <= bound) / n_pos
    # hard cases: positives by the event's `case`, false alarms by the tags of the clip they happened in
    cases: dict[str, dict] = {}
    for e, _, _ in tp:
        if e.type == kind:
            cases.setdefault(e.case or "(none)", {"tp": 0, "fn": 0, "fp": 0})["tp"] += 1
    for e, _ in fn:
        cases.setdefault(e.case or "(none)", {"tp": 0, "fn": 0, "fp": 0})["fn"] += 1
    for p, r in fp:
        for t in r.tags or ["(none)"]:
            cases.setdefault(t, {"tp": 0, "fn": 0, "fp": 0})["fp"] += 1
    out["by_case"] = cases
    return out
