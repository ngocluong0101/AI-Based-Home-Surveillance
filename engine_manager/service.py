"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

HTTP face of the engine manager (ROADMAP 3.5). It controls a program, so it listens on the loopback
interface only and has no authentication:

    uvicorn engine_manager.service:create_app --factory --host 127.0.0.1 --port 8100
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine_manager.manager import CameraSpec, EngineManager, ManagerConfig


class ZoneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["restricted", "lie_ok", "ignore"]
    zone_id: int = Field(ge=0, le=100000)
    points: list[list[float]] = Field(min_length=3, max_length=32)

    @field_validator("points")
    @classmethod
    def _in_frame(cls, value: list[list[float]]) -> list[list[float]]:
        for p in value:
            if len(p) != 2 or not all(0.0 <= v <= 1.0 for v in p):
                raise ValueError("each point is [x, y] with both between 0 and 1")
        return value


class CameraIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0, le=2**31 - 1)
    uri: str = Field(min_length=1, max_length=2000)
    # optional: an older backend sends only id and uri
    conf_threshold: float | None = Field(default=None, ge=0, le=1)
    absence_timeout: float | None = Field(default=None, ge=0, le=86400)
    ai_enabled: bool | None = None
    behaviors: list[Literal["fall", "fight", "zones"]] | None = Field(default=None, max_length=3)
    zones: list[ZoneIn] | None = Field(default=None, max_length=8)
    fall_net: Literal[0, 1, 2] | None = None
    fall_net_thr: float | None = Field(default=None, ge=0, le=1)
    fall_net_k: int | None = Field(default=None, ge=1, le=100)

    @field_validator("uri")
    @classmethod
    def _printable(cls, value: str) -> str:
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("control characters are not allowed in a URI")
        return value


class CamerasIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cameras: list[CameraIn] = Field(max_length=64)
    revision: str | None = Field(default=None, max_length=128)

    @field_validator("cameras")
    @classmethod
    def _unique_ids(cls, value: list[CameraIn]) -> list[CameraIn]:
        ids = [c.id for c in value]
        if len(ids) != len(set(ids)):
            raise ValueError("camera ids must be unique")
        return value


def create_app(manager: EngineManager | None = None, configure_logging: bool = True) -> FastAPI:
    manager = manager or EngineManager(ManagerConfig.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if configure_logging:
            from src.logging_config import setup_logging

            setup_logging(log_file="logs/engine_manager.log")   # also gets what the engine prints
        manager.start()
        yield
        manager.stop()   # SIGTERM to the engine, so that it flushes before the manager goes

    app = FastAPI(title="Engine manager", lifespan=lifespan)
    app.state.manager = manager

    @app.get("/status")
    def status() -> dict:
        return manager.status()

    @app.put("/cameras")
    def put_cameras(body: CamerasIn) -> dict:
        """Replace the list of cameras that should run. [] stops the engine."""
        manager.set_cameras(
            [
                CameraSpec(
                    c.id, c.uri, c.conf_threshold, c.absence_timeout, c.ai_enabled,
                    None if c.behaviors is None else tuple(c.behaviors),
                    None if c.zones is None else tuple((z.kind, z.zone_id, tuple((p[0], p[1]) for p in z.points)) for z in c.zones),
                    c.fall_net, c.fall_net_thr, c.fall_net_k,
                )
                for c in body.cameras
            ],
            body.revision,
        )
        return manager.status()

    return app
