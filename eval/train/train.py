"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Train and cross-validate the skeleton classifiers on the UR Fall windows (V2-5). Groups = sequences (never frames of one sequence on both sides).

    eval/.venv/bin/python eval/train/train.py --model stgcn --folds 5 --seeds 3
    eval/.venv/bin/python eval/train/train.py --model stgcn --final --out eval/work/train/stgcn_T48.pt      # all sequences, for the ONNX export

A cross-validation outputs per-window AUC/accuracy and, per sequence, the score curve over time (eval/train/events.py turns it into
events). UR Fall has one room, few people and no person id, so a fold can contain a person the training part also had: the numbers are
an upper bound.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.train import data, model as M, skeleton as sk  # noqa: E402

OUT = REPO / "eval/work/train"


def augment(x: torch.Tensor, g: torch.Generator) -> torch.Tensor:
    """x (N, 3, T, 17) on the GPU. flip, small rotation and scale, keypoint noise, dropped joints. Done on windows, so it is the same for every model."""
    n = x.shape[0]
    x = x.clone()
    flip = torch.rand(n, device=x.device, generator=g) < 0.5
    xf = x[flip]
    xf = xf[:, :, :, sk.FLIP]
    xf[:, 0] = -xf[:, 0]
    x[flip] = xf
    ang = (torch.rand(n, device=x.device, generator=g) - 0.5) * 2 * np.deg2rad(10)
    sc = 1 + (torch.rand(n, device=x.device, generator=g) - 0.5) * 0.2
    c, s = torch.cos(ang) * sc, torch.sin(ang) * sc
    px, py = x[:, 0].clone(), x[:, 1].clone()
    x[:, 0] = c[:, None, None] * px - s[:, None, None] * py
    x[:, 1] = s[:, None, None] * px + c[:, None, None] * py
    x[:, :2] += torch.randn(x[:, :2].shape, device=x.device, generator=g) * 0.02 * x[:, 2:3]
    drop = (torch.rand(x[:, 2:3].shape, device=x.device, generator=g) < 0.03)
    x = x * (~drop).float()
    return x


def fit(Xtr, ytr, name, T, epochs, seed, lr=2e-3, bs=128):
    torch.manual_seed(seed)
    g = torch.Generator(device="cuda").manual_seed(seed)
    net = M.build(name, T).cuda()
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-2)
    steps = epochs * ((len(Xtr) + bs - 1) // bs)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps)
    w = torch.tensor([1.0, (ytr == 0).sum() / max(1, (ytr == 1).sum())], dtype=torch.float32).cuda().clamp(max=10)
    lossf = nn.CrossEntropyLoss(weight=w, label_smoothing=0.05)
    X, Y = torch.tensor(Xtr).cuda(), torch.tensor(ytr).cuda()
    for ep in range(epochs):
        net.train()
        perm = torch.randperm(len(X), device="cuda", generator=g)
        for i in range(0, len(X), bs):
            b = perm[i:i + bs]
            loss = lossf(net(augment(X[b], g)), Y[b])
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
    return net.eval()


@torch.no_grad()
def predict(net, X):
    out = []
    for i in range(0, len(X), 512):
        out.append(torch.softmax(net(torch.tensor(X[i:i + 512]).cuda()), 1)[:, 1].cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0)


def auc(score, y):
    from scipy.stats import rankdata
    r = rankdata(score)
    pos = y == 1
    n1, n0 = pos.sum(), (~pos).sum()
    return float((r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else float("nan")


def folds_of(seqs: list[str], k: int, seed: int):
    """stratified over fall / adl, whole sequences per fold"""
    rng = np.random.default_rng(seed)
    falls = [s for s in seqs if s.startswith("fall")]
    adls = [s for s in seqs if s.startswith("adl")]
    rng.shuffle(falls)
    rng.shuffle(adls)
    return [sorted(falls[i::k] + adls[i::k]) for i in range(k)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="stgcn", choices=["stgcn", "tcn", "mlp"])
    ap.add_argument("--T", type=int, default=48)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seeds", type=int, default=1)
    ap.add_argument("--speeds", default="0.8,1.0,1.25")
    ap.add_argument("--final", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--tag", default="")
    ap.add_argument("--no-velocity", action="store_true", help="ablation: x, y, valid only")
    a = ap.parse_args()
    M.VELOCITY = not a.no_velocity
    OUT.mkdir(parents=True, exist_ok=True)
    seqs = data.sequences()
    speeds = tuple(float(s) for s in a.speeds.split(","))
    t0 = time.time()
    train = data.build(seqs, a.T, a.stride, speeds)
    print(f"{len(seqs)} sequences, {len(train['y'])} windows ({int(train['y'].sum())} fall), built in {time.time() - t0:.0f}s", flush=True)
    if a.final:
        net = fit(train["X"], train["y"], a.model, a.T, a.epochs, 0)
        out = Path(a.out or OUT / f"{a.model}_T{a.T}.pt")
        torch.save({"model": a.model, "T": a.T, "velocity": M.VELOCITY, "state": net.state_dict()}, out)
        print(f"{a.model}: {M.n_params(net)} parameters -> {out}")
        return
    plain = data.build(seqs, a.T, 1, (1.0,))          # what the model sees at run time: every frame, speed 1
    results = []
    for seed in range(a.seeds):
        scores = {}
        for k, held in enumerate(folds_of(seqs, a.folds, seed)):
            m = ~np.isin(train["seq"], held)
            net = fit(train["X"][m], train["y"][m], a.model, a.T, a.epochs, seed * 100 + k)
            hm = np.isin(plain["seq"], held)
            for s in held:
                sm = plain["seq"] == s
                scores[s] = {"end": plain["end"][sm].tolist(), "p": predict(net, plain["X"][sm]).tolist()}
            print(f"seed {seed} fold {k}: held out {len(held)} sequences, window AUC {auc(np.array(sum((scores[s]['p'] for s in held), [])), np.concatenate([plain['y'][plain['seq'] == s] for s in held])):.3f}", flush=True)
        results.append(scores)
    tag = a.tag or f"{a.model}_T{a.T}"
    (OUT / f"cv_{tag}.json").write_text(json.dumps({"model": a.model, "T": a.T, "params": M.n_params(M.build(a.model, a.T)), "runs": results,
                                                    "labels": {s: [data.window_label(e, data.fall_frames(s)) for e in r["end"]] for s, r in results[0].items()}}))
    print(f"wrote {OUT / ('cv_' + tag + '.json')}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
