#!/usr/bin/env python3
"""Test script: save a dummy snapshot and attempt to send Telegram notification.

Run locally to reproduce snapshot/save/send issues and view logs.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import sys
from pathlib import Path

# Ensure repository root is on sys.path so `from src...` imports work when
# running this script directly (python scripts/test_telegram_snapshot.py).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.logging_config import setup_logging
from src.config import SNAPSHOT_DIR, ENABLE_TELEGRAM

setup_logging()
logger = logging.getLogger(__name__)


def make_dummy_image(path: Path) -> bool:
    try:
        import cv2
        import numpy as np
    except Exception as exc:
        logger.error("cv2 or numpy not available: %s", exc)
        return False

    img = np.zeros((480, 640, 3), dtype=np.uint8)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(img, f"Test {ts}", (10, 240), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        ok = cv2.imwrite(str(path), img)
        logger.info("cv2.imwrite returned: %s for %s", ok, path)
        return ok
    except Exception as exc:
        logger.exception("Failed to write test image: %s", exc)
        return False


class _Evt:
    def __init__(self):
        self.track_id = 999
        self.confidence = 0.99
        self.timestamp = datetime.now()


def main():
    event = _Evt()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"TEST_SNAPSHOT_{ts}.jpg"
    path = Path(SNAPSHOT_DIR) / filename

    saved = make_dummy_image(path)
    if not saved:
        logger.error("Snapshot was not saved; check cv2 and filesystem permissions.")

    try:
        from src.notifications.telegram_notifier import TelegramNotifier
    except Exception as exc:
        logger.exception("Failed to import TelegramNotifier: %s", exc)
        return

    notifier = TelegramNotifier()
    if not notifier.enabled:
        logger.warning("TelegramNotifier is not enabled. Set ENABLE_TELEGRAM=true and provide TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.")
        return

    # Try send photo if saved, else send text-only
    if saved:
        ok = notifier.send_person_detected_photo(event, str(path))
        logger.info("send_person_detected_photo returned: %s", ok)
    else:
        ok = notifier.send_person_detected(event)
        logger.info("send_person_detected (text-only) returned: %s", ok)


if __name__ == "__main__":
    main()
