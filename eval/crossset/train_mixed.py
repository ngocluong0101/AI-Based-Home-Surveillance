"""Train the ST-GCN on UR Fall + GMDCSA-24 and ask whether it generalises better (V2-9b).

UR Fall has the time of every fall (frame-level labels). GMDCSA-24 only says "this clip has a fall" / "this clip is daily life". So:
  * ADL clips: every window is a negative.
  * fall clips: multiple-instance learning. Of the windows that look like a fall in the skeleton (the person goes from upright to lying and the hips come down:
    `candidates`), the one the CURRENT network scores highest, and its neighbours within +-6 frames, are this epoch's positives; the other windows of a fall clip are left
    out (they are before or after the fall, and nobody knows which). The first `warm` epochs use UR Fall only, so the selection starts from a network that already
    knows what a fall looks like. A fall clip with no candidate window (e.g. a fall from lying in bed, or no usable skeleton) is skipped and counted.

Two questions, each with a control run through the SAME code (so the comparison is fair):
  E1  leave one actor out: train on UR Fall + three actors of GMDCSA-24, test on the fourth (clip level, the numbers of eval/crossset/fall_gmdcsa.py).
      Control: the network trained on UR Fall only.
  E2  does UR Fall get worse? 5-fold cross-validation by sequence on UR Fall with GMDCSA-24 added to every training part (event level, eval/train/compare.py's rules).
      Control: the same folds and seeds with UR Fall only.

    eval/.venv/bin/python eval/crossset/train_mixed.py --write eval/results/<date>_v2-9_fall_mixed_training.json [--final eval/work/train/stgcn_T48_mixed.pt]
"""
import argparse
import json
import pickle
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import torch
from torch import nn

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from eval.crossset import datasets  # noqa: E402
from eval.train import data, model as M, skeleton as sk  # noqa: E402
from eval.train.compare import fires, judge  # noqa: E402
from eval.train.train import augment, folds_of, predict  # noqa: E402

T = 48
CACHE = datasets.WORK / "gmdcsa_windows.pkl"
THRS = (0.9, 0.95)


# ---------------------------------------------------------------- GMDCSA-24 windows

def build_gmdcsa() -> dict:
    """{clip name: {"label", "subject", "tracks": [{"end": (n,), "X": (n, 3, T, 17) float32}]}}, every frame of every track, speed 1."""
    if CACHE.exists():
        return pickle.loads(CACHE.read_bytes())
    out = {}
    for c in datasets.gmdcsa_clips():
        dump = datasets.WORK / "gmdcsa24" / f"{c.name}.csv"
        if not dump.exists():
            continue
        tracks = []
        for _, tr in sk.load_tracks(dump).items():
            ends, ws = [], []
            for e in range(int(tr.frames[0]) + sk.MIN_FRAMES - 1, int(tr.frames[-1]) + 1):
                w = sk.window(tr, e, T, 1.0)
                if w is not None:
                    ends.append(e)
                    ws.append(w)
            if ws:
                tracks.append({"end": np.array(ends), "X": np.stack(ws).astype(np.float32)})
        out[c.name] = {"label": c.label, "subject": c.camera, "tracks": tracks}
    CACHE.write_bytes(pickle.dumps(out, protocol=4))
    return out


def candidate_mask(X: np.ndarray) -> np.ndarray:
    """(n, 3, T, 17) -> bool (n,): does the window show a person going from upright to lying with the hips coming down?
    Torso angle from vertical: first 12 frames at most 40 degrees, last 8 frames at least 55; the hips (torso lengths, y down) come down by at least 0.3."""
    x, y, v = X[:, 0], X[:, 1], X[:, 2]

    def centre(a, b):
        wv = v[:, :, a] + v[:, :, b]
        cx = (x[:, :, a] * v[:, :, a] + x[:, :, b] * v[:, :, b]) / np.maximum(wv, 1e-6)
        cy = (y[:, :, a] * v[:, :, a] + y[:, :, b] * v[:, :, b]) / np.maximum(wv, 1e-6)
        return cx, cy, wv > 0

    sx, sy, sok = centre(sk.L_SHOULDER, sk.R_SHOULDER)
    hx, hy, hok = centre(sk.L_HIP, sk.R_HIP)
    both = sok & hok
    ang = np.degrees(np.arctan2(np.abs(sx - hx), -(sy - hy)))
    ang = np.where(both, ang, np.nan)
    hyn = np.where(hok, hy, np.nan)
    first, last = slice(0, 12), slice(T - 8, T)
    ok = (np.sum(both[:, first], 1) >= 4) & (np.sum(both[:, last], 1) >= 3) & (np.sum(hok[:, first], 1) >= 4) & (np.sum(hok[:, last], 1) >= 3)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        a0, a1 = np.nanmedian(ang[:, first], 1), np.nanmedian(ang[:, last], 1)
        drop = np.nanmedian(hyn[:, last], 1) - np.nanmedian(hyn[:, first], 1)
    return ok & (a0 <= 40) & (a1 >= 55) & (drop >= 0.3)


def pack_training(gm: dict, clips: list[str]) -> dict:
    """The GMDCSA-24 part of one training set: negatives (ADL windows, every other frame) and the candidate windows of the fall clips."""
    neg, cand, cand_end, cand_clip = [], [], [], []
    n_fall, n_with = 0, 0
    for ci, name in enumerate(clips):
        d = gm[name]
        if d["label"] == "adl":
            for tr in d["tracks"]:
                neg.append(tr["X"][::2])
        else:
            n_fall += 1
            found = False
            for tr in d["tracks"]:
                m = candidate_mask(tr["X"])
                if m.any():
                    found = True
                    cand.append(tr["X"][m])
                    cand_end.append(tr["end"][m])
                    cand_clip.append(np.full(int(m.sum()), ci))
            n_with += int(found)
    z = np.zeros((0, 3, T, 17), np.float32)
    return {"neg": np.concatenate(neg) if neg else z, "cand": np.concatenate(cand) if cand else z,
            "cand_end": np.concatenate(cand_end) if cand_end else np.zeros(0, int),
            "cand_clip": np.concatenate(cand_clip) if cand_clip else np.zeros(0, int), "fall_clips": n_fall, "fall_clips_with_candidates": n_with}


# ---------------------------------------------------------------- training

def fit_mil(ur_X, ur_y, g, epochs: int, warm: int, seed: int, lr: float = 2e-3, bs: int = 128):
    """UR Fall windows + (optionally) the GMDCSA-24 part g from pack_training. g None = UR Fall only (the control)."""
    torch.manual_seed(seed)
    gen = torch.Generator(device="cuda").manual_seed(seed)
    net = M.build("stgcn", T).cuda()
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=1e-2)
    n_neg = 0 if g is None else len(g["neg"])
    n_pos_max = 0 if g is None else g["fall_clips"] * 14
    steps = epochs * ((len(ur_X) + n_neg + n_pos_max + bs - 1) // bs)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps)
    URX, URY = torch.tensor(ur_X).cuda(), torch.tensor(ur_y).cuda()
    NEG = torch.tensor(g["neg"]).cuda() if g is not None and n_neg else None
    CAND = torch.tensor(g["cand"]).cuda() if g is not None and len(g["cand"]) else None
    used = 0
    for ep in range(epochs):
        net.eval()
        pos = None
        if CAND is not None and ep >= warm:
            with torch.no_grad():
                p = torch.cat([torch.softmax(net(CAND[i:i + 1024]), 1)[:, 1] for i in range(0, len(CAND), 1024)]).cpu().numpy()
            pick = np.zeros(len(p), bool)
            for c in np.unique(g["cand_clip"]):
                idx = np.where(g["cand_clip"] == c)[0]
                best = idx[np.argmax(p[idx])]
                pick[idx[np.abs(g["cand_end"][idx] - g["cand_end"][best]) <= 6]] = True
            pos = CAND[torch.tensor(np.where(pick)[0]).cuda()]
            used = int(pick.sum())
        parts_x, parts_y = [URX], [URY]
        if NEG is not None:
            parts_x.append(NEG)
            parts_y.append(torch.zeros(len(NEG), dtype=URY.dtype, device="cuda"))
        if pos is not None:
            parts_x.append(pos)
            parts_y.append(torch.ones(len(pos), dtype=URY.dtype, device="cuda"))
        X, Y = torch.cat(parts_x), torch.cat(parts_y)
        w = torch.tensor([1.0, (Y == 0).sum().item() / max(1, (Y == 1).sum().item())], dtype=torch.float32).cuda().clamp(max=10)
        lossf = nn.CrossEntropyLoss(weight=w, label_smoothing=0.05)
        net.train()
        perm = torch.randperm(len(X), device="cuda", generator=gen)
        for i in range(0, len(X), bs):
            b = perm[i:i + bs]
            loss = lossf(net(augment(X[b], gen)), Y[b])
            opt.zero_grad()
            loss.backward()
            opt.step()
            if sched.last_epoch < steps - 1:
                sched.step()
    net.eval()
    net.mil_positives = used
    return net


# ---------------------------------------------------------------- evaluation

@torch.no_grad()
def score_windows(net, X: np.ndarray) -> np.ndarray:
    return np.concatenate([torch.softmax(net(torch.tensor(X[i:i + 1024]).cuda()), 1)[:, 1].cpu().numpy() for i in range(0, len(X), 1024)]) if len(X) else np.zeros(0)


def clip_report(net, d: dict) -> dict:
    """{thr: bool}: does the network raise a fall (thr, 3 scores in a row) on any track of this clip?"""
    res = {t: False for t in THRS}
    for tr in d["tracks"]:
        p = score_windows(net, tr["X"])
        for t in THRS:
            if not res[t] and fires(tr["end"], p, t, 3):
                res[t] = True
    return res


def eval_gmdcsa(net, gm: dict, clips: list[str]) -> dict:
    out = {}
    for name in clips:
        out[name] = clip_report(net, gm[name])
    return out


def summarise(rep: dict, gm: dict, clips: list[str]) -> dict:
    s = {}
    for t in THRS:
        f = [c for c in clips if gm[c]["label"] == "fall"]
        a = [c for c in clips if gm[c]["label"] == "adl"]
        s[str(t)] = {"falls": [sum(rep[c][t] for c in f), len(f)], "adl": [sum(rep[c][t] for c in a), len(a)]}
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--warm", type=int, default=10)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--write", default="")
    ap.add_argument("--final", default="", help="also train on everything and save the checkpoint here")
    ap.add_argument("--skip-e2", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    seqs = data.sequences()
    ur = data.build(seqs, T, 2, (0.8, 1.0, 1.25))
    plain = data.build(seqs, T, 1, (1.0,))
    gm = build_gmdcsa()
    names = sorted(gm)
    subjects = sorted({gm[n]["subject"] for n in names})
    print(f"UR Fall: {len(ur['y'])} training windows; GMDCSA-24: {len(names)} clips, {sum(len(t['X']) for n in names for t in gm[n]['tracks'])} windows "
          f"({time.time() - t0:.0f}s)", flush=True)
    full = pack_training(gm, names)
    print(f"fall clips with at least one 'upright -> lying' candidate window: {full['fall_clips_with_candidates']}/{full['fall_clips']}", flush=True)
    out = {"epochs": a.epochs, "warm": a.warm, "seeds": a.seeds, "fall_clips": full["fall_clips"], "fall_clips_with_candidates": full["fall_clips_with_candidates"]}

    # ---- E1: leave one actor out
    e1 = {"mixed": {}, "ur_only": {}}
    rep_all = {"mixed": {}, "ur_only": {}}
    for seed in range(a.seeds):
        for held in subjects:
            train_clips = [n for n in names if gm[n]["subject"] != held]
            test_clips = [n for n in names if gm[n]["subject"] == held]
            g = pack_training(gm, train_clips)
            for kind, gg in (("mixed", g), ("ur_only", None)):
                net = fit_mil(ur["X"], ur["y"], gg, a.epochs, a.warm, seed * 100 + subjects.index(held))
                rep = eval_gmdcsa(net, gm, test_clips)
                rep_all[kind].setdefault(seed, {}).update(rep)
                print(f"E1 seed {seed} held out {held} [{kind}] positives picked {getattr(net, 'mil_positives', 0)}: "
                      f"{ {t: summarise(rep, gm, test_clips)[str(t)] for t in THRS} }  ({time.time() - t0:.0f}s)", flush=True)
    for kind in ("mixed", "ur_only"):
        e1[kind] = {"all_subjects": [summarise(rep_all[kind][s], gm, names) for s in range(a.seeds)],
                    "per_subject": {h: [summarise(rep_all[kind][s], gm, [n for n in names if gm[n]["subject"] == h]) for s in range(a.seeds)] for h in subjects}}
    out["E1_leave_one_actor_out"] = e1
    print("\nE1 summary (clip level, falls caught / ADL false alarms, per seed):")
    for kind in ("ur_only", "mixed"):
        for s in range(a.seeds):
            for t in THRS:
                r = e1[kind]["all_subjects"][s][str(t)]
                print(f"  {kind:8} seed {s} thr {t}: falls {r['falls'][0]}/{r['falls'][1]}, ADL {r['adl'][0]}/{r['adl'][1]}")
    if a.write:
        Path(a.write).write_text(json.dumps(out, indent=1, default=str))

    # ---- E2: does UR Fall get worse?
    if not a.skip_e2:
        e2 = {"mixed": [], "ur_only": []}
        for seed in range(a.seeds):
            for k, held in enumerate(folds_of(seqs, 5, seed)):
                m = ~np.isin(ur["seq"], held)
                for kind, gg in (("mixed", full), ("ur_only", None)):
                    net = fit_mil(ur["X"][m], ur["y"][m], gg, a.epochs, a.warm, seed * 100 + k)
                    per = {}
                    for t in THRS:
                        per[str(t)] = {}
                        for s in held:
                            sm = plain["seq"] == s
                            per[str(t)][s] = judge(s, fires(plain["end"][sm], predict(net, plain["X"][sm]), t, 3))
                    e2[kind].append({"seed": seed, "fold": k, "per_seq": per})
                print(f"E2 seed {seed} fold {k} done ({time.time() - t0:.0f}s)", flush=True)
        agg = {}
        for kind in ("ur_only", "mixed"):
            agg[kind] = {}
            for t in THRS:
                runs = []
                for seed in range(a.seeds):
                    tp = fp_adl = fp_fall = n_f = n_a = 0
                    for r in e2[kind]:
                        if r["seed"] != seed:
                            continue
                        for s, j in r["per_seq"][str(t)].items():
                            if s.startswith("fall"):
                                n_f += 1
                                tp += j["tp"]
                                fp_fall += j["fp"]
                            else:
                                n_a += 1
                                fp_adl += j["fp"]
                    runs.append({"falls": [tp, n_f], "fp_adl": [fp_adl, n_a], "fp_in_fall_sequences": fp_fall})
                agg[kind][str(t)] = runs
        out["E2_urfall_cv"] = agg
        print("\nE2 summary (UR Fall, 5-fold CV by sequence, event level, per seed):")
        for kind in ("ur_only", "mixed"):
            for t in THRS:
                for s, r in enumerate(agg[kind][str(t)]):
                    print(f"  {kind:8} thr {t} seed {s}: falls {r['falls'][0]}/{r['falls'][1]}, false alarms in ADL {r['fp_adl'][0]}/{r['fp_adl'][1]}, stray in fall sequences {r['fp_in_fall_sequences']}")
        if a.write:
            Path(a.write).write_text(json.dumps(out, indent=1, default=str))

    # ---- the network trained on everything
    if a.final:
        net = fit_mil(ur["X"], ur["y"], full, a.epochs, a.warm, 0)
        torch.save({"model": "stgcn", "T": T, "velocity": M.VELOCITY, "state": net.state_dict()}, a.final)
        print(f"trained on everything (positives picked in the last epoch: {net.mil_positives}) -> {a.final}")
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
