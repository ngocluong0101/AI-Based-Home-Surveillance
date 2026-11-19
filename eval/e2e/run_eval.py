"""Stage 2: replay the rules over the dumps and print the M1 / M2 tables (writes eval/results/<date>_v2-4_<tag>.json).

    eval/.venv/bin/python eval/e2e/run_eval.py --splits val --tag baseline
    eval/.venv/bin/python eval/e2e/run_eval.py --splits val --set fall_still_s=1.5 --tag still1.5

`--splits test` is refused unless --final is given: the test set is locked and must not be used to pick thresholds. `dev` clips
(public UR Fall, the rules were tuned on them) print a table but are never written to results as a measurement.
"""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from eval.e2e import labels, replay, score  # noqa: E402

DUMP_DIR = REPO / "eval/work/dumps"


def run(splits: set[str], overrides: dict) -> tuple[list[score.ClipResult], list[labels.ClipLabel], list[str]]:
    clips = labels.load_dir(splits=splits)
    results, used, missing = [], [], []
    for c in clips:
        dump = DUMP_DIR / f"{Path(c.clip).stem}.csv"
        if not dump.exists():
            missing.append(c.clip)
            continue
        results.append(score.match_clip(c, replay.replay(dump, c, overrides)))
        used.append(c)
    return results, used, missing


def pct(x):
    return "-" if x is None else f"{100 * x:.0f}%"


def show(table: dict) -> None:
    ci = table["recall_ci95"]
    print(f"\n== {table['type'].upper()}: {table['events']} events, {table['people']} people, {table['scenes']} scenes")
    print(f"   M1 recall {pct(table['recall'])} ({table['tp']}/{table['events']})" + (f", 95% CI {pct(ci[0])}-{pct(ci[1])}" if ci else "") +
          f"   precision {pct(table['precision'])}   F1 {pct(table['f1'])}   duplicates {table['duplicates']}")
    m2 = table["m2"]
    rate = "-" if m2["fp_per_camera_hour"] is None else f"{m2['fp_per_camera_hour']:.2f}"
    print(f"   M2 false alarms {m2['fp_raw']} in {m2["camera_hours"]:.3f} camera-hours ({m2['clips']} clips, {m2['scenes']} scenes) = {rate}/camera/hour;"
          f" {table['fp_in_incident_clips']} more FPs inside clips with an incident")
    if "rule_latency_s" in table:
        L = table["rule_latency_s"]
        print(f"   rule delay impact->report: median {L['median']:.1f}s p95 {L['p95']:.1f}s max {L['max']:.1f}s;"
              f" recall within 5s {pct(table['recall_within_5s'])}, within 10s {pct(table['recall_within_10s'])}")
    for k, v in sorted(table["by_case"].items()):
        print(f"   case {k:<14} TP {v['tp']}  FN {v['fn']}  FP {v['fp']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", default="val")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="BehaviorConfig field")
    ap.add_argument("--tag", default="run")
    ap.add_argument("--final", action="store_true", help="allow the locked test split")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    splits = set(a.splits.split(","))
    if "test" in splits:
        if not a.final:
            sys.exit("refusing to read the test split without --final (it is locked; see eval/DEFINITIONS.md section 2)")
        from eval.e2e import lock_test
        if not lock_test.verify():
            sys.exit("the test split is not locked, or its labels changed since: run eval/e2e/lock_test.py (see eval/RECORDING.md)")
    overrides = dict(s.split("=", 1) for s in a.set)
    results, used, missing = run(splits, overrides)
    if missing:
        print(f"no dump for {len(missing)} clips (run eval/e2e/make_dumps.py): {', '.join(missing)}")
    if not results:
        sys.exit("nothing to score")
    tables = {k: score.summarise(results, k, used) for k in ("fall", "fight")}
    for t in tables.values():
        if t["events"] or t["fp"]:
            show(t)
    if "dev" in splits or a.no_write:
        print("\n(not written to eval/results/: dev clips are tuning data, not a measurement)" if "dev" in splits else "")
        return
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=REPO).stdout.strip()
    except OSError:
        commit = "unknown"
    out = REPO / "eval/results" / f"{datetime.date.today()}_v2-4_{a.tag}.json"
    out.write_text(json.dumps({"splits": sorted(splits), "overrides": overrides, "commit": commit, "clips": [c.clip for c in used], "tables": tables}, indent=1))
    print(f"\nwrote {out.relative_to(REPO)}")


if __name__ == "__main__":
    main()
