"""Make a copy of engine/configs/pgie_yolov8n.txt for an evaluation run inside the container.

The copy has absolute container paths (/work/...) so it can live anywhere, the engine file of the right batch
size (N), and the detection settings you ask for. The original is never changed.

    python eval/tools/make_pgie_config.py --batch 1 --threshold 0.001 --out eval/work/pgie_b1_t0001.txt
    python eval/tools/make_pgie_config.py --batch 1 --nms-iou 0.7 --out eval/work/pgie_b1_iou07.txt

--set KEY=VALUE changes (or adds to [property]) any other key, e.g. --set scaling-filter=1.
--threshold = pre-cluster-threshold (detections under it are dropped before NMS), --nms-iou = nms-iou-threshold.
The paths use /work (where eval/tools/in_container.sh mounts the repo), not the host path.
"""
import argparse
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "engine" / "configs" / "pgie_yolov8n.txt"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, required=True, help="batch size = number of cameras of the run")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--nms-iou", type=float, default=None)
    ap.add_argument("--topk", type=int, default=None)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="set any other key of [property], e.g. --set scaling-filter=1 (repeatable)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    replace = {
        "onnx-file": "/work/engine/models/yolov8n.onnx",
        "model-engine-file": f"/work/engine/models/yolov8n.onnx_b{args.batch}_gpu0_fp16.engine",
        "labelfile-path": "/work/engine/configs/labels.txt",
        "custom-lib-path": "/work/engine/build/libnvds_yolov8_parser.so",
    }
    if args.threshold is not None:
        replace["pre-cluster-threshold"] = repr(args.threshold)
    if args.nms_iou is not None:
        replace["nms-iou-threshold"] = repr(args.nms_iou)
    if args.topk is not None:
        replace["topk"] = str(args.topk)

    seen = set()
    out = []
    for line in SRC.read_text().splitlines():
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
        raise SystemExit(f"keys not found in {SRC.name}: {sorted(missing)}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(out) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
