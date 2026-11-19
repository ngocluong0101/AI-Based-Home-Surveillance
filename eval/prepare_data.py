"""Download and unpack the datasets V2-1 needs into datasets/ (COCO val2017 + person annotations, MOT17 train).

    python eval/prepare_data.py            # about 7 GB of downloads, ~10 GB unpacked
    python eval/prepare_data.py --only coco

Safe to run again: finished downloads are skipped, an interrupted one is resumed (curl -C -). COCO is fetched over
plain http because https://images.cocodataset.org did not answer from this machine. Read datasets/README.md for the
licenses first: the data may be used for evaluation here but must not be redistributed or committed.
"""
import argparse
import subprocess
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "datasets"
DOWNLOADS = DATA / "_downloads"

FILES = {
    "coco_annotations": ("http://images.cocodataset.org/annotations/annotations_trainval2017.zip", 252907541),
    "coco_val": ("http://images.cocodataset.org/zips/val2017.zip", 815585330),
    "mot17": ("https://motchallenge.net/data/MOT17.zip", 5860214001),
}


def fetch(name: str) -> Path:
    url, size = FILES[name]
    dest = DOWNLOADS / Path(url).name
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size == size:
        print(f"{dest.name}: already downloaded")
        return dest
    print(f"{dest.name}: downloading ({size / 1e6:.0f} MB)")
    subprocess.run(["curl", "-sS", "-L", "-C", "-", "-o", str(dest), url], check=True)
    if dest.stat().st_size != size:
        raise SystemExit(f"{dest.name}: size {dest.stat().st_size} != expected {size}; run again to resume")
    return dest


def unpack(zip_path: Path, target: Path, prefix: str = "") -> None:
    with zipfile.ZipFile(zip_path) as z:
        members = [m for m in z.namelist() if m.startswith(prefix)]
        target.mkdir(parents=True, exist_ok=True)
        z.extractall(target, members)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["coco", "mot17"], default=None)
    args = ap.parse_args()
    if args.only in (None, "coco"):
        unpack(fetch("coco_annotations"), DATA / "coco", "annotations/instances_val2017.json")
        unpack(fetch("coco_val"), DATA / "coco")
    if args.only in (None, "mot17"):
        unpack(fetch("mot17"), DATA / "mot17", "MOT17/train/")   # test sequences have no ground truth
    print("done")


if __name__ == "__main__":
    main()
