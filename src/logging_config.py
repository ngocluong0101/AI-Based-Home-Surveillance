"""Logging configuration for AI-Based-Home-Surveillance."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

from src.config import LOG_LEVEL, LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT


def setup_logging(level: Optional[str] = None, log_file: Optional[str] = None) -> None:
    """Configure root logging for the application.

    `level` overrides the config `LOG_LEVEL` if provided, `log_file` the config `LOG_FILE` (two programs
    must not rotate the same file, so each of them names its own). Logs go to both the
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
            log_path = Path(log_file or LOG_FILE)
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
            root.warning("Could not set up file logging at %s; using console only", log_file or LOG_FILE)

    root.setLevel(level_value)
    # One line per HTTP request of the clients (the backend asks the engine manager every few seconds).
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(level_value, logging.WARNING))
