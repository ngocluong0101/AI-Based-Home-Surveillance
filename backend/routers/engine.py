"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

What the web app needs to know about the engine as a whole (not about one camera).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.engine_client import EngineManagerError
from backend.reconciler import desired_cameras, revision_of
from backend.schemas import EngineSummary

router = APIRouter(prefix="/api/engine", tags=["engine"])


@router.get("", response_model=EngineSummary)
def engine_summary(request: Request, db: Session = Depends(get_db)) -> EngineSummary:
    """How many cameras should run, how many places the running engine has, and whether one more camera fits (`room`).
    Within the places a camera is added without a restart; beyond them the engine restarts once.
    """
    wanted = desired_cameras(db)
    reconciler = request.app.state.reconciler
    if reconciler is None:
        return EngineSummary(manager="not_configured", wanted=len(wanted))
    try:
        st = reconciler.client.status()
    except EngineManagerError:
        return EngineSummary(manager="unreachable", wanted=len(wanted))
    # The places of the running engine; while none runs, the places the next one will have. 0 or missing = the
    # manager keeps no spare places (or is an older one): there is nothing to tell then.
    capacity = st.get("capacity") or st.get("max_cameras") or None
    return EngineSummary(
        manager="reachable",
        state=st.get("state"),
        capacity=capacity,
        running=len(st.get("running_cameras") or []),
        wanted=len(wanted),
        applied=st.get("revision") == revision_of(wanted),
        room=None if capacity is None else max(capacity - len(wanted), 0),
    )
