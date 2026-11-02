from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from backend.db import normalize_url
from backend.main import create_app
from backend.models import Base
from backend.settings import Settings

from .conftest import migrate


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok"}


def test_health_is_503_when_the_database_is_unreachable():
    settings = Settings(database_url="postgresql+psycopg://nobody@127.0.0.1:1/none", configure_logging=False)
    with TestClient(create_app(settings)) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 503
    assert resp.json()["database"] == "down"


def test_missing_database_url_is_a_clear_error(monkeypatch):
    import pytest
    from src import config

    monkeypatch.setattr(config, "DATABASE_URL", None)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        Settings.from_env()


def test_plain_postgresql_urls_use_the_psycopg3_driver():
    assert normalize_url("postgresql://u@h/db") == "postgresql+psycopg://u@h/db"
    assert normalize_url("postgres://u@h/db") == "postgresql+psycopg://u@h/db"
    assert normalize_url("postgresql+psycopg://u@h/db") == "postgresql+psycopg://u@h/db"


def test_migration_matches_the_models(database_url, db_engine):
    """The tables built by `alembic upgrade head` are exactly what backend/models.py describes."""
    engine = create_engine(normalize_url(database_url))
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_migration_downgrades_and_upgrades_again(database_url, db_engine):
    migrate(database_url, "base", downgrade=True)
    engine = create_engine(normalize_url(database_url))
    assert not {"cameras", "events"} & set(inspect(engine).get_table_names())
    migrate(database_url)
    assert {"cameras", "events"} <= set(inspect(engine).get_table_names())
    engine.dispose()
