"""Camera API (ROADMAP 3.3). start / stop / status come with the engine manager (3.5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import Camera
from backend.schemas import CameraCreate, CameraOut, CameraUpdate

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


def _get_active(db: Session, camera_id: int) -> Camera:
    camera = db.get(Camera, camera_id)
    if camera is None or camera.deleted_at is not None:
        raise HTTPException(404, "camera not found")
    return camera


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
def update_camera(camera_id: int, body: CameraUpdate, db: Session = Depends(get_db)) -> Camera:
    camera = _get_active(db, camera_id)
    for field in body.model_fields_set:
        setattr(camera, field, getattr(body, field))
    camera.updated_at = func.now()
    _commit_or_conflict(db)
    db.refresh(camera)
    return camera


@router.delete("/{camera_id}", status_code=204)
def delete_camera(camera_id: int, db: Session = Depends(get_db)) -> Response:
    """Soft delete: the row stays (its events point at it), the name becomes free."""
    camera = _get_active(db, camera_id)
    camera.deleted_at = func.now()
    camera.enabled = False
    camera.desired_state = "stopped"
    camera.updated_at = func.now()
    db.commit()
    return Response(status_code=204)
