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


def resolve_target(origin: tuple[str, str] | None, lane_target: tuple[str, str] | None, *,
                   allowed_chats: set[str], generic_origins: set[tuple[str, str]]) -> tuple[str, str] | None:
    """Destino de un aviso. None = el default del .env.

    1. Origen-Telegram del cuerpo, si su chat está permitido y no es un origen genérico (DM de Oscar, Gestión t5).
    2. Destino del carril (lanes.yaml, config de confianza: no pasa por la allowlist).
    3. Default del .env.
    """
    if origin and origin[0] in allowed_chats and not (
            (origin[0], "*") in generic_origins or tuple(origin) in generic_origins):
        return tuple(origin)
    return tuple(lane_target) if lane_target else None


class TelegramAPIError(RuntimeError):
    """Telegram respondió con error; `description` es el texto de la API (nunca contiene el token)."""

    def __init__(self, message: str, description: str = ""):
        super().__init__(message)
        self.description = description


class TelegramNotifier:
    def __init__(self, token: str | None, chat_id: str | None, thread_id: str | None = None, timeout: int = 15,
                 allowed_chats: set[str] | None = None, generic_origins: set[tuple[str, str]] | None = None):
        self._token = token
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.timeout = timeout
        # Origen-Telegram comes from the task body: only chats listed here (plus the default) may be targeted,
        # so a task text cannot redirect review summaries to an arbitrary chat where the bot is present.
        self.allowed_chats = {str(c) for c in (allowed_chats or ())} | ({str(chat_id)} if chat_id else set())
        self.generic_origins = set(generic_origins or ())

    @property
    def enabled(self) -> bool:
        return bool(self._token and self.chat_id)

    def _api(self, method: str, payload: dict) -> dict:
        """Llama a la Bot API y devuelve `result`. Los errores nunca incluyen la URL (lleva el token)."""
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            # HTTPError's str() contains the URL (and therefore the token): report only code + description.
            try:
                desc = str(json.loads(exc.read() or b"{}").get("description", ""))[:200]
            except Exception:
                desc = ""
            raise TelegramAPIError(f"telegram HTTP {exc.code}" + (f": {desc}" if desc else ""), desc) from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"telegram no alcanzable ({type(exc.reason).__name__})") from None
        if not body.get("ok"):
            desc = str(body.get("description", "?"))[:200]
            raise TelegramAPIError(f"telegram rechazó el mensaje: {desc}", desc)
        return body.get("result") or {}

    def send(self, text: str, target: tuple[str, str] | None = None, lane_target: tuple[str, str] | None = None,
             *, silent: bool = False, html: bool = True) -> dict | None:
        """Envía y devuelve {chat_id, thread_id, message_id} (None si Telegram está desactivado).

        `target` = Origen-Telegram of the task, `lane_target` = lane destination; see resolve_target()."""
        if not self._token:
            return None
        target = resolve_target(target, lane_target, allowed_chats=self.allowed_chats,
                                generic_origins=self.generic_origins)
        chat_id, thread_id = target if target else (self.chat_id, self.thread_id)
        if not chat_id:
            return None
        payload = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if html:
            payload["parse_mode"] = "HTML"
        if silent:
            payload["disable_notification"] = True
        if thread_id and int(thread_id) != 0:
            payload["message_thread_id"] = int(thread_id)
        result = self._api("sendMessage", payload)
        return {"chat_id": str(chat_id), "thread_id": str(thread_id or "0"), "message_id": result.get("message_id")}

    def edit(self, chat_id: str, message_id, text: str, *, html: bool = True) -> bool:
        """True si el mensaje queda con ese texto. False si ya no se puede editar (borrado, >48 h...)."""
        if not self._token or not message_id:
            return False
        payload = {"chat_id": chat_id, "message_id": int(message_id), "text": text[:4000],
                   "disable_web_page_preview": True}
        if html:
            payload["parse_mode"] = "HTML"
        try:
            self._api("editMessageText", payload)
        except TelegramAPIError as exc:
            return "not modified" in exc.description.lower()  # mismo texto: no es un fallo (evita duplicados)
        return True

    def delete(self, chat_id: str, message_id) -> bool:
        if not self._token or not message_id:
            return False
        try:
            self._api("deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)})
        except TelegramAPIError:
            return False
        return True

    def __call__(self, text: str, target: tuple[str, str] | None = None,
                 lane_target: tuple[str, str] | None = None) -> None:
        """Compat: envío de texto plano."""
        self.send(text, target, lane_target, html=False)

    def __repr__(self) -> str:  # never expose the token
        return f"TelegramNotifier(chat_id={self.chat_id!r}, thread_id={self.thread_id!r}, token=***)"
