"""FastAPI application.

Run it (from the repository root, after `alembic -c backend/alembic.ini upgrade head`):

    uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from backend.db import make_engine, make_session_factory
from backend.engine_client import EngineClient
from backend.heartbeats import HeartbeatStore
from backend.janitor import SnapshotJanitor
from backend.models import Event
from backend.notifier import Notifier
from backend.realtime import CameraStatusPublisher, Hub
from backend.reconciler import Reconciler
from backend.routers import cameras, engine, events, health, internal, ws
from backend.routers.events import publish_event
from backend.settings import Settings


async def _watch_heartbeats(app: FastAPI, interval: float) -> None:
    """Pushes camera.status for cameras whose heartbeats stopped (nothing else would tell the browser)."""
    while True:
        await asyncio.sleep(interval)
        app.state.camera_status.check_stale()


def create_app(settings: Settings | None = None, engine_client: EngineClient | None = None,
               telegram=None) -> FastAPI:
    """`telegram`: anything with `enabled`, `send_person_detected(event)` and
    `send_person_detected_photo(event, path)`; default is the TelegramNotifier configured by .env."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.configure_logging:
            from src.logging_config import setup_logging

            setup_logging()
        if app.state.reconciler is not None:
            app.state.reconciler.start()
        app.state.notifier.start()
        app.state.janitor.start()
        app.state.hub.attach(asyncio.get_running_loop())
        watcher = asyncio.create_task(_watch_heartbeats(app, settings.status_check_interval))
        yield
        watcher.cancel()
        with suppress(asyncio.CancelledError):
            await watcher
        app.state.hub.detach()
        app.state.notifier.stop()
        app.state.janitor.stop()
        if app.state.reconciler is not None:
            app.state.reconciler.stop()
            app.state.reconciler.client.close()
        app.state.engine.dispose()

    app = FastAPI(title="AI Home Surveillance", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)   # connects lazily, on the first request
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.heartbeats = HeartbeatStore(settings.heartbeat_stale_after)
    app.state.hub = Hub()
    if telegram is None:
        from src.notifications.telegram_notifier import TelegramNotifier

        telegram = TelegramNotifier()

    def announce_update(event_id) -> None:
        with app.state.session_factory() as db:
            event = db.get(Event, event_id)
            if event is not None:
                publish_event(app.state.hub, "event.updated", event)

    app.state.notifier = Notifier(app.state.session_factory, telegram, settings.snapshot_dir,
                                  settings.notify_interval, settings.notify_max_age, announce_update)
    app.state.janitor = SnapshotJanitor(app.state.session_factory, settings.snapshot_dir,
                                        settings.snapshot_retention_days, settings.snapshot_cleanup_every,
                                        settings.cleanup_check_interval)
    app.state.camera_status = CameraStatusPublisher(app.state.heartbeats, app.state.hub)

    # Without an engine manager the start/stop endpoints still record what should run.
    if engine_client is None and settings.engine_manager_url:
        engine_client = EngineClient(settings.engine_manager_url)
    app.state.reconciler = (
        Reconciler(app.state.session_factory, engine_client, settings.reconcile_interval) if engine_client else None
    )

    app.include_router(health.router)
    app.include_router(cameras.router)
    app.include_router(engine.router)
    app.include_router(events.router)
    app.include_router(internal.router)
    app.include_router(ws.router)
    return app
