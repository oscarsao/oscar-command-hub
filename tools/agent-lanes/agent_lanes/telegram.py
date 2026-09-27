"""Telegram Bot API notifier. The token is read from agent-lanes/.env and never logged or raised."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

# Contract with W3b (Hermes topics): a task created from a Telegram topic carries this line in its body,
# and every lane notice for that task goes back to that chat/thread. thread=0 means the group's General.
ORIGIN_RE = re.compile(r"^\s*origen-telegram:\s*chat=(-?\d+)\s+thread=(\d+)\s*$", re.I | re.M)


def telegram_target(body: str | None) -> tuple[str, str] | None:
    m = ORIGIN_RE.search(body or "")
    return (m.group(1), m.group(2)) if m else None


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None, thread_id: str | None = None, timeout: int = 15):
        self._token = token
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._token and self.chat_id)

    def __call__(self, text: str, target: tuple[str, str] | None = None) -> None:
        """Send to `target` (chat, thread) when the task has an Origen-Telegram line, else to the .env default."""
        if not self._token:
            return
        chat_id, thread_id = target if target else (self.chat_id, self.thread_id)
        if not chat_id:
            return
        payload = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if thread_id and int(thread_id) != 0:
            payload["message_thread_id"] = int(thread_id)
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
