"""Person tracking using Ultralytics ByteTrack."""

from __future__ import annotations

from typing import Any, Callable
from pathlib import Path

from src.detection import PersonDetector
from src.events import PersonDetectedEvent, PersonDetectedEventManager
from src.config import (
    CONFIDENCE_THRESHOLD,
    ABSENCE_TIMEOUT,
    ENABLE_SNAPSHOT,
    ENABLE_TELEGRAM,
    SNAPSHOT_DIR,
    SNAPSHOT_RETENTION_DAYS,
    SNAPSHOT_CLEANUP_INTERVAL,
)
from src.snapshot_management import SnapshotCleaner
import logging

# Called once per emitted event with the saved snapshot path (None if not saved).
EventCallback = Callable[[PersonDetectedEvent, Path | None], None]


class PersonTracker:
    """Track people across frames with ByteTrack."""

    def __init__(
        self,
        confidence_threshold: float | None = None,
        model_name: str = "yolov8n.pt",
        absence_timeout: float | None = None,
        event_callback: EventCallback | None = None,
    ) -> None:
        self.event_callback = event_callback
        # Use config defaults when None is provided
        conf = confidence_threshold if confidence_threshold is not None else CONFIDENCE_THRESHOLD
        absent = absence_timeout if absence_timeout is not None else ABSENCE_TIMEOUT

        self.detector = PersonDetector(
            confidence_threshold=conf,
            model_name=model_name,
        )
        self.event_manager = PersonDetectedEventManager(absence_timeout=absent)
        self.snapshot_cleaner = SnapshotCleaner(retention_days=SNAPSHOT_RETENTION_DAYS)
        self._cleanup_counter = 0

    @property
    def device(self) -> str:
        """Expose the active inference device."""
        return self.detector.device

    def annotate_frame(self, frame: Any) -> Any:
        """Annotate a frame with tracked persons and their IDs."""
        results = self.detector._run_inference(frame, track=True)

        detections: list[tuple[int, float]] = []
        for box in results[0].boxes:
            if getattr(box, "id", None) is None:
                continue

            track_id = int(box.id[0])
            confidence = float(box.conf[0])
            detections.append((track_id, confidence))

        emitted_events = self.event_manager.process(detections)

        # keep pipeline-local person count logic; shared API state removed

        # Save a snapshot for each newly emitted PERSON_DETECTED event (configurable).
        if emitted_events:
            logger = logging.getLogger(__name__)
            saved_paths: dict[int, Path] = {}

            if ENABLE_SNAPSHOT:
                try:
                    import cv2
                except ImportError:
                    logger.warning("cv2 not available; skipping snapshot save")
                else:
                    snapshots_dir = Path(SNAPSHOT_DIR)
                    snapshots_dir.mkdir(parents=True, exist_ok=True)

                    for event in emitted_events:
                        filename = f"{event.event_type}_id{event.track_id}_{event.timestamp.strftime('%Y%m%d_%H%M%S')}.jpg"
                        save_path = snapshots_dir / filename
                        try:
                            cv2.imwrite(str(save_path), frame.copy())
                            logger.info("Snapshot saved: %s", save_path)
                            saved_paths[event.track_id] = save_path
                            self._cleanup_counter += 1
                            if self._cleanup_counter >= SNAPSHOT_CLEANUP_INTERVAL:
                                self._cleanup_counter = 0
                                try:
                                    self.snapshot_cleaner.cleanup(snapshots_dir)
                                except Exception as exc:
                                    logger.error("Snapshot cleanup failed: %s", exc, exc_info=True)
                        except Exception as exc:
                            logger.error("Failed to save snapshot %s: %s", save_path, exc, exc_info=True)

            # Notify listeners (the GUI event panel) before the slower network call below.
            if self.event_callback is not None:
                for event in emitted_events:
                    try:
                        self.event_callback(event, saved_paths.get(event.track_id))
                    except Exception as exc:
                        logger.error("Event callback failed for id %s: %s", event.track_id, exc, exc_info=True)

            # Send Telegram notifications for newly emitted events when enabled.
            try:
                from src.notifications.telegram_notifier import TelegramNotifier
            except ImportError:
                TelegramNotifier = None

            if TelegramNotifier is not None and ENABLE_TELEGRAM:
                try:
                    notifier = TelegramNotifier()
                    for event in emitted_events:
                        try:
                            photo_path = saved_paths.get(event.track_id)
                            if photo_path is not None:
                                notifier.send_person_detected_photo(event, str(photo_path))
                            else:
                                # fallback to text-only if photo not available
                                notifier.send_person_detected(event)
                        except Exception as exc:
                            logger.error("Notification error for id %s: %s", event.track_id, exc, exc_info=True)
                except Exception as exc:
                    logger.error("Failed to initialize TelegramNotifier: %s", exc, exc_info=True)

        # Persist emitted events to API event store removed (no in-process event store)

        return self.detector._annotate_results(frame, results, include_track_id=True)