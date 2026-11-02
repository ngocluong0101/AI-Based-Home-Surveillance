"""POST /internal/heartbeat and the `stream` part of GET /api/cameras/{id}/status (ROADMAP 3.6)."""

import json
import time
import uuid

import pytest

from backend.heartbeats import CameraBeat, HeartbeatStore

from .helpers import make_camera

URL = "/internal/heartbeat"
RUN = "0f8fad5b-d9cb-469f-a165-70867728950e"

# Byte for byte what the engine writes (engine/tests/test_heartbeat_record.c, golden JSON).
ENGINE_HEARTBEAT = (
    '{"schema_version":1,"run_id":"' + RUN + '","cameras":['
    '{"camera_id":1,"state":"RUNNING","fps":28.4,"last_frame_age":0.030},'
    '{"camera_id":2,"state":"RECONNECTING","fps":0.0,"last_frame_age":12.500}]}'
)


def beat(*cameras, run=RUN, **extra) -> dict:
    body = {"schema_version": 1, "run_id": run, "cameras": list(cameras)}
    body.update(extra)
    return body


def cam(camera_id=1, state="RUNNING", fps=25.0, age=0.04) -> dict:
    return {"camera_id": camera_id, "state": state, "fps": fps, "last_frame_age": age}


def stream(client, camera_id) -> dict:
    resp = client.get(f"/api/cameras/{camera_id}/status")
    assert resp.status_code == 200, resp.text
    return resp.json()["stream"]


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# ---- the store -------------------------------------------------------------------------------------


def test_store_knows_nothing_before_the_first_heartbeat():
    store = HeartbeatStore(15.0, FakeClock())
    h = store.health(1)
    assert h.reporting is False and h.heartbeat_age is None and h.state is None


def test_store_reports_the_latest_state_and_goes_stale():
    clock = FakeClock()
    store = HeartbeatStore(15.0, clock)
    store.update(uuid.UUID(RUN), {1: CameraBeat("RUNNING", 28.4, 0.03)})

    clock.now += 4
    h = store.health(1)
    assert (h.reporting, h.state, h.fps, h.last_frame_age, h.heartbeat_age) == (True, "RUNNING", 28.4, 0.03, 4.0)

    clock.now += 11.5   # 15.5 s since the heartbeat: the engine stopped talking
    h = store.health(1)
    assert h.reporting is False and h.state is None and h.fps is None and h.heartbeat_age == 15.5


def test_store_a_new_heartbeat_replaces_the_old_one_as_a_whole():
    clock = FakeClock()
    store = HeartbeatStore(15.0, clock)
    store.update(uuid.UUID(RUN), {1: CameraBeat("RUNNING", 30.0, 0.0), 2: CameraBeat("RUNNING", 30.0, 0.0)})
    new_run = uuid.uuid4()
    store.update(new_run, {2: CameraBeat("RECONNECTING", 0.0, 9.0)})

    assert store.run_id() == new_run
    assert store.health(1).reporting is False   # camera 1 is not in the engine any more
    assert store.health(2).state == "RECONNECTING"


# ---- the endpoint ----------------------------------------------------------------------------------


def test_the_engines_own_json_is_accepted(client):
    make_camera(client, "a")
    make_camera(client, "b", source_uri="rtsp://10.0.0.2/stream")
    resp = client.post(URL, content=ENGINE_HEARTBEAT, headers={"Content-Type": "application/json"})
    assert resp.status_code == 204 and resp.content == b""

    one, two = stream(client, 1), stream(client, 2)
    assert (one["reporting"], one["state"], one["fps"], one["last_frame_age"]) == (True, "RUNNING", 28.4, 0.03)
    assert (two["reporting"], two["state"], two["fps"], two["last_frame_age"]) == (True, "RECONNECTING", 0.0, 12.5)
    assert 0 <= one["heartbeat_age"] < 5


def test_a_camera_the_engine_does_not_mention_is_not_reporting(client):
    make_camera(client, "a")
    make_camera(client, "b", source_uri="rtsp://10.0.0.2/stream")
    assert client.post(URL, json=beat(cam(1))).status_code == 204
    assert stream(client, 1)["reporting"] is True
    other = stream(client, 2)
    assert other["reporting"] is False and other["state"] is None and other["heartbeat_age"] is not None


def test_status_without_any_heartbeat(client):
    make_camera(client)
    assert stream(client, 1) == {"reporting": False, "state": None, "fps": None, "last_frame_age": None,
                                 "heartbeat_age": None}


def test_an_old_heartbeat_stops_counting(make_client):
    client = make_client(heartbeat_stale_after=0.2)
    make_camera(client)
    assert client.post(URL, json=beat(cam(1))).status_code == 204
    assert stream(client, 1)["reporting"] is True
    time.sleep(0.4)
    assert stream(client, 1)["reporting"] is False


def test_a_heartbeat_without_cameras_is_fine(client):
    make_camera(client)
    assert client.post(URL, json=beat()).status_code == 204
    assert stream(client, 1)["reporting"] is False


def test_unknown_fields_are_ignored(client):
    make_camera(client)
    body = beat({**cam(1), "future_field": 1}, extra_field={"a": 1})
    assert client.post(URL, json=body).status_code == 204


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(schema_version=2),
        lambda b: b.pop("schema_version"),
        lambda b: b.update(run_id="not-a-uuid"),
        lambda b: b.pop("run_id"),
        lambda b: b.pop("cameras"),
        lambda b: b.update(cameras="x"),
        lambda b: b["cameras"][0].update(state="PAUSED"),
        lambda b: b["cameras"][0].update(fps=-1),
        lambda b: b["cameras"][0].update(last_frame_age=-0.1),
        lambda b: b["cameras"][0].update(camera_id=-1),
        lambda b: b["cameras"][0].update(camera_id=2**31),
        lambda b: b["cameras"][0].update(fps=None),
    ],
)
def test_bad_bodies_are_refused_with_422(client, mutate):
    body = beat(cam(1))
    mutate(body)
    assert client.post(URL, json=body).status_code == 422


def test_garbage_is_refused_and_keeps_the_last_good_heartbeat(client):
    make_camera(client)
    assert client.post(URL, json=beat(cam(1, fps=21.0))).status_code == 204
    assert client.post(URL, content="{nope", headers={"Content-Type": "application/json"}).status_code == 422
    assert stream(client, 1)["fps"] == 21.0


def test_the_token_is_required_when_one_is_configured(make_client):
    client = make_client(internal_token="s3cret")
    make_camera(client)
    assert client.post(URL, json=beat(cam(1))).status_code == 401
    assert client.post(URL, json=beat(cam(1)), headers={"X-Internal-Token": "wrong"}).status_code == 401
    assert stream(client, 1)["reporting"] is False
    assert client.post(URL, json=beat(cam(1)), headers={"X-Internal-Token": "s3cret"}).status_code == 204
    assert stream(client, 1)["reporting"] is True


def test_the_status_of_start_carries_the_stream_too(client):
    make_camera(client)
    assert client.post(URL, json=beat(cam(1))).status_code == 204
    started = client.post("/api/cameras/1/start").json()
    assert started["stream"]["reporting"] is True
    assert json.dumps(started)   # serialisable, no stray types
