"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Replay the shared behaviour vectors (tests/vectors/behavior_*.json) on the Python specification; behavior.c replays the same files
in `make test-core`. A failure here means the rules changed without regenerating the vectors
(tests/vectors/generate_behavior_vectors.py).
"""
import json
from pathlib import Path

import pytest

from src.events import BehaviorConfig, BehaviorEngine, Person, Zone

VECTOR_DIR = Path(__file__).resolve().parent / "vectors"
VECTOR_FILES = sorted(VECTOR_DIR.glob("behavior_*.json"))


def test_vectors_exist():
    assert len(VECTOR_FILES) >= 15, f"expected at least 15 behavior_*.json in {VECTOR_DIR}"


@pytest.mark.parametrize("path", VECTOR_FILES, ids=lambda p: p.stem)
def test_vector(path):
    v = json.loads(path.read_text(encoding="utf-8"))
    w, h = v["frame"]
    engine = BehaviorEngine(BehaviorConfig(**v["config"]),
                            [Zone(z["kind"], [tuple(p) for p in z["points"]], z["zone_id"]) for z in v["zones"]], w, h)
    for index, step in enumerate(v["steps"]):
        people = [Person(d["track_id"], tuple(d["box"]), None if d["keypoints"] is None else [tuple(p) for p in d["keypoints"]])
                  for d in step["people"]]
        got = engine.process(people, step["now_ns"], [tuple(x) for x in step.get("scores", [])])
        want = step["expected_events"]
        assert len(got) == len(want), f"step {index}: {len(got)} events, expected {len(want)}"
        for e, x in zip(got, want):
            assert (e.type, list(e.track_ids), e.zone_id, e.occurred_ns, e.now_ns) == \
                   (x["type"], x["track_ids"], x["zone_id"], x["occurred_ns"], step["now_ns"]), f"step {index}"
            assert e.score == pytest.approx(x["score"], abs=1e-6), f"step {index}"
        assert engine.track_count() == step["tracks_after"], f"step {index}"
