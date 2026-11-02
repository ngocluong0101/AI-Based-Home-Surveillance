from sqlalchemy import text

from backend.schemas import mask_uri

from .helpers import make_camera


def test_create_get_list(client):
    cam = make_camera(client)
    assert cam["id"] == 1
    assert cam["desired_state"] == "stopped"
    assert cam["enabled"] and cam["ai_enabled"]
    assert cam["conf_threshold"] == 0.5 and cam["absence_timeout"] == 10.0

    assert client.get(f"/api/cameras/{cam['id']}").json() == cam
    assert client.get("/api/cameras").json() == [cam]


def test_credentials_are_never_returned(client, db_engine):
    cam = make_camera(client, source_uri="rtsp://admin:secret@192.168.1.10:554/stream?token=abc")
    for body in (cam, client.get("/api/cameras").json()[0], client.get(f"/api/cameras/{cam['id']}").json()):
        assert "secret" not in str(body) and "admin" not in str(body) and "abc" not in str(body)
        assert body["source_uri"] == "rtsp://***@192.168.1.10:554/stream?***"
    # ... but the engine has to be started with the real one, so the database keeps it
    with db_engine.connect() as conn:
        stored = conn.execute(text("SELECT source_uri FROM cameras")).scalar_one()
    assert "admin:secret@" in stored


def test_mask_uri():
    assert mask_uri("file:///data/videos/a.mp4") == "file:///data/videos/a.mp4"
    assert mask_uri("rtsp://u:p@host/x") == "rtsp://***@host/x"
    assert mask_uri("rtsp://u@[fe80::1]:8554/x") == "rtsp://***@[fe80::1]:8554/x"
    assert mask_uri("rtsp://host:554/x?key=1") == "rtsp://host:554/x?***"


def test_validation(client):
    ok = {"name": "n", "source_type": "file", "source_uri": "file:///a.mp4"}
    assert client.post("/api/cameras", json={**ok, "source_uri": "rtsp://h/x"}).status_code == 422   # type != scheme
    assert client.post("/api/cameras", json={**ok, "source_type": "usb"}).status_code == 422
    assert client.post("/api/cameras", json={**ok, "name": "  "}).status_code == 422
    assert client.post("/api/cameras", json={**ok, "conf_threshold": 1.5}).status_code == 422
    assert client.post("/api/cameras", json={**ok, "absence_timeout": -1}).status_code == 422
    assert client.post("/api/cameras", json={**ok, "desired_state": "running"}).status_code == 422   # not settable here
    assert client.post("/api/cameras", json={"name": "n"}).status_code == 422
    assert client.get("/api/cameras").json() == []


def test_name_must_be_unique_among_active_cameras(client):
    cam = make_camera(client, name="porch")
    resp = client.post("/api/cameras", json={"name": "porch", "source_type": "file", "source_uri": "file:///a.mp4"})
    assert resp.status_code == 409
    assert client.patch(f"/api/cameras/{make_camera(client, name='yard')['id']}", json={"name": "porch"}).status_code == 409

    # a deleted camera frees its name
    assert client.delete(f"/api/cameras/{cam['id']}").status_code == 204
    again = make_camera(client, name="porch")
    assert again["id"] != cam["id"]


def test_patch_changes_only_what_is_sent(client):
    cam = make_camera(client)
    resp = client.patch(f"/api/cameras/{cam['id']}", json={"enabled": False, "conf_threshold": 0.7})
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False and body["conf_threshold"] == 0.7
    assert body["name"] == cam["name"] and body["source_uri"] == cam["source_uri"]
    assert body["updated_at"] >= cam["updated_at"]


def test_patch_source_needs_type_and_uri_together(client):
    cam = make_camera(client)
    url = f"/api/cameras/{cam['id']}"
    assert client.patch(url, json={"source_uri": "rtsp://other/x"}).status_code == 422
    assert client.patch(url, json={"source_type": "file"}).status_code == 422
    assert client.patch(url, json={"source_type": "file", "source_uri": "rtsp://x/y"}).status_code == 422
    assert client.patch(url, json={"name": None}).status_code == 422
    ok = client.patch(url, json={"source_type": "file", "source_uri": "file:///a.mp4"})
    assert ok.status_code == 200 and ok.json()["source_type"] == "file"


def test_unknown_and_deleted_cameras_are_404(client):
    assert client.get("/api/cameras/99").status_code == 404
    assert client.patch("/api/cameras/99", json={"enabled": False}).status_code == 404
    assert client.delete("/api/cameras/99").status_code == 404

    cam = make_camera(client)
    assert client.delete(f"/api/cameras/{cam['id']}").status_code == 204
    assert client.get(f"/api/cameras/{cam['id']}").status_code == 404
    assert client.get("/api/cameras").json() == []
    assert client.delete(f"/api/cameras/{cam['id']}").status_code == 404
