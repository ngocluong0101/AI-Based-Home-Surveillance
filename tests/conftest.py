import cv2
import numpy as np
import pytest

FRAME_COUNT = 10
FRAME_SIZE = (64, 48)  # (width, height)


@pytest.fixture
def frame_count():
    """Number of frames in the `sample_video` fixture."""
    return FRAME_COUNT


@pytest.fixture
def sample_video(tmp_path):
    """A tiny synthetic video (10 frames, 64x48) so tests need no camera or model."""
    path = tmp_path / "sample.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10.0, FRAME_SIZE)
    assert writer.isOpened(), "OpenCV cannot write MJPG .avi files in this environment"
    for index in range(FRAME_COUNT):
        writer.write(np.full((FRAME_SIZE[1], FRAME_SIZE[0], 3), index * 20, dtype=np.uint8))
    writer.release()
    return path
