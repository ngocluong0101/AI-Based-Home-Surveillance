"""The VLM check of falls and fights (V2-6): stored as pending, settled by the verifier, Telegram held back meanwhile - and above all
FAIL-OPEN: a VLM that errs, is unsure, is slow or is absent never keeps an alert from going out."""
import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import text

from backend.frames import FrameSource
from backend.vlm_client import VlmAnswer, VlmClient

from .helpers import ENGINE_FALL_EVENT, event_body, make_camera
from .test_websocket import recv

URL = "/internal/events"
JPEG = b"\xff\xd8\xff\xe0fake"


class FakeVlm:
    def __init__(self, verdict="yes", p_yes=0.9, reason="a person is lying on the floor", raises=None, delay=0.0):
        self.verdict, self.p_yes, self.reason, self.raises, self.delay = verdict, p_yes, reason, raises, delay
        self.calls = []

    def verify(self, event_type, frames, seconds_apart, reason=True):
        self.calls.append((event_type, len(frames), seconds_apart))
        time.sleep(self.delay)
        if self.raises:
            raise self.raises
        return VlmAnswer(self.verdict, self.p_yes, self.reason, 0.4)

    def close(self):
        pass


class FakeTelegram:
    enabled = True

    def __init__(self):
        self.alerts = []

    def send_alert(self, caption, photo_path=None):
        self.alerts.append(caption)
        return True

    def send_person_detected(self, event):
        return True

    send_person_detected_photo = send_person_detected


@pytest.fixture
def checked(make_client, snapshot_dir):
    """client with a VLM stand-in and a camera; a snapshot exists for events that name `snap.jpg`"""
    def factory(vlm=None, **kw):
        tg = FakeTelegram()
        vlm = vlm or FakeVlm()
        client = make_client(telegram=tg, vlm_client=vlm, vlm_url="http://vlm.invalid", verify_timeout=kw.pop("verify_timeout", 8.0), **kw)
        client.tg, client.vlm = tg, vlm
        make_camera(client)
        (snapshot_dir / "FALL_cam1_snap.jpg").write_bytes(JPEG)
        return client
    return factory


def fall(**kw):
    return event_body(type="FALL", track_ids=[12], occurred_at="2026-01-01T11:59:58.100Z", schema_version=2, snapshot="FALL_cam1_snap.jpg", **kw)


def row(db_engine, event_id):
    with db_engine.connect() as c:
        return c.execute(text("SELECT verification, verification_score, verification_reason, notify_status FROM events WHERE event_id=:i"),
                         {"i": event_id}).one()


def notify_round(client):
    client.app.state.notifier.run_once()


def test_falls_and_fights_wait_for_the_vlm_other_events_do_not(checked, db_engine):
    client = checked()
    ids = {}
    for kind in ("FALL", "FIGHT", "RESTRICTED_AREA", "PERSON_DETECTED"):
        extra = dict(track_ids=[1], occurred_at=None, schema_version=2) if kind != "PERSON_DETECTED" else {}
        extra.update(zone_id=1) if kind == "RESTRICTED_AREA" else None
        b = event_body(type=kind, **extra)
        assert client.post(URL, json=b).status_code == 201, kind
        ids[kind] = b["event_id"]
    assert {k: row(db_engine, v).verification for k, v in ids.items()} == {
        "FALL": "pending", "FIGHT": "pending", "RESTRICTED_AREA": "not_required", "PERSON_DETECTED": "not_required"}


def test_without_a_vlm_nothing_is_ever_pending(make_client, db_engine):
    client = make_client(telegram=FakeTelegram())
    make_camera(client)
    b = fall()
    client.post(URL, json=b)
    assert row(db_engine, b["event_id"]).verification == "not_required"
    assert client.app.state.verifier is None


def test_the_telegram_message_waits_for_the_verdict_and_then_goes_out(checked, db_engine):
    client = checked(FakeVlm("yes", 0.93, "the person is on the floor"))
    b = fall()
    client.post(URL, json=b)
    notify_round(client)
    assert client.tg.alerts == [] and row(db_engine, b["event_id"]).notify_status == "pending"        # held back
    assert client.app.state.verifier.run_once() == 1
    r = row(db_engine, b["event_id"])
    assert (r.verification, r.verification_score) == ("confirmed", pytest.approx(0.93)) and "on the floor" in r.verification_reason
    notify_round(client)
    assert len(client.tg.alerts) == 1 and "confirmed" in client.tg.alerts[0]
    assert client.vlm.calls == [("FALL", 1, 0.0)]       # the snapshot is the only frame without a ring


def test_a_rejected_event_is_stored_and_shown_but_not_sent(checked, db_engine):
    client = checked(FakeVlm("no", 0.02, "the person is sitting on a sofa"))
    b = fall()
    client.post(URL, json=b)
    client.app.state.verifier.run_once()
    notify_round(client)
    assert client.tg.alerts == []
    r = row(db_engine, b["event_id"])
    assert (r.verification, r.notify_status) == ("rejected", "skipped")
    shown = client.get(f"/api/events/{b['event_id']}").json()
    assert shown["verification"] == "rejected" and "sofa" in shown["verification_reason"] and shown["type"] == "FALL"
    assert client.get("/api/events", params={"verification": "rejected"}).json()["total"] == 1


@pytest.mark.parametrize("vlm", [
    FakeVlm("unsure", 0.4, "hard to tell"),
    FakeVlm(raises=httpx.ConnectError("down")),
    FakeVlm(raises=ValueError("garbage")),
])
def test_an_unsure_or_broken_vlm_never_stops_the_alert(checked, db_engine, vlm):
    client = checked(vlm)
    b = fall()
    client.post(URL, json=b)
    client.app.state.verifier.run_once()
    notify_round(client)
    assert row(db_engine, b["event_id"]).verification == "unverified"
    assert len(client.tg.alerts) == 1 and "Not checked by the VLM" in client.tg.alerts[0]


def test_no_picture_at_all_is_unverified_not_stuck(checked, db_engine):
    client = checked()
    b = event_body(type="FALL", track_ids=[1], occurred_at=None, schema_version=2)      # no snapshot named, no ring
    client.post(URL, json=b)
    client.app.state.verifier.run_once()
    r = row(db_engine, b["event_id"])
    assert r.verification == "unverified" and "no picture" in r.verification_reason
    notify_round(client)
    assert len(client.tg.alerts) == 1


def test_a_vlm_that_is_too_slow_does_not_hold_the_alert_beyond_the_timeout(checked, db_engine):
    client = checked(verify_timeout=5.0)
    b = fall()
    client.post(URL, json=b)
    with db_engine.begin() as c:     # the event arrived 6 s ago and the verifier has not got to it
        c.execute(text("UPDATE events SET received_at = :t WHERE event_id=:i"),
                  {"t": datetime.now(timezone.utc) - timedelta(seconds=6), "i": b["event_id"]})
    notify_round(client)
    assert len(client.tg.alerts) == 1                                  # sent although still pending: fail-open
    assert row(db_engine, b["event_id"]).verification == "pending"
    client.app.state.verifier.run_once()
    r = row(db_engine, b["event_id"])
    assert r.verification == "unverified" and "had not answered" in r.verification_reason and client.vlm.calls == []


def test_a_verdict_that_comes_after_the_alert_is_recorded_but_cannot_unsend_it(checked, db_engine):
    client = checked(FakeVlm("no", 0.01, "nothing"), verify_timeout=5.0)
    b = fall()
    client.post(URL, json=b)
    with db_engine.begin() as c:
        c.execute(text("UPDATE events SET received_at = :t WHERE event_id=:i"),
                  {"t": datetime.now(timezone.utc) - timedelta(seconds=4), "i": b["event_id"]})
        c.execute(text("UPDATE events SET notify_status='sent' WHERE event_id=:i"), {"i": b["event_id"]})
    client.app.state.verifier.run_once()
    r = row(db_engine, b["event_id"])
    assert (r.verification, r.notify_status) == ("rejected", "sent")


def test_the_browser_hears_about_each_verdict(checked):
    client = checked(FakeVlm("yes", 0.9, "ok"))
    with client.websocket_connect("/api/ws") as ws:
        b = fall()
        client.post(URL, json=b)
        created = recv(ws)
        assert created["type"] == "event.created" and created["data"]["verification"] == "pending"
        client.app.state.verifier.run_once()
        updated = recv(ws)
        assert updated["type"] == "event.updated" and updated["data"]["verification"] == "confirmed"


def test_unknown_verification_filter_is_422(client):
    assert client.get("/api/events", params={"verification": "maybe"}).status_code == 422


def test_the_engines_own_fall_json_is_still_accepted(checked, db_engine):
    client = checked()
    body = json.loads(ENGINE_FALL_EVENT)
    assert client.post(URL, json=body).status_code == 201
    assert row(db_engine, body["event_id"]).verification == "pending"


# ---- the pieces ----------------------------------------------------------------------------------


def test_frames_come_from_the_ring_around_the_event_and_fall_back_to_the_snapshot(tmp_path):
    ring, snaps = tmp_path / "ring", tmp_path / "snaps"
    ring.mkdir(), snaps.mkdir()
    base = 1_700_000_000.0
    for i in range(40):                                              # a frame every 0.5 s, 20 s of them
        (ring / f"ring_cam1_{int((base + i * 0.5) * 1000)}.jpg").write_bytes(bytes([i]))
    (ring / f"ring_cam2_{int(base * 1000)}.jpg").write_bytes(b"other camera")
    (ring / "notes.txt").write_text("not a frame")
    (snaps / "s.jpg").write_bytes(b"snapshot")
    fs = FrameSource(snaps, ring, n=6, before=2.5, after=0.5)

    got = fs.for_event(1, occurred_ts=base + 10.0, reported_ts=base + 10.3, snapshot_file="s.jpg")
    assert got.source == "ring" and len(got.jpegs) == 6
    times = [b[0] * 0.5 for b in got.jpegs]                          # frame i is at 0.5 * i s after base
    assert min(times) >= 7.5 and max(times) <= 10.8 and times == sorted(times)      # [occurred - 2.5, reported + 0.5]
    assert got.seconds_apart == pytest.approx((times[-1] - times[0]) / 5)

    far = fs.for_event(1, base + 100, base + 100.3, "s.jpg")           # nothing in the ring there
    assert far.source == "snapshot" and far.jpegs == [b"snapshot"]
    assert fs.for_event(1, base + 100, base + 100.3, "gone.jpg").jpegs == []
    assert FrameSource(snaps, None).for_event(1, base, base, "s.jpg").source == "snapshot"      # no ring configured


def test_the_client_posts_base64_frames_and_checks_the_answer():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        verdict = seen.get("force") or "yes"
        return httpx.Response(200, json={"verdict": verdict, "p_yes": 0.8, "reason": "r", "latency_s": 1.0})

    client = VlmClient("http://vlm", 3.0, transport=httpx.MockTransport(handler))
    a = client.verify("FALL", [b"abc", b"def"], 0.5)
    assert (a.verdict, a.p_yes) == ("yes", 0.8)
    assert [base64.b64decode(f) for f in seen["frames"]] == [b"abc", b"def"] and seen["event_type"] == "FALL"
    bad = VlmClient("http://vlm", 3.0, transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"verdict": "maybe"})))
    with pytest.raises(ValueError):
        bad.verify("FALL", [b"x"], 0.1)
    err = VlmClient("http://vlm", 3.0, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        err.verify("FALL", [b"x"], 0.1)


def test_with_a_ring_the_verifier_waits_for_the_frames_after_the_event(make_client, db_engine, snapshot_dir, tmp_path):
    vlm = FakeVlm()
    client = make_client(telegram=FakeTelegram(), vlm_client=vlm, vlm_url="http://vlm.invalid", frame_ring_dir=str(tmp_path))
    make_camera(client)
    (snapshot_dir / "FALL_cam1_snap.jpg").write_bytes(JPEG)
    b = fall()
    client.post(URL, json=b)
    assert client.app.state.verifier.settle >= 2.0
    assert client.app.state.verifier.run_once() == 0 and vlm.calls == []          # too early: the frames after the fall are not there yet
    with db_engine.begin() as c:
        c.execute(text("UPDATE events SET received_at = :t WHERE event_id=:i"), {"t": datetime.now(timezone.utc) - timedelta(seconds=3), "i": b["event_id"]})
    assert client.app.state.verifier.run_once() == 1 and len(vlm.calls) == 1
