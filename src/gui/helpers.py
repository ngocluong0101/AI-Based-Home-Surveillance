"""Display-independent helpers for the Tkinter GUI (unit-testable without a screen)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SNAPSHOT_EXTENSIONS = (".jpg", ".jpeg", ".png")

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
            raise ValueError("Webcam index must be a non-negative integer (e.g. 0).")
    elif source_type == SOURCE_FILE:
        if not value or not Path(value).expanduser().is_file():
            raise ValueError(f"Video file not found: {value or '(empty)'}")
    elif source_type == SOURCE_STREAM:
        if not value.lower().startswith(STREAM_PREFIXES):
            raise ValueError("Stream URL must start with rtsp://, rtsps://, http:// or https://.")
    else:
        raise ValueError(f"Invalid source type: {source_type}")

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


@dataclass(frozen=True)
class SnapshotInfo:
    path: Path
    saved_at: datetime
    size_bytes: int


def list_snapshots(directory: str | Path) -> list[SnapshotInfo]:
    """Return the image files in `directory`, newest first. A missing directory gives an empty list."""
    folder = Path(directory)
    if not folder.is_dir():
        return []

    infos = []
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in SNAPSHOT_EXTENSIONS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue  # deleted by the cleanup job between listing and stat
        infos.append(SnapshotInfo(path, datetime.fromtimestamp(stat.st_mtime), stat.st_size))

    infos.sort(key=lambda info: (info.saved_at, info.path.name), reverse=True)
    return infos


def format_snapshot_row(info: SnapshotInfo) -> tuple[str, str, str]:
    """Turn a snapshot into the (name, saved time, size) table cells."""
    if info.size_bytes >= 1024 * 1024:
        size = f"{info.size_bytes / (1024 * 1024):.1f} MB"
    else:
        size = f"{max(1, round(info.size_bytes / 1024))} KB"
    return info.path.name, info.saved_at.strftime("%Y-%m-%d %H:%M:%S"), size


def load_image_ppm(path: str | Path, max_width: int, max_height: int) -> bytes:
    """Read an image from disk and return it fitted into the box as PPM bytes.

    Raises ValueError if the file is missing or is not a readable image.
    """
    import cv2

    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return frame_to_ppm(image, max_width, max_height)


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
