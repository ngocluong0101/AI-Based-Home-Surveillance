"""Display-independent helpers for the Tkinter GUI (unit-testable without a screen)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

SOURCE_WEBCAM = "Webcam"
SOURCE_FILE = "File video"
SOURCE_STREAM = "RTSP / IP camera"
SOURCE_TYPES = (SOURCE_WEBCAM, SOURCE_FILE, SOURCE_STREAM)

STREAM_PREFIXES = ("rtsp://", "rtsps://", "http://", "https://")


def classify_source(source: str | None) -> tuple[str, str]:
    """Map a CLI-style source to the (source type, value) pair shown in the GUI."""
    value = (source or "").strip()
    if not value:
        return SOURCE_WEBCAM, "0"
    if value.isdigit():
        return SOURCE_WEBCAM, value
    if value.lower().startswith(STREAM_PREFIXES):
        return SOURCE_STREAM, value
    return SOURCE_FILE, value


def validate_source(source_type: str, value: str) -> str:
    """Return the cleaned source string, or raise ValueError with a user-facing message."""
    value = value.strip()

    if source_type == SOURCE_WEBCAM:
        if not value.isdigit():
            raise ValueError("Chỉ số webcam phải là số nguyên không âm (ví dụ: 0).")
    elif source_type == SOURCE_FILE:
        if not value or not Path(value).expanduser().is_file():
            raise ValueError(f"Không tìm thấy file video: {value or '(để trống)'}")
    elif source_type == SOURCE_STREAM:
        if not value.lower().startswith(STREAM_PREFIXES):
            raise ValueError("URL luồng video phải bắt đầu bằng rtsp://, rtsps://, http:// hoặc https://.")
    else:
        raise ValueError(f"Loại nguồn không hợp lệ: {source_type}")

    return value


def format_event_row(event: Any, snapshot_path: Path | None) -> tuple[str, str, str, str]:
    """Turn a PERSON_DETECTED event into the (time, person, confidence, snapshot) table cells."""
    snapshot = snapshot_path.name if snapshot_path is not None else "—"
    return (
        event.timestamp.strftime("%H:%M:%S"),
        f"ID {event.track_id}",
        f"{event.confidence:.2f}",
        snapshot,
    )


def frame_to_ppm(frame: Any, max_width: int, max_height: int) -> bytes:
    """Fit a BGR frame inside the given box (keeping aspect ratio) and encode it as PPM.

    PPM needs no extra imaging dependency: `tk.PhotoImage(data=..., format="PPM")`
    reads it natively, and OpenCV handles the BGR -> RGB conversion on encode.
    """
    import cv2

    height, width = frame.shape[:2]
    scale = min(max_width / width, max_height / height)
    if scale != 1:
        new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
        frame = cv2.resize(frame, new_size, interpolation=interpolation)

    ok, buffer = cv2.imencode(".ppm", frame)
    if not ok:
        raise ValueError("Could not encode frame for display")
    return buffer.tobytes()
