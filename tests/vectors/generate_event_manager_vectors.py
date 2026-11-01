"""Generate tests/vectors/event_manager_*.json from PersonDetectedEventManager.

The Python manager is the specification: each scenario below only lists the inputs (a clock value and
the detections of one frame per step); the expected events and the number of tracks still held after
the step are recorded by running the manager. The C port (engine/src/core/event_manager.c) must give
the same answers for the same files (`make test-core`), and so must the manager itself
(tests/test_event_manager_vectors.py), so a vector can never silently disagree with the spec.

Regenerate after a deliberate change of the specification:
    PYTHONPATH=. python tests/vectors/generate_event_manager_vectors.py
and review the diff of the JSON files.

File format:
    name, description         free text
    absence_timeout_s          constructor argument of the manager
    steps[]                    processed in order, on one manager instance
      now_ns                   clock passed to process(), integer nanoseconds, a multiple of 1000
                               (Python datetime has microsecond resolution)
      detections[]             {track_id, conf}, in the order given to process()
      expected_events[]        {track_id, confidence}; each event carries timestamp == now_ns
      tracks_after             number of tracks the manager still remembers after the step
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from src.events import PersonDetectedEventManager

OUT_DIR = Path(__file__).resolve().parent
EPOCH = datetime(1970, 1, 1)

S = 1_000_000_000  # one second in ns
US = 1_000  # one microsecond in ns

# 2026-01-01 12:00:00 UTC as Unix time, the order of magnitude of a real stream clock
# (stream_start + pts). It does not fit a double exactly with ns resolution, so it checks that the
# C side keeps the clock in an integer.
REAL_CLOCK_NS = 1_767_268_800 * S


def to_datetime(now_ns: int) -> datetime:
    assert now_ns % 1000 == 0, "now_ns must be a multiple of 1000 (datetime has microsecond resolution)"
    return EPOCH + timedelta(microseconds=now_ns // 1000)


def det(track_id: int, conf: float) -> dict:
    return {"track_id": track_id, "conf": conf}


def step(now_ns: int, *detections: dict) -> dict:
    return {"now_ns": now_ns, "detections": list(detections)}


def scenarios() -> list[dict]:
    many = [det(1000 + i, round(0.30 + i * 0.01, 2)) for i in range(60)]

    return [
        {
            "name": "no_detections",
            "description": "Frames without detections emit nothing and remember nothing.",
            "absence_timeout_s": 10.0,
            "steps": [step(0), step(1 * S), step(2 * S)],
        },
        {
            "name": "new_track_emits_once",
            "description": "A track emits one event on its first frame, none while it stays visible.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(0, det(1, 0.87)),
                step(1 * S, det(1, 0.90)),
                step(2 * S, det(1, 0.95)),
                step(3 * S, det(1, 0.60)),
            ],
        },
        {
            "name": "several_new_tracks_one_frame",
            "description": "Every new track of a frame emits, in detection order; a later newcomer emits alone.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(0, det(3, 0.70), det(1, 0.90), det(2, 0.80)),
                step(1 * S, det(1, 0.90), det(2, 0.80), det(3, 0.70)),
                step(2 * S, det(1, 0.90), det(4, 0.55), det(3, 0.70)),
            ],
        },
        {
            "name": "stationary_for_ten_minutes",
            "description": "A person standing still for 10 minutes (a frame every 5 s) is one event.",
            "absence_timeout_s": 10.0,
            "steps": [step(i * 5 * S, det(1, 0.90 if i % 2 else 0.85)) for i in range(121)],
        },
        {
            "name": "reappear_after_timeout",
            "description": "A track back after more than absence_timeout emits again; its new confidence is used.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(0, det(1, 0.80)),
                step(5 * S),
                step(15 * S, det(1, 0.65)),
                step(16 * S, det(1, 0.99)),
            ],
        },
        {
            "name": "timeout_boundary_is_exclusive",
            "description": "Absent exactly absence_timeout is still the same appearance; one microsecond more is not.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(0, det(1, 0.80)),
                step(10 * S, det(1, 0.81)),
                step(20 * S + 1 * US, det(1, 0.82)),
                step(30 * S + 1 * US, det(1, 0.83)),
                step(40 * S + 2 * US, det(1, 0.84)),
            ],
        },
        {
            "name": "stale_tracks_are_forgotten",
            "description": "A track that is gone is dropped once absent longer than the timeout, not before.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(0, det(1, 0.80), det(2, 0.70)),
                step(5 * S, det(2, 0.70)),
                step(10 * S, det(2, 0.70)),
                step(10 * S + 1 * US, det(2, 0.70)),
                step(11 * S),
                step(20 * S + 1 * US),
                step(20 * S + 2 * US, det(2, 0.71)),
            ],
        },
        {
            "name": "flicker_within_timeout",
            "description": "A track that blinks out for less than the timeout each time stays one appearance.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(0, det(7, 0.80)),
                step(3 * S),
                step(6 * S, det(7, 0.80)),
                step(9 * S),
                step(12 * S),
                step(15 * S, det(7, 0.80)),
            ],
        },
        {
            "name": "tracks_are_independent",
            "description": "One track timing out and returning does not disturb another that stays.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(0, det(1, 0.80), det(2, 0.70)),
                step(4 * S, det(2, 0.72)),
                step(8 * S, det(2, 0.72)),
                step(12 * S, det(1, 0.60), det(2, 0.72)),
                step(14 * S, det(1, 0.61), det(2, 0.72)),
            ],
        },
        {
            "name": "duplicate_track_in_frame",
            "description": "The same track twice in one frame emits once, with the confidence of the first.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(0, det(5, 0.50), det(5, 0.90)),
                step(1 * S, det(5, 0.90), det(5, 0.95)),
                step(30 * S, det(5, 0.40), det(5, 0.45)),
            ],
        },
        {
            "name": "zero_timeout",
            "description": "With absence_timeout 0 only a gap of at least one clock tick starts a new appearance.",
            "absence_timeout_s": 0.0,
            "steps": [
                step(0, det(1, 0.80)),
                step(0, det(1, 0.81)),
                step(1 * US, det(1, 0.82)),
                step(1 * US),
                step(2 * US, det(1, 0.83)),
            ],
        },
        {
            "name": "short_timeout_fractional",
            "description": "absence_timeout is in seconds and may be fractional.",
            "absence_timeout_s": 0.5,
            "steps": [
                step(0, det(1, 0.80)),
                step(500_000 * US, det(1, 0.80)),
                step(1_000_000 * US + 1 * US, det(1, 0.82)),
                step(1_500_000 * US + 1 * US, det(1, 0.83)),
            ],
        },
        {
            "name": "clock_goes_backwards",
            "description": "A clock that steps back (a looped file) is never 'absent too long': nothing emits, nothing is dropped.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(100 * S, det(1, 0.80)),
                step(50 * S, det(1, 0.80)),
                step(50 * S, det(2, 0.70)),
                step(51 * S, det(2, 0.70)),
            ],
        },
        {
            "name": "track_id_zero_and_large",
            "description": "Track ID 0 is a valid ID, and IDs above 32 bits are kept apart.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(0, det(0, 0.80), det(1 << 32, 0.70), det((1 << 32) + 1, 0.60)),
                step(1 * S, det(0, 0.80), det(1 << 32, 0.70), det((1 << 32) + 1, 0.60)),
                step(2 * S, det(1, 0.50)),
            ],
        },
        {
            "name": "real_stream_clock",
            "description": "Clock values of a real stream (Unix time in ns, beyond 2^53) keep their exact resolution.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(REAL_CLOCK_NS, det(1, 0.80)),
                step(REAL_CLOCK_NS + 10 * S, det(1, 0.80)),
                step(REAL_CLOCK_NS + 20 * S + 1 * US, det(1, 0.80)),
                step(REAL_CLOCK_NS + 30 * S + 1 * US, det(1, 0.80)),
                step(REAL_CLOCK_NS + 40 * S + 3 * US, det(1, 0.80)),
            ],
        },
        {
            "name": "many_tracks_in_one_frame",
            "description": "60 tracks in one frame (more than a small table holds) all emit and are all remembered.",
            "absence_timeout_s": 10.0,
            "steps": [
                step(0, *many),
                step(1 * S, *many),
                step(2 * S),
                step(12 * S + 1 * US),
            ],
        },
    ]


def record(scenario: dict) -> dict:
    """Run the specification on the scenario's inputs and attach what it answers."""
    manager = PersonDetectedEventManager(absence_timeout=scenario["absence_timeout_s"])
    steps = []
    for s in scenario["steps"]:
        now = to_datetime(s["now_ns"])
        events = manager.process([(d["track_id"], d["conf"]) for d in s["detections"]], now=now)
        for event in events:
            assert event.timestamp == now
        steps.append(
            {
                **s,
                "expected_events": [{"track_id": e.track_id, "confidence": e.confidence} for e in events],
                "tracks_after": len(manager._tracks),
            }
        )
    return {**scenario, "steps": steps}


def main() -> None:
    for scenario in scenarios():
        path = OUT_DIR / f"event_manager_{scenario['name']}.json"
        path.write_text(json.dumps(record(scenario), indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.name}")


if __name__ == "__main__":
    main()
