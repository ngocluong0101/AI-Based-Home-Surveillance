"""Logging configuration for AI-Based-Home-Surveillance."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

from src.config import LOG_LEVEL, LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT


def setup_logging(level: Optional[str] = None) -> None:
    """Configure root logging for the application.

    `level` overrides the config `LOG_LEVEL` if provided. Logs go to both the
    terminal and a rotating file (`LOG_FILE`, default `logs/app.log`) so
    history survives restarts when running headless/as a service.
    """
    level_name = (level or LOG_LEVEL or "INFO").upper()
    try:
        level_value = getattr(logging, level_name)
    except Exception:
        level_value = logging.INFO

    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    formatter = logging.Formatter(fmt)

    root = logging.getLogger()
    # Avoid adding multiple handlers in repeated calls
    if not root.handlers:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        root.addHandler(console_handler)

        try:
            log_path = Path(LOG_FILE)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:
            # Fall back to console-only logging if the log file/dir can't be created.
            root.warning("Could not set up file logging at %s; using console only", LOG_FILE)

    root.setLevel(level_value)
