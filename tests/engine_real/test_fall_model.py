"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

The skeleton network (V2-5) inside the real engine: scores come back from the TensorRT worker and become FALL events, a camera
without the model falls back to the rules, and an ordinary clip raises nothing. Needs the GPU plus eval/work/pgie_pose_b1.txt,
a UR Fall clip in eval/work/urfall/ and engine/models/stgcn_fall.onnx; skipped when they are missing.
"""
import json
import subprocess

import pytest

from .rig import IMAGE, REPO

PGIE = REPO / "eval/work/pgie_pose_b1.txt"
MODEL = REPO / "engine/models/stgcn_fall.onnx"
FALL_CLIP, ADL_CLIP = REPO / "eval/work/urfall/fall-09.mp4", REPO / "eval/work/urfall/adl-05.mp4"

pytestmark = pytest.mark.gpu_engine


@pytest.fixture
def work():
    """a directory inside the repo (the container mounts the repo, not /tmp)"""
    d = REPO / "eval/work/test_fall_model"
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_engine(work, clip, settings, model=MODEL):
    for need in (PGIE, clip, MODEL):
        if not need.exists():
            pytest.skip(f"{need} is missing")
    def inside(p):          # the repo is mounted at /work, like eval/tools/in_container.sh (the pose config's paths assume it)
        return "/work/" + str(p.relative_to(REPO))
    ini = work / "cameras.ini"
    ini.write_text(f"[camera.1]\nuri=file://{inside(clip)}\nbehaviors=fall\n{settings}\n")
    ini.chmod(0o600)
    args = ["--config", inside(PGIE), "--tracker-config", "/work/engine/configs/tracker_nvdcf.yml", "--cameras-file", inside(ini), "--fps-interval", "0"]
    if model is not None:
        args += ["--fall-model", inside(model) if model.is_relative_to(REPO) else str(model)]
    r = subprocess.run(["docker", "run", "--rm", "--gpus", "all", "-u", "1000:1000", "-v", f"{REPO}:/work", "-w", "/work/engine",
                        "--entrypoint", "./build/ds_engine", IMAGE, *args], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-1500:]
    events = [json.loads(line[len("event "):]) for line in r.stdout.splitlines() if line.startswith("event ")]
    return [e for e in events if e["type"] == "FALL"], r.stdout + r.stderr


def test_the_network_reports_a_fall_without_waiting_for_the_person_to_stay_down(work):
    falls, out = run_engine(work, FALL_CLIP, "fall_net=1")
    assert "fall model:" in out and "errors=0" in out
    assert len(falls) == 1 and falls[0]["schema_version"] == 2
    # the rules (2 s of stillness by default) cannot report this early: the event comes within ~1 s of the impact
    assert falls[0]["occurred_at"] < falls[0]["timestamp"]     # occurred_at: the first window that scored high; timestamp: the third


def test_an_ordinary_clip_raises_nothing(work):
    falls, out = run_engine(work, ADL_CLIP, "fall_net=1")
    assert falls == [] and "dropped=0" in out


def test_a_missing_model_means_the_rules_decide(work):
    """fall_net=1 asks for the network only; with no model the engine must not leave the camera without a fall detector"""
    falls, out = run_engine(work, FALL_CLIP, "fall_net=1\nfall_still_s=1.0", model=work / "no_such.onnx")
    assert "WARNING: the fall model is not used" in out
    assert len(falls) == 1


def test_the_frame_ring_keeps_recent_frames_of_a_camera_with_a_fall_rule(work):
    """V2-6: --frame-ring-dir gives the VLM check its pictures: ~2 JPEGs a second per camera with a fall or fight rule, named by camera and time"""
    import re
    ring = work / "ring"
    ring.mkdir(exist_ok=True)
    for old in ring.glob("*"):
        old.unlink()
    ini = work / "cameras_ring.ini"
    ini.write_text(f"[camera.1]\nuri=file:///work/eval/work/urfall/fall-09.mp4\nbehaviors=fall\n")
    ini.chmod(0o600)
    for need in (PGIE, FALL_CLIP, ADL_CLIP):
        if not need.exists():
            pytest.skip(f"{need} is missing")
    r = subprocess.run(["docker", "run", "--rm", "--gpus", "all", "-u", "1000:1000", "-v", f"{REPO}:/work", "-w", "/work/engine", "--entrypoint", "./build/ds_engine",
                        IMAGE, "--config", "/work/eval/work/pgie_pose_b1.txt",
                        "--tracker-config", "/work/engine/configs/tracker_nvdcf.yml", "--cameras-file", "/work/eval/work/test_fall_model/cameras_ring.ini",
                        "--frame-ring-dir", "/work/eval/work/test_fall_model/ring", "--fps-interval", "0"], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-1500:]
    files = sorted(ring.glob("*.jpg"))
    assert files and all(re.fullmatch(r"ring_cam1_\d{13}\.jpg", f.name) for f in files)    
    assert 8 <= len(files) <= 16                                                              # a 6.4 s clip at 2 per second
    assert all(f.read_bytes()[:2] == b"\xff\xd8" for f in files)
    times = [int(f.stem.split("_")[2]) for f in files]
    assert all(0.4 <= (b - a) / 1000 <= 0.7 for a, b in zip(times, times[1:]))
    assert "frame ring: submitted=" in r.stdout
