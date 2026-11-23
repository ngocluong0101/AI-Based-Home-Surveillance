"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

The engine names a snapshot after its event type; the cleaner and the usage count must know all the names.
"""
import os
import time

from backend.app_settings import snapshot_usage
from src.snapshot_management import SnapshotCleaner

NAMES = ["PERSON_DETECTED_cam1_id1_a.jpg", "FALL_cam1_id2_b.jpg", "FIGHT_cam1_id3_c.jpg", "RESTRICTED_AREA_cam1_id4_d.jpg"]


def test_old_snapshots_of_every_event_type_are_deleted_and_other_files_are_left(tmp_path):
    old = time.time() - 10 * 86400
    for n in NAMES + ["unrelated.jpg", "FALL_notes.txt"]:
        p = tmp_path / n
        p.write_bytes(b"x")
        os.utime(p, (old, old))
    (tmp_path / "FALL_cam1_id9_fresh.jpg").write_bytes(b"x")   # young: stays
    SnapshotCleaner(retention_days=3).cleanup(tmp_path)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["FALL_cam1_id9_fresh.jpg", "FALL_notes.txt", "unrelated.jpg"]


def test_usage_counts_every_event_type(tmp_path):
    for n in NAMES:
        (tmp_path / n).write_bytes(b"12345")
    (tmp_path / "other.jpg").write_bytes(b"123")
    assert snapshot_usage(tmp_path) == (4, 20)
