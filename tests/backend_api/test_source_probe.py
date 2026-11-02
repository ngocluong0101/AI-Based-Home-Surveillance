"""POST /api/cameras/test-connection and the RTSP probe behind it (ROADMAP 6.2).

The probe is tried against a small RTSP server that lives in this file: it can ask for a Basic or Digest login,
answer 404, stay silent, speak HTTP, or describe any codec. The SPS vectors are real ones, written by x264.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import threading
import time
from urllib.parse import quote

import pytest

from backend.source_probe import h264_resolution, parse_sdp_video, probe_file, probe_rtsp, sps_from_fmtp

from .helpers import make_camera

# (width, height, profile, SPS NAL unit as hex) taken from x264enc via GStreamer
SPS_VECTORS = [
    (1920, 1080, "high 4:2:0, cropped", "67640028acd940780227e5c05a808080a0000003002000000791e30632c0"),
    (1280, 720, "main", "674d401feca02802dd80b501010140000003004000000f23c60c6580"),
    (640, 360, "baseline, cropped", "6742c01ed900a02ff97016a0c0c0d4a0000003002000000791e2c5c9"),
    (352, 288, "baseline", "6742c015d9016096c05a8303035280000003008000001e478b1724"),
    (1920, 1080, "high 4:4:4", "67f4002890d9b280f0044fc4e02d40404050000003001000000303c8f1831960"),
    (704, 576, "high 4:4:4", "67f4001f90d9b2816024d80b50606064000003000400000300f23c60c658"),
]


@pytest.mark.parametrize("width,height,kind,sps", SPS_VECTORS, ids=[f"{w}x{h} {k}" for w, h, k, _ in SPS_VECTORS])
def test_h264_resolution_from_real_sps(width, height, kind, sps):
    assert h264_resolution(bytes.fromhex(sps)) == (width, height)


def test_h264_resolution_rejects_what_is_not_an_sps():
    assert h264_resolution(b"") is None
    assert h264_resolution(bytes.fromhex("68ce3c80")) is None   # a PPS
    assert h264_resolution(bytes.fromhex("6742c0")) is None   # cut short


def sdp(codec="H264", fmtp=""):
    return (
        "v=0\r\no=- 0 0 IN IP4 0.0.0.0\r\ns=cam\r\nt=0 0\r\n"
        "m=audio 0 RTP/AVP 97\r\na=rtpmap:97 PCMA/8000\r\n"
        f"m=video 0 RTP/AVP 96\r\na=rtpmap:96 {codec}/90000\r\n"
        + (f"a=fmtp:96 {fmtp}\r\n" if fmtp else "")
    )


def h264_fmtp(sps_hex):
    sps = base64.b64encode(bytes.fromhex(sps_hex)).decode()
    return f"packetization-mode=1;profile-level-id=640028;sprop-parameter-sets={sps},aM4G4g=="


def test_sdp_and_sprop_parsing():
    media = parse_sdp_video(sdp("H264", h264_fmtp(SPS_VECTORS[0][3])))
    assert media.codec == "H264"
    assert h264_resolution(sps_from_fmtp(media.fmtp)) == (1920, 1080)
    assert parse_sdp_video(sdp("HEVC")).codec == "H265"   # some cameras say HEVC
    assert parse_sdp_video("v=0\r\nm=audio 0 RTP/AVP 97\r\na=rtpmap:97 PCMA/8000\r\n") is None
    assert parse_sdp_video("m=video 0 RTP/AVP 26\r\n").codec == "JPEG"   # static payload type, no rtpmap


class FakeRtsp:
    """Answers DESCRIBE like a camera. `auth` is None, "basic" or "digest"; `mode` "rtsp", "silent" or "http"."""

    REALM, NONCE = "fake-cam", "abcdef0123456789"

    def __init__(self, *, auth=None, user="admin", password="s3cret", body=None, path="/stream", mode="rtsp"):
        self.auth, self.user, self.password, self.body, self.path, self.mode = auth, user, password, body, path, mode
        self.requests: list[str] = []
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(5)
        self.port = self._server.getsockname()[1]
        self._conns: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self._server.close()
        for c in self._conns:
            c.close()

    def _accept(self):
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        buf = b""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while b"\r\n\r\n" in buf:
                    head, _, buf = buf.partition(b"\r\n\r\n")
                    self._answer(conn, head.decode())
        except OSError:
            return

    def _answer(self, conn, head):
        self.requests.append(head)
        if self.mode == "silent":
            return
        if self.mode == "http":
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return
        lines = head.split("\r\n")
        method, uri, _ = lines[0].split(" ")
        headers = {k.lower(): v for k, _, v in (ln.partition(": ") for ln in lines[1:])}
        cseq = headers.get("cseq", "0")

        def reply(status, extra=(), body=""):
            data = body.encode()
            out = [f"RTSP/1.0 {status}", f"CSeq: {cseq}", *extra]
            if data:
                out += ["Content-Type: application/sdp", f"Content-Length: {len(data)}"]
            conn.sendall(("\r\n".join(out) + "\r\n\r\n").encode() + data)

        if self.auth and not self._authorised(method, uri, headers.get("authorization", "")):
            challenge = (
                f'Digest realm="{self.REALM}", nonce="{self.NONCE}", qop="auth"' if self.auth == "digest"
                else f'Basic realm="{self.REALM}"'
            )
            return reply("401 Unauthorized", [f"WWW-Authenticate: {challenge}"])
        if not uri.endswith(self.path):
            return reply("404 Not Found")
        reply("200 OK", body=self.body or "")

    def _authorised(self, method, uri, header):
        if self.auth == "basic":
            return header == "Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        if not header.startswith("Digest "):
            return False
        pairs = (part.strip().split("=", 1) for part in header[7:].split(", "))
        fields = {k: v.strip('"') for k, v in pairs}

        def md5(text):
            return hashlib.md5(text.encode()).hexdigest()

        ha1 = md5(f"{self.user}:{self.REALM}:{self.password}")
        ha2 = md5(f"{method}:{uri}")
        expected = md5(f"{ha1}:{self.NONCE}:{fields['nc']}:{fields['cnonce']}:auth:{ha2}")
        return fields.get("username") == self.user and fields.get("uri") == uri and fields.get("response") == expected


@pytest.fixture
def rtsp():
    servers: list[FakeRtsp] = []

    def start(**kwargs) -> FakeRtsp:
        servers.append(FakeRtsp(**kwargs))
        return servers[-1]

    yield start
    for s in servers:
        s.close()


H264_1080 = sdp("H264", h264_fmtp(SPS_VECTORS[0][3]))


def names(result):
    return [(c.name, c.ok) for c in result.checks]


def test_open_camera_reports_codec_and_resolution(rtsp):
    server = rtsp(body=H264_1080)
    result = probe_rtsp(f"rtsp://127.0.0.1:{server.port}/stream", timeout=3)
    assert result.ok, result.summary
    assert (result.codec, result.width, result.height) == ("H.264", 1920, 1080)
    assert names(result) == [("connect", True), ("auth", True), ("stream", True), ("codec", True), ("resolution", True)]
    assert result.latency_ms is not None and "1920x1080" in result.summary
    assert "Authorization" not in server.requests[0]   # no login is offered before it is asked for


@pytest.mark.parametrize("scheme", ["digest", "basic"])
def test_login_is_answered(rtsp, scheme):
    server = rtsp(auth=scheme, body=H264_1080)
    result = probe_rtsp(f"rtsp://admin:s3cret@127.0.0.1:{server.port}/stream", timeout=3)
    assert result.ok, result.summary
    assert ("auth", True) in names(result)


def test_password_with_special_characters_is_decoded(rtsp):
    password = "p@ss:w/rd%#?"
    server = rtsp(auth="digest", password=password, body=H264_1080)
    result = probe_rtsp(f"rtsp://admin:{quote(password, safe='')}@127.0.0.1:{server.port}/stream", timeout=3)
    assert result.ok, result.summary
    # the login never goes on the wire inside the address
    assert all("@" not in request.split("\r\n")[0] for request in server.requests)


def test_wrong_password_and_missing_login(rtsp):
    server = rtsp(auth="digest", body=H264_1080)
    wrong = probe_rtsp(f"rtsp://admin:nope@127.0.0.1:{server.port}/stream", timeout=3)
    assert not wrong.ok and names(wrong)[-1] == ("auth", False)
    assert "username or password" in wrong.summary

    missing = probe_rtsp(f"rtsp://127.0.0.1:{server.port}/stream", timeout=3)
    assert not missing.ok and names(missing)[-1] == ("auth", False)
    assert "needs a username and password" in missing.summary


def test_unknown_path(rtsp):
    server = rtsp(body=H264_1080)
    result = probe_rtsp(f"rtsp://127.0.0.1:{server.port}/other", timeout=3)
    assert not result.ok and names(result)[-1] == ("stream", False)
    assert "no stream at this path" in result.summary


def test_h265_is_accepted_without_a_resolution(rtsp):
    server = rtsp(body=sdp("H265", "sprop-vps=abc"))
    result = probe_rtsp(f"rtsp://127.0.0.1:{server.port}/stream", timeout=3)
    assert result.ok and result.codec == "H.265" and result.width is None
    assert ("resolution", True) in names(result)


def test_codec_the_engine_is_not_tested_with_fails(rtsp):
    server = rtsp(body=sdp("MP4V-ES"))
    result = probe_rtsp(f"rtsp://127.0.0.1:{server.port}/stream", timeout=3)
    assert not result.ok and result.codec == "MP4V-ES"
    assert ("codec", False) in names(result) and "Set the camera to H.264" in result.summary


def test_stream_without_video(rtsp):
    server = rtsp(body="v=0\r\nm=audio 0 RTP/AVP 97\r\na=rtpmap:97 PCMA/8000\r\n")
    result = probe_rtsp(f"rtsp://127.0.0.1:{server.port}/stream", timeout=3)
    assert not result.ok and "no video" in result.summary


def test_connection_refused():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    result = probe_rtsp(f"rtsp://127.0.0.1:{port}/stream", timeout=3)
    assert not result.ok and names(result) == [("connect", False)]
    assert "refused" in result.summary and str(port) in result.summary


def test_silent_camera_times_out_in_time(rtsp):
    server = rtsp(mode="silent")
    started = time.monotonic()
    result = probe_rtsp(f"rtsp://127.0.0.1:{server.port}/stream", timeout=0.6)
    assert time.monotonic() - started < 2
    assert not result.ok and names(result)[-1] == ("auth", False)
    assert "did not answer within" in result.summary


def test_not_an_rtsp_server(rtsp):
    server = rtsp(mode="http")
    result = probe_rtsp(f"rtsp://127.0.0.1:{server.port}/stream", timeout=3)
    assert not result.ok and "does not speak RTSP" in result.summary


def test_unknown_host():
    result = probe_rtsp("rtsp://no-such-host.invalid/stream", timeout=3)
    assert not result.ok and names(result) == [("connect", False)]


def test_credentials_never_appear_in_the_result(rtsp):
    server = rtsp(auth="digest", body=H264_1080)
    for uri in (f"rtsp://admin:s3cret@127.0.0.1:{server.port}/stream", f"rtsp://admin:wrong@127.0.0.1:{server.port}/stream"):
        text = repr(probe_rtsp(uri, timeout=3))
        assert "s3cret" not in text and "wrong" not in text and "admin" not in text


def test_probe_file(tmp_path):
    video = tmp_path / "a b.mp4"
    video.write_bytes(b"x" * 2048)
    assert probe_file(video.as_uri()).ok
    missing = probe_file((tmp_path / "missing.mp4").as_uri())
    assert not missing.ok and "no such file" in missing.summary
    assert not probe_file(f"file://{tmp_path}").ok   # a directory


# ---- the endpoint

def test_endpoint_with_an_address(client, rtsp):
    server = rtsp(auth="digest", body=H264_1080)
    resp = client.post("/api/cameras/test-connection", json={
        "source_type": "rtsp", "source_uri": f"rtsp://admin:s3cret@127.0.0.1:{server.port}/stream", "timeout": 3})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] and (body["codec"], body["width"], body["height"]) == ("H.264", 1920, 1080)
    assert [c["name"] for c in body["checks"]] == ["connect", "auth", "stream", "codec", "resolution"]
    assert "s3cret" not in resp.text and "admin" not in resp.text


def test_endpoint_failure_is_a_normal_answer(client, rtsp):
    server = rtsp(auth="digest", body=H264_1080)
    resp = client.post("/api/cameras/test-connection", json={
        "source_type": "rtsp", "source_uri": f"rtsp://admin:bad@127.0.0.1:{server.port}/stream", "timeout": 3})
    assert resp.status_code == 200
    assert resp.json()["ok"] is False and "username or password" in resp.json()["summary"]


def test_endpoint_with_a_saved_camera_uses_its_stored_login(client, rtsp):
    server = rtsp(auth="digest", body=H264_1080)
    cam = make_camera(client, source_uri=f"rtsp://admin:s3cret@127.0.0.1:{server.port}/stream")
    resp = client.post("/api/cameras/test-connection", json={"camera_id": cam["id"], "timeout": 3})
    assert resp.status_code == 200 and resp.json()["ok"], resp.text
    assert "s3cret" not in resp.text


def test_endpoint_with_a_file(client, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    ok = client.post("/api/cameras/test-connection", json={"source_type": "file", "source_uri": video.as_uri()})
    assert ok.json()["ok"] and ok.json()["checks"][0]["name"] == "file"
    gone = client.post("/api/cameras/test-connection", json={"source_type": "file", "source_uri": "file:///nope/x.mp4"})
    assert gone.status_code == 200 and gone.json()["ok"] is False


def test_endpoint_validation(client):
    def post(body):
        return client.post("/api/cameras/test-connection", json=body).status_code

    assert post({}) == 422
    assert post({"source_type": "rtsp"}) == 422
    assert post({"source_uri": "rtsp://h/x"}) == 422
    assert post({"source_type": "file", "source_uri": "rtsp://h/x"}) == 422   # scheme does not match the type
    assert post({"camera_id": 1, "source_type": "rtsp", "source_uri": "rtsp://h/x"}) == 422
    assert post({"source_type": "rtsp", "source_uri": "rtsp://h/x", "timeout": 60}) == 422
    assert post({"camera_id": 999}) == 404


def test_endpoint_does_not_find_a_deleted_camera(client):
    cam = make_camera(client)
    assert client.delete(f"/api/cameras/{cam['id']}").status_code == 204
    assert client.post("/api/cameras/test-connection", json={"camera_id": cam["id"]}).status_code == 404
