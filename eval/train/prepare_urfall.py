"""V2-5 data: UR Fall (cam0 RGB, all 30 fall + 40 ADL sequences) -> mp4 -> pose dump of the DEPLOYED pipeline (YOLOv8n-pose in ds_engine + NvDCF).
Training on skeletons the real engine produced (not on the dataset's own Kinect joints, and not on a PyTorch re-run) keeps the model's inputs
the same as at run time, errors and track switches included (ROADMAP_V2 V2-5.1).

    eval/.venv/bin/python eval/train/prepare_urfall.py            # every zip that is in datasets/urfall/_zips and has no dump yet
    eval/.venv/bin/python eval/train/prepare_urfall.py --only fall-07

Per sequence it unzips the PNGs, encodes eval/work/urfall/<seq>.mp4 (NVENC, in the DeepStream container), deletes the PNGs, runs the engine to
write eval/work/urfall/<seq>.csv. Sequences are 640x480; the engine stretches them to 16:9 like everywhere else in this project.
Needs eval/work/pgie_pose_b1.txt (see eval/e2e/make_dumps.py) and the engine built.
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ZIPS = REPO / "datasets/urfall/_zips"
OUT = REPO / "eval/work/urfall"
TMP = REPO / "eval/work/urfall_png"


def run(cmd):
    return subprocess.run(["nice", "-n", "19", *map(str, cmd)], capture_output=True, text=True)


def process(zp: Path) -> str:
    seq = zp.name.replace("-cam0-rgb.zip", "")
    mp4, csv = OUT / f"{seq}.mp4", OUT / f"{seq}.csv"
    if csv.exists():
        return "have dump"
    t0 = time.time()
    if not mp4.exists():
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True)
        with zipfile.ZipFile(zp) as z:
            names = sorted(n for n in z.namelist() if n.lower().endswith(".png"))
            for n in names:
                (TMP / Path(n).name).write_bytes(z.read(n))
        m = re.search(r"-(\d+)\.png$", names[0])
        width, first = len(m.group(1)), int(m.group(1))
        r = run([REPO / "eval/tools/in_container.sh", "eval/tools/encode_h264.sh", "png",
                 f"eval/work/urfall_png/{seq}-cam0-rgb-%0{width}d.png", f"eval/work/urfall/{seq}.mp4", 30, 20000000, first])
        shutil.rmtree(TMP, ignore_errors=True)
        if r.returncode != 0 or not mp4.exists():
            mp4.unlink(missing_ok=True)
            return f"encode failed: {r.stderr[-300:]}"
    r = run([REPO / "eval/tools/in_container.sh", "engine/build/ds_engine", "--config", "/work/eval/work/pgie_pose_b1.txt",
             "--tracker-config", "/work/engine/configs/tracker_nvdcf.yml", "--camera", f"1=file:///work/eval/work/urfall/{seq}.mp4",
             "--dump-detections", f"/work/eval/work/urfall/{seq}.csv", "--fps-interval", "0"])
    if r.returncode != 0:
        csv.unlink(missing_ok=True)
        return f"engine failed: {r.stdout[-300:]}"
    return f"ok in {time.time() - t0:.0f}s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--wait", action="store_true", help="keep going until the download script has finished (datasets/urfall/_zips/dl_all_done.txt)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    only = {s for s in a.only.split(",") if s}
    while True:
        zips = [z for z in sorted(ZIPS.glob("*-cam0-rgb.zip")) if not only or z.name.replace("-cam0-rgb.zip", "") in only]
        todo = [z for z in zips if not (OUT / (z.name.replace("-cam0-rgb.zip", "") + ".csv")).exists()]
        for z in todo:
            print(z.name, process(z), flush=True)
        if not a.wait or (ZIPS / "dl_all_done.txt").exists() and not todo:
            break
        if not todo:
            time.sleep(20)


if __name__ == "__main__":
    main()
