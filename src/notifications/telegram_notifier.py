"""Simple Telegram notifier using Bot API and environment variables.

This module keeps failures non-fatal and logs errors to the terminal.
"""

from __future__ import annotations

from typing import Any
import logging

import requests
from requests import RequestException
from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM


class TelegramNotifier:
    """Notifier that sends plain text messages via Telegram Bot API.

    It reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from the environment.
    If they're not present the notifier is disabled but safe to instantiate.
    """

    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        # Prefer explicit constructor values, then config module values.
        self.token = token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        # Allow a global enable flag in config to turn off notifications.
        self.enabled = bool(self.token and self.chat_id and ENABLE_TELEGRAM)

        self.logger = logging.getLogger(__name__)
        if not self.enabled:
            self.logger.info("Telegram disabled or not configured (set ENABLE_TELEGRAM, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)")

    def send_person_detected(self, event: Any) -> bool:
        """Send PERSON_DETECTED message for a given event.

        Returns True on success, False otherwise. Does not raise.
        """
        if not self.enabled:
            return False

        caption = self._build_caption(event)
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": caption}

        try:
            resp = requests.post(url, data=payload, timeout=5)
            resp.raise_for_status()
        except RequestException as exc:  # network/HTTP errors
            self.logger.error("Failed to send Telegram message: %s", exc, exc_info=True)
            return False

        self.logger.info("Notification sent (chat_id=%s) event=PERSON_DETECTED id=%s", self.chat_id, event.track_id)
        return True

    def _build_caption(self, event: Any) -> str:
        """Build a caption string for person-detected events.

        Centralizing caption formatting avoids duplication between
        text-only and photo notifications.
        """
        return (
            "⚠️ PERSON DETECTED\n"
            f"Person ID: {event.track_id}\n"
            f"Confidence: {event.confidence:.2f}\n"
            f"Time: {event.timestamp.strftime('%H:%M:%S')}"
        )

    def send_person_detected_photo(self, event: Any, photo_path: str) -> bool:
        """Send a photo with a caption for a PERSON_DETECTED event.

        Uses the `sendPhoto` endpoint. Returns True on success, False otherwise.
        Exceptions are caught and logged; callers should not expect raises.
        """
        if not self.enabled:
            return False

        caption = self._build_caption(event)
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"

        try:
            with open(photo_path, "rb") as fh:
                files = {"photo": fh}
                data = {"chat_id": self.chat_id, "caption": caption}
                resp = requests.post(url, data=data, files=files, timeout=10)
                resp.raise_for_status()
        except (RequestException, OSError) as exc:  # network/IO errors
            self.logger.error("Failed to send Telegram photo: %s", exc, exc_info=True)
            return False

        self.logger.info(
            "Photo notification sent (chat_id=%s) event=PERSON_DETECTED id=%s",
            self.chat_id,
            event.track_id,
        )
        return True
