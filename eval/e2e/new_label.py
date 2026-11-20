"""Start a label file for a clip you recorded: fills in fps / size / duration from the video, leaves the events for you.

    eval/.venv/bin/python eval/e2e/new_label.py datasets/own/clips/livingroom_ann_03.mp4 --scene livingroom --person ann --split train

Then open eval/labels/<clip>.json and write the events (times in seconds on the clip's own timeline: step the video frame by
frame in mpv/VLC; `impact_s` is the first frame the body touches the floor). `eval/e2e/check_labels.py` validates the result.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.e2e import labels  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("--scene", required=True)
    ap.add_argument("--person", required=True, help="who is in it; several people joined with '+' (ann+bob)")
    ap.add_argument("--split", required=True, choices=labels.SPLITS)
    ap.add_argument("--tags", default="", help="hard-case tags of a clip without incident, comma list: sit_hard,lie_sofa,pick_up,hug")
    a = ap.parse_args()
    cap = cv2.VideoCapture(str(a.video))
    fps, n = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if not fps or not n:
        sys.exit(f"cannot read {a.video}")
    out = labels.LABELS_DIR / f"{a.video.stem}.json"
    if out.exists():
        sys.exit(f"{out} exists; not overwriting")
    doc = dict(clip=a.video.name, fps=round(fps, 3), width=w, height=h, duration_s=round(n / fps, 3), scene=a.scene, person=a.person, split=a.split,
               has_incident=False, tags=[t for t in a.tags.split(",") if t], lie_zones=[], restricted_zones=[], events=[])
    out.write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote {out.relative_to(REPO)}: {w}x{h} {fps:.2f} fps {n / fps:.1f} s. Add events, set has_incident, then run check_labels.py")


if __name__ == "__main__":
    main()
