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
    heartbeat_stale_after: float = 15.0     # a heartbeat older than this says nothing about a camera any more
    status_check_interval: float = 1.0      # how often silent heartbeats are looked for (to push camera.status)
    notify_interval: float = 5.0            # seconds between Telegram rounds; 0 = no notifier thread
    notify_max_age: float = 600.0           # an alert undeliverable for longer than this is marked failed
    snapshot_retention_days: int = 7
    snapshot_cleanup_every: int = 100       # run the cleaner after this many new snapshots (or hourly)
    cleanup_check_interval: float = 60.0    # seconds between looks at whether a cleanup is due; 0 = no janitor thread

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
            heartbeat_stale_after=config.HEARTBEAT_STALE_AFTER,
            notify_max_age=config.NOTIFY_MAX_AGE,
            snapshot_retention_days=config.SNAPSHOT_RETENTION_DAYS,
            snapshot_cleanup_every=config.SNAPSHOT_CLEANUP_INTERVAL,
        )
