#!/bin/bash
# check_camera_settings.sh - does the engine apply the per-camera settings of the cameras file?
#
# An offline test tool, not part of the product. It runs the real engine (DeepStream container, GPU) on four
# cameras that all play the same video, each with one setting of its own:
#
#   camera 1  nothing set            the reference
#   camera 2  conf_threshold=0.6     fewer objects, tracks and events than camera 1
#   camera 3  ai_enabled=false       no object, no event, no "people" in the heartbeat
#   camera 4  absence_timeout=0.2    more events than camera 1 (the same people count as new sooner)
#
# Usage, from anywhere:  engine/tools/check_camera_settings.sh [VIDEO]
# The default video is data/videos/sample_1080p_h264.mp4. Needs engine/build/ds_engine (make CUDA_VER=12.6 in the
# container), the TensorRT engine for 4 cameras (built on the first start, ~150 s) and a free GPU: do not run it
# next to the soak test. Output goes to a temporary directory that is printed at the end.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
VIDEO=$(readlink -f "${1:-$REPO/data/videos/sample_1080p_h264.mp4}")
OUT=$(mktemp -d)
IMAGE=nvcr.io/nvidia/deepstream:7.1-triton-multiarch
PORT=${PORT:-18098}

cat > "$OUT/cameras.ini" <<EOF
[camera.1]
uri=file://$VIDEO

[camera.2]
uri=file://$VIDEO
conf_threshold=0.6

[camera.3]
uri=file://$VIDEO
ai_enabled=false

[camera.4]
uri=file://$VIDEO
absence_timeout=0.2
EOF
chmod 600 "$OUT/cameras.ini"

# the nvinfer config for 4 cameras, made the way the engine manager makes it (absolute paths, engine file for batch 4)
PGIE=$(cd "$REPO" && python3 -c "
from pathlib import Path
from engine_manager.manager import write_engine_config
cfg, _engine = write_engine_config(Path('engine/configs/pgie_yolov8n.txt').resolve(), Path('$OUT').resolve(), 4)
print(cfg)
") || exit 1

python3 "$REPO/engine/tools/fake_receiver.py" --port "$PORT" --log "$OUT/events.jsonl" \
    --heartbeat-log "$OUT/heartbeats.jsonl" > "$OUT/receiver.out" 2>&1 &
RECEIVER=$!
trap 'kill $RECEIVER 2>/dev/null' EXIT
sleep 1

docker run --rm --gpus all -u 1000:1000 --network host -v "$REPO":"$REPO" -v "$OUT":"$OUT" -v "$(dirname "$VIDEO")":"$(dirname "$VIDEO")":ro \
    -w "$REPO/engine" --entrypoint ./build/ds_engine "$IMAGE" \
    --config "$PGIE" --tracker-config configs/tracker_nvdcf.yml --cameras-file "$OUT/cameras.ini" \
    --fps-interval 0 --backend-url "http://127.0.0.1:$PORT/internal/events" --spool-file "$OUT/spool.jsonl" \
    --heartbeat-url "http://127.0.0.1:$PORT/internal/heartbeat" --heartbeat-interval 1 \
    > "$OUT/engine.out" 2> "$OUT/engine.err"
echo "engine exit code: $?"
kill $RECEIVER 2>/dev/null

echo "=== settings as the engine understood them"
grep "^source_id=.*scheme=" "$OUT/engine.out"
echo "=== per-camera result"
grep -E "^source_id=[0-9]+ camera_id=[0-9]+ frames=" "$OUT/engine.out"
echo "=== events received per camera"
python3 - "$OUT/events.jsonl" <<'PY'
import collections, json, sys
counts = collections.Counter(json.loads(l)["camera_id"] for l in open(sys.argv[1]) if l.strip())
print(dict(sorted(counts.items())))
PY
echo "=== people in the first heartbeats (camera 3 has AI off: absent = unknown)"
python3 - "$OUT/heartbeats.jsonl" <<'PY'
import json, sys
beats = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
for b in beats[:3]:
    print({c["camera_id"]: c.get("people", "absent") for c in b["cameras"]})
print(len(beats), "heartbeats")
PY
echo "(files in $OUT)"
