"""FastAPI application.

Run it (from the repository root, after `alembic -c backend/alembic.ini upgrade head`):

    uvicorn backend.main:create_app --factory --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.db import make_engine, make_session_factory
from backend.routers import cameras, events, health, internal
from backend.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.configure_logging:
            from src.logging_config import setup_logging

            setup_logging()
        yield
        app.state.engine.dispose()

    app = FastAPI(title="AI Home Surveillance", lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)   # connects lazily, on the first request
    app.state.session_factory = make_session_factory(app.state.engine)

    app.include_router(health.router)
    app.include_router(cameras.router)
    app.include_router(events.router)
    app.include_router(internal.router)
    return app
