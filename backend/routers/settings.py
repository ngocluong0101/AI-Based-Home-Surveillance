"""The Settings page: what is changed at run time (backend/app_settings.py), plus a few read-only facts."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from backend import __version__
from backend.app_settings import as_dict, snapshot_usage
from backend.schemas import NotificationCheckOut, SettingsOut, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])
log = logging.getLogger(__name__)

TEST_MESSAGE = "Test message from AI Home Surveillance. If you can read this, alerts will arrive here."


def _view(request: Request) -> SettingsOut:
    state = request.app.state
    count, size = snapshot_usage(state.settings.snapshot_dir)
    return SettingsOut(
        **as_dict(state.app_settings.get()),
        telegram_configured=bool(getattr(state.telegram, "enabled", False)),
        snapshot_count=count,
        snapshot_bytes=size,
        version=__version__,
    )


@router.get("", response_model=SettingsOut)
def get_settings(request: Request) -> SettingsOut:
    return _view(request)


@router.patch("", response_model=SettingsOut)
def change_settings(body: SettingsUpdate, request: Request) -> SettingsOut:
    changes = body.model_dump(exclude_unset=True)
    store = request.app.state.app_settings
    before = store.get()
    after = {**as_dict(before), **changes}
    if after["quiet_hours_enabled"] and after["quiet_start"] == after["quiet_end"]:
        raise HTTPException(422, "quiet_start and quiet_end must differ")
    if changes:
        store.update(changes)
        if "snapshot_retention_days" in changes and changes["snapshot_retention_days"] != before.snapshot_retention_days:
            request.app.state.janitor.nudge()   # apply the new retention soon instead of at the next hourly pass
    return _view(request)


@router.post("/test-notification", response_model=NotificationCheckOut)
def test_notification(request: Request) -> NotificationCheckOut:
    """Sends a message to Telegram, whatever the alert switch says, so the connection can be checked."""
    telegram = request.app.state.telegram
    if not getattr(telegram, "enabled", False):
        return NotificationCheckOut(
            ok=False,
            message="Telegram is not set up. Set ENABLE_TELEGRAM, TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env and restart the backend.",
        )
    send = getattr(telegram, "send_text", None)
    if send is None:
        return NotificationCheckOut(ok=False, message="This notifier cannot send a test message.")
    try:
        sent = bool(send(TEST_MESSAGE))
    except Exception:   # the notifier promises not to raise; a stand-in or a bug must not become a 500
        log.exception("Telegram test message raised")
        sent = False
    if sent:
        return NotificationCheckOut(ok=True, message="Test message sent. Check Telegram.")
    return NotificationCheckOut(ok=False, message="Telegram did not accept the message. The backend log says why.")
