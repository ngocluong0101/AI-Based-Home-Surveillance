"""The manager gives the engine an nvinfer config made for the number of cameras (one TensorRT engine
file per batch size), so that a third camera does not make nvinfer rebuild its engine at every start."""
import json
import logging
import stat
import sys
import time
from pathlib import Path

import pytest

from engine_manager.manager import CameraSpec, EngineManager, ManagerConfig, write_engine_config

FAKE = str(Path(__file__).with_name("fake_engine.py"))
REPO = Path(__file__).resolve().parents[2]
REAL_CONFIG = REPO / "engine" / "configs" / "pgie_yolov8n.txt"

SAMPLE = """\
# a comment that mentions model-engine-file=../nowhere.engine
[property]
gpu-id=0
onnx-file=../models/m.onnx
model-engine-file=../models/m.onnx_b2_gpu0_fp16.engine
labelfile-path=labels.txt
custom-lib-path=../build/lib.so
#onnx-file=../models/old.onnx
network-mode=2

[class-attrs-all]
pre-cluster-threshold=0.25
"""


def lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


@pytest.fixture
def base(tmp_path):
    cfg = tmp_path / "configs"
    cfg.mkdir()
    path = cfg / "pgie.txt"
    path.write_text(SAMPLE)
    return path


def test_the_engine_file_follows_the_number_of_cameras(base, tmp_path):
    out = tmp_path / "run"
    for n in (1, 3, 12):
        path, engine = write_engine_config(base, out, n)
        assert path == out / f"pgie_b{n}.txt"
        want = (tmp_path / "models" / f"m.onnx_b{n}_gpu0_fp16.engine").resolve()
        assert engine == want
        assert f"model-engine-file={want}" in lines(path)
        assert sum(l.startswith("model-engine-file=") for l in lines(path)) == 1


def test_paths_become_absolute_and_nothing_else_changes(base, tmp_path):
    path, _ = write_engine_config(base, tmp_path / "run", 2)
    got = lines(path)
    assert f"onnx-file={(tmp_path / 'models' / 'm.onnx').resolve()}" in got
    assert f"labelfile-path={(base.parent / 'labels.txt').resolve()}" in got
    assert f"custom-lib-path={(tmp_path / 'build' / 'lib.so').resolve()}" in got
    # comments (even ones that look like settings), other keys and sections are kept as they were
    assert got[0] == "# a comment that mentions model-engine-file=../nowhere.engine"
    assert "#onnx-file=../models/old.onnx" in got
    for same in ("[property]", "gpu-id=0", "network-mode=2", "[class-attrs-all]", "pre-cluster-threshold=0.25", ""):
        assert same in got
    assert len(got) == len(SAMPLE.splitlines())


def test_a_config_without_model_engine_file_gets_one(base, tmp_path):
    base.write_text(SAMPLE.replace("model-engine-file=../models/m.onnx_b2_gpu0_fp16.engine\n", ""))
    path, engine = write_engine_config(base, tmp_path / "run", 5)
    got = lines(path)
    assert got[got.index("[property]") + 1] == f"model-engine-file={engine}"
    assert engine.name == "m.onnx_b5_gpu0_fp16.engine"


def test_without_onnx_file_the_engine_is_left_alone(base, tmp_path):
    base.write_text("[property]\nmodel-engine-file=../models/fixed.engine\nlabelfile-path=l.txt\n")
    path, engine = write_engine_config(base, tmp_path / "run", 4)
    assert engine is None
    assert f"model-engine-file={(tmp_path / 'models' / 'fixed.engine').resolve()}" in lines(path)
    base.write_text("[property]\nlabelfile-path=l.txt\n")
    path, engine = write_engine_config(base, tmp_path / "run", 4)
    assert engine is None and not any(l.startswith("model-engine-file") for l in lines(path))


def test_the_copy_is_readable_by_a_container_and_leaves_no_temp_files(base, tmp_path):
    out = tmp_path / "run"
    path, _ = write_engine_config(base, out, 2)
    write_engine_config(base, out, 2)   # again, as at every start
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert sorted(p.name for p in out.iterdir()) == ["pgie_b2.txt"]


def test_the_real_config_of_the_repo(tmp_path):
    path, engine = write_engine_config(REAL_CONFIG, tmp_path, 7)
    models = REPO / "engine" / "models"
    assert engine == models / "yolov8n.onnx_b7_gpu0_fp16.engine"
    got = lines(path)
    assert f"onnx-file={models / 'yolov8n.onnx'}" in got
    assert f"custom-lib-path={REPO / 'engine' / 'build' / 'libnvds_yolov8_parser.so'}" in got
    assert f"labelfile-path={REPO / 'engine' / 'configs' / 'labels.txt'}" in got
    assert not any(l.startswith(("onnx-file=..", "labelfile-path=labels", "custom-lib-path=..")) for l in got)


# ---- through the manager ----

def wait_for(pred, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = pred()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture
def manager(tmp_path, monkeypatch, base):
    rec = tmp_path / "rec"
    rec.mkdir()
    monkeypatch.setenv("FAKE_ENGINE_DIR", str(rec))
    monkeypatch.setenv("FAKE_MODE", "run")
    made = []

    def factory(engine_config=base):
        m = EngineManager(ManagerConfig(command=[sys.executable, FAKE], engine_args=["--tracker-config", "t.yml"],
                                        runtime_dir=tmp_path / "run", engine_config=engine_config,
                                        stop_timeout=3.0, backoff_base=0.2, backoff_max=0.8, poll_interval=0.05))
        m.start()
        made.append(m)
        return m

    factory.rec = rec
    yield factory
    for m in made:
        m.stop()


def runs(rec):
    return [json.loads(p.read_text()) for p in sorted(rec.glob("run-*.json"), key=lambda p: int(p.stem[4:]))]


def cams(*ids):
    return [CameraSpec(i, f"rtsp://u:p@10.0.0.{i}/s") for i in ids]


def test_every_start_gets_the_config_of_its_camera_count(manager, tmp_path):
    m = manager()
    m.set_cameras(cams(1, 2))
    wait_for(lambda: len(runs(manager.rec)) == 1)
    m.set_cameras(cams(1, 2, 3))
    wait_for(lambda: len(runs(manager.rec)) == 2)

    for run, n in zip(runs(manager.rec), (2, 3)):
        argv = run["argv"]
        assert argv.count("--config") == 1
        config = Path(argv[argv.index("--config") + 1])
        assert config == tmp_path / "run" / f"pgie_b{n}.txt"
        assert f"model-engine-file={(tmp_path / 'models' / f'm.onnx_b{n}_gpu0_fp16.engine').resolve()}" in lines(config)
        assert argv[argv.index("--tracker-config") + 1] == "t.yml"   # the other arguments are untouched


def test_a_missing_engine_is_announced_once_per_start(manager, tmp_path, caplog):
    models = tmp_path / "models"
    models.mkdir()
    (models / "m.onnx_b2_gpu0_fp16.engine").write_bytes(b"x")   # there is one for 2 cameras, not for 3
    caplog.set_level(logging.WARNING, logger="engine_manager.manager")
    m = manager()
    m.set_cameras(cams(1, 2))
    wait_for(lambda: len(runs(manager.rec)) == 1)
    assert not [r for r in caplog.records if "TensorRT engine" in r.message]
    m.set_cameras(cams(1, 2, 3))
    wait_for(lambda: len(runs(manager.rec)) == 2)
    warned = [r.message for r in caplog.records if "TensorRT engine" in r.message]
    assert len(warned) == 1 and "3 camera" in warned[0] and "b3" in warned[0]


def test_a_missing_config_file_is_a_failed_start_not_a_crash(manager, tmp_path):
    m = manager(engine_config=tmp_path / "configs" / "nope.txt")
    m.set_cameras(cams(1))
    wait_for(lambda: m.status()["state"] == "backoff")
    st = m.status()
    assert "could not start" in st["last_error"] and st["pid"] is None
    assert not list(manager.rec.glob("run-*.json"))


def test_from_env_hands_the_config_to_the_manager_not_to_the_arguments(monkeypatch, tmp_path):
    from src import config

    monkeypatch.setattr(config, "ENGINE_CONFIG", str(tmp_path / "x.txt"))
    cfg = ManagerConfig.from_env()
    assert cfg.engine_config == (tmp_path / "x.txt").resolve()
    assert "--config" not in cfg.engine_args and "--tracker-config" in cfg.engine_args
