"""Formato fijo de los títulos del kanban: «[Proyecto] Qué · paso» (decisión de Oscar, 21-oct).

El PASO final (lista cerrada) es la clave de clasificación: sin esquema nuevo, todo se deriva del título. Con él se
separan las vistas (épicas, retenidas, backlog del bot, pendientes de Oscar) del TRABAJO VIVO, que es lo único que
muestran la cola de Telegram (/decisiones, /atencion) y los recordatorios. Un título sin paso reconocible NO se oculta:
sale como «sin clasificar» con un aviso. Lógica pura, sin red.
"""
from __future__ import annotations

import logging
import re
import unicodedata

log = logging.getLogger("agent_lanes")

PROYECTOS = ("Hub", "OHQ", "MigraTeam", "Píldora", "Signal", "NextJobs", "Scraper", "Personal")
EPICA, RETENIDA, BACKLOG_BOT, TUYO, DECIDIR = "épica", "retenida", "backlog bot", "tuyo", "decidir"
PASOS = (EPICA, RETENIDA, BACKLOG_BOT, TUYO, DECIDIR, "spec", "implementar", "revisar", "integrar")
UNCLASSIFIED = "sin clasificar"
# Fuera de la cola de Telegram: son vistas aparte, no trabajo vivo.
NOT_LIVE = (EPICA, RETENIDA, BACKLOG_BOT)
OSCAR_STEPS = (TUYO, DECIDIR)  # «Pendientes de Oscar» (con assignee oscar)

_TITLE_RE = re.compile(r"^\s*\[(?P<proyecto>[^\]]*)\]\s*(?P<que>.*?)\s*·\s*(?P<paso>[^·]*?)\s*$", re.S)


def _fold(text: str) -> str:
    """Minúsculas y sin acentos: «Épica»/«epica» y «Píldora»/«pildora» valen igual."""
    nfd = unicodedata.normalize("NFD", " ".join((text or "").split()).casefold())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


_PASO_BY_FOLD = {_fold(p): p for p in PASOS}
_PROYECTO_BY_FOLD = {_fold(p): p for p in PROYECTOS}


def parse(title: str | None) -> dict:
    """{'proyecto','que','paso'}: cada campo es el valor canónico, o None si no se reconoce (paso fuera de la lista
    cerrada, proyecto desconocido o título sin el formato)."""
    m = _TITLE_RE.match(title or "")
    if not m:
        return {"proyecto": None, "que": None, "paso": None}
    return {"proyecto": _PROYECTO_BY_FOLD.get(_fold(m.group("proyecto"))), "que": m.group("que") or None,
            "paso": _PASO_BY_FOLD.get(_fold(m.group("paso")))}


def step_of(task: dict | None) -> str | None:
    """Paso canónico de una tarjeta, o None («sin clasificar»)."""
    return parse((task or {}).get("title"))["paso"]


def classify(task: dict | None) -> str:
    return step_of(task) or UNCLASSIFIED


def is_live(task: dict | None) -> bool:
    """Trabajo vivo: todo menos épicas, retenidas y backlog del bot. Lo «sin clasificar» cuenta como vivo (se ve y
    se avisa, nunca se oculta)."""
    return step_of(task) not in NOT_LIVE


def is_oscar_pending(task: dict | None) -> bool:
    """«Pendientes de Oscar»: assignee oscar con paso tuyo o decidir."""
    return (task or {}).get("assignee") == "oscar" and step_of(task) in OSCAR_STEPS


def problems(title: str | None) -> list[str]:
    """Qué incumple un título respecto al formato (vacío = bien formado)."""
    m = _TITLE_RE.match(title or "")
    if not m:
        return ["no sigue «[Proyecto] Qué · paso»"]
    out = []
    if _fold(m.group("proyecto")) not in _PROYECTO_BY_FOLD:
        out.append(f"proyecto «{m.group('proyecto').strip()}» fuera de la lista ({', '.join(PROYECTOS)})")
    if _fold(m.group("paso")) not in _PASO_BY_FOLD:
        out.append(f"paso «{m.group('paso').strip()}» fuera de la lista ({', '.join(PASOS)})")
    if not m.group("que"):
        out.append("falta el «Qué»")
    return out


def warn_title(title: str | None) -> list[str]:
    """Validador al crear una tarjeta: AVISA en el log, nunca bloquea. Devuelve los problemas."""
    found = problems(title)
    if found:
        log.warning("título de tarjeta mal formado (%s): %s", "; ".join(found), (title or "")[:80])
    return found
