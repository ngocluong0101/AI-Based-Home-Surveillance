"""Fixtures for the tests that run the real engine on the GPU.

They are skipped unless RUN_GPU_TESTS=1: they need Docker with the NVIDIA runtime, a built engine/build/ds_engine
and the GPU to themselves (about 15 minutes for the whole directory), and they must not run by accident next to
a soak test or a benchmark. Run them with:   RUN_GPU_TESTS=1 pytest tests/engine_real -v
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from .rig import REPO, EngineRig

# A video that lasts longer than any of the tests, made once by joining copies of the sample (not re-encoded).
LOOP_VIDEO = REPO / "data" / "videos" / "engine_test_loop.mp4"
LOOP_HOURS = 0.2


def pytest_collection_modifyitems(config, items):
    reason = None
    if os.environ.get("RUN_GPU_TESTS") != "1":
        reason = "set RUN_GPU_TESTS=1 to run the real engine on the GPU"
    elif shutil.which("docker") is None or shutil.which("nvidia-smi") is None:
        reason = "needs docker and an NVIDIA GPU"
    elif not (REPO / "engine" / "build" / "ds_engine").exists():
        reason = "engine/build/ds_engine is not built (make CUDA_VER=12.6 in the DeepStream container)"
    if reason:
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if "gpu_engine" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def video() -> Path:
    if not LOOP_VIDEO.exists():
        from deploy.soak import make_video

        make_video(LOOP_HOURS, LOOP_VIDEO)
    return LOOP_VIDEO


@pytest.fixture
def rig(video):
    """Make engines; every one is stopped and removed at the end of the test, whatever happened."""
    made: list[EngineRig] = []

    def factory(slots: int, cameras: dict[int, list[str]], stream: bool = False) -> EngineRig:
        r = EngineRig(video, slots, cameras, stream)
        made.append(r)
        return r

    yield factory
    for r in made:
        r.close()
