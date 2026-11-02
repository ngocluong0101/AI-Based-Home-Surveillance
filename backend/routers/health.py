from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.db import get_db

router = APIRouter(prefix="/api", tags=["health"])
log = logging.getLogger(__name__)


@router.get("/health")
def health(response: Response, db: Session = Depends(get_db)) -> dict[str, str]:
    """200 when the backend can reach its database, 503 when it cannot."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.exception("database health check failed")
        response.status_code = 503
        return {"status": "degraded", "database": "down"}
    return {"status": "ok", "database": "ok"}
