"""Backend settings, taken from src.config (environment / .env)."""

from __future__ import annotations

from dataclasses import dataclass

from src import config


@dataclass(frozen=True)
class Settings:
    database_url: str
    internal_token: str | None = None   # None = /internal/* needs no token (LAN only)
    snapshot_dir: str = "data/snapshots"
    configure_logging: bool = True
    engine_manager_url: str | None = None   # None = no engine is managed from here
    reconcile_interval: float = 5.0         # seconds between looks at the manager; 0 = no background thread

    @classmethod
    def from_env(cls) -> "Settings":
        if not config.DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is not set, e.g. postgresql+psycopg://surveillance@127.0.0.1:5432/surveillance"
            )
        return cls(
            database_url=config.DATABASE_URL,
            internal_token=config.INTERNAL_TOKEN,
            snapshot_dir=config.SNAPSHOT_DIR,
            engine_manager_url=config.ENGINE_MANAGER_URL,
        )
