"""Tables (ROADMAP 3.2). Per-frame metadata is deliberately not stored."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Camera(Base):
    __tablename__ = "cameras"

    # The id is the camera_id the engine puts in its events (--camera ID=URI).
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    source_type: Mapped[str] = mapped_column(String(8))
    # May hold a password (rtsp://user:pass@host/...): it is what the engine is started with, so it
    # is stored as is, and the API only ever shows a masked form.
    source_uri: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    ai_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    desired_state: Mapped[str] = mapped_column(String(8), server_default=text("'stopped'"))
    # 0.25 = what the detector itself already produces (pre-cluster-threshold in the nvinfer config); a higher value
    # drops the less certain detections of this camera, a lower one changes nothing.
    conf_threshold: Mapped[float] = mapped_column(Float, server_default=text("0.25"))
    absence_timeout: Mapped[float] = mapped_column(Float, server_default=text("10"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("source_type IN ('file', 'rtsp')", name="ck_cameras_source_type"),
        CheckConstraint("desired_state IN ('running', 'stopped')", name="ck_cameras_desired_state"),
        CheckConstraint("conf_threshold >= 0 AND conf_threshold <= 1", name="ck_cameras_conf_threshold"),
        CheckConstraint("absence_timeout >= 0", name="ck_cameras_absence_timeout"),
        # A deleted camera keeps its row (events point at it) but frees its name.
        Index("uq_cameras_name_active", "name", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )


class Event(Base):
    __tablename__ = "events"

    # Chosen by the engine; a second POST of the same event_id changes nothing.
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    camera_id: Mapped[int] = mapped_column(ForeignKey("cameras.id"))
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    type: Mapped[str] = mapped_column(String(32))
    track_id: Mapped[int] = mapped_column(BigInteger)
    confidence: Mapped[float] = mapped_column(Float)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bbox: Mapped[list[float]] = mapped_column(JSONB)   # [x, y, w, h], each 0..1
    snapshot_file: Mapped[str | None] = mapped_column(String(192))   # file name inside SNAPSHOT_DIR
    status: Mapped[str] = mapped_column(String(16), server_default=text("'new'"))
    notify_status: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_events_confidence"),
        CheckConstraint("status IN ('new', 'acknowledged')", name="ck_events_status"),
        CheckConstraint(
            "notify_status IN ('pending', 'sent', 'failed', 'disabled')", name="ck_events_notify_status"
        ),
        Index("ix_events_camera_timestamp", "camera_id", text("timestamp DESC")),
        Index("ix_events_timestamp", text("timestamp DESC")),
    )
