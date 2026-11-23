"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

Simple Telegram notifier using Bot API and environment variables.

This module keeps failures non-fatal and logs errors to the terminal.
"""

from __future__ import annotations

from typing import Any
import logging

import requests
from requests import RequestException
from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_TELEGRAM


class TelegramNotifier:
    """Sends text and photo messages via the Telegram Bot API; disabled (but safe to create) without a token."""

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

    def send_text(self, text: str) -> bool:
        """Send a plain message (the Settings page's "send a test message"). Returns True on success, never raises."""
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            resp = requests.post(url, data={"chat_id": self.chat_id, "text": text}, timeout=5)
            resp.raise_for_status()
        except RequestException as exc:
            self.logger.error("Failed to send Telegram message: %s", exc, exc_info=True)
            return False
        return True

    def _build_caption(self, event: Any) -> str:
        """Caption for person-detected events."""
        return (
            "⚠️ PERSON DETECTED\n"
            f"Person ID: {event.track_id}\n"
            f"Confidence: {event.confidence:.2f}\n"
            f"Time: {event.timestamp.strftime('%H:%M:%S')}"
        )

    def send_person_detected_photo(self, event: Any, photo_path: str) -> bool:
        """Send a photo with a caption for a PERSON_DETECTED event. Returns True on success; never raises."""
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

    def send_alert(self, caption: str, photo_path: str | None = None) -> bool:
        """Send an alert whose text the caller wrote, with a photo when there is one. Returns True on success; never raises."""
        if not self.enabled:
            return False
        try:
            if photo_path is not None:
                url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
                with open(photo_path, "rb") as fh:
                    resp = requests.post(url, data={"chat_id": self.chat_id, "caption": caption}, files={"photo": fh}, timeout=10)
            else:
                url = f"https://api.telegram.org/bot{self.token}/sendMessage"
                resp = requests.post(url, data={"chat_id": self.chat_id, "text": caption}, timeout=10)
            resp.raise_for_status()
        except (RequestException, OSError) as exc:
            self.logger.error("Failed to send Telegram alert: %s", exc, exc_info=True)
            return False
        self.logger.info("Alert sent (chat_id=%s)", self.chat_id)
        return True
