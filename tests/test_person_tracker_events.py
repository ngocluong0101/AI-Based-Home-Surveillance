from types import SimpleNamespace

import numpy as np
import pytest

from src.tracking import person_tracker
from src.tracking.person_tracker import PersonTracker


class FakeDetector:
    """Stands in for PersonDetector so no YOLO model is loaded."""

    device = "cpu"

    def __init__(self, confidence_threshold=None, model_name=None):
        self.track_ids = [1]

    def _run_inference(self, frame, track=False):
        boxes = [SimpleNamespace(id=[track_id], conf=[0.9]) for track_id in self.track_ids]
        return [SimpleNamespace(boxes=boxes)]

    def _annotate_results(self, frame, results, include_track_id=False):
        return frame


@pytest.fixture
def frame():
    return np.zeros((48, 64, 3), dtype=np.uint8)


@pytest.fixture
def make_tracker(monkeypatch, tmp_path):
    monkeypatch.setattr(person_tracker, "PersonDetector", FakeDetector)
    monkeypatch.setattr(person_tracker, "ENABLE_TELEGRAM", False)
    monkeypatch.setattr(person_tracker, "SNAPSHOT_DIR", str(tmp_path))

    def factory(snapshot: bool, **kwargs):
        monkeypatch.setattr(person_tracker, "ENABLE_SNAPSHOT", snapshot)
        return PersonTracker(**kwargs)

    return factory


def test_callback_receives_event_and_snapshot_path(make_tracker, frame, tmp_path):
    received = []
    tracker = make_tracker(True, event_callback=lambda event, path: received.append((event, path)))

    tracker.annotate_frame(frame)

    assert len(received) == 1
    event, path = received[0]
    assert event.track_id == 1
    assert event.confidence == pytest.approx(0.9)
    assert path is not None and path.parent == tmp_path and path.exists()


def test_callback_gets_none_path_when_snapshots_disabled(make_tracker, frame):
    received = []
    tracker = make_tracker(False, event_callback=lambda event, path: received.append(path))

    tracker.annotate_frame(frame)

    assert received == [None]


def test_callback_fires_once_per_appearance(make_tracker, frame):
    received = []
    tracker = make_tracker(False, event_callback=lambda event, path: received.append(event))

    for _ in range(5):
        tracker.annotate_frame(frame)

    assert len(received) == 1


def test_callback_can_be_assigned_after_construction(make_tracker, frame):
    tracker = make_tracker(False)
    received = []
    tracker.event_callback = lambda event, path: received.append(event)

    tracker.annotate_frame(frame)

    assert len(received) == 1


def test_failing_callback_never_breaks_the_pipeline(make_tracker, frame):
    def boom(event, path):
        raise RuntimeError("listener bug")

    tracker = make_tracker(False, event_callback=boom)

    assert tracker.annotate_frame(frame) is frame


def test_no_callback_is_fine(make_tracker, frame):
    tracker = make_tracker(False)

    assert tracker.annotate_frame(frame) is frame
