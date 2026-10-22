"""Backlog personal de Oscar fuera del kanban de carriles (t_d6ff8363): propuesta + mecanismo de traslado.

Hay decenas de tarjetas `ready` asignadas a «oscar» desde septiembre que no son decisiones de ningún carril: son su lista
personal y ensucian la vista de los carriles. Aquí vive el mecanismo para llevarlas a un board `personal`, SIN mover nada
por sí solo: `candidatas` propone, Oscar confirma con botones (/personal) y solo entonces `mover` actúa.

- `candidatas`: tarjetas a nombre de Oscar, en los estados de `personal.yaml`, que NO son decisión de carril (ni de
  decisión por etiqueta, ni paso «decidir»), que no están aplazadas ni descartadas con [✋ Dejarlas].
- `mover`: crea la tarjeta en el board destino y SOLO si eso salió bien archiva la original con un comentario que apunta a
  la nueva. Si el board destino no existe (se crea en la config de Hermes, que aquí no se toca), la original queda intacta.
- Descartadas: `.state/personal_descartadas.json` (ids que Oscar quiere dejar donde están; no vuelven a proponerse).

Todo se ajusta en `personal.yaml` (board destino, estados, antigüedad mínima, tope).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import titulos
from .config import ROOT
from .notices import OSCAR_ASSIGNEE, is_decision_card

log = logging.getLogger("agent_lanes")
SETTINGS_PATH = ROOT / "personal.yaml"
DESCARTADAS_FILE = "personal_descartadas.json"
MOVED_AUTHOR = "agent-lanes"


@dataclass(frozen=True)
class Ajustes:
    board: str = "personal"                     # board destino
    estados: tuple = ("ready", "todo")           # estados que se consideran backlog
    min_dias: float = 0                          # antigüedad mínima para proponerla
    max_por_tanda: int = 60                      # tarjetas por propuesta/traslado (el rec del botón debe caber en disco)
    boards_origen: tuple = ()                    # vacío = los boards que lea el CommandCenter


def load_ajustes(path: Path | str | None = None) -> Ajustes:
    d = Ajustes()
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return d
    if not isinstance(raw, dict):
        return d
    board = raw.get("board")
    estados = raw.get("estados")
    min_dias, tope = raw.get("min_dias"), raw.get("max_por_tanda")
    origen = raw.get("boards_origen")
    ok_num = lambda v, lo: isinstance(v, (int, float)) and not isinstance(v, bool) and v >= lo  # noqa: E731
    return Ajustes(
        board=board.strip() if isinstance(board, str) and board.strip().replace("-", "").replace("_", "").isalnum() else d.board,
        estados=tuple(s for s in estados if isinstance(s, str)) or d.estados if isinstance(estados, list) else d.estados,
        min_dias=float(min_dias) if ok_num(min_dias, 0) else d.min_dias,
        max_por_tanda=min(int(tope), 200) if ok_num(tope, 1) else d.max_por_tanda,
        boards_origen=tuple(s for s in origen if isinstance(s, str)) if isinstance(origen, list) else d.boards_origen)


def es_decision_de_carril(task: dict) -> bool:
    """Lo que SÍ espera a Oscar como parte del flujo de los carriles y no debe irse al backlog personal."""
    return is_decision_card(task) or titulos.step_of(task) == titulos.DECIDIR


def candidatas(cards: list[dict], aj: Ajustes, now: float, excluidas: set[str] = frozenset()) -> list[dict]:
    """[{board, task, since}] -> las que se proponen para `personal`, la más antigua primero (sin duplicados por id)."""
    out, vistos = [], set()
    for c in cards:
        t = c["task"]
        if (t.get("assignee") != OSCAR_ASSIGNEE or t.get("status") not in aj.estados or c.get("board") == aj.board
                or t["id"] in vistos or t["id"] in excluidas or es_decision_de_carril(t) or not titulos.is_live(t)):
            continue
        if aj.min_dias and c.get("since") and now - c["since"] < aj.min_dias * 86400:
            continue
        vistos.add(t["id"])
        out.append(c)
    return sorted(out, key=lambda c: (c.get("since") or 0, c["task"]["id"]))


def mover(card: dict, hermes_for, aj: Ajustes) -> tuple[bool, str]:
    """Traslada una tarjeta al board `personal`. (ok, nueva_id o motivo). Archiva la original solo si la nueva existe."""
    t, src = card["task"], card["board"]
    body = (t.get("body") or "")
    body = f"{body}\n\n— Movida desde el board «{src}» ({t['id']}) al backlog personal." if body else \
        f"Movida desde el board «{src}» ({t['id']}) al backlog personal."
    try:
        new = hermes_for(aj.board).create(t.get("title") or t["id"], body, OSCAR_ASSIGNEE, priority=t.get("priority"))
    except Exception as exc:
        log.warning("personal: no pude crear %s en %s: %s", t["id"], aj.board, exc)
        return False, f"no pude crearla en «{aj.board}» (¿existe ese board?)"
    new_id = new if isinstance(new, str) else ""
    try:
        src_h = hermes_for(src)
        src_h.comment(t["id"], f"Movida al backlog personal ({aj.board}" + (f"/{new_id}" if new_id else "") + ").",
                      author=MOVED_AUTHOR)
        if not src_h.archive(t["id"]):
            return False, f"creada en «{aj.board}» pero no pude archivar la original (revisa {t['id']})"
    except Exception as exc:
        log.warning("personal: %s creada en %s pero no archivada: %s", t["id"], aj.board, exc)
        return False, f"creada en «{aj.board}» pero no pude archivar la original (revisa {t['id']})"
    return True, new_id or ""


class Descartadas:
    """Ids que Oscar decidió dejar en su sitio: `.state/personal_descartadas.json` {id: epoch}."""

    def __init__(self, path: Path | str, now=time.time):
        self.path, self._now, self._lock = Path(path), now, threading.Lock()

    def ids(self) -> set[str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        return {str(k) for k in raw} if isinstance(raw, dict) else set()

    def add(self, ids) -> None:
        with self._lock:
            data = {k: self._now() for k in self.ids()}
            data.update({str(i): self._now() for i in ids})
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
