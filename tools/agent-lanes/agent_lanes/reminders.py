"""Recordatorios de decisiones pendientes: lunes a viernes a las 13:00 y a las 18:00 (hora de Madrid).

Corre en un hilo propio del runner-servicio: el bucle principal puede quedarse horas dentro de una pasada (un worker
dura hasta 2 h) y se comería la franja. SOLO si hay algo pendiente, un mensaje corto al DM de Oscar con el bot de
carriles que cuenta las dos cosas de /decisiones: las preguntas de los agentes (tareas de carril en needs_input) y sus
tarjetas de decisión (asignadas a oscar con [DECISIÓN…]/[SEMANA…]/[IDEA…]):
"Tienes N preguntas de agentes (la más antigua hace X h) y M tarjetas de decisión · /decisiones".

La franja enviada se guarda en .state/reminders.json: un reinicio no la repite. Si el runner arranca tarde, la
franja se recupera durante WINDOW_MINUTES.

Oscar tiene que haber abierto el DM con el bot (mandarle /start): si no, Telegram responde "chat not found" o 403,
se anota UNA vez en el log y la franja se da por hecha.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .notices import hours_ago

log = logging.getLogger("agent_lanes")

MADRID = ZoneInfo("Europe/Madrid")
SLOTS = (13, 18)
WINDOW_MINUTES = 60
DM_CLOSED_HINT = "Oscar debe mandar /start al bot de carriles para recibir los recordatorios en su DM"


def reminder_text(n: int, oldest_hours: int, cards: int = 0) -> str:
    """`n` preguntas de agentes (la más antigua hace `oldest_hours`) y `cards` tarjetas de decisión; un lado a 0
    no se nombra."""
    parts = []
    if n:
        parts.append(("1 pregunta" if n == 1 else f"{n} preguntas") + f" de agentes (la más antigua hace {oldest_hours} h)")
    if cards:
        parts.append(("1 tarjeta" if cards == 1 else f"{cards} tarjetas") + " de decisión")
    return f"Tienes {' y '.join(parts) or 'nada pendiente'} · /decisiones"


def due_slot(now: datetime, sent: set[str], window_minutes: int = WINDOW_MINUTES) -> str | None:
    """Clave "AAAA-MM-DD-HH" de la franja que toca enviar ahora (lunes a viernes), o None."""
    now = now.astimezone(MADRID)
    if now.weekday() >= 5:
        return None
    for hour in SLOTS:
        start = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        key = f"{start:%Y-%m-%d}-{hour:02d}"
        if start <= now < start + timedelta(minutes=window_minutes) and key not in sent:
            return key
    return None


def _dm_closed(exc: Exception) -> bool:
    text = f"{exc} {getattr(exc, 'description', '')}".lower()
    return "chat not found" in text or "403" in text or "forbidden" in text or "bot was blocked" in text


class Reminders:
    def __init__(self, notifier, owner_id: str, pending: Callable[[], list], state_path: Path | str, *,
                 now: Callable[[], datetime] = lambda: datetime.now(MADRID), interval: float = 60,
                 cards: Callable[[], list] | None = None):
        """`pending()` -> preguntas de agentes con `.since` (la lista de /decisiones); `cards()` -> tarjetas de
        decisión de Oscar (la segunda sección de /decisiones)."""
        self.notifier = notifier
        self.owner_id = str(owner_id)
        self.pending = pending
        self.cards = cards
        self.state_path = Path(state_path)
        self._now = now
        self.interval = interval
        self._stop = threading.Event()
        self._warned_dm = False

    def _sent(self) -> set[str]:
        try:
            return set(json.loads(self.state_path.read_text(encoding="utf-8")).get("sent") or ())
        except (OSError, ValueError, AttributeError):
            return set()

    def _mark(self, key: str) -> None:
        sent = sorted(self._sent() | {key})[-20:]
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"sent": sent}), encoding="utf-8")

    def tick(self) -> str | None:
        """Una comprobación. Devuelve el texto enviado (o None)."""
        now = self._now()
        key = due_slot(now, self._sent())
        if not key:
            return None
        pending = self.pending()
        cards = []
        if self.cards:
            try:
                cards = self.cards()
            except Exception as exc:  # sin tarjetas antes que sin recordatorio
                log.warning("recordatorio: tarjetas de decisión no leídas: %s", exc)
        if not pending and not cards:
            self._mark(key)  # nada que recordar: la franja queda hecha
            return None
        oldest = min((p.since for p in pending if getattr(p, "since", None)), default=None)
        text = reminder_text(len(pending), hours_ago(oldest, now.timestamp()), len(cards))
        try:
            self.notifier.send_to(self.owner_id, None, text, html=False)
        except Exception as exc:
            if _dm_closed(exc):
                if not self._warned_dm:
                    log.warning("recordatorio no entregado (%s): %s", exc, DM_CLOSED_HINT)
                    self._warned_dm = True
                self._mark(key)
                return None
            log.warning("recordatorio %s falló (se reintenta en la próxima comprobación): %s", key, exc)
            return None
        self._mark(key)
        log.info("recordatorio %s enviado: %d preguntas, %d tarjetas", key, len(pending), len(cards))
        return text

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # nunca tumba el runner
                log.warning("recordatorios: %s", exc)
            self._stop.wait(self.interval)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="lane-reminders", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
