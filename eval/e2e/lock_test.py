"""Lock the test split: write eval/labels/test.lock with a hash of every test label file. `run_eval.py --final` refuses to run if the
test labels changed since (or if there is no lock), so a threshold cannot be tuned on the test set by accident or by "fixing a label".

    eval/.venv/bin/python eval/e2e/lock_test.py            # after the last test label is written and you stopped tuning
    eval/.venv/bin/python eval/e2e/lock_test.py --verify
"""
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from eval.e2e import labels  # noqa: E402

LOCK = labels.LABELS_DIR / "test.lock"


def digest() -> str:
    h = hashlib.sha256()
    for c in labels.load_dir(splits={"test"}):
        p = labels.LABELS_DIR / f"{Path(c.clip).stem}.json"
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def verify() -> bool:
    return LOCK.exists() and LOCK.read_text().strip() == digest()


if __name__ == "__main__":
    if "--verify" in sys.argv:
        print("test split matches the lock" if verify() else "test split is NOT locked or changed since the lock")
        sys.exit(0 if verify() else 1)
    if LOCK.exists():
        sys.exit(f"{LOCK} exists; delete it by hand if you really mean to relock (and say why in the log)")
    if not labels.load_dir(splits={"test"}):
        sys.exit("no test clips")
    LOCK.write_text(digest() + "\n")
    print(f"locked: {LOCK.relative_to(REPO)}")
