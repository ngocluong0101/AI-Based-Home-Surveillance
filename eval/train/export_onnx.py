"""PyTorch -> ONNX for the engine (V2-5.4), with a check that the two agree.

    eval/.venv/bin/python eval/train/export_onnx.py eval/work/train/stgcn_T48.pt --out engine/models/stgcn_fall.onnx

Input `skeleton` (N, 3, T, 17) float32 (see eval/train/skeleton.py), output `logits` (N, 2); N is dynamic, T is fixed by the checkpoint.
The softmax is NOT in the graph: the engine applies it (one place for the threshold logic). Prints the largest difference PyTorch vs ONNX Runtime on random
and on real windows.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import torch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.train import data, model as M  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ckpt", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    ck = torch.load(a.ckpt, map_location="cpu")
    M.VELOCITY = ck.get("velocity", True)
    net = M.build(ck["model"], ck["T"])
    net.load_state_dict(ck["state"])
    net.eval()
    T = ck["T"]
    dummy = torch.zeros(2, 3, T, 17)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(net, dummy, str(a.out), input_names=["skeleton"], output_names=["logits"], opset_version=17,
                      dynamic_axes={"skeleton": {0: "batch"}, "logits": {0: "batch"}}, dynamo=False)
    import onnxruntime as ort
    sess = ort.InferenceSession(str(a.out), providers=["CPUExecutionProvider"])
    real = data.build(data.sequences()[:6], T, 5)["X"][:64]
    worst = 0.0
    for name, x in (("random", np.random.default_rng(0).normal(size=(16, 3, T, 17)).astype(np.float32)), ("real windows", real)):
        with torch.no_grad():
            ref = net(torch.tensor(x)).numpy()
        got = sess.run(None, {"skeleton": x})[0]
        d = float(np.abs(ref - got).max())
        worst = max(worst, d)
        print(f"{name}: {len(x)} windows, max |PyTorch - ONNX Runtime| on the logits = {d:.2e}")
    ref_dir = REPO / "eval/work/train"
    ref = data.build(data.sequences(), T, 7)["X"][:256]
    with torch.no_grad():
        probs = torch.softmax(net(torch.tensor(ref)), 1)[:, 1].numpy().astype(np.float32)
    ref.astype(np.float32).tofile(ref_dir / "ref_windows.f32")
    probs.tofile(ref_dir / "ref_probs.f32")
    print(f"reference for engine/build/stgcn_check: {len(ref)} windows in {ref_dir}/ref_windows.f32, PyTorch probabilities in ref_probs.f32")
    print(f"wrote {a.out} ({a.out.stat().st_size / 1e3:.0f} kB); {M.n_params(net)} parameters")
    if worst > 1e-3:
        sys.exit("ONNX does not match PyTorch")


if __name__ == "__main__":
    main()
