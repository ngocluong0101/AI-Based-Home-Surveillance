"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

HTTP client of the VLM service (vlm_service/app.py), one call per event. Any failure is an exception the verifier turns into
`unverified`. Frames travel as base64 JPEG in the JSON body.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class VlmAnswer:
    verdict: str          # yes | no | unsure
    p_yes: float
    reason: str
    latency_s: float


class VlmClient:
    def __init__(self, base_url: str, timeout: float, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    def verify(self, event_type: str, frames: list[bytes], seconds_apart: float, reason: bool = True) -> VlmAnswer:
        r = self._http.post("/verify", json={"event_type": event_type, "seconds_apart": seconds_apart, "reason": reason,
                                             "frames": [base64.b64encode(f).decode("ascii") for f in frames]})
        r.raise_for_status()
        d = r.json()
        if d.get("verdict") not in ("yes", "no", "unsure"):
            raise ValueError(f"the VLM service answered verdict {d.get('verdict')!r}")
        return VlmAnswer(d["verdict"], float(d.get("p_yes", 0.0)), str(d.get("reason", "")), float(d.get("latency_s", 0.0)))

    def close(self) -> None:
        self._http.close()
