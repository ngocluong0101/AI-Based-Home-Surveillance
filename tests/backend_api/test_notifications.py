"""Telegram alerts from the background notifier, snapshot cleanup, PATCH /api/events (ROADMAP 3.8)."""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from .helpers import event_body, make_camera
from .test_websocket import recv, wait_for

URL = "/internal/events"


class FakeTelegram:
    """Stands in for TelegramNotifier: records what it was asked to send."""

    def __init__(self, enabled=True, delay=0.0, fail=False):
        self.enabled = enabled
        self.delay = delay
        self.fail = fail
        self.sent = []   # ("text" | "photo", track_id, local time, path)

    def _send(self, kind, event, path=None):
        time.sleep(self.delay)
        self.sent.append((kind, event.track_id, event.timestamp, path))
        return not self.fail

    def send_person_detected(self, event):
        return self._send("text", event)

    def send_person_detected_photo(self, event, photo_path):
        return self._send("photo", event, photo_path)


def notify_status(engine, event_id) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT notify_status FROM events WHERE event_id = :i"), {"i": event_id}).scalar_one()


@pytest.fixture
def notified(make_client):
    """A client with a running notifier; the fake Telegram is `client.telegram`."""
    def factory(**kw):
        tg = kw.pop("telegram", None) or FakeTelegram()
        kw.setdefault("notify_interval", 0.05)
        client = make_client(telegram=tg, **kw)
        client.telegram = tg
        make_camera(client)
        return client
    return factory


# ---- the notifier ----------------------------------------------------------------------------------


def test_a_new_event_is_sent_to_telegram_and_marked_sent(notified, db_engine):
    client = notified()
    body = event_body(track_id=42)
    assert client.post(URL, json=body).status_code == 201
    assert wait_for(lambda: notify_status(db_engine, body["event_id"]) == "sent")
    kind, track_id, when, path = client.telegram.sent[0]
    assert (kind, track_id) == ("text", 42) and path is None
    assert when.tzinfo is not None   # local time of the server, not naive UTC


def test_the_snapshot_is_sent_as_a_photo_when_the_file_is_there(notified, db_engine, snapshot_dir):
    client = notified()
    (snapshot_dir / "PERSON_DETECTED_cam1_id1_x.jpg").write_bytes(b"\xff\xd8jpeg")
    with_file = event_body(snapshot="PERSON_DETECTED_cam1_id1_x.jpg")
    missing = event_body(snapshot="PERSON_DETECTED_cam1_id2_gone.jpg")   # the cleaner took it
    client.post(URL, json=with_file)
    client.post(URL, json=missing)
    assert wait_for(lambda: len(client.telegram.sent) == 2)
    kinds = sorted(k for k, *_ in client.telegram.sent)
    assert kinds == ["photo", "text"]
    assert [p for k, _, _, p in client.telegram.sent if k == "photo"] == [str(snapshot_dir / "PERSON_DETECTED_cam1_id1_x.jpg")]


def test_a_slow_telegram_does_not_slow_down_the_engines_post(notified, db_engine):
    client = notified(telegram=FakeTelegram(delay=3.0))
    first, second = event_body(), event_body()
    start = time.monotonic()
    assert client.post(URL, json=first).status_code == 201
    assert client.post(URL, json=second).status_code == 201
    assert time.monotonic() - start < 1.0   # both stored while Telegram is still busy with the first
    assert notify_status(db_engine, second["event_id"]) == "pending"
    assert wait_for(lambda: notify_status(db_engine, second["event_id"]) == "sent", timeout=15)


def test_without_telegram_events_are_marked_disabled(notified, db_engine):
    client = notified(telegram=FakeTelegram(enabled=False))
    body = event_body()
    client.post(URL, json=body)
    assert wait_for(lambda: notify_status(db_engine, body["event_id"]) == "disabled")
    assert client.telegram.sent == []


def test_a_failing_telegram_keeps_the_event_pending_and_the_event_itself_safe(notified, db_engine):
    client = notified(telegram=FakeTelegram(fail=True))
    body = event_body()
    assert client.post(URL, json=body).status_code == 201
    assert wait_for(lambda: len(client.telegram.sent) >= 1)
    assert notify_status(db_engine, body["event_id"]) == "pending"
    assert client.get(f"/api/events/{body['event_id']}").status_code == 200

    client.telegram.fail = False   # Telegram is back (the retry pause is 1 s)
    assert wait_for(lambda: notify_status(db_engine, body["event_id"]) == "sent", timeout=10)


def test_an_alert_that_is_too_late_is_marked_failed_not_sent(notified, db_engine):
    client = notified(telegram=FakeTelegram(fail=True), notify_max_age=0.3)
    body = event_body()
    client.post(URL, json=body)
    assert wait_for(lambda: len(client.telegram.sent) >= 1)
    assert wait_for(lambda: notify_status(db_engine, body["event_id"]) == "failed", timeout=10)


def test_pending_events_from_before_a_restart_are_sent_oldest_first(make_client, db_engine):
    client = make_client()   # notifier off: the events stay pending
    make_camera(client)
    a, b = event_body(track_id=1), event_body(track_id=2)
    client.post(URL, json=a)
    client.post(URL, json=b)
    tg = FakeTelegram()
    from backend.notifier import Notifier

    notifier = Notifier(client.app.state.session_factory, tg, "unused", 1.0, 600.0)
    assert notifier.run_once() is True
    assert [t for _, t, _, _ in tg.sent] == [1, 2]
    assert notify_status(db_engine, a["event_id"]) == notify_status(db_engine, b["event_id"]) == "sent"


def test_the_websocket_announces_the_new_notify_status(notified):
    client = notified()
    with client.websocket_connect("/api/ws") as ws:
        body = event_body()
        client.post(URL, json=body)
        created = recv(ws)
        updated = recv(ws)
    assert created["type"] == "event.created" and created["data"]["notify_status"] == "pending"
    assert updated["type"] == "event.updated" and updated["data"]["notify_status"] == "sent"
    assert updated["data"]["event_id"] == body["event_id"]


# ---- PATCH /api/events/{id} -------------------------------------------------------------------------


def test_an_event_can_be_acknowledged_and_the_websocket_hears_of_it(client):
    make_camera(client)
    body = event_body()
    client.post(URL, json=body)
    with client.websocket_connect("/api/ws") as ws:
        resp = client.patch(f"/api/events/{body['event_id']}", json={"status": "acknowledged"})
        assert resp.status_code == 200 and resp.json()["status"] == "acknowledged"
        pushed = recv(ws)
        # the same status again changes nothing and says nothing
        assert client.patch(f"/api/events/{body['event_id']}", json={"status": "acknowledged"}).status_code == 200
        back = client.patch(f"/api/events/{body['event_id']}", json={"status": "new"})
        assert recv(ws)["data"]["status"] == "new"
    assert pushed["type"] == "event.updated" and pushed["data"]["status"] == "acknowledged"
    assert back.json()["status"] == "new"
    assert client.get("/api/events", params={"status": "new"}).json()["total"] == 1


@pytest.mark.parametrize("body", [{"status": "deleted"}, {}, {"status": "new", "confidence": 0}, {"confidence": 0.1}])
def test_a_bad_patch_is_refused(client, body):
    make_camera(client)
    event = event_body()
    client.post(URL, json=event)
    assert client.patch(f"/api/events/{event['event_id']}", json=body).status_code == 422


def test_patching_an_unknown_event_is_404(client):
    assert client.patch(f"/api/events/{event_body()['event_id']}", json={"status": "new"}).status_code == 404


# ---- the snapshot janitor ---------------------------------------------------------------------------


def age_file(path, days):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_old_snapshots_are_deleted_and_their_events_stop_pointing_at_them(client, db_engine, snapshot_dir):
    from backend.janitor import SnapshotJanitor

    make_camera(client)
    old_name, new_name = "PERSON_DETECTED_cam1_id1_old.jpg", "PERSON_DETECTED_cam1_id2_new.jpg"
    for name in (old_name, new_name):
        (snapshot_dir / name).write_bytes(b"x")
    age_file(snapshot_dir / old_name, 10)
    (snapshot_dir / "keep.txt").write_text("not a snapshot")
    age_file(snapshot_dir / "keep.txt", 30)
    old, new = event_body(snapshot=old_name), event_body(snapshot=new_name)
    client.post(URL, json=old)
    client.post(URL, json=new)
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE events SET received_at = :t WHERE event_id = :i"),
                     {"t": datetime.now(timezone.utc) - timedelta(days=10), "i": old["event_id"]})

    janitor = SnapshotJanitor(client.app.state.session_factory, snapshot_dir, 7, 100, 60)
    assert janitor.run_once() == (1, 0)

    assert not (snapshot_dir / old_name).exists() and (snapshot_dir / new_name).exists()
    assert (snapshot_dir / "keep.txt").exists()
    assert client.get(f"/api/events/{old['event_id']}").json()["snapshot_url"] is None
    assert client.get(f"/api/events/{old['event_id']}/snapshot").status_code == 404
    assert client.get(f"/api/events/{new['event_id']}").json()["snapshot_url"] is not None


def test_a_vanished_file_gives_a_clean_404(client, snapshot_dir):
    make_camera(client)
    body = event_body(snapshot="PERSON_DETECTED_cam1_id1_x.jpg")   # never written
    client.post(URL, json=body)
    resp = client.get(f"/api/events/{body['event_id']}/snapshot")
    assert resp.status_code == 404 and resp.json()["detail"]


def test_a_cleanup_is_due_after_n_snapshots_or_when_idle_for_long(client, snapshot_dir):
    from backend.janitor import SnapshotJanitor

    j = SnapshotJanitor(client.app.state.session_factory, snapshot_dir, 7, 3, 60, max_idle=3600)
    assert j.due()   # never ran
    j.run_once()
    assert not j.due()
    j.note_snapshot(); j.note_snapshot()
    assert not j.due()
    j.note_snapshot()
    assert j.due()
    j.run_once()
    assert not j.due()
    j._last_run -= 3601
    assert j.due()


def test_the_janitor_thread_runs_a_pass_at_start_up(make_client, snapshot_dir):
    path = snapshot_dir / "PERSON_DETECTED_cam1_id1_old.jpg"
    path.write_bytes(b"x")
    age_file(path, 10)
    make_client(cleanup_check_interval=0.05)
    assert wait_for(lambda: not path.exists())
