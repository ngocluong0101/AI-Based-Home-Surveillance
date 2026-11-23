"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Make a copy of an nvinfer config of engine/configs (pgie_yolov8n.txt, or --base pgie_yolov8n_pose.txt) for an evaluation run inside the
container: absolute container paths (/work/..., where in_container.sh mounts the repo), the engine file of batch size N, and the
detection settings you ask for. The original is never changed.

    python eval/tools/make_pgie_config.py --batch 1 --threshold 0.001 --out eval/work/pgie_b1_t0001.txt
    python eval/tools/make_pgie_config.py --batch 1 --nms-iou 0.7 --out eval/work/pgie_b1_iou07.txt

--threshold = pre-cluster-threshold (dropped before NMS), --nms-iou = nms-iou-threshold, --set KEY=VALUE changes or adds any other
[property] key, e.g. --set scaling-filter=1.
"""
import argparse
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONFIGS = REPO / "engine" / "configs"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="pgie_yolov8n.txt", help="config in engine/configs to copy (default pgie_yolov8n.txt; "
                    "pgie_yolov8n_pose.txt for the pose model)")
    ap.add_argument("--batch", type=int, required=True, help="batch size = number of cameras of the run")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--nms-iou", type=float, default=None)
    ap.add_argument("--topk", type=int, default=None)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="set any other key of [property], e.g. --set scaling-filter=1 (repeatable)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    src = CONFIGS / args.base
    onnx = next(re.match(r"onnx-file=(.*)", l).group(1) for l in src.read_text().splitlines() if l.startswith("onnx-file="))
    onnx_name = Path(onnx).name
    replace = {
        "onnx-file": f"/work/engine/models/{onnx_name}",
        "model-engine-file": f"/work/engine/models/{onnx_name}_b{args.batch}_gpu0_fp16.engine",
        "labelfile-path": "/work/engine/configs/labels.txt",
        "custom-lib-path": "/work/engine/build/libnvds_yolov8_parser.so",
    }
    if args.threshold is not None:
        replace["pre-cluster-threshold"] = repr(args.threshold)
    if args.nms_iou is not None:
        replace["nms-iou-threshold"] = repr(args.nms_iou)   # detection model only: the pose parser does its own NMS
    if args.topk is not None:
        replace["topk"] = str(args.topk)

    seen = set()
    out = []
    for line in src.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+)=", line)
        if m and m.group(1) in replace:
            out.append(f"{m.group(1)}={replace[m.group(1)]}")
            seen.add(m.group(1))
        else:
            out.append(line)
    for item in args.set:
        key, _, value = item.partition("=")
        if not value:
            raise SystemExit(f"--set needs KEY=VALUE, got {item!r}")
        if key in seen or any(re.match(rf"^{re.escape(key)}=", l) for l in out):
            out = [f"{key}={value}" if re.match(rf"^{re.escape(key)}=", l) else l for l in out]
        else:
            out.insert(out.index("[property]") + 1, f"{key}={value}")
        seen.add(key)
    missing = set(replace) - seen
    if missing:
        raise SystemExit(f"keys not found in {src.name}: {sorted(missing)}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(out) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
