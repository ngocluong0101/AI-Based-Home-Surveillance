"""GET /api/cameras/{id}/stream and the engine's --stream-url (ROADMAP 4.3)."""

from .helpers import make_camera
from .test_heartbeat import beat, cam

BASE = "http://192.168.1.20:8889"


def test_the_link_names_mediamtx_and_never_the_camera(make_client):
    client = make_client(mediamtx_webrtc_url=BASE)
    made = make_camera(client, source_uri="rtsp://admin:hunter2@192.168.1.10:554/h264?token=abc")
    resp = client.get(f"/api/cameras/{made['id']}/stream")
    assert resp.status_code == 200
    assert resp.json() == {"camera_id": 1, "whep_url": f"{BASE}/live/cam1/whep",
                           "player_url": f"{BASE}/live/cam1", "available": False}
    for secret in ("hunter2", "admin", "192.168.1.10", "token", "rtsp"):
        assert secret not in resp.text


def test_available_follows_the_heartbeat(make_client):
    client = make_client(mediamtx_webrtc_url=BASE)
    make_camera(client)
    assert client.get("/api/cameras/1/stream").json()["available"] is False
    client.post("/internal/heartbeat", json=beat(cam(1)))
    assert client.get("/api/cameras/1/stream").json()["available"] is True


def test_prefix_and_trailing_slashes_are_normalised(make_client):
    client = make_client(mediamtx_webrtc_url=BASE + "/", stream_path_prefix="/home/")
    make_camera(client)
    assert client.get("/api/cameras/1/stream").json()["whep_url"] == f"{BASE}/home/cam1/whep"


def test_without_mediamtx_configured_it_answers_503(client):
    make_camera(client)
    resp = client.get("/api/cameras/1/stream")
    assert resp.status_code == 503 and "MEDIAMTX_WEBRTC_URL" in resp.json()["detail"]


def test_unknown_and_deleted_cameras_are_404(make_client):
    client = make_client(mediamtx_webrtc_url=BASE)
    assert client.get("/api/cameras/9/stream").status_code == 404
    make_camera(client)
    assert client.delete("/api/cameras/1").status_code == 204
    assert client.get("/api/cameras/1/stream").status_code == 404
