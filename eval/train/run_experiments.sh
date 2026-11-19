#!/bin/bash
# V2-5 experiments: model ladder, window length, velocity, seeds. Cross-validated on whole UR Fall sequences. One after another (laptop GPU shared).
#   nice -n 10 eval/train/run_experiments.sh        # ~30 min; results in eval/work/train/cv_*.json, table from eval/train/compare.py
set -e
cd "$(dirname "$0")/../.."
PY=eval/.venv/bin/python
run() { tag=$1; shift; echo "== $tag"; $PY eval/train/train.py --folds 5 --epochs 30 --tag "$tag" "$@" | tail -1; }
run mlp_T48      --model mlp   --T 48 --seeds 3
run tcn_T48      --model tcn   --T 48 --seeds 3
run stgcn_T48    --model stgcn --T 48 --seeds 3
run stgcn_T30    --model stgcn --T 30 --seeds 3
run stgcn_T64    --model stgcn --T 64 --seeds 3
run stgcn_T48_novel --model stgcn --T 48 --seeds 3 --no-velocity
