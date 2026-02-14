from fastapi import APIRouter
from server.services.event_service import get_events

router = APIRouter()

@router.get("/events")
def list_events():
    return get_events()
