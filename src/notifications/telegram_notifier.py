"""Simple Telegram notifier using Bot API and environment variables.

This module keeps failures non-fatal and logs errors to the terminal.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional
    load_dotenv = None

import requests


class TelegramNotifier:
    """Notifier that sends plain text messages via Telegram Bot API.

    It reads `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` from the environment.
    If they're not present the notifier is disabled but safe to instantiate.
    """

    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        if load_dotenv:
            try:
                load_dotenv()
            except Exception:
                pass

        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
        self.enabled = bool(self.token and self.chat_id)

        if not self.enabled:
            print("[NOTIFIER] Telegram not configured (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing)")

    def send_person_detected(self, event: Any) -> bool:
        """Send PERSON_DETECTED message for a given event.

        Returns True on success, False otherwise. Does not raise.
        """
        if not self.enabled:
            return False

        message = (
            "⚠️ PERSON DETECTED\n"
            f"Person ID: {event.track_id}\n"
            f"Confidence: {event.confidence:.2f}\n"
            f"Time: {event.timestamp.strftime('%H:%M:%S')}"
        )

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": message}

        try:
            resp = requests.post(url, data=payload, timeout=5)
            resp.raise_for_status()
        except Exception as exc:  # pragma: no cover - network
            print(f"[NOTIFICATION ERROR] Failed to send Telegram message: {exc}")
            return False

        print(f"[NOTIFICATION SENT] chat_id={self.chat_id} | event=PERSON_DETECTED id={event.track_id}")
        return True
