"""Person detection using a pretrained YOLO model."""

from __future__ import annotations

from typing import Any


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

        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "Ultralytics is not installed. Run: pip install -r requirements.txt"
            ) from exc

        self.model = YOLO(self.model_name)

    def annotate_frame(self, frame: Any) -> Any:
        """Detect persons in a frame and draw bounding boxes with labels."""
        import cv2

        results = self.model.predict(
            frame,
            conf=self.confidence_threshold,
            classes=[self.person_class_id],
            verbose=False,
        )

        annotated_frame = frame.copy()
        for box in results[0].boxes:
            coordinates = box.xyxy[0]
            if hasattr(coordinates, "tolist"):
                coordinates = coordinates.tolist()

            x1, y1, x2, y2 = map(int, coordinates)

            confidence = float(box.conf[0])
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