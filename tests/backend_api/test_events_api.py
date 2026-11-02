import uuid
from datetime import datetime, timedelta, timezone

from .helpers import event_body, make_camera

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def post(client, **overrides):
    body = event_body(**overrides)
    assert client.post("/internal/events", json=body).status_code == 201
    return body["event_id"]


def at(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat()


def seed(client):
    make_camera(client, name="a")
    make_camera(client, name="b", source_type="file", source_uri="file:///x.mp4")
    ids = {}
    for minute, cam in [(0, 1), (10, 1), (20, 2), (30, 1), (40, 2)]:
        ids[minute] = post(client, camera_id=cam, timestamp=at(minute))
    return ids


def test_list_is_newest_first_with_total(client):
    ids = seed(client)
    page = client.get("/api/events").json()
    assert page["total"] == 5 and page["limit"] == 50 and page["offset"] == 0
    assert [e["event_id"] for e in page["items"]] == [ids[m] for m in (40, 30, 20, 10, 0)]


def test_pagination(client):
    ids = seed(client)
    first = client.get("/api/events", params={"limit": 2}).json()
    second = client.get("/api/events", params={"limit": 2, "offset": 2}).json()
    last = client.get("/api/events", params={"limit": 2, "offset": 4}).json()
    got = [e["event_id"] for p in (first, second, last) for e in p["items"]]
    assert got == [ids[m] for m in (40, 30, 20, 10, 0)]
    assert first["total"] == second["total"] == last["total"] == 5
    assert client.get("/api/events", params={"offset": 99}).json()["items"] == []


def test_pagination_limits(client):
    for params in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
        assert client.get("/api/events", params=params).status_code == 422


def test_equal_timestamps_keep_a_stable_order(client):
    make_camera(client)
    posted = {post(client, timestamp=at(0)) for _ in range(5)}
    one = [e["event_id"] for e in client.get("/api/events").json()["items"]]
    two = [e["event_id"] for e in client.get("/api/events").json()["items"]]
    assert one == two and set(one) == posted
    paged = [e["event_id"] for off in range(5) for e in client.get("/api/events", params={"limit": 1, "offset": off}).json()["items"]]
    assert paged == one   # no event repeated or skipped between pages


def test_filter_by_camera(client):
    ids = seed(client)
    got = client.get("/api/events", params={"camera_id": 2}).json()
    assert got["total"] == 2
    assert [e["event_id"] for e in got["items"]] == [ids[40], ids[20]]
    assert client.get("/api/events", params={"camera_id": 99}).json()["total"] == 0


def test_date_range_is_from_inclusive_to_exclusive(client):
    ids = seed(client)
    got = client.get("/api/events", params={"from": at(10), "to": at(30)}).json()
    assert [e["event_id"] for e in got["items"]] == [ids[20], ids[10]]   # 10 in, 30 out
    only_from = client.get("/api/events", params={"from": at(30)}).json()
    assert only_from["total"] == 2
    only_to = client.get("/api/events", params={"to": at(10)}).json()
    assert [e["event_id"] for e in only_to["items"]] == [ids[0]]


def test_date_range_understands_time_zones(client):
    ids = seed(client)
    # 19:10 at +07:00 is 12:10 UTC, the minute-10 event
    got = client.get("/api/events", params={"from": "2026-01-01T19:10:00+07:00", "to": "2026-01-01T19:11:00+07:00"}).json()
    assert [e["event_id"] for e in got["items"]] == [ids[10]]


def test_filters_combine(client):
    ids = seed(client)
    got = client.get("/api/events", params={"camera_id": 1, "from": at(5), "to": at(35)}).json()
    assert [e["event_id"] for e in got["items"]] == [ids[30], ids[10]]
    assert got["total"] == 2


def test_filter_by_type_and_status(client):
    seed(client)
    assert client.get("/api/events", params={"type": "PERSON_DETECTED"}).json()["total"] == 5
    assert client.get("/api/events", params={"type": "OTHER"}).json()["total"] == 0
    assert client.get("/api/events", params={"status": "new"}).json()["total"] == 5
    assert client.get("/api/events", params={"status": "acknowledged"}).json()["total"] == 0


def test_bad_date_is_422(client):
    assert client.get("/api/events", params={"from": "yesterday"}).status_code == 422


def test_unknown_event_is_404(client):
    assert client.get(f"/api/events/{uuid.uuid4()}").status_code == 404
    assert client.get("/api/events/not-a-uuid").status_code == 422


# ---- snapshot ----

NAME = "PERSON_DETECTED_cam1_id12_20260101T120000_123e4567.jpg"


def test_snapshot_is_served(client, snapshot_dir):
    make_camera(client)
    (snapshot_dir / NAME).write_bytes(b"\xff\xd8\xff fake jpeg")
    event_id = post(client, snapshot=NAME)
    resp = client.get(f"/api/events/{event_id}/snapshot")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == b"\xff\xd8\xff fake jpeg"


def test_snapshot_404_when_the_event_has_none(client):
    make_camera(client)
    event_id = post(client)
    resp = client.get(f"/api/events/{event_id}/snapshot")
    assert resp.status_code == 404


def test_snapshot_404_when_the_cleaner_removed_the_file(client, snapshot_dir):
    make_camera(client)
    (snapshot_dir / NAME).write_bytes(b"x")
    event_id = post(client, snapshot=NAME)
    (snapshot_dir / NAME).unlink()
    resp = client.get(f"/api/events/{event_id}/snapshot")
    assert resp.status_code == 404
    assert client.get(f"/api/events/{event_id}").status_code == 200   # the event itself stays


def test_snapshot_cannot_leave_the_directory(client, snapshot_dir, db_engine, tmp_path):
    from sqlalchemy import text

    make_camera(client)
    (tmp_path / "secret.jpg").write_bytes(b"secret")
    event_id = post(client, snapshot=NAME)
    # the API refuses such names, but a row written some other way must not open a hole either
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE events SET snapshot_file = '../secret.jpg'"))
    assert client.get(f"/api/events/{event_id}/snapshot").status_code == 404
