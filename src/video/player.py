"""Video playback utilities for local files, webcams, and RTSP streams."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


WINDOW_NAME = "AI-Based-Home-Surveillance"
FrameProcessor = Callable[[Any], Any]


def _resolve_source(source: str) -> str | int:
    """Convert a CLI source into a capture-friendly value."""
    if source.isdigit():
        return int(source)

    return source


def _validate_local_file(source: str) -> Path | None:
    """Return the local path if the source points to an existing file."""
    path = Path(source).expanduser()
    if path.exists():
        return path

    return None


def play_video_source(source: str, frame_processor: FrameProcessor | None = None) -> int:
    """Play a video source frame by frame until the user quits or it ends."""
    local_path = _validate_local_file(source)
    if local_path is None and not source.isdigit() and not source.startswith(("rtsp://", "rtsps://", "http://", "https://")):
        print(f"Error: video file not found: {source}")
        return 1

    try:
        import cv2
    except ImportError:
        print("Error: OpenCV is not installed. Run: pip install -r requirements.txt")
        return 1

    capture_source: str | int = _resolve_source(source)
    if local_path is not None:
        capture_source = str(local_path)

    capture = cv2.VideoCapture(capture_source)
    if not capture.isOpened():
        print(f"Error: could not open video source: {source}")
        capture.release()
        cv2.destroyAllWindows()
        return 1

    try:
        while True:
            success, frame = capture.read()
            if not success:
                print("Video ended or no more frames available.")
                break

            if frame_processor is not None:
                frame = frame_processor(frame)

            cv2.imshow(WINDOW_NAME, frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        capture.release()
        cv2.destroyAllWindows()

    return 0