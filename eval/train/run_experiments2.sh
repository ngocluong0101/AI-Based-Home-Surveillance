#!/bin/bash
# V2-5 second round: the same, after windows were allowed to start 16 frames into a track (partial windows; see skeleton.py MIN_FRAMES).
set -e
cd "$(dirname "$0")/../.."
PY=eval/.venv/bin/python
run() { tag=$1; shift; echo "== $tag"; $PY eval/train/train.py --folds 5 --epochs 30 --tag "$tag" "$@" | tail -1; }
run p_stgcn_T48 --model stgcn --T 48 --seeds 3
run p_stgcn_T30 --model stgcn --T 30 --seeds 3
run p_tcn_T48   --model tcn   --T 48 --seeds 3
run p_stgcn_T64 --model stgcn --T 64 --seeds 3
