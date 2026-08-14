"""Person detection using a pretrained YOLO model."""

from __future__ import annotations

from typing import Any


def _resolve_device() -> str:
    """Return the best available inference device."""
    try:
        import torch
    except ImportError:
        return "cpu"

    return "cuda:0" if torch.cuda.is_available() else "cpu"


class PersonDetector:
    """Detect and annotate people in video frames."""

    def __init__(
        self,
        confidence_threshold: float = 0.5,
        model_name: str = "yolov8n.pt",
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.model_name = model_name
        self.person_class_id = 0
        self.device = _resolve_device()

        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Ultralytics is not installed. Run: pip install -r requirements.txt"
            ) from exc

        self.model = YOLO(self.model_name)

    def _run_inference(self, frame: Any, track: bool = False) -> Any:
        """Run person-only inference on a frame."""
        inference_kwargs = {
            "conf": self.confidence_threshold,
            "classes": [self.person_class_id],
            "device": self.device,
            "verbose": False,
        }

        if track:
            return self.model.track(
                frame,
                persist=True,
                tracker="bytetrack.yaml",
                **inference_kwargs,
            )

        return self.model.predict(frame, **inference_kwargs)

    def _annotate_results(self, frame: Any, results: Any, include_track_id: bool = False) -> Any:
        """Draw detection or tracking overlays on a frame."""
        import cv2

        annotated_frame = frame.copy()
        for box in results[0].boxes:
            coordinates = box.xyxy[0]
            if hasattr(coordinates, "tolist"):
                coordinates = coordinates.tolist()

            x1, y1, x2, y2 = map(int, coordinates)
            confidence = float(box.conf[0])

            if include_track_id and getattr(box, "id", None) is not None:
                track_id = int(box.id[0])
                label = f"Person ID: {track_id} | {confidence:.2f}"
            else:
                label = f"person {confidence:.2f}"

            cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            text_origin = (x1, max(25, y1 - 10))
            cv2.putText(
                annotated_frame,
                label,
                text_origin,
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

        return annotated_frame

    def annotate_frame(self, frame: Any) -> Any:
        """Detect persons in a frame and draw bounding boxes with labels."""
        results = self._run_inference(frame, track=False)
        return self._annotate_results(frame, results, include_track_id=False)

    def annotate_tracked_frame(self, frame: Any) -> Any:
        """Track persons and draw persistent person IDs with confidence."""
        results = self._run_inference(frame, track=True)
        return self._annotate_results(frame, results, include_track_id=True)