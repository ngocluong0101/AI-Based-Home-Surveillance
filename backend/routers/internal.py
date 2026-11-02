"""Endpoints for the engine, not for browsers (ROADMAP 3.5)."""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Event
from backend.heartbeats import CameraBeat
from backend.routers.events import publish_event
from backend.schemas import EventIn, HeartbeatIn

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger(__name__)


def require_token(request: Request, x_internal_token: str | None = Header(default=None)) -> None:
    expected = request.app.state.settings.internal_token
    if expected is None:
        return   # no token configured: LAN-only deployment
    if x_internal_token is None or not hmac.compare_digest(x_internal_token, expected):
        raise HTTPException(401, "missing or wrong X-Internal-Token")


@router.post("/events", status_code=201, dependencies=[Depends(require_token)])
def post_event(body: EventIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, object]:
    """Store an event of the engine. Idempotent on event_id: the engine resends what it is not sure
    arrived (after a timeout, or from its spool file after a restart), so a repeat answers 200
    "duplicate" and changes nothing. Answers 422 for an event that can never be stored (unknown
    camera, bad values), which tells the engine to drop it rather than retry."""
    stmt = (
        insert(Event)
        .values(
            event_id=body.event_id,
            camera_id=body.camera_id,
            pipeline_run_id=body.pipeline_run_id,
            type=body.type,
            track_id=body.track_id,
            confidence=body.confidence,
            timestamp=body.timestamp,
            bbox=body.bbox,
            snapshot_file=body.snapshot,
        )
        .on_conflict_do_nothing(index_elements=[Event.event_id])
        .returning(Event.event_id)
    )
    try:
        inserted = db.execute(stmt).scalar_one_or_none() is not None
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        log.warning("event %s refused: camera %s does not exist", body.event_id, body.camera_id)
        raise HTTPException(422, f"unknown camera_id {body.camera_id}") from exc

    if not inserted:
        response.status_code = 200
    else:
        stored = db.get(Event, body.event_id)
        if stored is not None:
            publish_event(request.app.state.hub, "event.created", stored)
        if body.snapshot:
            request.app.state.janitor.note_snapshot()
        request.app.state.notifier.trigger()
    return {"event_id": str(body.event_id), "duplicate": not inserted}


@router.post("/heartbeat", status_code=204, dependencies=[Depends(require_token)])
def post_heartbeat(body: HeartbeatIn, request: Request) -> Response:
    """The engine says it is alive and what each of its cameras is doing (ROADMAP 3.6). Kept in memory
    for GET /api/cameras/{id}/status; nothing is stored. A camera listed twice keeps its last entry."""
    request.app.state.heartbeats.update(
        body.run_id,
        {c.camera_id: CameraBeat(c.state, c.fps, c.last_frame_age, c.people) for c in body.cameras},
    )
    request.app.state.camera_status.on_heartbeat({c.camera_id for c in body.cameras})
    return Response(status_code=204)
