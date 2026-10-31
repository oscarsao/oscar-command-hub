"""Arrancar tarjetas desde Telegram (31-10, decisión de Oscar). Piezas sin Telegram ni Hermes reales:

- `sin_carril_ni_marca`: regla de creación. Una tarjeta nueva para `oscar` cuyo título no lleva «tuyo», «decidir» ni
  «integrador» no se queda en la cola de Oscar: el log lo avisa y `create` la deja en `triage`.
- `grupo_pendiente`: en qué lista de /pendientes cae una tarjeta de `oscar` (las decide él, las hace el integrador o
  es de código y nadie la ha lanzado).
- `carriles_para` / `carril_recomendado`: qué carriles pueden coger una tarjeta de un tablero y cuál se recomienda.
- `arrancar`: reasigna al carril y la deja en `ready` para que el runner la reclame. La prevención de choques de zona
  (choques.yaml → prevenir_al_lanzar, `Prevencion.filtrar`) la aplica el runner al reclamar, igual que con cualquier
  tarjeta: aquí no se salta nada. Nunca hay un «arrancar todas»: MigraTeam solo entra si Oscar elige su carril.
"""
from __future__ import annotations

import logging
import re
import unicodedata

from . import titulos

log = logging.getLogger("agent_lanes.arrancar")

OSCAR = "oscar"
ESTADOS_ARRANCABLES = ("ready", "todo", "scheduled")
# Carril recomendado por tablero; el resto de carriles del tablero también se ofrecen, sin recomendar.
CARRIL_POR_TABLERO = {"default": "claude-hub", "oscarhq": "claude-oscarhq", "migrateam": "claude-migrateam"}

G_DECIDES, G_INTEGRADOR, G_CODIGO = "decides", "integrador", "codigo"
TITULOS_GRUPO = {G_DECIDES: "Las decides tú", G_INTEGRADOR: "Las hace el integrador",
                 G_CODIGO: "De código sin lanzar"}

_MARCA_RE = re.compile(r"\b(tuyo|decidir|integrador|integrar)\b")


def _fold(text: str) -> str:
    nfd = unicodedata.normalize("NFD", (text or "").casefold())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def _marca(title: str | None) -> str | None:
    found = _MARCA_RE.search(_fold(title))
    return found.group(1) if found else None


def sin_carril_ni_marca(title: str | None, assignee: str | None) -> bool:
    """True si la tarjeta es para `oscar` y el título no dice que es suya, de decidir o del integrador."""
    return (assignee or "").strip().lower() == OSCAR and _marca(title) is None


def aviso_creacion(title: str | None) -> None:
    log.warning("tarjeta para oscar sin carril ni marca «tuyo»/«decidir»/«integrador» en el título: se deja en triage "
                "(%s)", (title or "")[:80])


def grupo_pendiente(task: dict) -> str:
    """Lista de /pendientes de una tarjeta asignada a `oscar` (ready/todo/scheduled)."""
    title = task.get("title")
    marca = _marca(title)
    if marca in ("tuyo", "decidir") or titulos.step_of(task) in titulos.OSCAR_STEPS:
        return G_DECIDES
    if marca in ("integrador", "integrar") or titulos.step_of(task) == "integrar":
        return G_INTEGRADOR
    return G_CODIGO


def carriles_para(board: str, lanes: dict) -> list[str]:
    """Carriles de implementación que trabajan en `board`, el recomendado primero. Los de review/ops no arrancan nada."""
    todos = [n for n, l in lanes.items() if getattr(l, "board", "") == board and getattr(l, "kind", "implement") == "implement"]
    rec = CARRIL_POR_TABLERO.get(board)
    return ([rec] if rec in todos else []) + [n for n in todos if n != rec]


def carril_recomendado(board: str, lanes: dict) -> str | None:
    rec = CARRIL_POR_TABLERO.get(board)
    return rec if rec in carriles_para(board, lanes) else None


def resolver_carril(texto: str, board: str, lanes: dict) -> str | None:
    """«hub» o «claude-hub» -> nombre del carril válido para ese tablero (o None)."""
    t = (texto or "").strip().lower()
    validos = carriles_para(board, lanes)
    return next((c for c in (t, f"claude-{t}") if c in validos), None)


def arrancar(h, tid: str, lane: str, quien: str, author: str) -> tuple[bool, str]:
    """Reasigna `tid` a `lane` y la deja lista. (hecho, texto para Oscar). No toca título ni cuerpo."""
    try:
        task = (h.show(tid) or {}).get("task") or {}
    except Exception as exc:
        log.warning("arrancar %s: no se pudo leer: %s", tid, exc)
        return False, f"⚠️ No pude leer {tid}; detalle en el log. Puedes volver a pulsar"
    status = task.get("status")
    if status not in ESTADOS_ARRANCABLES:
        return True, (f"📝 {tid} está en «{status or '?'}»: Arrancar solo vale para tarjetas en cola, en espera o "
                      "programadas")
    if not h.reassign(tid, lane, f"Arrancada por {quien} desde Telegram"):
        return False, f"⚠️ No pude asignar {tid} a {lane}; detalle en el log. Puedes volver a pulsar"
    after = status
    if status == "scheduled":
        h.unblock(tid)  # programada -> en cola
    try:
        after = (h.show(tid).get("task") or {}).get("status") or status
    except Exception:
        pass
    h.comment(tid, f"Arrancada por {quien} desde Telegram en {lane}", author=author)
    if after == "ready":
        return True, f"🚀 {tid} arrancada en {lane}: el runner la coge en menos de 2 minutos"
    return True, (f"🚀 {tid} asignada a {lane}, pero sigue en «{after}» (espera a otra tarjeta o a su hora): "
                  "saldrá sola cuando esté lista")
