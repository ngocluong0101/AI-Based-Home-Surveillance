"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Behaviour labels (eval/DEFINITIONS.md section 2): one JSON per clip in eval/labels/. Loading validates; a bad label stops the run,
because a mistake here silently changes every number that comes after it.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

LABELS_DIR = Path(__file__).resolve().parents[1] / "labels"
SPLITS = ("train", "val", "test", "dev")      # dev = public clips the rules were tuned on (UR Fall); never reported as a result
EVENT_TYPES = ("fall", "fight")
EVENT_LABELS = ("positive", "ambiguous")


class LabelError(ValueError):
    pass


@dataclass
class LabelEvent:
    type: str
    start_s: float
    end_s: float
    label: str
    impact_s: float | None = None      # falls only: first frame the body touches the floor (M4 reference)
    case: str = ""                     # hard-case tag, e.g. "occluded", "fall_half"


@dataclass
class ClipLabel:
    clip: str
    fps: float
    width: int
    height: int
    scene: str
    split: str
    person: str                        # who is in it (split by person: one person never sits in two splits)
    has_incident: bool
    duration_s: float
    events: list[LabelEvent] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)          # hard-case tags of a clip without incident: "sit_hard", "lie_sofa", "pick_up", "hug"
    lie_zones: list = field(default_factory=list)          # polygons in clip pixels
    restricted_zones: list = field(default_factory=list)


def parse(d: dict, name: str = "?") -> ClipLabel:
    def need(k):
        if k not in d:
            raise LabelError(f"{name}: missing '{k}'")
        return d[k]

    split = need("split")
    if split not in SPLITS:
        raise LabelError(f"{name}: split '{split}' is not one of {SPLITS}")
    has_incident = bool(need("has_incident"))
    events = []
    for i, e in enumerate(need("events")):
        t = e.get("type")
        if t not in EVENT_TYPES:
            raise LabelError(f"{name}: event {i} has type '{t}'")
        if e.get("label") not in EVENT_LABELS:
            raise LabelError(f"{name}: event {i} has label '{e.get('label')}'")
        if not (0 <= e["start_s"] <= e["end_s"]):
            raise LabelError(f"{name}: event {i} has start_s/end_s out of order")
        impact = e.get("impact_s")
        if t == "fall" and e["label"] == "positive":
            if impact is None or not (e["start_s"] <= impact <= e["end_s"]):
                raise LabelError(f"{name}: positive fall {i} needs impact_s between start_s and end_s")
        events.append(LabelEvent(t, float(e["start_s"]), float(e["end_s"]), e["label"], None if impact is None else float(impact), e.get("case", "")))
    duration = float(need("duration_s"))
    if any(e.end_s > duration + 0.5 for e in events):
        raise LabelError(f"{name}: an event ends after the clip does ({duration:.1f}s)")
    positives = [e for e in events if e.label == "positive"]
    if has_incident != bool(positives):
        raise LabelError(f"{name}: has_incident={has_incident} but the clip has {len(positives)} positive events")
    return ClipLabel(need("clip"), float(need("fps")), int(need("width")), int(need("height")), need("scene"), split, need("person"),
                     has_incident, duration, events, list(d.get("tags", [])), d.get("lie_zones", []), d.get("restricted_zones", []))


def load_dir(directory: Path = LABELS_DIR, splits=None) -> list[ClipLabel]:
    clips = []
    for p in sorted(directory.glob("*.json")):
        c = parse(json.loads(p.read_text()), p.name)
        if c.clip != p.stem and Path(c.clip).stem != p.stem:
            raise LabelError(f"{p.name}: file name does not match clip '{c.clip}'")
        if splits is None or c.split in splits:
            clips.append(c)
    check_split_leaks(clips)
    return clips


def check_split_leaks(clips: list[ClipLabel]) -> None:
    """One person and one scene never appear in two of train / val / test (dev is outside this rule)."""
    for key in ("person", "scene"):
        seen: dict[str, str] = {}
        for c in clips:
            if c.split == "dev":
                continue
            for v in (c.person.split("+") if key == "person" else [c.scene]):
                if seen.setdefault(v, c.split) != c.split:
                    raise LabelError(f"{key} '{v}' is in both '{seen[v]}' and '{c.split}' (leak)")
