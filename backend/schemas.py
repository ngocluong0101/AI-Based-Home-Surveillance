"""Request and response bodies."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

# What the engine may name a snapshot: a plain file name, so that it can never point outside SNAPSHOT_DIR.
SNAPSHOT_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,186}\.jpg$"

SourceType = Literal["file", "rtsp"]
_SCHEMES = {"file": ("file://",), "rtsp": ("rtsp://", "rtsps://")}


def mask_uri(uri: str) -> str:
    """The URI without credentials: user, password and the query string (it may hold a token)."""
    parts = urlsplit(uri)
    host = parts.hostname or ""
    if ":" in host:   # IPv6
        host = f"[{host}]"
    netloc = host + (f":{parts.port}" if parts.port else "")
    if parts.username is not None or parts.password is not None:
        netloc = "***@" + netloc
    return urlunsplit((parts.scheme, netloc, parts.path, "***" if parts.query else "", ""))


def _check_uri(source_type: str, uri: str) -> None:
    if not uri.lower().startswith(_SCHEMES[source_type]):
        raise ValueError(f"source_uri of a {source_type} camera must start with {' or '.join(_SCHEMES[source_type])}")


class CameraCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    source_type: SourceType
    source_uri: str = Field(min_length=1, max_length=2000)
    enabled: bool = True
    ai_enabled: bool = True
    conf_threshold: float = Field(default=0.5, ge=0, le=1)
    absence_timeout: float = Field(default=10.0, ge=0, le=86400)

    @model_validator(mode="after")
    def _uri_matches_type(self) -> "CameraCreate":
        _check_uri(self.source_type, self.source_uri)
        return self


class CameraUpdate(BaseModel):
    """PATCH body: only the fields that are present change. source_type and source_uri go together."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=100)
    source_type: SourceType | None = None
    source_uri: str | None = Field(default=None, min_length=1, max_length=2000)
    enabled: bool | None = None
    ai_enabled: bool | None = None
    conf_threshold: float | None = Field(default=None, ge=0, le=1)
    absence_timeout: float | None = Field(default=None, ge=0, le=86400)

    @model_validator(mode="after")
    def _no_nulls_and_pair(self) -> "CameraUpdate":
        for field in self.model_fields_set:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if ("source_type" in self.model_fields_set) != ("source_uri" in self.model_fields_set):
            raise ValueError("source_type and source_uri must be changed together")
        if self.source_type is not None and self.source_uri is not None:
            _check_uri(self.source_type, self.source_uri)
        return self


class CameraOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_type: SourceType
    source_uri: str   # masked, see mask_uri()
    enabled: bool
    ai_enabled: bool
    desired_state: Literal["running", "stopped"]
    conf_threshold: float
    absence_timeout: float
    created_at: datetime
    updated_at: datetime

    @field_validator("source_uri")
    @classmethod
    def _mask(cls, value: str) -> str:
        return mask_uri(value)


class EngineInfo(BaseModel):
    manager: Literal["reachable", "unreachable", "not_configured"]
    state: str | None = None   # stopped | running | stopping | backoff | exited (the engine process)
    in_engine: bool | None = None   # the running process serves this camera
    applied: bool | None = None   # the manager has the current camera list
    starts: int | None = None
    last_exit_code: int | None = None


class StreamInfo(BaseModel):
    """What the engine itself says about the camera, from its heartbeat (ROADMAP 3.6)."""

    reporting: bool   # a recent heartbeat names this camera; False = the engine is down, still starting, or not serving it
    state: Literal["RUNNING", "RECONNECTING"] | None = None
    fps: float | None = None
    last_frame_age: float | None = None   # seconds without a frame, as of the heartbeat
    heartbeat_age: float | None = None    # seconds since that heartbeat arrived; None = the engine never reported


class CameraStatus(BaseModel):
    camera_id: int
    enabled: bool
    desired_state: Literal["running", "stopped"]
    should_run: bool   # enabled and desired_state is running
    engine: EngineInfo
    stream: StreamInfo


class EventIn(BaseModel):
    """What the engine POSTs (event schema v1, ROADMAP 2.3). Unknown fields are ignored so that a
    newer engine can add some without breaking an older backend."""

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1]
    event_id: uuid.UUID
    camera_id: int = Field(ge=0, le=2**31 - 1)
    pipeline_run_id: uuid.UUID
    type: Literal["PERSON_DETECTED"]
    track_id: int = Field(ge=0, le=2**63 - 1)
    confidence: float = Field(ge=0, le=1)
    timestamp: AwareDatetime
    bbox: list[float] = Field(min_length=4, max_length=4)
    snapshot: str | None = Field(default=None, pattern=SNAPSHOT_NAME_PATTERN)

    @field_validator("bbox")
    @classmethod
    def _bbox_relative(cls, value: list[float]) -> list[float]:
        if not all(0.0 <= v <= 1.0 for v in value):
            raise ValueError("bbox values are relative to the frame and must be between 0 and 1")
        return value


class HeartbeatCameraIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    camera_id: int = Field(ge=0, le=2**31 - 1)
    state: Literal["RUNNING", "RECONNECTING"]
    fps: float = Field(ge=0, le=1e9)
    last_frame_age: float = Field(ge=0, le=1e9)


class HeartbeatIn(BaseModel):
    """What the engine POSTs every few seconds: one per process, all its cameras (ROADMAP 3.6)."""

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1]
    run_id: uuid.UUID
    cameras: list[HeartbeatCameraIn] = Field(max_length=1024)


class EventOut(BaseModel):
    event_id: uuid.UUID
    camera_id: int
    pipeline_run_id: uuid.UUID
    type: str
    track_id: int
    confidence: float
    timestamp: datetime
    bbox: list[float]
    snapshot_url: str | None   # /api/events/{id}/snapshot, or None when there is no image
    status: str
    notify_status: str


class EventPage(BaseModel):
    items: list[EventOut]
    total: int
    limit: int
    offset: int
