"""Logging configuration for AI-Based-Home-Surveillance."""

from __future__ import annotations

import logging
import os
from typing import Optional

from src.config import LOG_LEVEL


def setup_logging(level: Optional[str] = None) -> None:
    """Configure root logging for the application.

    `level` overrides the config `LOG_LEVEL` if provided.
    """
    level_name = (level or LOG_LEVEL or "INFO").upper()
    try:
        level_value = getattr(logging, level_name)
    except Exception:
        level_value = logging.INFO

    handler = logging.StreamHandler()
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handler.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    # Avoid adding multiple handlers in repeated calls
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(level_value)
