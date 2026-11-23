#!/bin/bash
# @author: Ngoc Luong <ngocluongg0101@gmail.com>

# Run a command inside the DeepStream container with the repo mounted at /work (the layout the engine was built in), for everything in
# eval/ that needs GStreamer/NVENC or the engine (the host has neither x264 nor DeepStream).
#
#   eval/tools/in_container.sh eval/tools/encode_h264.sh png 'eval/work/frames/%06d.png' eval/work/out.mp4
#   eval/tools/in_container.sh engine/build/ds_engine --config ...
#
# The first argument is the program (a path relative to the repo root, or a command on the container's PATH); the rest are its arguments.
set -e
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROG="$1"; shift
if [ -e "$REPO/$PROG" ]; then PROG="/work/$PROG"; fi
exec docker run --rm --gpus all -u "$(id -u):$(id -g)" -v "$REPO:/work" -w /work \
    -e GST_DEBUG="${GST_DEBUG:-1}" --entrypoint "$PROG" \
    nvcr.io/nvidia/deepstream:7.1-triton-multiarch "$@"
