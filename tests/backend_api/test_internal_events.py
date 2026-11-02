import json
import threading
import uuid

from sqlalchemy import text

from .helpers import ENGINE_EVENT, event_body, make_camera

URL = "/internal/events"
JSON = {"Content-Type": "application/json"}


def rows(engine, sql="SELECT count(*) FROM events"):
    with engine.connect() as conn:
        return conn.execute(text(sql)).scalar_one()


def test_the_engines_own_json_is_accepted_and_stored(client, db_engine):
    make_camera(client)
    resp = client.post(URL, content=ENGINE_EVENT, headers=JSON)
    assert resp.status_code == 201
    assert resp.json() == {"event_id": "123e4567-e89b-42d3-a456-426614174000", "duplicate": False}

    got = client.get("/api/events/123e4567-e89b-42d3-a456-426614174000").json()
    assert got["camera_id"] == 1 and got["track_id"] == 12 and got["confidence"] == 0.91
    assert got["timestamp"] == "2026-01-01T12:00:00.123000Z"
    assert got["bbox"] == [0.1, 0.2, 0.3, 0.4]
    assert got["snapshot_url"] is None
    assert got["status"] == "new" and got["notify_status"] == "pending"


def test_a_repeated_event_is_accepted_and_stored_once(client, db_engine):
    make_camera(client)
    body = event_body()
    assert client.post(URL, json=body).status_code == 201
    again = client.post(URL, json=body)
    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert rows(db_engine) == 1


def test_a_repeat_does_not_overwrite_what_is_stored(client, db_engine):
    make_camera(client)
    body = event_body()
    client.post(URL, json=body)
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE events SET notify_status = 'sent'"))
    client.post(URL, json={**body, "confidence": 0.1})
    assert client.get(f"/api/events/{body['event_id']}").json()["notify_status"] == "sent"
    assert client.get(f"/api/events/{body['event_id']}").json()["confidence"] == 0.91


def test_simultaneous_deliveries_of_one_event_store_one_row(make_client, db_engine):
    make_client().post("/api/cameras", json={"name": "c", "source_type": "file", "source_uri": "file:///a.mp4"})
    body = event_body()
    codes = []

    def send():
        with make_client() as c:
            codes.append(c.post(URL, json=body).status_code)

    threads = [threading.Thread(target=send) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200] * 7 + [201]
    assert rows(db_engine) == 1


def test_unknown_camera_is_422_so_the_engine_drops_it(client, db_engine):
    resp = client.post(URL, json=event_body(camera_id=42))
    assert resp.status_code == 422
    assert rows(db_engine) == 0


def test_events_of_a_deleted_camera_are_still_stored(client, db_engine):
    cam = make_camera(client)
    client.delete(f"/api/cameras/{cam['id']}")
    assert client.post(URL, json=event_body()).status_code == 201   # sent before the engine learned of it


def test_bad_payloads_are_422(client, db_engine):
    make_camera(client)
    bad = [
        event_body(schema_version=2),
        event_body(type="CAT_DETECTED"),
        event_body(event_id="not-a-uuid"),
        event_body(confidence=1.5),
        event_body(confidence=-0.1),
        event_body(bbox=[0.1, 0.2, 0.3]),
        event_body(bbox=[0.1, 0.2, 0.3, 1.2]),
        event_body(track_id=-1),
        event_body(camera_id=-1),
        event_body(timestamp="2026-01-01T12:00:00"),   # no time zone
        event_body(snapshot="../../etc/passwd"),
        event_body(snapshot="a/b.jpg"),
        event_body(snapshot="photo.png"),
    ]
    for body in bad:
        assert client.post(URL, json=body).status_code == 422, body
    assert client.post(URL, content="not json", headers=JSON).status_code == 422
    assert rows(db_engine) == 0


def test_unknown_fields_are_ignored(client):
    make_camera(client)
    assert client.post(URL, json={**event_body(), "added_in_schema_v2": [1, 2]}).status_code == 201


def test_snapshot_name_is_stored(client):
    make_camera(client)
    name = "PERSON_DETECTED_cam1_id12_20260101T120000_123e4567.jpg"
    body = event_body(snapshot=name)
    assert client.post(URL, json=body).status_code == 201
    got = client.get(f"/api/events/{body['event_id']}").json()
    assert got["snapshot_url"] == f"/api/events/{body['event_id']}/snapshot"


def test_token_is_required_when_configured(make_client, db_engine):
    client = make_client(internal_token="s3cret")
    make_camera(client)
    body = event_body()
    assert client.post(URL, json=body).status_code == 401
    assert client.post(URL, json=body, headers={"X-Internal-Token": "wrong"}).status_code == 401
    assert rows(db_engine) == 0
    assert client.post(URL, json=body, headers={"X-Internal-Token": "s3cret"}).status_code == 201
    # the public API does not need the token
    assert client.get("/api/events").status_code == 200


def test_no_token_configured_means_open(client):
    make_camera(client)
    assert client.post(URL, json=event_body()).status_code == 201


def test_largest_track_id_the_database_holds(client):
    make_camera(client)
    assert client.post(URL, json=event_body(track_id=2**63 - 1)).status_code == 201
    assert client.post(URL, json=event_body(track_id=2**63)).status_code == 422
