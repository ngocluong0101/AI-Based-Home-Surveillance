"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Read the cached VLM results and print the trade-off: for each threshold on p_yes, how many false alarms of the detectors the VLM would
remove and how many real falls it would take away with them.   eval/.venv/bin/python eval/vlm/analyse.py eval/work/vlm/results_options_f6.jsonl
"""
import json
import sys

import numpy as np
from scipy.stats import rankdata

rows = [json.loads(line) for line in open(sys.argv[1])]
ann = [r for r in rows if r["kind"] == "annotated"]
y = np.array([r["label"] for r in ann])
s = np.array([r["p_yes"] for r in ann])
rk = rankdata(s)
print(f"annotated windows: {len(ann)} ({y.sum()} falls); AUC of p_yes {(rk[y == 1].sum() - y.sum() * (y.sum() + 1) / 2) / (y.sum() * (~y.astype(bool)).sum()):.3f}")
print("  falls     p_yes min/5%/25%/median:", np.round(np.quantile(s[y == 1], [0, .05, .25, .5]), 3))
print("  non-falls p_yes median/75%/90%/95%/max:", np.round(np.quantile(s[y == 0], [.5, .75, .9, .95, 1]), 3))
for kind in ("alert_rule", "alert_net"):
    al = [r for r in rows if r["kind"] == kind]
    T, F = [r for r in al if r["label"]], [r for r in al if not r["label"]]
    print(f"{kind}: {len(T)} true, {len(F)} false alarms")
    for tau in (0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9):
        print(f"   suppress if p_yes < {tau:<5}: false removed {sum(r['p_yes'] < tau for r in F):>2}/{len(F)}   real falls lost {sum(r['p_yes'] < tau for r in T):>2}/{len(T)}")
if "-v" in sys.argv:
    for r in rows:
        if r["kind"] == "alert_rule" and not r["label"]:
            print(r["id"], round(r["p_yes"], 3), [round(x, 2) for x in (r.get("letters") or [])])
