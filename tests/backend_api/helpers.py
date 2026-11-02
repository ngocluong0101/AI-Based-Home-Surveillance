"""Shared by the backend tests."""

from __future__ import annotations

import uuid

# Byte for byte what the engine writes (engine/tests/test_event_record.c, golden JSON).
ENGINE_EVENT = (
    '{"schema_version":1,"event_id":"123e4567-e89b-42d3-a456-426614174000","camera_id":1,'
    '"pipeline_run_id":"0f8fad5b-d9cb-469f-a165-70867728950e","type":"PERSON_DETECTED","track_id":12,'
    '"confidence":0.9100,"timestamp":"2026-01-01T12:00:00.123Z",'
    '"bbox":[0.1000,0.2000,0.3000,0.4000],"snapshot":null}'
)


def event_body(**overrides) -> dict:
    body = {
        "schema_version": 1,
        "event_id": str(uuid.uuid4()),
        "camera_id": 1,
        "pipeline_run_id": "0f8fad5b-d9cb-469f-a165-70867728950e",
        "type": "PERSON_DETECTED",
        "track_id": 12,
        "confidence": 0.91,
        "timestamp": "2026-01-01T12:00:00.123Z",
        "bbox": [0.1, 0.2, 0.3, 0.4],
        "snapshot": None,
    }
    body.update(overrides)
    return body


def make_camera(client, name="front door", **overrides) -> dict:
    body = {"name": name, "source_type": "rtsp", "source_uri": "rtsp://admin:secret@192.168.1.10:554/stream"}
    body.update(overrides)
    resp = client.post("/api/cameras", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()
