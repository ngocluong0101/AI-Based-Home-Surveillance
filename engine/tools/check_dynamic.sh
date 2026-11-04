#!/bin/bash
# check_dynamic.sh - can cameras be added to and taken out of the RUNNING engine (SIGHUP, --max-cameras)?
#
# An offline test tool, not part of the product. It starts the real engine (DeepStream container, GPU) with room
# for 4 cameras and changes the cameras file while it runs. Every camera plays the same long video.
#
#   A  cameras 1, 2                     the engine starts
#   B  + camera 3                       3 starts producing events, 1 and 2 do not stop
#   C  + camera 4                       the engine is full
#   D  - camera 1                       1 stops, the others go on
#   E  + camera 5                       takes the free slot
#   F  + camera 6                       refused: no free place (nothing else changes)
#
# STREAM=1 also publishes every camera (--stream-url) to a MediaMTX container started here, and checks which
# paths the media server saw; the default is detection only.
# Usage, from anywhere:  [STREAM=1] engine/tools/check_dynamic.sh [VIDEO]       (default: data/videos/soak_loop.mp4)
# Needs engine/build/ds_engine, the TensorRT engine for 4 cameras and a free GPU. Prints a table; the files stay
# in a temporary directory that is printed at the end.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
VIDEO=$(readlink -f "${1:-$REPO/data/videos/soak_loop.mp4}")
OUT=$(mktemp -d)
IMAGE=nvcr.io/nvidia/deepstream:7.1-triton-multiarch
PORT=${PORT:-18097}
NAME=check-dynamic-$$
MTX=check-dynamic-mtx-$$
WINDOW=${WINDOW:-10}
MAX=4

cameras() {   # arguments: the camera ids that should be in the file
    local f="$OUT/cameras.new" id
    : > "$f"
    for id in "$@"; do
        printf '[camera.%s]\nuri=file://%s\n\n' "$id" "$VIDEO" >> "$f"
    done
    chmod 600 "$f"
    mv "$f" "$OUT/cameras.ini"
}

counts() {   # events received so far: "c1 c2 c3 c4 c5 c6"
    python3 - "$OUT/events.jsonl" <<'PY'
import collections, json, sys
c = collections.Counter(json.loads(l)["camera_id"] for l in open(sys.argv[1]) if l.strip())
print(*[c[i] for i in range(1, 7)])
PY
}

in_heartbeat() {   # the camera ids in the last heartbeat
    python3 - "$OUT/heartbeats.jsonl" <<'PY'
import json, sys
beats = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
print(",".join(str(c["camera_id"]) for c in beats[-1]["cameras"]) if beats else "-")
PY
}

window() {   # $1 = label: WINDOW seconds, then the events per camera in them
    local before after d=""
    before=($(counts))
    sleep "$WINDOW"
    after=($(counts))
    for i in 0 1 2 3 4 5; do d="$d c$((i + 1))=$((after[i] - before[i]))"; done
    printf '%-28s events in %ss:%s   heartbeat cameras: %s\n' "$1" "$WINDOW" "$d" "$(in_heartbeat)"
}

hup() { docker kill --signal=HUP "$NAME" > /dev/null; sleep 3; }

cameras 1 2
PGIE=$(cd "$REPO" && python3 -c "
from pathlib import Path
from engine_manager.manager import write_engine_config
cfg, _engine = write_engine_config(Path('engine/configs/pgie_yolov8n.txt').resolve(), Path('$OUT').resolve(), $MAX)
print(cfg)
") || exit 1

python3 "$REPO/engine/tools/fake_receiver.py" --port "$PORT" --log "$OUT/events.jsonl" \
    --heartbeat-log "$OUT/heartbeats.jsonl" > "$OUT/receiver.out" 2>&1 &
RECEIVER=$!
cleanup() { kill $RECEIVER 2>/dev/null; docker rm -f "$NAME" "$MTX" > /dev/null 2>&1; }
trap cleanup EXIT
sleep 1

STREAM_ARGS=()
if [ "${STREAM:-0}" = "1" ]; then
    docker run -d --rm --name "$MTX" --network host bluenviron/mediamtx:latest > /dev/null
    sleep 2
    STREAM_ARGS=(--stream-url rtmp://127.0.0.1:1935/live)
fi

docker run -d --name "$NAME" --gpus all -u 1000:1000 --network host -v "$REPO":"$REPO" -v "$OUT":"$OUT" \
    -v "$(dirname "$VIDEO")":"$(dirname "$VIDEO")":ro -w "$REPO/engine" --entrypoint ./build/ds_engine "$IMAGE" \
    --config "$PGIE" --tracker-config configs/tracker_nvdcf.yml --cameras-file "$OUT/cameras.ini" \
    --max-cameras "$MAX" --fps-interval 1 "${STREAM_ARGS[@]}" \
    --backend-url "http://127.0.0.1:$PORT/internal/events" --spool-file "$OUT/spool.jsonl" \
    --heartbeat-url "http://127.0.0.1:$PORT/internal/heartbeat" --heartbeat-interval 1 > /dev/null

for _ in $(seq 1 90); do   # until the two cameras have produced events
    read -r a b _ <<< "$(counts 2>/dev/null)"
    [ "${a:-0}" -gt 0 ] && [ "${b:-0}" -gt 0 ] && break
    sleep 1
done

window "A  cameras 1 2"
cameras 1 2 3;       hup; window "B  + camera 3"
cameras 1 2 3 4;     hup; window "C  + camera 4"
cameras 2 3 4;       hup; window "D  - camera 1"
cameras 2 3 4 5;     hup; window "E  + camera 5"
cameras 2 3 4 5 6;   hup; window "F  + camera 6 (refused)"
cameras 2 3 4 5;     hup; window "G  file back to 2 3 4 5"

docker logs "$NAME" > "$OUT/engine.log" 2>&1
echo "=== what the engine said about it"
grep -E "^reload:|removed|ERROR|WARNING|CRITICAL|^source_id=[0-9]+ camera_id=[0-9]+: |^Could not" "$OUT/engine.log" | grep -v gst-plugin-scanner
if [ "${STREAM:-0}" = "1" ]; then
    echo "=== paths the media server saw publishing"
    docker logs "$MTX" 2>&1 | grep -E "is publishing to path|is not publishing anymore" | sed -e 's/^.*\] //' | head -20
fi
echo "=== frame rate per camera, one reading per second (the first 3 and the last of each camera left out)"
python3 -c "
import collections, re, sys
rows = collections.defaultdict(list)
for line in open(sys.argv[1]):
    m = re.match(r'fps source_id=\\d+ camera_id=(\\d+) ([\\d.]+) (\\w+)', line)
    if m:
        rows[int(m.group(1))].append((float(m.group(2)), m.group(3)))
for cam, v in sorted(rows.items()):
    v = v[3:-1]
    fps = [f for f, _ in v]
    if fps:
        print(f'camera {cam}: {len(fps)} readings, min {min(fps):.1f}, readings under 5 fps: {sum(1 for f in fps if f < 5)}, not RUNNING: {sum(1 for _, st in v if st != \"RUNNING\")}')
" "$OUT/engine.log"
docker kill --signal=TERM "$NAME" > /dev/null
echo "engine exit code: $(docker wait "$NAME")"
echo "(files in $OUT)"
