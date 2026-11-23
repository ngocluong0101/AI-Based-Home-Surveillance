"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Event API (ROADMAP 3.4).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Event
from backend.schemas import EVENT_TYPES, SEVERITY, VERIFICATION_STATES, EventOut, EventPage, EventPatch

router = APIRouter(prefix="/api/events", tags=["events"])


def to_out(event: Event) -> EventOut:
    return EventOut(
        event_id=event.event_id,
        camera_id=event.camera_id,
        pipeline_run_id=event.pipeline_run_id,
        type=event.type,
        track_id=event.track_id,
        confidence=event.confidence,
        timestamp=event.timestamp,
        bbox=event.bbox,
        track_ids=list(event.track_ids) or [event.track_id],
        zone_id=event.zone_id,
        occurred_at=event.occurred_at,
        severity=SEVERITY.get(event.type, "info"),
        snapshot_url=f"/api/events/{event.event_id}/snapshot" if event.snapshot_file else None,
        status=event.status,
        notify_status=event.notify_status,
        verification=event.verification,
        verification_score=event.verification_score,
        verification_reason=event.verification_reason,
    )


def publish_event(hub, type_: str, event: Event) -> None:
    """Push an event to the WebSocket clients ("event.created" / "event.updated")."""
    hub.publish(type_, to_out(event).model_dump(mode="json"))


@router.get("", response_model=EventPage)
def list_events(
    camera_id: int | None = None,
    date_from: datetime | None = Query(None, alias="from", description="ISO 8601; naive times count as UTC"),
    date_to: datetime | None = Query(None, alias="to", description="exclusive upper bound"),
    type: str | None = Query(None, description="one or several types, comma separated: FALL,FIGHT"),
    status: str | None = None,
    verification: str | None = Query(None, description="one or several of not_required, pending, confirmed, rejected, unverified, comma separated"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> EventPage:
    """Newest first. `from` is inclusive, `to` exclusive, so consecutive ranges do not overlap."""
    conditions = []
    if camera_id is not None:
        conditions.append(Event.camera_id == camera_id)
    if date_from is not None:
        conditions.append(Event.timestamp >= date_from)
    if date_to is not None:
        conditions.append(Event.timestamp < date_to)
    if type is not None:
        wanted = [t.strip() for t in type.split(",") if t.strip()]
        unknown = [t for t in wanted if t not in EVENT_TYPES]
        if unknown or not wanted:
            raise HTTPException(422, f"type must be one or more of {', '.join(EVENT_TYPES)}")
        conditions.append(Event.type.in_(wanted))
    if status is not None:
        conditions.append(Event.status == status)
    if verification is not None:
        wanted_v = [v.strip() for v in verification.split(",") if v.strip()]
        if not wanted_v or any(v not in VERIFICATION_STATES for v in wanted_v):
            raise HTTPException(422, f"verification must be one or more of {', '.join(VERIFICATION_STATES)}")
        conditions.append(Event.verification.in_(wanted_v))

    total = db.scalar(select(func.count()).select_from(Event).where(*conditions)) or 0
    rows = db.scalars(
        select(Event)
        .where(*conditions)
        .order_by(Event.timestamp.desc(), Event.event_id)   # event_id: a stable order for equal times
        .limit(limit)
        .offset(offset)
    )
    return EventPage(items=[to_out(r) for r in rows], total=total, limit=limit, offset=offset)


def _get(db: Session, event_id: uuid.UUID) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "event not found")
    return event


@router.get("/{event_id}", response_model=EventOut)
def get_event(event_id: uuid.UUID, db: Session = Depends(get_db)) -> EventOut:
    return to_out(_get(db, event_id))


@router.patch("/{event_id}", response_model=EventOut)
def patch_event(event_id: uuid.UUID, body: EventPatch, request: Request, db: Session = Depends(get_db)) -> EventOut:
    """Acknowledge an event (or mark it new again). Pushes `event.updated` when the status changed."""
    event = _get(db, event_id)
    changed = event.status != body.status
    if changed:
        event.status = body.status
        db.commit()
        db.refresh(event)
        publish_event(request.app.state.hub, "event.updated", event)
    return to_out(event)


@router.get("/{event_id}/snapshot")
def get_snapshot(event_id: uuid.UUID, request: Request, db: Session = Depends(get_db)) -> FileResponse:
    """The JPEG of the event. 404 when the event has none or the cleaner already removed the file."""
    event = _get(db, event_id)
    if not event.snapshot_file:
        raise HTTPException(404, "this event has no snapshot")
    root = Path(request.app.state.settings.snapshot_dir).resolve()
    path = (root / event.snapshot_file).resolve()
    # Names are validated when the event is stored; this keeps a bad row from escaping the directory.
    if path.parent != root or not path.is_file():
        raise HTTPException(404, "snapshot file is gone")
    return FileResponse(path, media_type="image/jpeg")
