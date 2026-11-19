"""vlm_service/app.py with a stand-in model: the HTTP contract the backend relies on (no GPU, no model download)."""
import base64
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from vlm_service.app import MAX_FRAMES, create_app
from vlm_service.prompts import question_for
from vlm_service.verifier import Verdict, decide


class Stub:
    def __init__(self, raises=False):
        self.calls, self.raises = [], raises

    def verify(self, event_type, frames, seconds_apart, reason=True):
        self.calls.append((event_type, [f.size for f in frames], seconds_apart, reason))
        if self.raises:
            raise RuntimeError("cuda out of memory")
        return Verdict("no", 0.02, 0.97, 0.01, 0.4, "sitting", len(frames))


def jpeg(size=(1920, 1080)) -> str:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 120, 150)).save(buf, "JPEG")
    return base64.b64encode(buf.getvalue()).decode()


@pytest.fixture
def stub():
    return Stub()


@pytest.fixture
def client(stub):
    with TestClient(create_app(stub)) as c:
        yield c


def test_a_verdict_comes_back_with_its_numbers_and_big_frames_are_shrunk(client, stub):
    r = client.post("/verify", json={"event_type": "FALL", "frames": [jpeg(), jpeg()], "seconds_apart": 0.5})
    assert r.status_code == 200
    d = r.json()
    assert (d["verdict"], d["p_no"], d["frames"], d["reason"]) == ("no", 0.97, 2, "sitting")
    assert stub.calls[0][1] == [(640, 360), (640, 360)]          # longest side 640, aspect kept


@pytest.mark.parametrize("body,status", [
    ({"event_type": "SMILE", "frames": [jpeg((64, 64))]}, 422),
    ({"event_type": "FALL", "frames": []}, 422),
    ({"event_type": "FALL", "frames": ["!!notbase64!!"]}, 422),
    ({"event_type": "FALL", "frames": [base64.b64encode(b"not an image").decode()]}, 422),
    ({"event_type": "FALL", "frames": [jpeg((8, 8))] * (MAX_FRAMES + 1)}, 422),
])
def test_bad_requests_are_refused_before_the_model_is_touched(client, stub, body, status):
    assert client.post("/verify", json=body).status_code == status
    assert stub.calls == []


def test_a_model_failure_is_a_503_the_backend_turns_into_unverified():
    with TestClient(create_app(Stub(raises=True))) as c:
        assert c.post("/verify", json={"event_type": "FALL", "frames": [jpeg((64, 64))]}).status_code == 503
        assert c.get("/health").json()["loaded"] is True


def test_decide_needs_more_certainty_to_say_no_than_to_say_yes():
    assert decide(0.5, 0.4, 0.5, 0.9) == "yes"
    assert decide(0.3, 0.7, 0.5, 0.9) == "unsure"          # leaning no is not enough to silence an alert
    assert decide(0.05, 0.95, 0.5, 0.9) == "no"
    assert decide(0.5, 0.5, 0.5, 0.5) == "yes"             # yes is looked at first: when in doubt the alert stays


def test_the_question_names_the_frames_and_what_is_not_a_fall():
    q = question_for("FALL", 6, 0.5)
    assert "6 frames" in q and "0.5 seconds apart" in q and "NOT falls" in q and "yes, no or unsure" in q
    with pytest.raises(KeyError):
        question_for("RESTRICTED_AREA", 6, 0.5)
