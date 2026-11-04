#!/bin/bash
# check_reload.sh - does the running engine apply new per-camera settings when it gets SIGHUP?
#
# An offline test tool, not part of the product. It starts the real engine (DeepStream container, GPU) on three
# cameras that all play the same long video, and changes the cameras file while the engine runs:
#
#   phase A  every camera on defaults                          the reference rate of events per camera
#   phase B  cam 1 absence_timeout=0.2, cam 2 conf_threshold=0.9, cam 3 ai_enabled=false, then SIGHUP
#            -> cam 1 raises more events, cam 2 almost none, cam 3 none and no "people" in the heartbeat
#   phase C  a broken file + SIGHUP                            -> refused, phase B keeps going
#   phase D  a file with a fourth camera + SIGHUP              -> refused ("needs a restart"), phase B keeps going
#   phase E  everything back to defaults + SIGHUP              -> the rates of phase A come back
#
# Usage, from anywhere:  engine/tools/check_reload.sh [VIDEO]      (default: data/videos/soak_loop.mp4, a long file)
# Needs engine/build/ds_engine, the TensorRT engine for 3 cameras and a free GPU. Output: a table of events per
# window and the "reload:" lines of the engine; the files stay in a temporary directory that is printed at the end.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
VIDEO=$(readlink -f "${1:-$REPO/data/videos/soak_loop.mp4}")
OUT=$(mktemp -d)
IMAGE=nvcr.io/nvidia/deepstream:7.1-triton-multiarch
PORT=${PORT:-18099}
NAME=check-reload-$$
WINDOW=${WINDOW:-12}

write_cameras() {   # arguments: "<id>:<line>" pairs, each adds that line to that camera; none = all defaults
    local f="$OUT/cameras.new" e
    : > "$f"
    for id in 1 2 3; do
        printf '[camera.%s]\nuri=file://%s\n' "$id" "$VIDEO" >> "$f"
        for e in "$@"; do
            [ "${e%%:*}" = "$id" ] && printf '%s\n' "${e#*:}" >> "$f"
        done
        printf '\n' >> "$f"
    done
    chmod 600 "$f"
    mv "$f" "$OUT/cameras.ini"   # replaced in one step, as the engine manager does
}

counts() {   # events received so far, per camera: "c1 c2 c3"
    python3 - "$OUT/events.jsonl" <<'PY'
import collections, json, sys
c = collections.Counter(json.loads(l)["camera_id"] for l in open(sys.argv[1]) if l.strip())
print(c[1], c[2], c[3])
PY
}

people() {   # "people" of the last heartbeat per camera, "-" when absent
    python3 - "$OUT/heartbeats.jsonl" <<'PY'
import json, sys
beats = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
last = {c["camera_id"]: c.get("people", "-") for c in beats[-1]["cameras"]} if beats else {}
print(" ".join(str(last.get(i, "?")) for i in (1, 2, 3)))
PY
}

window() {   # $1 = label: let WINDOW seconds pass and print the events per camera in them
    local before after
    before=($(counts))
    sleep "$WINDOW"
    after=($(counts))
    printf '%-44s events in %ss: cam1=%-4s cam2=%-4s cam3=%-4s people now: %s\n' "$1" "$WINDOW" \
        $((after[0] - before[0])) $((after[1] - before[1])) $((after[2] - before[2])) "$(people)"
}

hup() { docker kill --signal=HUP "$NAME" > /dev/null; sleep 2; }

write_cameras
PGIE=$(cd "$REPO" && python3 -c "
from pathlib import Path
from engine_manager.manager import write_engine_config
cfg, _engine = write_engine_config(Path('engine/configs/pgie_yolov8n.txt').resolve(), Path('$OUT').resolve(), 3)
print(cfg)
") || exit 1

python3 "$REPO/engine/tools/fake_receiver.py" --port "$PORT" --log "$OUT/events.jsonl" \
    --heartbeat-log "$OUT/heartbeats.jsonl" > "$OUT/receiver.out" 2>&1 &
RECEIVER=$!
cleanup() { kill $RECEIVER 2>/dev/null; docker rm -f "$NAME" > /dev/null 2>&1; }
trap cleanup EXIT
sleep 1

docker run -d --name "$NAME" --gpus all -u 1000:1000 --network host -v "$REPO":"$REPO" -v "$OUT":"$OUT" \
    -v "$(dirname "$VIDEO")":"$(dirname "$VIDEO")":ro -w "$REPO/engine" --entrypoint ./build/ds_engine "$IMAGE" \
    --config "$PGIE" --tracker-config configs/tracker_nvdcf.yml --cameras-file "$OUT/cameras.ini" \
    --fps-interval 0 --backend-url "http://127.0.0.1:$PORT/internal/events" --spool-file "$OUT/spool.jsonl" \
    --heartbeat-url "http://127.0.0.1:$PORT/internal/heartbeat" --heartbeat-interval 1 > /dev/null

for _ in $(seq 1 60); do   # until every camera has produced events
    read -r a b c <<< "$(counts 2>/dev/null)"
    [ "${a:-0}" -gt 0 ] && [ "${b:-0}" -gt 0 ] && [ "${c:-0}" -gt 0 ] && break
    sleep 1
done

window "A  defaults"
write_cameras "1:absence_timeout=0.2" "2:conf_threshold=0.9" "3:ai_enabled=false"
hup
window "B  after SIGHUP: 0.2 s / conf 0.9 / AI off"
printf '[camera.1]\nuri=file://%s\nconf_threshold=banana\n' "$VIDEO" > "$OUT/cameras.ini"; chmod 600 "$OUT/cameras.ini"
hup
window "C  broken file refused"
{ for id in 1 2 3 4; do printf '[camera.%s]\nuri=file://%s\n\n' "$id" "$VIDEO"; done; } > "$OUT/cameras.ini"; chmod 600 "$OUT/cameras.ini"
hup
window "D  a fourth camera refused"
write_cameras
hup
window "E  back to defaults"

echo "=== what the engine said about it"
docker logs "$NAME" 2>&1 | grep -E "^reload:|^Cameras file"
docker kill --signal=TERM "$NAME" > /dev/null
echo "engine exit code: $(docker wait "$NAME")"
echo "(files in $OUT)"
