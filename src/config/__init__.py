"""Application configuration loaded from environment variables.

This module reads `.env` (via python-dotenv if available) and exposes
typed configuration values with sensible defaults for non-sensitive keys.
"""

from __future__ import annotations

import os
from typing import Optional

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency
    load_dotenv = None

if load_dotenv:
    try:
        load_dotenv()
    except Exception:
        pass


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    v = str(value).strip().lower()
    return v in ("1", "true", "yes", "on")


def _parse_int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except Exception:
        return default


def _parse_float(value: Optional[str], default: float) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def _parse_log_level(value: Optional[str], default: str) -> str:
    if value is None:
        return default
    return str(value).strip().upper()


# Strings / secrets
TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN") or None
TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID") or None
# Backend (FastAPI): PostgreSQL URL (the password may also come from ~/.pgpass / PGPASSWORD) and the
# shared secret the engine sends as X-Internal-Token (unset = the internal endpoints are open, LAN only)
DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL") or None
INTERNAL_TOKEN: Optional[str] = os.getenv("INTERNAL_TOKEN") or None

# Engine manager (ROADMAP 3.5). ENGINE_COMMAND is split like a shell would; when the engine runs in the
# DeepStream container it is the whole `docker run ... ds_engine` prefix (mount the runtime directory,
# the snapshot directory and the config files at the same absolute paths inside it, and pass
# `-e DS_ENGINE_TOKEN` through).
ENGINE_COMMAND: str = os.getenv("ENGINE_COMMAND") or "engine/build/ds_engine"
ENGINE_CONFIG: str = os.getenv("ENGINE_CONFIG") or "engine/configs/pgie_yolov8n.txt"
ENGINE_TRACKER_CONFIG: str = os.getenv("ENGINE_TRACKER_CONFIG") or "engine/configs/tracker_nvdcf.yml"
ENGINE_RUNTIME_DIR: str = os.getenv("ENGINE_RUNTIME_DIR") or "data/run"
ENGINE_BACKEND_URL: str = os.getenv("ENGINE_BACKEND_URL") or "http://127.0.0.1:8000/internal/events"
# Where the engine POSTs its heartbeat; by default next to ENGINE_BACKEND_URL (empty = no heartbeat)
_EVENTS_PATH = "/internal/events"
ENGINE_HEARTBEAT_URL: str = os.getenv("ENGINE_HEARTBEAT_URL") or (
    ENGINE_BACKEND_URL[: -len(_EVENTS_PATH)] + "/internal/heartbeat" if ENGINE_BACKEND_URL.endswith(_EVENTS_PATH) else ""
)
# Live video (ROADMAP 4): the engine publishes every camera over RTMP to MediaMTX at
# ENGINE_STREAM_URL/cam<ID> (empty = no video; the URL must end with the path prefix, e.g.
# rtmp://127.0.0.1:1935/live), and browsers watch it with WebRTC (WHEP) at MEDIAMTX_WEBRTC_URL/<prefix>/cam<ID>.
ENGINE_STREAM_URL: str = os.getenv("ENGINE_STREAM_URL") or ""
MEDIAMTX_WEBRTC_URL: Optional[str] = os.getenv("MEDIAMTX_WEBRTC_URL") or None   # e.g. http://192.168.1.20:8889
MEDIAMTX_PATH_PREFIX: str = os.getenv("MEDIAMTX_PATH_PREFIX") or "live"
ENGINE_EXTRA_ARGS: str = os.getenv("ENGINE_EXTRA_ARGS") or ""
# Where the backend finds the manager; unset = the backend does not manage an engine
ENGINE_MANAGER_URL: Optional[str] = os.getenv("ENGINE_MANAGER_URL") or None

# Typed values with defaults
# Seconds after which a heartbeat of the engine no longer says anything about a camera (it sends one every 5)
# Seconds an event may wait for its Telegram alert; after that it is marked failed instead of sent late
NOTIFY_MAX_AGE: float = _parse_float(os.getenv("NOTIFY_MAX_AGE"), 600.0)
HEARTBEAT_STALE_AFTER: float = _parse_float(os.getenv("HEARTBEAT_STALE_AFTER"), 15.0)
ABSENCE_TIMEOUT: float = _parse_float(os.getenv("ABSENCE_TIMEOUT"), 10.0)

ENABLE_TELEGRAM: bool = _parse_bool(os.getenv("ENABLE_TELEGRAM"), False)
ENABLE_SNAPSHOT: bool = _parse_bool(os.getenv("ENABLE_SNAPSHOT"), True)

# Logging
LOG_LEVEL: str = _parse_log_level(os.getenv("LOG_LEVEL"), "INFO")
LOG_FILE: str = os.getenv("LOG_FILE") or "logs/app.log"
LOG_MAX_BYTES: int = _parse_int(os.getenv("LOG_MAX_BYTES"), 5 * 1024 * 1024)
LOG_BACKUP_COUNT: int = _parse_int(os.getenv("LOG_BACKUP_COUNT"), 3)

# Paths
SNAPSHOT_DIR: str = os.getenv("SNAPSHOT_DIR") or "data/snapshots"

# Snapshot cleanup
SNAPSHOT_RETENTION_DAYS: int = _parse_int(os.getenv("SNAPSHOT_RETENTION_DAYS"), 7)
SNAPSHOT_CLEANUP_INTERVAL: int = _parse_int(os.getenv("SNAPSHOT_CLEANUP_INTERVAL"), 100)

# Lightweight exports for convenience
__all__ = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "DATABASE_URL",
    "INTERNAL_TOKEN",
    "ENGINE_COMMAND",
    "ENGINE_CONFIG",
    "ENGINE_TRACKER_CONFIG",
    "ENGINE_RUNTIME_DIR",
    "ENGINE_BACKEND_URL",
    "ENGINE_HEARTBEAT_URL",
    "HEARTBEAT_STALE_AFTER",
    "NOTIFY_MAX_AGE",
    "ENGINE_EXTRA_ARGS",
    "ENGINE_STREAM_URL",
    "MEDIAMTX_WEBRTC_URL",
    "MEDIAMTX_PATH_PREFIX",
    "ENGINE_MANAGER_URL",
    "ABSENCE_TIMEOUT",
    "ENABLE_TELEGRAM",
    "ENABLE_SNAPSHOT",
    "LOG_FILE",
    "LOG_MAX_BYTES",
    "LOG_BACKUP_COUNT",
    "SNAPSHOT_DIR",
    "SNAPSHOT_RETENTION_DAYS",
    "SNAPSHOT_CLEANUP_INTERVAL",
]
