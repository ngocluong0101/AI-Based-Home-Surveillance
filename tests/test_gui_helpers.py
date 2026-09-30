import numpy as np
import pytest

from src.gui.helpers import (
    SOURCE_FILE,
    SOURCE_STREAM,
    SOURCE_WEBCAM,
    classify_source,
    frame_to_ppm,
    validate_source,
)


@pytest.mark.parametrize(
    "source, expected",
    [
        (None, (SOURCE_WEBCAM, "0")),
        ("", (SOURCE_WEBCAM, "0")),
        ("1", (SOURCE_WEBCAM, "1")),
        ("rtsp://user:pw@host:554/stream", (SOURCE_STREAM, "rtsp://user:pw@host:554/stream")),
        ("RTSP://host/stream", (SOURCE_STREAM, "RTSP://host/stream")),
        ("http://host/video", (SOURCE_STREAM, "http://host/video")),
        ("data/videos/test.mp4", (SOURCE_FILE, "data/videos/test.mp4")),
    ],
)
def test_classify_source(source, expected):
    assert classify_source(source) == expected


def test_validate_webcam_accepts_index_and_strips_whitespace():
    assert validate_source(SOURCE_WEBCAM, " 2 ") == "2"


@pytest.mark.parametrize("value", ["", "abc", "-1", "0.5"])
def test_validate_webcam_rejects_non_index(value):
    with pytest.raises(ValueError):
        validate_source(SOURCE_WEBCAM, value)


def test_validate_file_accepts_existing_file(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"x")

    assert validate_source(SOURCE_FILE, str(video)) == str(video)


def test_validate_file_rejects_missing_file_and_directory(tmp_path):
    with pytest.raises(ValueError):
        validate_source(SOURCE_FILE, str(tmp_path / "missing.mp4"))
    with pytest.raises(ValueError):
        validate_source(SOURCE_FILE, str(tmp_path))
    with pytest.raises(ValueError):
        validate_source(SOURCE_FILE, "")


def test_validate_stream_requires_known_scheme():
    assert validate_source(SOURCE_STREAM, "rtsp://host/stream") == "rtsp://host/stream"
    with pytest.raises(ValueError):
        validate_source(SOURCE_STREAM, "host/stream")


def test_validate_unknown_source_type_is_rejected():
    with pytest.raises(ValueError):
        validate_source("Carrier pigeon", "0")


def _ppm_size(data: bytes) -> tuple[int, int]:
    magic, dims, maxval, _ = data.split(b"\n", 3)
    assert magic == b"P6" and maxval == b"255"
    width, height = map(int, dims.split())
    return width, height


def test_frame_to_ppm_downscales_keeping_aspect_ratio():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)

    assert _ppm_size(frame_to_ppm(frame, 50, 50)) == (50, 25)


def test_frame_to_ppm_upscales_to_fit_box():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)

    assert _ppm_size(frame_to_ppm(frame, 400, 400)) == (400, 200)


def test_frame_to_ppm_leaves_exact_fit_untouched():
    frame = np.zeros((48, 64, 3), dtype=np.uint8)

    assert _ppm_size(frame_to_ppm(frame, 64, 48)) == (64, 48)


def test_frame_to_ppm_converts_bgr_to_rgb():
    frame = np.zeros((2, 2, 3), dtype=np.uint8)
    frame[:, :] = (255, 0, 0)  # pure blue in BGR

    data = frame_to_ppm(frame, 2, 2)
    pixels = data.split(b"\n", 3)[3]

    assert pixels[:3] == bytes((0, 0, 255))
