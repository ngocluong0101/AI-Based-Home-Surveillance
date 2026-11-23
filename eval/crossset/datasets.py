"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Where the public datasets of the cross-dataset evaluation are. Nothing here downloads anything: files live in
datasets/_downloads/<name>/ (never committed; read each licence first, see datasets/README.md).

AIRTLab (Bianculli et al., Data in Brief 33, 2020, doi:10.1016/j.dib.2020.106587): 350 clips of 2-4 actors in ONE room, filmed by two
cameras at once; 230 violent clips and 120 non-violent ones that look like fights to a detector (hugs, high fives, claps...).
Released "for research and educational purposes".
"""
import csv
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOWNLOADS = REPO / "datasets" / "_downloads"
WORK = REPO / "eval" / "work" / "crossset"

AIRTLAB_ROOT = DOWNLOADS / "airtlab" / "repo" / "violence-detection-dataset"


@dataclass(frozen=True)
class Clip:
    dataset: str
    name: str            # unique inside the dataset, e.g. "violent_cam1_12"
    path: Path
    label: str           # "violent" | "non-violent" | "fall" | "adl"
    camera: str = ""
    number: int = 0


def airtlab_clips() -> list[Clip]:
    out = []
    for label in ("violent", "non-violent"):
        for cam in ("cam1", "cam2"):
            d = AIRTLAB_ROOT / label / cam
            for p in sorted(d.glob("*.mp4"), key=lambda q: int(q.stem) if q.stem.isdigit() else 0):
                out.append(Clip("airtlab", f"{label}_{cam}_{p.stem}", p, label, cam, int(p.stem) if p.stem.isdigit() else 0))
    return out


def airtlab_actions() -> dict[tuple[str, int], list[str]]:
    """(label, clip number) -> the actions the authors list for that clip (the same list for both cameras).
    The files are "N.mp4;action,action" with a header line, ';' between the clip and its actions."""
    res: dict[tuple[str, int], list[str]] = {}
    for label, fname in (("violent", "violent-action-classes.csv"), ("non-violent", "nonviolent-action-classes.csv")):
        p = AIRTLAB_ROOT / fname
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            name, _, rest = line.partition(";")
            stem = name.strip().removesuffix(".mp4")
            if stem.isdigit():
                res[(label, int(stem))] = [c.strip() for c in rest.split(",") if c.strip()]
    return res


GMDCSA_ROOT = next(iter(sorted((DOWNLOADS / "gmdcsa24" / "data").glob("*GMDCSA24*"))), DOWNLOADS / "gmdcsa24" / "data")


def gmdcsa_clips() -> list[Clip]:
    """GMDCSA-24 (Alam et al., Data in Brief 2024; CC BY 4.0 on Zenodo, MIT on GitHub): 81 falls + 79 daily activities, 4 actors, 3 home setups,
    clips of 5-10 s; a fall clip is a clip with a fall in it, no frame-level times. About a quarter of the clips are 15 fps, the rest ~30 fps."""
    out = []
    for subject in sorted(GMDCSA_ROOT.glob("Subject *")):
        n = int(subject.name.split()[-1])
        for kind, label in (("Fall", "fall"), ("ADL", "adl")):
            for p in sorted((subject / kind).glob("*.mp4")):
                out.append(Clip("gmdcsa24", f"s{n}_{label}_{p.stem}", p, label, f"subject{n}", int(p.stem) if p.stem.isdigit() else 0))
    return out


def gmdcsa_descriptions() -> dict[str, str]:
    """clip name -> the authors' one-line description (e.g. "Falling from sitting on the chair to the ground. Left side Fall. Full body not visible.")."""
    res = {}
    for subject in sorted(GMDCSA_ROOT.glob("Subject *")):
        n = int(subject.name.split()[-1])
        for kind, label in (("Fall", "fall"), ("ADL", "adl")):
            f = subject / f"{kind}.csv"
            if not f.exists():
                continue
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
                parts = [x.strip() for x in line.split(",", 2)]
                if len(parts) == 3 and parts[0].endswith(".mp4"):
                    res[f"s{n}_{label}_{parts[0].removesuffix('.mp4')}"] = parts[2]
    return res
