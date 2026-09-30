import threading
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.events import PersonDetectedEvent
from src.gui.worker import (
    STATE_IDLE,
    STATE_LOADING,
    STATE_STOPPED,
    PipelineWorker,
)


class FakeProcessor:
    device = "cpu"

    def __init__(self, delay: float = 0.0):
        self.confidence_threshold = 0.5
        self.delay = delay
        self.calls = 0

    def annotate_frame(self, frame):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return frame


def wait_until_finished(worker: PipelineWorker, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while worker.is_running:
        assert time.monotonic() < deadline, "worker did not finish in time"
        time.sleep(0.01)


def make_worker(source, processor, confidence=0.5, track=False) -> PipelineWorker:
    return PipelineWorker(source, confidence, track, processor_factory=lambda _conf, _track: processor)


def test_new_worker_is_idle():
    worker = make_worker("0", FakeProcessor())

    assert worker.state == STATE_IDLE
    assert not worker.is_running


def test_runs_video_to_the_end_and_publishes_frames(sample_video, frame_count):
    processor = FakeProcessor()
    worker = make_worker(str(sample_video), processor)

    worker.start()
    wait_until_finished(worker)

    assert worker.state == STATE_STOPPED
    assert worker.exit_code == 0
    assert worker.error is None
    assert processor.calls == frame_count
    assert worker.frames.get_nowait() is not None  # the newest frame is still queued


def test_frame_queue_keeps_only_the_latest_frame(sample_video):
    worker = make_worker(str(sample_video), FakeProcessor())

    worker.start()
    wait_until_finished(worker)

    assert worker.frames.qsize() == 1


def test_factory_receives_confidence_and_track_flag(sample_video):
    seen = []

    def factory(confidence, track):
        seen.append((confidence, track))
        return FakeProcessor()

    worker = PipelineWorker(str(sample_video), 0.7, True, processor_factory=factory)
    worker.start()
    wait_until_finished(worker)

    assert seen == [(0.7, True)]


def test_confidence_is_applied_to_processor(sample_video):
    processor = FakeProcessor()
    worker = make_worker(str(sample_video), processor, confidence=0.5)
    worker.set_confidence(0.8)

    worker.start()
    wait_until_finished(worker)

    assert processor.confidence_threshold == 0.8


def test_confidence_targets_inner_detector_of_a_tracker(sample_video):
    detector = SimpleNamespace(confidence_threshold=0.5)
    tracker = SimpleNamespace(detector=detector, annotate_frame=lambda frame: frame, device="cpu")
    worker = make_worker(str(sample_video), tracker, confidence=0.65)

    worker.start()
    wait_until_finished(worker)

    assert detector.confidence_threshold == 0.65


def test_stop_ends_playback_early_without_error(sample_video, frame_count):
    processor = FakeProcessor(delay=0.05)
    worker = make_worker(str(sample_video), processor)

    worker.start()
    time.sleep(0.12)
    worker.stop(timeout=5.0)

    assert not worker.is_running
    assert worker.state == STATE_STOPPED
    assert worker.error is None
    assert 0 < processor.calls < frame_count


def test_model_load_failure_is_reported(sample_video):
    def factory(_confidence, _track):
        raise RuntimeError("boom")

    worker = PipelineWorker(str(sample_video), 0.5, False, processor_factory=factory)
    worker.start()
    wait_until_finished(worker)

    assert worker.state == STATE_STOPPED
    assert worker.exit_code is None
    assert "boom" in worker.error


def test_unopenable_source_is_reported(tmp_path):
    worker = make_worker(str(tmp_path / "missing.mp4"), FakeProcessor())

    worker.start()
    wait_until_finished(worker)

    assert worker.exit_code == 1
    assert "missing.mp4" in worker.error


def test_processor_crash_is_reported_not_raised(sample_video):
    class Crashing(FakeProcessor):
        def annotate_frame(self, frame):
            raise ValueError("bad frame")

    worker = make_worker(str(sample_video), Crashing())
    worker.start()
    wait_until_finished(worker)

    assert worker.state == STATE_STOPPED
    assert "bad frame" in worker.error


def test_worker_cannot_be_started_twice(sample_video):
    worker = make_worker(str(sample_video), FakeProcessor())
    worker.start()

    with pytest.raises(RuntimeError):
        worker.start()
    wait_until_finished(worker)


class EventEmittingProcessor(FakeProcessor):
    """Mimics PersonTracker: exposes `event_callback` and fires it on the first frame."""

    def __init__(self):
        super().__init__()
        self.event_callback = None

    def annotate_frame(self, frame):
        if self.calls == 0 and self.event_callback is not None:
            event = PersonDetectedEvent("PERSON_DETECTED", 3, 0.91, datetime(2026, 1, 1, 12, 0, 0))
            self.event_callback(event, Path("data/snapshots/a.jpg"))
        return super().annotate_frame(frame)


def test_worker_wires_processor_events_into_its_queue(sample_video):
    worker = make_worker(str(sample_video), EventEmittingProcessor())

    worker.start()
    wait_until_finished(worker)

    event, snapshot = worker.events.get_nowait()
    assert (event.track_id, event.confidence) == (3, 0.91)
    assert snapshot == Path("data/snapshots/a.jpg")
    assert worker.events.empty()


def test_processor_without_event_callback_is_left_alone(sample_video):
    processor = FakeProcessor()
    worker = make_worker(str(sample_video), processor)

    worker.start()
    wait_until_finished(worker)

    assert not hasattr(processor, "event_callback")
    assert worker.events.empty()


def test_state_is_loading_right_after_start():
    release = threading.Event()

    def slow_factory(_confidence, _track):
        release.wait(5)
        return FakeProcessor()

    worker = PipelineWorker("0", 0.5, False, processor_factory=slow_factory)
    worker.start()
    try:
        assert worker.state == STATE_LOADING
    finally:
        worker.stop()
        release.set()
        wait_until_finished(worker)
