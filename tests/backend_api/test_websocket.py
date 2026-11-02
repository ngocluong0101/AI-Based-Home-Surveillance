"""WS /api/ws: event.created and camera.status pushed to browsers (ROADMAP 3.7)."""

import asyncio
import queue
import threading
import time

import pytest

from backend.realtime import Hub

from .helpers import event_body, make_camera
from .test_heartbeat import beat, cam

WS = "/api/ws"


def recv(ws, timeout=5.0) -> dict:
    """The next message, or a failure instead of a hang (a blocked receive cannot be interrupted)."""
    box: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: box.put(ws.receive_json()), daemon=True).start()
    try:
        return box.get(timeout=timeout)
    except queue.Empty:
        pytest.fail(f"no WebSocket message within {timeout} s")


def wait_for(condition, timeout=3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


# ---- event.created ---------------------------------------------------------------------------------


def test_a_new_event_is_pushed_in_the_shape_of_the_rest_api(client):
    make_camera(client)
    with client.websocket_connect(WS) as ws:
        body = event_body()
        assert client.post("/internal/events", json=body).status_code == 201
        msg = recv(ws)

    assert msg["type"] == "event.created"
    assert msg["data"] == client.get(f"/api/events/{body['event_id']}").json()
    assert msg["data"]["camera_id"] == 1 and msg["data"]["status"] == "new"


def test_a_repeated_event_is_not_pushed_twice(client):
    make_camera(client)
    with client.websocket_connect(WS) as ws:
        first, second = event_body(), event_body()
        assert client.post("/internal/events", json=first).status_code == 201
        assert client.post("/internal/events", json=first).status_code == 200   # duplicate
        assert client.post("/internal/events", json=second).status_code == 201
        assert recv(ws)["data"]["event_id"] == first["event_id"]
        assert recv(ws)["data"]["event_id"] == second["event_id"]   # nothing in between


def test_a_refused_event_is_not_pushed(client):
    make_camera(client)
    with client.websocket_connect(WS) as ws:
        assert client.post("/internal/events", json=event_body(camera_id=99)).status_code == 422
        good = event_body()
        assert client.post("/internal/events", json=good).status_code == 201
        assert recv(ws)["data"]["event_id"] == good["event_id"]


def test_every_client_gets_the_message_and_a_closed_one_is_forgotten(client):
    make_camera(client)
    hub = client.app.state.hub
    with client.websocket_connect(WS) as one, client.websocket_connect(WS) as two:
        assert wait_for(lambda: hub.clients == 2)
        body = event_body()
        client.post("/internal/events", json=body)
        assert recv(one)["data"]["event_id"] == body["event_id"]
        assert recv(two)["data"]["event_id"] == body["event_id"]
    assert wait_for(lambda: hub.clients == 0)
    assert client.post("/internal/events", json=event_body()).status_code == 201   # nobody listens: fine


def test_what_the_client_sends_is_ignored(client):
    make_camera(client)
    with client.websocket_connect(WS) as ws:
        ws.send_text("hello")
        ws.send_text('{"type":"event.created"}')
        body = event_body()
        client.post("/internal/events", json=body)
        assert recv(ws)["data"]["event_id"] == body["event_id"]


# ---- camera.status ---------------------------------------------------------------------------------


def test_a_heartbeat_pushes_the_status_of_each_camera(client):
    with client.websocket_connect(WS) as ws:
        body = beat(cam(1, fps=28.4, age=0.03, people=3), cam(2, "RECONNECTING", fps=0.0, age=9.0))
        assert client.post("/internal/heartbeat", json=body).status_code == 204
        one, two = recv(ws), recv(ws)

    assert one["type"] == "camera.status"
    assert (one["data"]["camera_id"], one["data"]["reporting"], one["data"]["state"]) == (1, True, "RUNNING")
    assert one["data"]["fps"] == 28.4 and one["data"]["last_frame_age"] == 0.03
    assert one["data"]["people"] == 3 and two["data"]["people"] is None
    assert (two["data"]["camera_id"], two["data"]["state"]) == (2, "RECONNECTING")


def test_a_camera_that_leaves_the_heartbeat_is_reported_as_gone(client):
    with client.websocket_connect(WS) as ws:
        client.post("/internal/heartbeat", json=beat(cam(1), cam(2)))
        recv(ws), recv(ws)
        client.post("/internal/heartbeat", json=beat(cam(2)))   # camera 1 was stopped
        first, second = recv(ws), recv(ws)

    assert (first["data"]["camera_id"], first["data"]["reporting"], first["data"]["state"]) == (1, False, None)
    assert (second["data"]["camera_id"], second["data"]["reporting"]) == (2, True)


def test_silent_heartbeats_are_noticed_and_reported_once(make_client):
    client = make_client(heartbeat_stale_after=0.2, status_check_interval=0.05)
    with client.websocket_connect(WS) as ws:
        client.post("/internal/heartbeat", json=beat(cam(1)))
        assert recv(ws)["data"]["reporting"] is True
        gone = recv(ws)   # no further heartbeat: found by the timer
        assert (gone["data"]["camera_id"], gone["data"]["reporting"]) == (1, False)

        client.post("/internal/heartbeat", json=beat(cam(1)))   # the engine is back
        assert recv(ws)["data"]["reporting"] is True


# ---- the hub ---------------------------------------------------------------------------------------


def test_a_client_that_falls_behind_is_dropped_not_buffered_forever():
    async def scenario():
        hub = Hub(queue_size=3)
        hub.attach(asyncio.get_running_loop())
        slow, fine = hub.connect(), hub.connect()
        sent = threading.Thread(target=lambda: [hub.publish("x", {"n": n}) for n in range(5)])
        sent.start()
        sent.join()
        await asyncio.sleep(0.05)   # let the loop run the deliveries

        # `fine` never reads either, so both overflowed; each now holds only the close marker.
        assert hub.clients == 0
        assert slow.queue.qsize() == 1 and slow.queue.get_nowait() is None
        assert fine.queue.get_nowait() is None

    asyncio.run(scenario())


def test_publishing_without_a_running_loop_does_nothing():
    hub = Hub()
    hub.publish("x", {})   # never attached
    hub2 = Hub()
    loop = asyncio.new_event_loop()
    hub2.attach(loop)
    loop.close()
    hub2.publish("x", {})   # attached to a loop that is gone


# ---- a real server ---------------------------------------------------------------------------------


def test_with_a_real_uvicorn_server_and_a_real_websocket_client(database_url, db_engine, snapshot_dir):
    """TestClient shares one loop between the app and the test; this goes over a TCP socket instead."""
    import json
    import socket

    import httpx
    import uvicorn
    from websockets.sync.client import connect

    from backend.main import create_app
    from backend.settings import Settings

    from .conftest import NoTelegram

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = Settings(database_url=database_url, snapshot_dir=str(snapshot_dir), configure_logging=False,
                        reconcile_interval=0, notify_interval=0, cleanup_check_interval=0)
    server = uvicorn.Server(uvicorn.Config(create_app(settings, telegram=NoTelegram()), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        assert wait_for(lambda: server.started, timeout=10)
        base = f"http://127.0.0.1:{port}"
        make = httpx.post(f"{base}/api/cameras", json={"name": "a", "source_type": "rtsp",
                                                       "source_uri": "rtsp://10.0.0.1/s"})
        assert make.status_code == 201

        with connect(f"ws://127.0.0.1:{port}/api/ws", open_timeout=5) as ws:
            body = event_body()
            assert httpx.post(f"{base}/internal/events", json=body).status_code == 201
            assert httpx.post(f"{base}/internal/heartbeat", json=beat(cam(1))).status_code == 204
            first = json.loads(ws.recv(timeout=5))
            second = json.loads(ws.recv(timeout=5))
        assert first["type"] == "event.created" and first["data"]["event_id"] == body["event_id"]
        assert second["type"] == "camera.status" and second["data"]["reporting"] is True
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    assert not thread.is_alive()
