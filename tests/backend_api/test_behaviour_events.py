"""Behaviour events (falls, fights, restricted areas, ROADMAP_V2 V2-3): schema 2 in, the API out, the Telegram alert, and
the per-camera rules (behaviors, zones) on their way to the engine."""

import pytest
from sqlalchemy import text

from backend.captions import behaviour_caption
from .helpers import ENGINE_EVENT, ENGINE_FALL_EVENT, event_body, make_camera
from .test_notifications import FakeTelegram, notify_status
from .test_websocket import wait_for

URL = "/internal/events"
JSON = {"Content-Type": "application/json"}
SQUARE = [[0.1, 0.1], [0.9, 0.1], [0.9, 0.9], [0.1, 0.9]]


# ---- events -----------------------------------------------------------------------------------------


def test_the_engines_own_fall_json_is_accepted_and_shown(client):
    make_camera(client)
    assert client.post(URL, content=ENGINE_FALL_EVENT, headers=JSON).status_code == 201
    got = client.get("/api/events/123e4567-e89b-42d3-a456-426614174000").json()
    assert got["type"] == "FALL" and got["severity"] == "high"
    assert got["track_id"] == 12 and got["track_ids"] == [12] and got["zone_id"] is None
    assert got["timestamp"] == "2026-01-01T12:00:00.123000Z"
    assert got["occurred_at"] == "2026-01-01T11:59:58.100000Z"      # the impact, two seconds before the report


def test_a_fight_names_two_people_and_a_restricted_area_its_zone(client):
    make_camera(client)
    fight = event_body(schema_version=2, type="FIGHT", track_id=3, track_ids=[3, 7])
    area = event_body(schema_version=2, type="RESTRICTED_AREA", track_id=5, track_ids=[5], zone_id=2)
    assert client.post(URL, json=fight).status_code == 201 and client.post(URL, json=area).status_code == 201
    f = client.get(f"/api/events/{fight['event_id']}").json()
    a = client.get(f"/api/events/{area['event_id']}").json()
    assert (f["type"], f["track_ids"], f["severity"]) == ("FIGHT", [3, 7], "high")
    assert (a["type"], a["zone_id"], a["severity"]) == ("RESTRICTED_AREA", 2, "medium")


def test_a_schema_1_person_event_reads_as_before_plus_the_new_fields(client):
    make_camera(client)
    assert client.post(URL, content=ENGINE_EVENT, headers=JSON).status_code == 201
    got = client.get("/api/events/123e4567-e89b-42d3-a456-426614174000").json()
    assert got["type"] == "PERSON_DETECTED" and got["severity"] == "info"
    assert got["track_ids"] == [12] and got["zone_id"] is None and got["occurred_at"] is None


def test_an_event_stored_before_the_upgrade_has_no_track_ids_and_still_reads(client, db_engine):
    make_camera(client)
    body = event_body()
    client.post(URL, json=body)
    with db_engine.begin() as conn:     # what migration 0004 leaves in an old row
        conn.execute(text("UPDATE events SET track_ids = '[]'::jsonb, occurred_at = NULL"))
    assert client.get(f"/api/events/{body['event_id']}").json()["track_ids"] == [12]


def test_the_list_filters_by_one_or_several_types(client):
    make_camera(client)
    client.post(URL, json=event_body())
    client.post(URL, json=event_body(schema_version=2, type="FALL", track_ids=[1]))
    client.post(URL, json=event_body(schema_version=2, type="FIGHT", track_ids=[1, 2]))
    types = lambda q: sorted(e["type"] for e in client.get("/api/events", params=q).json()["items"])  # noqa: E731
    assert types({"type": "FALL"}) == ["FALL"]
    assert types({"type": "FALL,FIGHT"}) == ["FALL", "FIGHT"]
    assert len(types({})) == 3


def test_behaviour_events_reach_the_websocket_clients_with_the_new_fields(client):
    make_camera(client)
    with client.websocket_connect("/api/ws") as ws:
        client.post(URL, json=event_body(schema_version=2, type="FALL", track_ids=[4]))
        msg = ws.receive_json()
    assert msg["type"] == "event.created"
    assert msg["data"]["type"] == "FALL" and msg["data"]["severity"] == "high" and msg["data"]["track_ids"] == [4]


# ---- the alert -----------------------------------------------------------------------------------------


class AlertTelegram(FakeTelegram):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.alerts = []    # (caption, photo path or None)

    def send_alert(self, caption, photo_path=None):
        self.alerts.append((caption, photo_path))
        return not self.fail


def test_a_fall_is_sent_as_an_alert_with_its_own_text_and_the_person_event_is_not(make_client, db_engine):
    telegram = AlertTelegram()
    client = make_client(telegram=telegram, notify_interval=0.05)
    make_camera(client, name="hallway")
    fall = event_body(schema_version=2, type="FALL", track_id=9, track_ids=[9], occurred_at="2026-01-01T11:59:58.100Z")
    person = event_body(track_id=42)
    client.post(URL, json=fall)
    client.post(URL, json=person)
    assert wait_for(lambda: notify_status(db_engine, fall["event_id"]) == "sent"
                    and notify_status(db_engine, person["event_id"]) == "sent")
    (caption, photo), = telegram.alerts
    assert photo is None and caption.startswith("🚨 FALL DETECTED") and "hallway" in caption and "#9" in caption
    assert [s[1] for s in telegram.sent] == [42]            # the PERSON_DETECTED text still goes the old way


def test_a_fall_with_a_snapshot_goes_as_a_photo(make_client, db_engine, snapshot_dir):
    telegram = AlertTelegram()
    client = make_client(telegram=telegram, notify_interval=0.05)
    make_camera(client)
    name = "FALL_cam1_id9_20260101T120000_123e4567.jpg"
    (snapshot_dir / name).write_bytes(b"\xff\xd8jpeg")
    body = event_body(schema_version=2, type="FALL", track_id=9, track_ids=[9], snapshot=name)
    client.post(URL, json=body)
    assert wait_for(lambda: notify_status(db_engine, body["event_id"]) == "sent")
    assert telegram.alerts[0][1] == str(snapshot_dir / name)


def test_captions_say_what_happened_where_and_when():
    from datetime import datetime, timezone
    t = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    began = datetime(2026, 1, 1, 11, 59, 58, tzinfo=timezone.utc)
    fall = behaviour_caption("FALL", "hallway", [9], None, t, began, 0.87)
    assert fall.splitlines()[0] == "🚨 FALL DETECTED" and "Camera: hallway" in fall and "Began:" in fall and "0.87" in fall
    fight = behaviour_caption("FIGHT", "yard", [3, 7], None, t, t, 0.6)
    assert "#3 and #7" in fight and "Began:" not in fight and "Time:" in fight          # reported when it began: one time
    area = behaviour_caption("RESTRICTED_AREA", "gate", [5], 2, t, began, 1.0)
    assert "restricted zone 2" in area and area.startswith("⛔")


# ---- the rules of a camera --------------------------------------------------------------------------------


def test_a_camera_starts_with_no_rules_and_takes_rules_and_zones(client):
    cam = make_camera(client)
    assert cam["behaviors"] == [] and cam["zones"] == []
    got = client.patch(f"/api/cameras/{cam['id']}", json={
        "behaviors": ["fall", "zones"],
        "zones": [{"kind": "restricted", "points": SQUARE}, {"kind": "lie_ok", "zone_id": 7, "points": SQUARE},
                  {"kind": "ignore", "points": SQUARE}],
    })
    assert got.status_code == 200
    body = got.json()
    assert body["behaviors"] == ["fall", "zones"]
    assert [(z["kind"], z["zone_id"]) for z in body["zones"]] == [("restricted", 1), ("lie_ok", 7), ("ignore", 2)]   # free numbers are filled in


def test_the_fall_network_is_off_by_default_and_takes_0_1_2(client):
    assert make_camera(client)["fall_net"] == 0
    cam = make_camera(client, "hall", behaviors=["fall"], fall_net=1)
    assert cam["fall_net"] == 1
    for value in (0, 2):
        assert client.patch(f"/api/cameras/{cam['id']}", json={"fall_net": value}).json()["fall_net"] == value


@pytest.mark.parametrize("patch", [
    {"fall_net": 3},
    {"fall_net": -1},
    {"fall_net": None},
    {"fall_net": "network"},
    {"fall_net": 1.5},
    {"behaviors": ["fall", "fall"]},
    {"behaviors": ["jump"]},
    {"zones": [{"kind": "restricted", "points": SQUARE[:2]}]},                              # fewer than 3 points
    {"zones": [{"kind": "restricted", "points": [[0.1, 0.1], [1.5, 0.1], [0.5, 0.9]]}]},   # outside the frame
    {"zones": [{"kind": "restricted", "points": [[0.1, 0.1, 0.3], [0.9, 0.1], [0.5, 0.9]]}]},
    {"zones": [{"kind": "wall", "points": SQUARE}]},
    {"zones": [{"kind": "restricted", "zone_id": 1, "points": SQUARE}, {"kind": "ignore", "zone_id": 1, "points": SQUARE}]},
    {"zones": [{"kind": "restricted", "points": SQUARE}] * 9},
    {"zones": None},
])
def test_bad_rules_are_422(client, patch):
    cam = make_camera(client)
    assert client.patch(f"/api/cameras/{cam['id']}", json=patch).status_code == 422


def test_migration_0006_adds_the_fall_network_choice_and_downgrades(database_url, db_engine):
    from .conftest import migrate
    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO cameras (id, name, source_type, source_uri, fall_net) VALUES (1, 'c', 'file', 'file:///a.mp4', 2)"))
    try:
        migrate(database_url, "0005", downgrade=True)
        with db_engine.connect() as conn:
            cols = {r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'cameras'"))}
        assert "fall_net" not in cols
    finally:
        migrate(database_url)
    with db_engine.begin() as conn:
        conn.execute(text("INSERT INTO cameras (id, name, source_type, source_uri) VALUES (2, 'd', 'file', 'file:///b.mp4')"))
        assert conn.execute(text("SELECT fall_net FROM cameras WHERE id = 2")).scalar_one() == 0     # an older camera keeps the rules
    with pytest.raises(Exception):
        with db_engine.begin() as conn:
            conn.execute(text("UPDATE cameras SET fall_net = 3 WHERE id = 2"))
