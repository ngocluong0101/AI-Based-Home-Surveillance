"""FastAPI application.

Run it (from the repository root, after `alembic -c backend/alembic.ini upgrade head`):

    uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.db import make_engine, make_session_factory
from backend.engine_client import EngineClient
from backend.heartbeats import HeartbeatStore
from backend.reconciler import Reconciler
from backend.routers import cameras, events, health, internal
from backend.settings import Settings


def create_app(settings: Settings | None = None, engine_client: EngineClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.configure_logging:
            from src.logging_config import setup_logging

            setup_logging()
        if app.state.reconciler is not None:
            app.state.reconciler.start()
        yield
        if app.state.reconciler is not None:
            app.state.reconciler.stop()
            app.state.reconciler.client.close()
        app.state.engine.dispose()

    app = FastAPI(title="AI Home Surveillance", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)   # connects lazily, on the first request
    app.state.session_factory = make_session_factory(app.state.engine)
    app.state.heartbeats = HeartbeatStore(settings.heartbeat_stale_after)

    # Without an engine manager the start/stop endpoints still record what should run.
    if engine_client is None and settings.engine_manager_url:
        engine_client = EngineClient(settings.engine_manager_url)
    app.state.reconciler = (
        Reconciler(app.state.session_factory, engine_client, settings.reconcile_interval) if engine_client else None
    )

    app.include_router(health.router)
    app.include_router(cameras.router)
    app.include_router(events.router)
    app.include_router(internal.router)
    return app
