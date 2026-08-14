"""Person tracking using Ultralytics ByteTrack."""

from __future__ import annotations

from typing import Any

from src.detection import PersonDetector


class PersonTracker:
    """Track people across frames with ByteTrack."""

    def __init__(
        self,
        confidence_threshold: float = 0.5,
        model_name: str = "yolov8n.pt",
    ) -> None:
        self.detector = PersonDetector(
            confidence_threshold=confidence_threshold,
            model_name=model_name,
        )

    @property
    def device(self) -> str:
        """Expose the active inference device."""
        return self.detector.device

    def annotate_frame(self, frame: Any) -> Any:
        """Annotate a frame with tracked persons and their IDs."""
        return self.detector.annotate_tracked_frame(frame)