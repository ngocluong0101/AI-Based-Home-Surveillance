"""Export YOLOv8 pose weights (.pt) to ONNX with a dynamic batch dimension, for DeepStream's nvinfer.

Offline tool: run it once on the dev machine. It is not part of the frame-processing path.

    python engine/tools/export_yolov8_pose_onnx.py                        # yolov8n-pose.pt, 640x384 (16:9)
    python engine/tools/export_yolov8_pose_onnx.py --height 640 --width 640

The exported network takes `images` [batch, 3, H, W] (RGB, 0..1) and returns `output0` [batch, 56, anchors]:
for each anchor cx, cy, w, h (input pixels), the person score, then 17 keypoints as x, y, visibility
(x and y in input pixels, visibility a probability). For 640x384 that is [batch, 56, 5040]. NMS is not part
of the model: the custom parser in engine/parser/ reads this tensor and does the NMS itself.

The default input is rectangular (640 wide, 384 high) because V2-1 measured that a 16:9 camera loses people
when it is squeezed into a 640x640 square with black bars (see ROADMAP_V2_NHAT_KY.md, 2.4). Use --height 640
for a square network. The weights are downloaded by Ultralytics on first use if the file is missing.
"""
import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weights", type=Path, default=REPO_ROOT / "yolov8n-pose.pt")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "engine" / "models" / "yolov8n-pose.onnx")
    parser.add_argument("--height", type=int, default=384, help="network input height (default 384)")
    parser.add_argument("--width", type=int, default=640, help="network input width (default 640)")
    parser.add_argument("--opset", type=int, default=17)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    from ultralytics import YOLO

    model = YOLO(str(args.weights))   # downloads yolov8n-pose.pt when it is not there
    exported = Path(model.export(format="onnx", imgsz=[args.height, args.width], opset=args.opset,
                                 dynamic=True, simplify=True))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(exported), str(args.output))
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
