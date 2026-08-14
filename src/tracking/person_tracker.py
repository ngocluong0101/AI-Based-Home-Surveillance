"""Person tracking using Ultralytics ByteTrack."""

from __future__ import annotations

from typing import Any

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

        self.event_manager.process(detections)
        return self.detector._annotate_results(frame, results, include_track_id=True)