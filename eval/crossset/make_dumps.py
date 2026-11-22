"""Stage 1 of the cross-dataset evaluation: run the real engine (pose model, NvDCF) over the clips of a public dataset and keep one dump per clip.

Four clips go through ONE engine run (four file sources, batch 4: the TensorRT engine of that batch exists), and the dump is split by source_id
afterwards, so a dataset of 350 clips takes a quarter of the time of one engine start per clip. A clip that already has a dump is skipped.

    eval/.venv/bin/python eval/crossset/make_dumps.py --set airtlab
    eval/.venv/bin/python eval/crossset/make_dumps.py --set airtlab --limit 8        # a quick try

Needs eval/work/pgie_pose_b4.txt: `python eval/tools/make_pgie_config.py --base pgie_yolov8n_pose.txt --batch 4 --out eval/work/pgie_pose_b4.txt`.
Output: eval/work/crossset/<set>/<clip name>.csv, in the format of eval/common/ds_dump.py (source_id column kept at 0).
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from eval.crossset import datasets  # noqa: E402

BATCH = 4
PGIE = "eval/work/pgie_pose_b4.txt"


def clips_of(name: str):
    if name == "airtlab":
        return datasets.airtlab_clips()
    if name == "gmdcsa24":
        return datasets.gmdcsa_clips()
    raise SystemExit(f"unknown dataset '{name}'")


def split_dump(whole: Path, clips: list, out_dir: Path) -> None:
    """The dump of a run with several sources -> one dump per clip, in the format of a one-source run (source_id 0)."""
    df = pd.read_csv(whole, dtype=str, keep_default_na=False)
    for i, c in enumerate(clips):
        part = df[df.source_id == str(i)].copy()
        part["source_id"] = "0"
        part.to_csv(out_dir / f"{c.name}.csv", index=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, choices=["airtlab", "gmdcsa24"])
    ap.add_argument("--limit", type=int, default=0, help="only the first N clips (a quick try)")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    out_dir = datasets.WORK / a.set
    out_dir.mkdir(parents=True, exist_ok=True)
    todo = [c for c in clips_of(a.set) if a.force or not (out_dir / f"{c.name}.csv").exists()]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{a.set}: {len(todo)} clips to do, {BATCH} per engine run")
    t_all = time.time()
    for i in range(0, len(todo), BATCH):
        group = todo[i:i + BATCH]
        group_pad = group + [group[-1]] * (BATCH - len(group))   # the engine needs exactly BATCH sources for the b4 engine: the last clip repeats
        whole = out_dir / f".run_{i}.csv"
        cmd = ["nice", "-n", "19", str(REPO / "eval/tools/in_container.sh"), "engine/build/ds_engine", "--config", f"/work/{PGIE}",
               "--tracker-config", "/work/engine/configs/tracker_nvdcf.yml", "--dump-detections", f"/work/{whole.relative_to(REPO)}",
               "--fps-interval", "0"]
        for n, c in enumerate(group_pad, 1):
            cmd += ["--camera", f"{n}=file:///work/{c.path.relative_to(REPO)}"]
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not whole.exists():
            print(f"run {i}: exit {r.returncode}: {r.stdout[-300:]}", file=sys.stderr)
            whole.unlink(missing_ok=True)
            continue
        split_dump(whole, group, out_dir)
        whole.unlink()
        done = min(i + BATCH, len(todo))
        print(f"{done}/{len(todo)} clips ({time.time() - t0:.0f}s this run, {time.time() - t_all:.0f}s in all)", flush=True)


if __name__ == "__main__":
    main()
