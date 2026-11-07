"""The Settings page: GET/PATCH /api/settings, the alert rules, the snapshot retention, what a new camera starts with."""

import os
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from backend.app_settings import AppSettings
from .conftest import migrate
from .helpers import event_body, make_camera
from .test_notifications import FakeTelegram, notify_status
from .test_websocket import wait_for

URL = "/internal/events"


class TextTelegram(FakeTelegram):
    """A Telegram that can also send a plain message (what the "send a test message" button uses)."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.texts = []

    def send_text(self, text):
        self.texts.append(text)
        return not self.fail


@pytest.fixture
def running(make_client):
    """A client whose notifier runs; the fake Telegram is `client.telegram`, and one camera exists."""
    def factory(telegram=None, **kw):
        tg = telegram or FakeTelegram()
        client = make_client(telegram=tg, notify_interval=0.05, **kw)
        client.telegram = tg
        make_camera(client)
        return client
    return factory


def hhmm(moment: datetime) -> str:
    return moment.strftime("%H:%M")


# ---- reading and changing ----------------------------------------------------------------------------


def test_defaults_are_the_documented_ones(client):
    body = client.get("/api/settings").json()
    assert body == {
        "alerts_enabled": True,
        "quiet_hours_enabled": False,
        "quiet_start": "22:00",
        "quiet_end": "06:00",
        "snapshot_retention_days": 7,
        "default_conf_threshold": 0.25,
        "default_absence_timeout": 10.0,
        "telegram_configured": False,
        "snapshot_count": 0,
        "snapshot_bytes": 0,
        "version": body["version"],
    }
    assert body["version"]


def test_what_nobody_changed_comes_from_the_environment(make_client):
    body = make_client(snapshot_retention_days=30, default_conf_threshold=0.4, default_absence_timeout=20.0).get("/api/settings").json()
    assert (body["snapshot_retention_days"], body["default_conf_threshold"], body["default_absence_timeout"]) == (30, 0.4, 20.0)


def test_patch_changes_only_what_is_sent_and_it_is_kept(client, make_client):
    resp = client.patch("/api/settings", json={"alerts_enabled": False, "snapshot_retention_days": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert (body["alerts_enabled"], body["snapshot_retention_days"]) == (False, 3)
    assert (body["quiet_start"], body["default_conf_threshold"]) == ("22:00", 0.25)   # the rest untouched
    again = make_client().get("/api/settings").json()   # a second backend on the same database: it is stored, not in memory
    assert (again["alerts_enabled"], again["snapshot_retention_days"]) == (False, 3)
    assert client.patch("/api/settings", json={}).json() == client.get("/api/settings").json()   # nothing to change is fine


@pytest.mark.parametrize("body", [
    {"quiet_start": "25:00"},
    {"quiet_end": "9:00"},
    {"quiet_start": "22:60"},
    {"quiet_start": "noon"},
    {"snapshot_retention_days": 0},
    {"snapshot_retention_days": 366},
    {"snapshot_retention_days": 7.5},
    {"default_conf_threshold": 1.5},
    {"default_conf_threshold": -0.1},
    {"default_absence_timeout": -1},
    {"default_absence_timeout": 86401},
    {"alerts_enabled": None},
    {"unknown_setting": 1},
])
def test_a_bad_value_is_refused_and_nothing_changes(client, body):
    before = client.get("/api/settings").json()
    assert client.patch("/api/settings", json=body).status_code == 422
    assert client.get("/api/settings").json() == before


def test_quiet_hours_cannot_start_and_end_at_the_same_minute(client):
    assert client.patch("/api/settings", json={"quiet_hours_enabled": True, "quiet_end": "22:00"}).status_code == 422
    assert client.get("/api/settings").json()["quiet_hours_enabled"] is False   # the whole change was refused
    # while the quiet hours are off the two times may be anything, so a page can be edited in any order
    assert client.patch("/api/settings", json={"quiet_start": "08:00", "quiet_end": "08:00"}).status_code == 200
    assert client.patch("/api/settings", json={"quiet_hours_enabled": True}).status_code == 422
    assert client.patch("/api/settings", json={"quiet_hours_enabled": True, "quiet_end": "09:00"}).status_code == 200


# ---- read-only facts ---------------------------------------------------------------------------------


def test_storage_figures_count_the_snapshots_the_cleaner_manages(client, snapshot_dir):
    (snapshot_dir / "PERSON_DETECTED_a.jpg").write_bytes(b"123")
    (snapshot_dir / "PERSON_DETECTED_b.jpg").write_bytes(b"12345")
    (snapshot_dir / "other.jpg").write_bytes(b"not ours")   # the cleaner never touches it, so it is not counted
    body = client.get("/api/settings").json()
    assert (body["snapshot_count"], body["snapshot_bytes"]) == (2, 8)


def test_telegram_configured_follows_the_notifier(make_client):
    assert make_client(telegram=FakeTelegram(enabled=True)).get("/api/settings").json()["telegram_configured"] is True
    assert make_client(telegram=FakeTelegram(enabled=False)).get("/api/settings").json()["telegram_configured"] is False


# ---- the test message --------------------------------------------------------------------------------


def test_the_test_message_says_when_telegram_is_not_set_up(client):
    body = client.post("/api/settings/test-notification").json()
    assert body["ok"] is False and "not set up" in body["message"]


def test_the_test_message_is_sent_even_when_alerts_are_off(make_client):
    tg = TextTelegram()
    client = make_client(telegram=tg)
    client.patch("/api/settings", json={"alerts_enabled": False})
    body = client.post("/api/settings/test-notification").json()
    assert body["ok"] is True
    assert len(tg.texts) == 1


def test_the_test_message_reports_a_telegram_that_refuses_or_breaks(make_client):
    refusing = make_client(telegram=TextTelegram(fail=True))
    body = refusing.post("/api/settings/test-notification").json()
    assert body["ok"] is False and "did not accept" in body["message"]

    class Breaking(TextTelegram):
        def send_text(self, text):
            raise RuntimeError("boom")

    assert make_client(telegram=Breaking()).post("/api/settings/test-notification").json()["ok"] is False   # not a 500
    assert make_client(telegram=FakeTelegram()).post("/api/settings/test-notification").json()["ok"] is False   # cannot send text


# ---- the alert rules ---------------------------------------------------------------------------------


@pytest.mark.parametrize("start,end,at,quiet", [
    ("22:00", "06:00", "23:30", True),    # past midnight: the evening part
    ("22:00", "06:00", "03:00", True),    # ... and the morning part
    ("22:00", "06:00", "22:00", True),    # the start minute is quiet
    ("22:00", "06:00", "06:00", False),   # the end minute is not
    ("22:00", "06:00", "12:00", False),
    ("22:00", "06:00", "21:59", False),
    ("13:00", "15:00", "14:59", True),    # within one day
    ("13:00", "15:00", "15:00", False),
    ("13:00", "15:00", "03:00", False),
    ("08:00", "08:00", "08:00", False),   # no hours at all
])
def test_quiet_hours_cover_the_start_but_not_the_end_and_may_pass_midnight(start, end, at, quiet):
    rules = AppSettings(quiet_hours_enabled=True, quiet_start=start, quiet_end=end)
    hours, minutes = map(int, at.split(":"))
    assert rules.in_quiet_hours(datetime(2026, 1, 1, hours, minutes, 30)) is quiet


def test_quiet_hours_off_means_never_quiet():
    assert AppSettings(quiet_hours_enabled=False).in_quiet_hours(datetime(2026, 1, 1, 23, 0)) is False


def test_alerts_off_stores_and_shows_the_event_but_sends_nothing(running, db_engine):
    client = running()
    client.patch("/api/settings", json={"alerts_enabled": False})
    skipped = event_body()
    assert client.post(URL, json=skipped).status_code == 201
    assert wait_for(lambda: notify_status(db_engine, skipped["event_id"]) == "skipped")
    assert client.telegram.sent == []
    assert client.get("/api/events").json()["total"] == 1   # the event is there for the web app
    assert client.get(f"/api/events/{skipped['event_id']}").json()["notify_status"] == "skipped"

    client.patch("/api/settings", json={"alerts_enabled": True})   # back on: the next one is sent
    sent = event_body()
    client.post(URL, json=sent)
    assert wait_for(lambda: notify_status(db_engine, sent["event_id"]) == "sent")
    assert len(client.telegram.sent) == 1


def test_quiet_hours_hold_back_the_alert_only_inside_them(running, db_engine):
    client = running()
    now = datetime.now().astimezone()
    inside = {"quiet_hours_enabled": True, "quiet_start": hhmm(now - timedelta(hours=1)), "quiet_end": hhmm(now + timedelta(hours=1))}
    assert client.patch("/api/settings", json=inside).status_code == 200
    quiet = event_body()
    client.post(URL, json=quiet)
    assert wait_for(lambda: notify_status(db_engine, quiet["event_id"]) == "skipped")
    assert client.telegram.sent == []

    outside = {"quiet_start": hhmm(now + timedelta(hours=2)), "quiet_end": hhmm(now + timedelta(hours=3))}
    assert client.patch("/api/settings", json=outside).status_code == 200
    loud = event_body()
    client.post(URL, json=loud)
    assert wait_for(lambda: notify_status(db_engine, loud["event_id"]) == "sent")


def test_a_skip_is_announced_to_open_browsers_like_any_other_change(running, db_engine):
    client = running()
    client.patch("/api/settings", json={"alerts_enabled": False})
    with client.websocket_connect("/api/ws") as ws:
        client.post(URL, json=event_body())
        seen = []

        def got_the_update() -> bool:
            seen.append(ws.receive_json())
            return any(m["type"] == "event.updated" and m["data"]["notify_status"] == "skipped" for m in seen)

        assert wait_for(got_the_update, timeout=5)


# ---- what a new camera starts with -------------------------------------------------------------------


def test_a_new_camera_starts_with_the_defaults_of_the_page_unless_it_says_otherwise(client):
    old = make_camera(client, "old one")
    client.patch("/api/settings", json={"default_conf_threshold": 0.5, "default_absence_timeout": 30})
    new = make_camera(client, "new one")
    assert (new["conf_threshold"], new["absence_timeout"]) == (0.5, 30.0)
    chosen = make_camera(client, "chosen", conf_threshold=0.3, absence_timeout=5)   # what the form sends: it wins
    assert (chosen["conf_threshold"], chosen["absence_timeout"]) == (0.3, 5.0)
    assert client.get(f"/api/cameras/{old['id']}").json()["conf_threshold"] == 0.25   # existing cameras are not touched


# ---- the snapshot retention --------------------------------------------------------------------------


def test_the_retention_setting_decides_which_snapshots_the_cleaner_removes(client, snapshot_dir):
    old = snapshot_dir / "PERSON_DETECTED_cam1_old.jpg"
    old.write_bytes(b"x")
    three_days_ago = time.time() - 3 * 86400
    os.utime(old, (three_days_ago, three_days_ago))

    janitor = client.app.state.janitor
    janitor.run_once()
    assert old.exists()   # 7 days is the default: 3 days old stays

    client.patch("/api/settings", json={"snapshot_retention_days": 2})
    assert janitor.due()   # the change asks for a pass soon instead of in an hour
    janitor.run_once()
    assert not old.exists()


# ---- the migration -----------------------------------------------------------------------------------


def test_migration_0003_downgrades_by_turning_skipped_into_disabled_and_upgrades_again(database_url, db_engine):
    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO cameras (id, name, source_type, source_uri) VALUES (1, 'c', 'file', 'file:///a.mp4')"))
        conn.execute(text(
            "INSERT INTO events (event_id, camera_id, pipeline_run_id, type, track_id, confidence, timestamp, bbox, notify_status) "
            "VALUES ('123e4567-e89b-42d3-a456-426614174000', 1, '0f8fad5b-d9cb-469f-a165-70867728950e', 'PERSON_DETECTED', 1, 0.9, now(), "
            "'[0.1,0.2,0.3,0.4]'::jsonb, 'skipped')"))
        conn.execute(text("INSERT INTO app_settings (key, value) VALUES ('alerts_enabled', 'false'::jsonb)"))
    try:
        migrate(database_url, "0002", downgrade=True)
        with db_engine.connect() as conn:
            assert conn.execute(text("SELECT notify_status FROM events")).scalar_one() == "disabled"
            tables = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))}
        assert "app_settings" not in tables
    finally:
        migrate(database_url)
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM app_settings")).scalar_one() == 0
