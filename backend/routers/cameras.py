"""Camera API (ROADMAP 3.3, 3.5)."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.engine_client import EngineManagerError
from backend.models import Camera
from backend.reconciler import desired_cameras, revision_of
from backend.schemas import CameraCreate, CameraOut, CameraStatus, CameraUpdate, EngineInfo, StreamInfo, StreamLink

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


def _get_active(db: Session, camera_id: int) -> Camera:
    camera = db.get(Camera, camera_id)
    if camera is None or camera.deleted_at is not None:
        raise HTTPException(404, "camera not found")
    return camera


def _engine_changed(request: Request) -> None:
    """Tell the background reconciler to look now (no-op when no engine manager is configured)."""
    reconciler = request.app.state.reconciler
    if reconciler is not None:
        reconciler.trigger()


def _commit_or_conflict(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # The only constraint a valid request can hit is the unique name among active cameras.
        raise HTTPException(409, "a camera with this name already exists") from exc


@router.get("", response_model=list[CameraOut])
def list_cameras(db: Session = Depends(get_db)) -> list[Camera]:
    return list(db.scalars(select(Camera).where(Camera.deleted_at.is_(None)).order_by(Camera.id)))


@router.post("", response_model=CameraOut, status_code=201)
def create_camera(body: CameraCreate, db: Session = Depends(get_db)) -> Camera:
    camera = Camera(**body.model_dump())
    db.add(camera)
    _commit_or_conflict(db)
    db.refresh(camera)
    return camera


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, db: Session = Depends(get_db)) -> Camera:
    return _get_active(db, camera_id)


@router.patch("/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: int, body: CameraUpdate, request: Request, db: Session = Depends(get_db)) -> Camera:
    camera = _get_active(db, camera_id)
    for field in body.model_fields_set:
        setattr(camera, field, getattr(body, field))
    camera.updated_at = func.now()
    _commit_or_conflict(db)
    db.refresh(camera)
    _engine_changed(request)
    return camera


@router.delete("/{camera_id}", status_code=204)
def delete_camera(camera_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    """Soft delete: the row stays (its events point at it), the name becomes free."""
    camera = _get_active(db, camera_id)
    camera.deleted_at = func.now()
    camera.enabled = False
    camera.desired_state = "stopped"
    camera.updated_at = func.now()
    db.commit()
    _engine_changed(request)
    return Response(status_code=204)


def _status(request: Request, db: Session, camera: Camera) -> CameraStatus:
    should_run = camera.enabled and camera.desired_state == "running"
    reconciler = request.app.state.reconciler
    engine = EngineInfo(manager="not_configured")
    if reconciler is not None:
        try:
            st = reconciler.client.status()
        except EngineManagerError:
            engine = EngineInfo(manager="unreachable")
        else:
            engine = EngineInfo(
                manager="reachable",
                state=st["state"],
                in_engine=camera.id in st["running_cameras"],
                applied=st.get("revision") == revision_of(desired_cameras(db)),
                starts=st["starts"],
                last_exit_code=st["last_exit_code"],
            )
    stream = StreamInfo(**asdict(request.app.state.heartbeats.health(camera.id)))
    return CameraStatus(camera_id=camera.id, enabled=camera.enabled, desired_state=camera.desired_state,
                        should_run=should_run, engine=engine, stream=stream)


def _set_desired(request: Request, db: Session, camera_id: int, state: str) -> CameraStatus:
    camera = _get_active(db, camera_id)
    if state == "running" and not camera.enabled:
        raise HTTPException(409, "camera is disabled; enable it first")
    camera.desired_state = state
    camera.updated_at = func.now()
    db.commit()
    _engine_changed(request)
    return _status(request, db, camera)


@router.post("/{camera_id}/start", response_model=CameraStatus)
def start_camera(camera_id: int, request: Request, db: Session = Depends(get_db)) -> CameraStatus:
    """Record that the camera should run. The engine is (re)started with the new list in the background
    (a few seconds); GET .../status shows when it has been applied."""
    return _set_desired(request, db, camera_id, "running")


@router.post("/{camera_id}/stop", response_model=CameraStatus)
def stop_camera(camera_id: int, request: Request, db: Session = Depends(get_db)) -> CameraStatus:
    return _set_desired(request, db, camera_id, "stopped")


@router.get("/{camera_id}/status", response_model=CameraStatus)
def camera_status(camera_id: int, request: Request, db: Session = Depends(get_db)) -> CameraStatus:
    return _status(request, db, _get_active(db, camera_id))


@router.get("/{camera_id}/stream", response_model=StreamLink)
def camera_stream(camera_id: int, request: Request, db: Session = Depends(get_db)) -> StreamLink:
    """The URLs to watch the camera live (video with boxes and track IDs, WebRTC through MediaMTX)."""
    camera = _get_active(db, camera_id)
    settings = request.app.state.settings
    if not settings.mediamtx_webrtc_url:
        raise HTTPException(503, "live video is not configured (MEDIAMTX_WEBRTC_URL)")
    base = f"{settings.mediamtx_webrtc_url.rstrip('/')}/{settings.stream_path_prefix.strip('/')}/cam{camera.id}"
    available = request.app.state.heartbeats.health(camera.id).reporting
    return StreamLink(camera_id=camera.id, whep_url=f"{base}/whep", player_url=base, available=available)
