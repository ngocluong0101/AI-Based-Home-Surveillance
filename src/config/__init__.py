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
RTSP_URL: Optional[str] = os.getenv("RTSP_URL") or None
TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN") or None
TELEGRAM_CHAT_ID: Optional[str] = os.getenv("TELEGRAM_CHAT_ID") or None

# Typed values with defaults
CONFIDENCE_THRESHOLD: float = _parse_float(os.getenv("CONFIDENCE_THRESHOLD"), 0.5)
RECONNECT_DELAY: float = _parse_float(os.getenv("RECONNECT_DELAY"), 5.0)
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

# Lightweight exports for convenience
__all__ = [
    "RTSP_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "CONFIDENCE_THRESHOLD",
    "RECONNECT_DELAY",
    "ABSENCE_TIMEOUT",
    "ENABLE_TELEGRAM",
    "ENABLE_SNAPSHOT",
    "LOG_FILE",
    "LOG_MAX_BYTES",
    "LOG_BACKUP_COUNT",
]
