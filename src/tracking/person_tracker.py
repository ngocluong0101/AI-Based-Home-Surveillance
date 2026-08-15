"""Person tracking using Ultralytics ByteTrack."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from src.detection import PersonDetector
from src.events import PersonDetectedEventManager


class PersonTracker:
    """Track people across frames with ByteTrack."""

    def __init__(
        self,
        confidence_threshold: float = 0.5,
        model_name: str = "yolov8n.pt",
        absence_timeout: float = 10.0,
    ) -> None:
        self.detector = PersonDetector(
            confidence_threshold=confidence_threshold,
            model_name=model_name,
        )
        self.event_manager = PersonDetectedEventManager(absence_timeout=absence_timeout)

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

        # Save a snapshot for each newly emitted PERSON_DETECTED event.
        if emitted_events:
            try:
                import cv2
            except Exception:
                print("[SNAPSHOT] cv2 not available; skipping snapshot save")
            else:
                snapshots_dir = Path("data") / "snapshots"
                snapshots_dir.mkdir(parents=True, exist_ok=True)

                for event in emitted_events:
                    filename = f"{event.event_type}_id{event.track_id}_{event.timestamp.strftime('%Y%m%d_%H%M%S')}.jpg"
                    save_path = snapshots_dir / filename
                    cv2.imwrite(str(save_path), frame.copy())
                    print(f"[SNAPSHOT SAVED] {save_path}")

                # Send Telegram notifications for newly emitted events.
                try:
                    from src.notifications.telegram_notifier import TelegramNotifier
                except Exception:
                    TelegramNotifier = None

                if TelegramNotifier is not None:
                    try:
                        notifier = TelegramNotifier()
                        for event in emitted_events:
                            try:
                                notifier.send_person_detected(event)
                            except Exception as exc:
                                print(f"[NOTIFICATION ERROR] {exc}")
                    except Exception as exc:
                        print(f"[NOTIFICATION ERROR] init failed: {exc}")

        return self.detector._annotate_results(frame, results, include_track_id=True)