"""Export YOLOv8 weights (.pt) to ONNX with a dynamic batch dimension, for DeepStream's nvinfer.

Offline tool: run it once on the dev machine. It is not part of the frame-processing path.

    python engine/tools/export_yolov8_onnx.py
    python engine/tools/export_yolov8_onnx.py --weights yolov8n.pt --output engine/models/yolov8n.onnx

The exported network takes `images` [batch, 3, imgsz, imgsz] (RGB, 0..1) and returns `output0`
[batch, 4 + num_classes, anchors]: for each anchor the box (cx, cy, w, h in input pixels) followed by
one score per class. For yolov8n at 640 that is [batch, 84, 8400]. NMS is not part of the model; the
custom parser in engine/parser/ reads this tensor and nvinfer runs the NMS.
"""
import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--weights", type=Path, default=REPO_ROOT / "yolov8n.pt")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "engine" / "models" / "yolov8n.onnx")
    parser.add_argument("--imgsz", type=int, default=640, help="square network input size (default 640)")
    parser.add_argument("--opset", type=int, default=17)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.weights.is_file():
        print(f"Weights not found: {args.weights}", file=sys.stderr)
        return 1

    from ultralytics import YOLO

    model = YOLO(str(args.weights))
    # dynamic=True makes the batch dimension variable, so nvinfer can build an engine for batch N.
    exported = Path(model.export(format="onnx", imgsz=args.imgsz, opset=args.opset, dynamic=True, simplify=True))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(exported), str(args.output))
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
