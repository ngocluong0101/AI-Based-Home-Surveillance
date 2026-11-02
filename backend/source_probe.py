"""Looks at a camera source the way a player would, without decoding any video (ROADMAP 6.2).

For RTSP: connect, ask the camera to DESCRIBE its stream (answering a Basic or Digest login when asked), read the
stream description (SDP) for the codec, and for H.264 decode the announced SPS for the resolution. For a file: check
that the backend can read it. Nothing here logs or returns the URL: it may hold a password.

The checks are a list in the order they ran; the check that failed is the last one, so the later ones are
missing when an earlier one failed.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
import ssl
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

USER_AGENT = "home-surveillance-probe/1"
MAX_RESPONSE = 64 * 1024
SUPPORTED_CODECS = {"H264": "H.264", "H265": "H.265"}
CHECK_ORDER = ["connect", "auth", "stream", "codec", "resolution"]
NOT_ANNOUNCED = "Not announced by the camera; the engine finds it out when it starts."


@dataclass
class Check:
    name: str   # connect | auth | stream | codec | resolution | file
    ok: bool
    message: str


@dataclass
class ProbeResult:
    ok: bool
    summary: str
    checks: list[Check] = field(default_factory=list)
    codec: str | None = None
    width: int | None = None
    height: int | None = None
    latency_ms: int | None = None


class _Failure(Exception):
    """A check failed; the message is safe to show (no URL, no credentials)."""


# ---------------------------------------------------------------- H.264 SPS

class _Bits:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def u(self, n: int) -> int:
        value = 0
        for _ in range(n):
            byte, bit = divmod(self.pos, 8)
            if byte >= len(self.data):
                raise ValueError("SPS ends early")
            value = (value << 1) | ((self.data[byte] >> (7 - bit)) & 1)
            self.pos += 1
        return value

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 32:
                raise ValueError("bad exp-Golomb code")
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)

    def se(self) -> int:
        k = self.ue()
        return (k + 1) // 2 if k % 2 else -(k // 2)


_HIGH_PROFILES = {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}


def _unescape(nal: bytes) -> bytes:
    """Drop the emulation prevention bytes (00 00 03 -> 00 00)."""
    return re.sub(rb"\x00\x00\x03", b"\x00\x00", nal)


def h264_resolution(sps_nal: bytes) -> tuple[int, int] | None:
    """Width and height in pixels (after cropping) from an H.264 SPS NAL unit, None if it cannot be read."""
    try:
        if not sps_nal or sps_nal[0] & 0x1F != 7:
            return None
        r = _Bits(_unescape(sps_nal[1:]))
        profile = r.u(8)
        r.u(8)   # constraint flags
        r.u(8)   # level
        r.ue()   # sps id
        chroma = 1
        separate_planes = 0
        if profile in _HIGH_PROFILES:
            chroma = r.ue()
            if chroma == 3:
                separate_planes = r.u(1)
            r.ue()   # bit depths
            r.ue()
            r.u(1)   # qpprime_y_zero_transform_bypass
            if r.u(1):   # scaling matrix
                for i in range(8 if chroma != 3 else 12):
                    if r.u(1):
                        last = nxt = 8
                        for _ in range(16 if i < 6 else 64):
                            if nxt != 0:
                                nxt = (last + r.se() + 256) % 256
                            last = last if nxt == 0 else nxt
        r.ue()   # log2_max_frame_num_minus4
        poc_type = r.ue()
        if poc_type == 0:
            r.ue()
        elif poc_type == 1:
            r.u(1)
            r.se()
            r.se()
            for _ in range(r.ue()):
                r.se()
        r.ue()   # max_num_ref_frames
        r.u(1)   # gaps_in_frame_num_allowed
        width = (r.ue() + 1) * 16
        map_units = r.ue() + 1
        frame_mbs_only = r.u(1)
        if not frame_mbs_only:
            r.u(1)   # mb_adaptive_frame_field
        r.u(1)   # direct_8x8_inference
        height = map_units * 16 * (2 - frame_mbs_only)
        if r.u(1):   # cropping
            left, right, top, bottom = r.ue(), r.ue(), r.ue(), r.ue()
            if separate_planes or chroma == 0:
                unit_x, unit_y = 1, 2 - frame_mbs_only
            else:
                unit_x = 2 if chroma in (1, 2) else 1
                unit_y = (2 if chroma == 1 else 1) * (2 - frame_mbs_only)
            width -= unit_x * (left + right)
            height -= unit_y * (top + bottom)
        return (width, height) if width > 0 and height > 0 else None
    except ValueError:
        return None


# ---------------------------------------------------------------- SDP

@dataclass
class VideoMedia:
    codec: str | None
    fmtp: str


_RTPMAP = re.compile(r"^a=rtpmap:(\d+)\s+([^/\s]+)", re.I)
_FMTP = re.compile(r"^a=fmtp:(\d+)\s+(.*)$", re.I)


def parse_sdp_video(sdp: str) -> VideoMedia | None:
    """The first video stream of an SDP: its codec (H264, H265, JPEG, ...) and its fmtp parameters."""
    media: list[dict] = []
    for raw in sdp.splitlines():
        line = raw.strip()
        if line.startswith("m="):
            parts = line[2:].split()
            media.append({"kind": parts[0] if parts else "", "pts": parts[3:], "rtpmap": {}, "fmtp": {}})
        elif media:
            if m := _RTPMAP.match(line):
                media[-1]["rtpmap"][m.group(1)] = m.group(2).upper()
            elif m := _FMTP.match(line):
                media[-1]["fmtp"][m.group(1)] = m.group(2)
    for m in media:
        if m["kind"] != "video" or not m["pts"]:
            continue
        pt = m["pts"][0]
        codec = m["rtpmap"].get(pt) or ("JPEG" if pt == "26" else None)
        if codec == "HEVC":
            codec = "H265"
        return VideoMedia(codec, m["fmtp"].get(pt, ""))
    return None


def sps_from_fmtp(fmtp: str) -> bytes | None:
    """The first SPS NAL unit announced in an H.264 `sprop-parameter-sets`, if any."""
    m = re.search(r"sprop-parameter-sets=([^;\s]+)", fmtp, re.I)
    if not m:
        return None
    for item in m.group(1).split(","):
        try:
            nal = base64.b64decode(item + "=" * (-len(item) % 4))
        except ValueError:
            continue
        if nal and nal[0] & 0x1F == 7:
            return nal
    return None


# ---------------------------------------------------------------- RTSP

_AUTH_PARAM = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]*))')


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()   # RFC 2617 digest login (what cameras speak), not a secret store


def authorization(challenges: list[str], user: str, password: str, method: str, uri: str) -> str:
    """The Authorization header value answering the camera's WWW-Authenticate challenges (Digest preferred)."""
    digest = next((c for c in challenges if c.lower().startswith("digest ")), None)
    if digest:
        params = {k.lower(): (q or p) for k, q, p in _AUTH_PARAM.findall(digest[7:])}
        algorithm = params.get("algorithm", "MD5").upper()
        if algorithm != "MD5":
            raise _Failure(f"The camera asks for {algorithm} digest login, which this test cannot do.")
        realm, nonce = params.get("realm", ""), params.get("nonce", "")
        ha1, ha2 = _md5(f"{user}:{realm}:{password}"), _md5(f"{method}:{uri}")
        fields = [f'username="{user}"', f'realm="{realm}"', f'nonce="{nonce}"', f'uri="{uri}"']
        if "auth" in [q.strip() for q in params.get("qop", "").split(",")]:
            cnonce = uuid.uuid4().hex[:16]
            response = _md5(f"{ha1}:{nonce}:00000001:{cnonce}:auth:{ha2}")
            fields += [f'response="{response}"', "qop=auth", "nc=00000001", f'cnonce="{cnonce}"']
        else:
            fields.append(f'response="{_md5(f"{ha1}:{nonce}:{ha2}")}"')
        if "opaque" in params:
            fields.append(f'opaque="{params["opaque"]}"')
        return "Digest " + ", ".join(fields)
    if any(c.lower().startswith("basic") for c in challenges):
        return "Basic " + base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    raise _Failure("The camera asks for a login method this test does not know.")


@dataclass
class _Response:
    status: int
    reason: str
    headers: dict[str, list[str]]
    body: str


def _recv(sock: socket.socket, deadline: float) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise socket.timeout
    sock.settimeout(remaining)
    chunk = sock.recv(4096)
    if not chunk:
        raise _Failure("The camera closed the connection.")
    return chunk


def _read_response(sock: socket.socket, deadline: float) -> _Response:
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += _recv(sock, deadline)
        if len(buf) > MAX_RESPONSE:
            raise _Failure("The answer is not an RTSP response.")
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status_line = lines[0].split(" ", 2)
    if len(status_line) < 2 or not status_line[0].startswith("RTSP/") or not status_line[1].isdigit():
        raise _Failure("The server does not speak RTSP. Check the address and port.")
    headers: dict[str, list[str]] = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers.setdefault(name.strip().lower(), []).append(value.strip())
    declared = (headers.get("content-length") or ["0"])[0]
    length = min(int(declared), MAX_RESPONSE) if declared.isdigit() else 0
    while len(rest) < length:
        rest += _recv(sock, deadline)
    return _Response(int(status_line[1]), status_line[2] if len(status_line) > 2 else "", headers,
                     rest[:length].decode("utf-8", "replace"))


def _describe(sock: socket.socket, url: str, cseq: int, deadline: float, auth: str | None = None) -> _Response:
    lines = [f"DESCRIBE {url} RTSP/1.0", f"CSeq: {cseq}", f"User-Agent: {USER_AGENT}", "Accept: application/sdp"]
    if auth:
        lines.append(f"Authorization: {auth}")
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii", "replace"))
    return _read_response(sock, deadline)


def _next_check(done: list[Check]) -> str:
    """Name of the check that was running when something failed."""
    names = {c.name for c in done}
    return next((n for n in CHECK_ORDER if n not in names), "stream")


def _connect(host: str, port: int, secure: bool, deadline: float, timeout: float) -> socket.socket:
    try:
        sock = socket.create_connection((host, port), timeout=max(0.1, deadline - time.monotonic()))
        if secure:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE   # cameras use self-signed certificates; the test only reads
            sock = ctx.wrap_socket(sock, server_hostname=host)
        return sock
    except socket.gaierror:
        raise _Failure("The host name cannot be found. Check the address.") from None
    except ConnectionRefusedError:
        raise _Failure(f"The camera refused the connection on port {port}. Check the port.") from None
    except (socket.timeout, TimeoutError):
        raise _Failure(f"No answer within {timeout:g} s. Is the camera on, and on this network?") from None
    except ssl.SSLError:
        raise _Failure("The secure (TLS) connection could not be set up.") from None
    except OSError as exc:
        raise _Failure(f"The network refused the connection ({exc.strerror or 'error'}).") from None


def probe_rtsp(uri: str, timeout: float = 5.0) -> ProbeResult:
    parts = urlsplit(uri)
    host = parts.hostname
    if parts.scheme not in ("rtsp", "rtsps") or not host:
        message = "This is not a valid rtsp:// address."
        return ProbeResult(False, message, [Check("connect", False, message)])
    secure = parts.scheme == "rtsps"
    try:
        port = parts.port or (322 if secure else 554)
    except ValueError:
        message = "The port in the address is not valid."
        return ProbeResult(False, message, [Check("connect", False, message)])
    user, password = unquote(parts.username or ""), unquote(parts.password or "")
    # What goes on the wire: the address without the login
    host_part = f"[{host}]" if ":" in host else host
    url = f"{parts.scheme}://{host_part}:{port}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")

    checks: list[Check] = []
    result = ProbeResult(False, "", checks)
    deadline = time.monotonic() + timeout
    sock: socket.socket | None = None
    try:
        started = time.monotonic()
        sock = _connect(host, port, secure, deadline, timeout)
        checks.append(Check("connect", True, f"Connected to port {port}."))

        response = _describe(sock, url, 1, deadline)
        result.latency_ms = round((time.monotonic() - started) * 1000)
        if response.status == 401:
            if not user and not password:
                raise _Failure("The camera needs a username and password.")
            auth = authorization(response.headers.get("www-authenticate", []), user, password, "DESCRIBE", url)
            response = _describe(sock, url, 2, deadline, auth)
            if response.status == 401:
                raise _Failure("The camera did not accept the username or password.")
            checks.append(Check("auth", True, "The username and password were accepted."))
        else:
            checks.append(Check("auth", True, "No login needed."))

        if response.status == 403:
            raise _Failure("The camera does not let this account watch this stream.")
        if response.status == 404:
            raise _Failure("The camera has no stream at this path. Check the end of the address.")
        if response.status != 200:
            raise _Failure(f"The camera answered {response.status} {response.reason}.".replace(" .", "."))
        video = parse_sdp_video(response.body)
        if video is None:
            raise _Failure("The camera answered, but the stream has no video.")
        checks.append(Check("stream", True, "The camera is streaming video."))

        name = SUPPORTED_CODECS.get(video.codec or "")
        result.codec = name or video.codec
        if name:
            checks.append(Check("codec", True, f"{name}."))
        else:
            checks.append(Check("codec", False, f"{video.codec or 'An unknown codec'} is not tested with this engine "
                                                "(H.264 and H.265 are). Set the camera to H.264."))
        sps = sps_from_fmtp(video.fmtp) if video.codec == "H264" else None
        size = h264_resolution(sps) if sps else None
        if size:
            result.width, result.height = size
            checks.append(Check("resolution", True, f"{size[0]}x{size[1]}."))
        else:
            checks.append(Check("resolution", True, NOT_ANNOUNCED))
    except _Failure as failure:
        checks.append(Check(_next_check(checks), False, str(failure)))
    except (socket.timeout, TimeoutError):
        checks.append(Check(_next_check(checks), False, f"The camera did not answer within {timeout:g} s."))
    except OSError as exc:
        checks.append(Check(_next_check(checks), False, f"The connection broke ({exc.strerror or 'error'})."))
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    failing = next((c for c in checks if not c.ok), None)
    result.ok = failing is None
    if failing:
        result.summary = failing.message
    else:
        size_text = f" {result.width}x{result.height}" if result.width else ""
        result.summary = f"Works: {result.codec}{size_text}, answered in {result.latency_ms} ms."
    return result


# ---------------------------------------------------------------- file

def probe_file(uri: str) -> ProbeResult:
    parts = urlsplit(uri)
    path = unquote(parts.path)
    if parts.scheme != "file" or parts.netloc not in ("", "localhost") or not path:
        check = Check("file", False, "This is not a file:// address.")
    elif not os.path.isfile(path):
        check = Check("file", False, "There is no such file (as seen by the backend).")
    elif not os.access(path, os.R_OK):
        check = Check("file", False, "The file exists but the backend cannot read it.")
    else:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        check = Check("file", True, f"The file exists and is readable ({size_mb:.1f} MB).")
    return ProbeResult(check.ok, check.message, [check])


def probe_source(source_type: str, uri: str, timeout: float = 5.0) -> ProbeResult:
    return probe_file(uri) if source_type == "file" else probe_rtsp(uri, timeout)
