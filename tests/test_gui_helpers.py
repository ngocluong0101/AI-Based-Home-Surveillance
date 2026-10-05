from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from src.events import PersonDetectedEvent
from src.gui.helpers import (
    SOURCE_FILE,
    SOURCE_STREAM,
    SOURCE_WEBCAM,
    SnapshotInfo,
    classify_source,
    format_event_row,
    format_snapshot_row,
    frame_to_ppm,
    list_snapshots,
    load_image_ppm,
    validate_source,
    validate_telegram_settings,
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


def test_format_event_row_with_snapshot():
    event = PersonDetectedEvent("PERSON_DETECTED", 3, 0.913, datetime(2026, 1, 1, 22, 30, 15))

    row = format_event_row(event, Path("data/snapshots/PERSON_DETECTED_id3_20260101_223015.jpg"))

    assert row == ("22:30:15", "ID 3", "0.91", "PERSON_DETECTED_id3_20260101_223015.jpg")


def test_format_event_row_without_snapshot_shows_dash():
    event = PersonDetectedEvent("PERSON_DETECTED", 12, 0.5, datetime(2026, 1, 1, 7, 5, 9))

    assert format_event_row(event, None) == ("07:05:09", "ID 12", "0.50", "—")


def test_list_snapshots_missing_directory_is_empty(tmp_path):
    assert list_snapshots(tmp_path / "does-not-exist") == []


def test_list_snapshots_keeps_only_images_newest_first(tmp_path):
    import os

    old = tmp_path / "old.jpg"
    new = tmp_path / "new.JPG"
    notes = tmp_path / "notes.txt"
    for path in (old, new, notes):
        path.write_bytes(b"x")
    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))
    (tmp_path / "subdir.jpg").mkdir()

    names = [info.path.name for info in list_snapshots(tmp_path)]

    assert names == ["new.JPG", "old.jpg"]


def test_list_snapshots_reports_size_and_mtime(tmp_path):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"12345")

    (info,) = list_snapshots(tmp_path)

    assert info.size_bytes == 5
    assert info.saved_at.timestamp() == pytest.approx(path.stat().st_mtime)


@pytest.mark.parametrize(
    "size, expected_size",
    [(0, "1 KB"), (2048, "2 KB"), (3 * 1024 * 1024 + 512 * 1024, "3.5 MB")],
)
def test_format_snapshot_row(tmp_path, size, expected_size):
    info = SnapshotInfo(tmp_path / "PERSON_DETECTED_id3.jpg", datetime(2026, 1, 1, 9, 5, 0), size)

    assert format_snapshot_row(info) == ("PERSON_DETECTED_id3.jpg", "2026-01-01 09:05:00", expected_size)


def test_load_image_ppm_fits_the_box(tmp_path):
    import cv2

    path = tmp_path / "shot.jpg"
    cv2.imwrite(str(path), np.zeros((100, 200, 3), dtype=np.uint8))

    assert _ppm_size(load_image_ppm(path, 400, 400)) == (400, 200)


def test_load_image_ppm_rejects_missing_or_corrupt_file(tmp_path):
    with pytest.raises(ValueError):
        load_image_ppm(tmp_path / "gone.jpg", 100, 100)

    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image")
    with pytest.raises(ValueError):
        load_image_ppm(broken, 100, 100)


def test_telegram_settings_accept_valid_values_and_strip_them():
    token, chat_id = validate_telegram_settings(True, " 123456:ABC-def_1 ", " -100123 ")

    assert (token, chat_id) == ("123456:ABC-def_1", "-100123")


def test_telegram_settings_disabled_may_be_left_blank():
    assert validate_telegram_settings(False, "", "") == ("", "")


@pytest.mark.parametrize(
    "token",
    ["abc", "123456", "123456:", "12x:ABC", "123456:AB C"],
)
def test_telegram_token_shape_is_checked(token):
    with pytest.raises(ValueError, match="bot token"):
        validate_telegram_settings(False, token, "")


@pytest.mark.parametrize("chat_id", ["@mychannel", "12.5", "--1", "1 2"])
def test_telegram_chat_id_must_be_a_number(chat_id):
    with pytest.raises(ValueError, match="chat ID"):
        validate_telegram_settings(False, "", chat_id)


def test_enabling_telegram_requires_token_and_chat_id():
    with pytest.raises(ValueError, match="fill in both"):
        validate_telegram_settings(True, "123:abc", "")
    with pytest.raises(ValueError, match="fill in both"):
        validate_telegram_settings(True, "", "123")
