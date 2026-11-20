"""Stage 1 of the end-to-end evaluation: run the real engine (pose model, NvDCF) over each labelled clip and keep its dump.
One clip at a time, `nice`d: the GPU is shared with the V1 stack on this laptop. A clip that already has a dump is skipped.

    eval/.venv/bin/python eval/e2e/make_dumps.py --splits val,test
    eval/.venv/bin/python eval/e2e/make_dumps.py --only hallway_03 --force

Clips are looked up in datasets/own/clips/ (your own recordings), then eval/work/urfall/ (public UR Fall clips, split 'dev').
Needs eval/work/pgie_pose_b1.txt: `eval/.venv/bin/python eval/tools/make_pgie_config.py --base pgie_yolov8n_pose.txt --batch 1 --out eval/work/pgie_pose_b1.txt`.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from eval.e2e import labels  # noqa: E402

CLIP_DIRS = [REPO / "datasets/own/clips", REPO / "eval/work/urfall"]
DUMP_DIR = REPO / "eval/work/dumps"
PGIE = "eval/work/pgie_pose_b1.txt"


def find_clip(name: str) -> Path | None:
    for d in CLIP_DIRS:
        if (d / name).exists():
            return d / name
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="train,val,test,dev")
    ap.add_argument("--only", default="", help="comma list of clip names (without extension)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    only = {s for s in a.only.split(",") if s}
    for c in labels.load_dir(splits=set(a.splits.split(","))):
        stem = Path(c.clip).stem
        if only and stem not in only:
            continue
        out = DUMP_DIR / f"{stem}.csv"
        src = find_clip(c.clip)
        if src is None:
            print(f"{stem}: clip file not found, skipped")
            continue
        if out.exists() and not a.force:
            print(f"{stem}: dump exists")
            continue
        rel = src.relative_to(REPO)
        cmd = ["nice", "-n", "19", str(REPO / "eval/tools/in_container.sh"), "engine/build/ds_engine", "--config", f"/work/{PGIE}",
               "--tracker-config", "/work/engine/configs/tracker_nvdcf.yml", "--camera", f"1=file:///work/{rel}",
               "--dump-detections", f"/work/{out.relative_to(REPO)}", "--fps-interval", "0"]
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True)
        print(f"{stem}: exit {r.returncode} in {time.time() - t0:.0f}s")
        if r.returncode != 0:
            print(r.stdout[-800:], r.stderr[-800:])
            out.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
