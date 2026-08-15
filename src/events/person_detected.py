"""PERSON_DETECTED event management."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
import logging


@dataclass(frozen=True)
class PersonDetectedEvent:
    """A single person-detected event."""

    event_type: str
    track_id: int
    confidence: float
    timestamp: datetime


@dataclass
class _TrackState:
    last_seen: datetime


class PersonDetectedEventManager:
    """Create PERSON_DETECTED events once per person appearance."""

    def __init__(self, absence_timeout: float = 10.0) -> None:
        self.absence_timeout = absence_timeout
        self._tracks: dict[int, _TrackState] = {}

    def process(self, detections: Iterable[tuple[int, float]], now: datetime | None = None) -> list[PersonDetectedEvent]:
        """Update track state and emit new events when a person reappears."""
        current_time = now or datetime.now()
        current_track_ids: set[int] = set()
        emitted_events: list[PersonDetectedEvent] = []

        for track_id, confidence in detections:
            current_track_ids.add(track_id)
            previous_state = self._tracks.get(track_id)

            if previous_state is None or self._is_absent_too_long(previous_state.last_seen, current_time):
                event = PersonDetectedEvent(
                    event_type="PERSON_DETECTED",
                    track_id=track_id,
                    confidence=confidence,
                    timestamp=current_time,
                )
                emitted_events.append(event)
                self._log_event(event)

            self._tracks[track_id] = _TrackState(last_seen=current_time)

        self._cleanup(current_track_ids, current_time)
        return emitted_events

    def _is_absent_too_long(self, last_seen: datetime, current_time: datetime) -> bool:
        """Check whether a track has been absent longer than the timeout."""
        return (current_time - last_seen).total_seconds() > self.absence_timeout

    def _cleanup(self, current_track_ids: set[int], current_time: datetime) -> None:
        """Remove inactive tracks that have been gone long enough."""
        stale_track_ids = [
            track_id
            for track_id, state in self._tracks.items()
            if track_id not in current_track_ids and self._is_absent_too_long(state.last_seen, current_time)
        ]

        for track_id in stale_track_ids:
            del self._tracks[track_id]

    def _log_event(self, event: PersonDetectedEvent) -> None:
        """Log the event in the requested terminal format."""
        logger = logging.getLogger(__name__)
        logger.info(
            "%s ID=%s | confidence=%.2f | time=%s",
            event.event_type,
            event.track_id,
            event.confidence,
            event.timestamp.strftime("%H:%M:%S"),
        )