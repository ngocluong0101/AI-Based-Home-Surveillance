"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

The VLM service (V2-6): `POST /verify` with a few frames of an event answers yes / no / unsure.

    uvicorn vlm_service.app:create_app --factory --host 127.0.0.1 --port 8200

Loopback, no auth (LAN). The model loads at start-up and requests are served one at a time. Environment: VLM_MODEL (default
Qwen/Qwen3-VL-2B-Instruct), VLM_YES_THR (0.5), VLM_NO_THR (0.9): "no" must be much surer than "yes" because it silences an alert.
"""
from __future__ import annotations

import base64
import binascii
import io
import os
import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .prompts import QUESTIONS

MAX_FRAMES = 12
MAX_SIDE = 640


class VerifyIn(BaseModel):
    event_type: str
    frames: list[str] = Field(min_length=1, max_length=MAX_FRAMES)       # base64 JPEG, oldest first
    seconds_apart: float = Field(default=0.5, ge=0, le=60)
    reason: bool = True


class VerifyOut(BaseModel):
    verdict: str
    p_yes: float
    p_no: float
    p_unsure: float
    reason: str
    latency_s: float
    frames: int


def _decode(frames: list[str]):
    from PIL import Image
    out = []
    for i, b in enumerate(frames):
        try:
            img = Image.open(io.BytesIO(base64.b64decode(b, validate=True))).convert("RGB")
        except (binascii.Error, OSError, ValueError) as exc:
            raise HTTPException(422, f"frame {i} is not a base64 image") from exc
        if max(img.size) > MAX_SIDE:
            img.thumbnail((MAX_SIDE, MAX_SIDE))
        out.append(img)
    return out


def create_app(verifier=None) -> FastAPI:
    """`verifier`: anything with `verify(event_type, frames, seconds_apart, reason)` returning a Verdict; default loads Qwen3-VL."""
    app = FastAPI(title="VLM service")
    app.state.verifier = verifier
    app.state.lock = threading.Lock()

    @app.on_event("startup")
    def load() -> None:
        if app.state.verifier is None:
            from .verifier import DEFAULT_MODEL, QwenVL
            app.state.verifier = QwenVL(os.getenv("VLM_MODEL", DEFAULT_MODEL), yes_thr=float(os.getenv("VLM_YES_THR", "0.5")),
                                        no_thr=float(os.getenv("VLM_NO_THR", "0.9")))

    @app.get("/health")
    def health() -> dict:
        return {"loaded": app.state.verifier is not None, "event_types": sorted(QUESTIONS)}

    @app.post("/verify", response_model=VerifyOut)
    def verify(body: VerifyIn) -> VerifyOut:
        if body.event_type not in QUESTIONS:
            raise HTTPException(422, f"event_type must be one of {', '.join(sorted(QUESTIONS))}")
        if app.state.verifier is None:
            raise HTTPException(503, "the model is not loaded")
        images = _decode(body.frames)
        with app.state.lock:
            try:
                v = app.state.verifier.verify(body.event_type, images, body.seconds_apart, reason=body.reason)
            except Exception as exc:
                raise HTTPException(503, f"the model failed: {type(exc).__name__}") from exc
        return VerifyOut(verdict=v.verdict, p_yes=v.p_yes, p_no=v.p_no, p_unsure=v.p_unsure, reason=v.reason, latency_s=v.latency_s, frames=len(images))

    return app
