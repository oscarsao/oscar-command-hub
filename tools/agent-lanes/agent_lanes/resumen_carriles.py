"""Resumen diario de las 9:00 por carril (t_f8f58acb): una línea por carril (qué hace, cola, bloqueos) y debajo las
decisiones pendientes de Oscar con @mención y botón. Complementa al resumen de las 8:00 por marca (brief/brief_diario).

Se envía al tema Atención (con @mención) y, sin él, al DM de Oscar. Lo dispara `CommandCenter.veto_tick`; la fecha
enviada se guarda en un JSON (un reinicio no lo repite) y, si el runner arranca tarde, se recupera hasta las 11:00.
Solo lee el kanban: no cambia nada.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .ajustes_hub import valor
from .aviso import mention
from .formato_aviso import PIE_TEXTO_LIBRE
from .notices import hours_ago, truncate

log = logging.getLogger("agent_lanes")

MADRID = ZoneInfo("Europe/Madrid")
# Valores por defecto; los vigentes se editan con /ajustes (ajustes_hub: avisos.*).
SLOT_HOUR = 9
WINDOW_HOURS = 2     # si el runner estaba parado a las 9:00, se envía hasta las 11:00
DECISIONS_SHOWN = 5


def _hour(hour: int | None) -> int:
    v = valor("avisos.hora_parte") if hour is None else hour
    return SLOT_HOUR if v is None else v


def lane_row(name: str, h, kind: str = "implement") -> dict:
    """Estado de un carril leyendo su tablero: lo que hace, cola (ready) y bloqueos (blocked + triage)."""
    def titles(status):
        return [t.get("title") or t.get("id") for t in h.list_status(name, status)]
    running, ready = titles("running"), titles("ready")
    return {"name": name, "kind": kind, "doing": running, "queue": len(ready),
            "blocked": len(titles("blocked")) + len(titles("triage"))}


def collect_rows(lanes: dict, hermes_for: Callable) -> list[dict]:
    rows = []
    for name, lane in lanes.items():
        try:
            rows.append(lane_row(name, hermes_for(lane.board), getattr(lane, "kind", "implement")))
        except Exception as exc:  # un carril ilegible no tumba el resumen
            log.warning("resumen 9:00: carril %s no leído: %s", name, exc)
            rows.append({"name": name, "error": True})
    return rows


def render_lane(row: dict) -> str:
    if row.get("error"):
        return f"⚪ {row['name']}: sin datos (no pude leer su tablero)"
    doing = row["doing"]
    what = truncate(" + ".join(truncate(t, 40) for t in doing[:2]), 90) + (f" (+{len(doing) - 2})" if len(doing) > 2 else "") \
        if doing else "libre"
    icon = "🔵" if doing else ("🟡" if row["blocked"] else "🟢")
    return f"{icon} {row['name']}: {what} · cola {row['queue']} · bloqueos {row['blocked']}"


def render_summary(rows: list[dict], decisions: list, guard_n: int, now: float, who: str = "", extra: str = "",
                   hour: int | None = None) -> str:
    """El mensaje: una línea por carril (también los vacíos) y las decisiones de Oscar con su próximo paso.
    `extra`: líneas del integrador (estado de sus consolas y PR de sesiones interactivas), ya compuestas."""
    hour, shown = _hour(hour), valor("avisos.decisiones_en_resumen")
    lines = [f"☀️ Resumen de las {hour}:00 por carril"]
    lines += [render_lane(r) for r in rows] or ["(no hay carriles configurados)"]
    if guard_n:
        lines.append(f"🧑‍💻 {guard_n} en la cola del integrador (/integrador)")
    if extra:
        lines.append(extra)
    lines.append("")
    if not decisions:
        lines.append("Decisiones pendientes de Oscar: ninguna. ➡️ Próximo paso: ninguno · Lo da: nadie")
        return "\n".join(lines)
    lines.append(f"{who + ' ' if who else ''}Decisiones pendientes de Oscar ({len(decisions)}):")
    for p in decisions[:shown]:
        lines.append(f"• {p.tid} · {truncate((p.task or {}).get('title'), 60)} · hace {hours_ago(p.since, now)} h")
    if len(decisions) > shown:
        lines.append(f"… y {len(decisions) - shown} más")
    lines.append("➡️ Próximo paso: pulsa «Ver decisiones» y responde con los botones · Lo da: Oscar")
    lines.append(PIE_TEXTO_LIBRE)  # formato único de aviso (t_211c9902): botones + texto libre
    return "\n".join(lines)


def due_date(now: datetime, sent: set[str], hour: int | None = None) -> str | None:
    """Clave AAAA-MM-DD si toca enviar (desde `hour`:00 hasta el margen configurado, hora de Madrid, todos los días) y no
    está enviada."""
    now, hour = now.astimezone(MADRID), _hour(hour)
    key = f"{now:%Y-%m-%d}"
    return key if hour <= now.hour < hour + valor("avisos.resumen_ventana_horas") and key not in sent else None


class DailyLaneSummary:
    """`rows()` -> filas por carril; `decisions()` -> Pending de Oscar; `guard_n()` -> tamaño de la cola del
    integrador; `send(text, with_button)` -> True si salió. Cualquier fallo se reintenta en la siguiente pasada."""

    def __init__(self, rows: Callable[[], list], decisions: Callable[[], list], guard_n: Callable[[], int],
                 send: Callable[[str, bool], bool], state_path: Path | str, *, who: str = "",
                 now: Callable[[], datetime] = lambda: datetime.now(MADRID), hour: int | None = None,
                 extra: Callable[[], str] = lambda: ""):
        self.rows, self.decisions, self.guard_n, self.send = rows, decisions, guard_n, send
        self.state_path, self.who, self._now = Path(state_path), who, now
        self.hour, self.extra = hour, extra

    def _sent(self) -> set[str]:
        try:
            return set(json.loads(self.state_path.read_text(encoding="utf-8")).get("sent") or ())
        except (OSError, ValueError, AttributeError):
            return set()

    def tick(self) -> str | None:
        """Una comprobación. Devuelve el texto enviado (o None)."""
        now = self._now()
        key = due_date(now, self._sent(), self.hour)
        if not key:
            return None
        decisions = self.decisions()
        text = render_summary(self.rows(), decisions, self.guard_n(), now.timestamp(), self.who if decisions else "",
                              self.extra(), self.hour)
        if not self.send(text, bool(decisions)):
            return None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"sent": sorted(self._sent() | {key})[-14:]}), encoding="utf-8")
        return text
