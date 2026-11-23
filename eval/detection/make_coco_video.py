"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Turn a fixed random sample of COCO val2017 images into the frames of a 1080p test video. Each image is scaled to fit 1920x1080 (aspect
kept) and centred on a grey canvas; the mapping is saved so boxes can be moved back to original-image coordinates, where the COCO
ground truth lives. A video because the engine reads video, and feeding the same file to the Python pipelines gives identical pixels.

    python eval/detection/make_coco_video.py --n 1000
    eval/tools/in_container.sh eval/tools/encode_h264.sh png eval/work/coco/frames/%06d.png eval/work/coco/coco1000.mp4
"""
import argparse
import json
import random
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[2]
CANVAS_W, CANVAS_H = 1920, 1080
GREY = (114, 114, 114)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000, help="number of images (default 1000)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--coco", type=Path, default=REPO / "datasets" / "coco")
    ap.add_argument("--out", type=Path, default=REPO / "eval" / "work" / "coco")
    args = ap.parse_args()

    ann = json.loads((args.coco / "annotations" / "instances_val2017.json").read_text())
    images = sorted(ann["images"], key=lambda im: im["id"])
    chosen = sorted(random.Random(args.seed).sample(images, args.n), key=lambda im: im["id"])

    frames_dir = args.out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    mapping = []
    for k, im in enumerate(chosen):
        img = cv2.imread(str(args.coco / "val2017" / im["file_name"]), cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        scale = min(CANVAS_W / w, CANVAS_H / h)
        nw, nh = round(w * scale), round(h * scale)
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((CANVAS_H, CANVAS_W, 3), GREY, np.uint8)
        pad_x, pad_y = (CANVAS_W - nw) // 2, (CANVAS_H - nh) // 2
        canvas[pad_y:pad_y + nh, pad_x:pad_x + nw] = resized
        cv2.imwrite(str(frames_dir / f"{k:06d}.png"), canvas)
        mapping.append({"frame": k, "image_id": im["id"], "file_name": im["file_name"], "width": w, "height": h,
                        "scale": scale, "pad_x": pad_x, "pad_y": pad_y})
    (args.out / "frames.json").write_text(json.dumps({"canvas": [CANVAS_W, CANVAS_H], "frames": mapping}))
    print(f"wrote {len(mapping)} frames to {frames_dir}")


if __name__ == "__main__":
    main()
