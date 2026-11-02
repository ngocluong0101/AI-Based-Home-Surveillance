"""Event API (ROADMAP 3.4)."""

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
from backend.schemas import EventOut, EventPage

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
        snapshot_url=f"/api/events/{event.event_id}/snapshot" if event.snapshot_file else None,
        status=event.status,
        notify_status=event.notify_status,
    )


@router.get("", response_model=EventPage)
def list_events(
    camera_id: int | None = None,
    date_from: datetime | None = Query(None, alias="from", description="ISO 8601; naive times count as UTC"),
    date_to: datetime | None = Query(None, alias="to", description="exclusive upper bound"),
    type: str | None = None,
    status: str | None = None,
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
        conditions.append(Event.type == type)
    if status is not None:
        conditions.append(Event.status == status)

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
