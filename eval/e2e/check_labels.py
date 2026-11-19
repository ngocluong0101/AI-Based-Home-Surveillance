"""Validate every label file and print what the data set holds, split by split (so you see what is still missing).

    eval/.venv/bin/python eval/e2e/check_labels.py
"""
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.e2e import labels  # noqa: E402

TARGET = {"test": dict(falls=20, fights=10, hours=3.0, people=2, scenes=1)}     # what V2-4 asks of the locked test split (eval/RECORDING.md)


def main() -> None:
    try:
        clips = labels.load_dir()
    except labels.LabelError as e:
        sys.exit(f"INVALID: {e}")
    rows = defaultdict(lambda: dict(clips=0, falls=0, fights=0, ambiguous=0, quiet_h=0.0, people=set(), scenes=set()))
    for c in clips:
        r = rows[c.split]
        r["clips"] += 1
        r["people"].update(c.person.split("+"))
        r["scenes"].add(c.scene)
        if not c.has_incident:
            r["quiet_h"] += c.duration_s / 3600
        for e in c.events:
            if e.label == "ambiguous":
                r["ambiguous"] += 1
            elif e.type == "fall":
                r["falls"] += 1
            else:
                r["fights"] += 1
    print(f"{len(clips)} label files, no leaks between train/val/test\n")
    print(f"{'split':<6}{'clips':>6}{'falls':>7}{'fights':>8}{'ambig.':>8}{'quiet h':>9}{'people':>8}{'scenes':>8}")
    for s in labels.SPLITS:
        if s in rows:
            r = rows[s]
            print(f"{s:<6}{r['clips']:>6}{r['falls']:>7}{r['fights']:>8}{r['ambiguous']:>8}{r['quiet_h']:>9.2f}{len(r['people']):>8}{len(r['scenes']):>8}")
    t, g = rows.get("test"), TARGET["test"]
    print("\ntest split target:", g)
    if not t:
        print("  no test clips yet")
    else:
        for k, have in (("falls", t["falls"]), ("fights", t["fights"]), ("hours", t["quiet_h"]), ("people", len(t["people"])), ("scenes", len(t["scenes"]))):
            print(f"  {k:<7} {have:g} / {g[k]:g}{'' if have >= g[k] else '   <- short'}")


if __name__ == "__main__":
    main()
