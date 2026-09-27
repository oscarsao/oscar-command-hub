"""Telegram Bot API notifier. The token is read from agent-lanes/.env and never logged or raised."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None, thread_id: str | None = None, timeout: int = 15):
        self._token = token
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._token and self.chat_id)

    def __call__(self, text: str) -> None:
        if not self.enabled:
            return
        payload = {"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if self.thread_id:
            payload["message_thread_id"] = int(self.thread_id)
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/sendMessage",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read() or b"{}")
            if not body.get("ok"):
                raise RuntimeError(f"telegram rechazó el mensaje: {body.get('description', '?')}")
        except urllib.error.HTTPError as exc:
            # HTTPError's str() contains the URL (and therefore the token): report only the code.
            raise RuntimeError(f"telegram HTTP {exc.code}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"telegram no alcanzable ({type(exc.reason).__name__})") from None

    def __repr__(self) -> str:  # never expose the token
        return f"TelegramNotifier(chat_id={self.chat_id!r}, thread_id={self.thread_id!r}, token=***)"
