from datetime import datetime, timedelta

import pytest

from src.events import PersonDetectedEvent, PersonDetectedEventManager

T0 = datetime(2026, 1, 1, 12, 0, 0)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture
def manager() -> PersonDetectedEventManager:
    return PersonDetectedEventManager(absence_timeout=10.0)


def test_no_detections_emits_nothing(manager):
    assert manager.process([], now=T0) == []


def test_new_track_emits_event_with_correct_fields(manager):
    events = manager.process([(1, 0.87)], now=T0)

    assert events == [
        PersonDetectedEvent(event_type="PERSON_DETECTED", track_id=1, confidence=0.87, timestamp=T0)
    ]


def test_same_track_in_consecutive_frames_emits_only_once(manager):
    assert len(manager.process([(1, 0.9)], now=at(0))) == 1
    assert manager.process([(1, 0.9)], now=at(1)) == []
    assert manager.process([(1, 0.9)], now=at(2)) == []


def test_multiple_new_tracks_in_one_frame_emit_one_event_each(manager):
    events = manager.process([(1, 0.9), (2, 0.8), (3, 0.7)], now=T0)

    assert [e.track_id for e in events] == [1, 2, 3]
    assert [e.confidence for e in events] == [0.9, 0.8, 0.7]


def test_second_track_appearing_later_emits_only_for_new_one(manager):
    manager.process([(1, 0.9)], now=at(0))

    events = manager.process([(1, 0.9), (2, 0.8)], now=at(1))

    assert [e.track_id for e in events] == [2]


def test_track_returning_after_timeout_emits_again(manager):
    manager.process([(1, 0.9)], now=at(0))

    events = manager.process([(1, 0.85)], now=at(10.5))

    assert len(events) == 1
    assert events[0].track_id == 1
    assert events[0].confidence == 0.85
    assert events[0].timestamp == at(10.5)


def test_track_returning_exactly_at_timeout_does_not_emit_again(manager):
    manager.process([(1, 0.9)], now=at(0))

    assert manager.process([(1, 0.9)], now=at(10.0)) == []


def test_brief_gap_shorter_than_timeout_does_not_emit_again(manager):
    manager.process([(1, 0.9)], now=at(0))
    manager.process([], now=at(5))

    assert manager.process([(1, 0.9)], now=at(9)) == []


def test_continuous_presence_refreshes_last_seen(manager):
    for second in range(0, 40, 5):
        events = manager.process([(1, 0.9)], now=at(second))
        assert bool(events) == (second == 0)


def test_stale_track_is_removed_after_timeout(manager):
    manager.process([(1, 0.9)], now=at(0))
    assert 1 in manager._tracks

    manager.process([], now=at(11))

    assert 1 not in manager._tracks


def test_absent_track_within_timeout_is_kept(manager):
    manager.process([(1, 0.9)], now=at(0))

    manager.process([], now=at(5))

    assert 1 in manager._tracks


def test_present_track_is_never_cleaned_up(manager):
    manager.process([(1, 0.9)], now=at(0))
    manager.process([(1, 0.9)], now=at(100))

    assert 1 in manager._tracks


def test_tracks_are_independent(manager):
    manager.process([(1, 0.9), (2, 0.9)], now=at(0))

    assert manager.process([(2, 0.9)], now=at(6)) == []
    events = manager.process([(2, 0.9)], now=at(11))
    assert events == []

    events = manager.process([(1, 0.9), (2, 0.9)], now=at(12))
    assert [e.track_id for e in events] == [1]


def test_custom_absence_timeout_is_respected():
    manager = PersonDetectedEventManager(absence_timeout=2.0)
    manager.process([(1, 0.9)], now=at(0))

    assert manager.process([(1, 0.9)], now=at(2)) == []
    assert len(manager.process([(1, 0.9)], now=at(5))) == 1


def test_process_defaults_now_to_current_time(manager):
    before = datetime.now()
    events = manager.process([(1, 0.9)])
    after = datetime.now()

    assert len(events) == 1
    assert before <= events[0].timestamp <= after


def test_process_accepts_any_iterable_of_detections(manager):
    events = manager.process(iter([(1, 0.9), (2, 0.8)]), now=T0)

    assert [e.track_id for e in events] == [1, 2]


def test_event_is_immutable(manager):
    event = manager.process([(1, 0.9)], now=T0)[0]

    with pytest.raises(AttributeError):
        event.track_id = 99


def test_emitted_event_is_logged(manager, caplog):
    with caplog.at_level("INFO", logger="src.events.person_detected"):
        manager.process([(7, 0.91)], now=at(30))

    assert "PERSON_DETECTED ID=7 | confidence=0.91 | time=12:00:30" in caplog.text
