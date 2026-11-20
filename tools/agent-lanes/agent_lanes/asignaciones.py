"""Tarjetas asignadas a alguien que no ejecuta nada (decisión de Oscar 20-11, tarjeta t_006e919c).

`paradas.py` solo mira las tarjetas de carriles de lanes.yaml. Una tarjeta en ready, todo, blocked o triage asignada a
`oscar-e2` (perfil de consola, sin worker) o a un nombre inventado no la coge ningún runner y se queda parada sin aviso.

- `Asignaciones.encontrar`: solo lectura. Lista, por tablero, las tarjetas de esos estados cuyo asignado no es un carril
  de lanes.yaml (ni sus alias) ni un perfil conocido (`asignaciones.perfiles`: oscar, integrador, apoyo, codex) y que
  llevan más de `asignaciones.horas` (4) sin moverse. Propone el carril por repo; NO reasigna ni comenta nada.
- `AsignacionesAviso.publicar`: UN mensaje al día para la Integradora (no a Oscar), agrupado por asignado. Una tarjeta ya
  avisada no se repite hasta que cambie de estado o pasen 3 días. Lo engancha `paradas.ParadasTicker` como pasada extra.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ajustes_hub import valor
from .config import ROOT
from .notices import truncate
from .paradas import desde_cuando, duracion

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "asignaciones.json"
STATUSES = ("ready", "todo", "blocked", "triage")
LABELS = {"ready": "en cola", "todo": "pendiente", "blocked": "bloqueada", "triage": "en triage"}
CONSOLE_PROFILES = ("oscar-e2",)  # perfil de consola sin worker (= paradas.INTEGRATOR_PROFILE)
DEFAULT_HOURS = 4.0
DEFAULT_PROFILES = ("oscar", "integrador", "apoyo", "codex")
REPEAT_DAYS = 3
TITLE_MAX = 60


@dataclass(frozen=True)
class SinWorker:
    tid: str
    board: str
    assignee: str
    title: str
    status: str
    since: float
    suggestion: str

    def linea(self, now: float) -> str:
        return (f"{self.tid} · {truncate(self.title, TITLE_MAX)} · {LABELS.get(self.status, self.status)} · "
                f"hace {duracion(now - self.since)} · {self.suggestion}")


def _horas() -> float:
    try:
        h = float(valor("asignaciones.horas"))
        return h if h > 0 else DEFAULT_HOURS
    except (TypeError, ValueError):
        return DEFAULT_HOURS


def _perfiles() -> set[str]:
    try:
        raw = valor("asignaciones.perfiles")
        names = [str(x).strip().lower() for x in raw if str(x).strip()] if isinstance(raw, (list, tuple)) else []
    except Exception:
        names = []
    return set(names or DEFAULT_PROFILES)


def _repo_names(lane) -> set[str]:
    """Nombres con los que una tarjeta puede citar el repo del carril (carpeta, alias de `repos:`)."""
    names = set()
    for path in [getattr(lane, "repo", "")] + [(v or {}).get("repo", "") for v in (getattr(lane, "repos", None) or {}).values()]:
        base = str(path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower()
        if base:
            names.add(base)
    names.update(str(k).lower() for k in (getattr(lane, "repos", None) or {}))
    return names


def sugerir_carril(lanes: dict, board: str, text: str) -> str:
    """Carril de implementación que corresponde por el repo citado en la tarjeta; sin repo claro, los del tablero."""
    low = (text or "").lower()
    impl = {n: ln for n, ln in lanes.items() if getattr(ln, "kind", "implement") == "implement"}
    hits = [n for n, ln in impl.items() if any(re.search(r"(?<![\w-])" + re.escape(r) + r"(?![\w-])", low)
                                               for r in _repo_names(ln))]
    mine = [n for n in hits if getattr(impl[n], "board", "") == board] or hits
    if len(mine) == 1:
        return f"reasignar a {mine[0]}"
    cands = mine or [n for n, ln in impl.items() if getattr(ln, "board", "") == board]
    if cands:
        return "reasignar a " + " o ".join(sorted(cands)[:3]) + " (según su repo)"
    return "reasignar al carril que corresponda por su repo"


class Asignaciones:
    """Lee los tableros de los carriles. `hermes_for(board)` -> cliente; `lanes`: lanes.yaml (nombre -> Lane)."""

    def __init__(self, lanes: dict, hermes_for: Callable, clock: Callable[[], float] = time.time):
        self.lanes, self.hermes_for, self.clock = lanes, hermes_for, clock

    def _conocidos(self) -> set[str]:
        names = {str(n).lower() for n in self.lanes}
        for ln in self.lanes.values():
            names.update(str(a).lower() for a in getattr(ln, "aliases", ()) or ())
        return names | _perfiles()

    def encontrar(self, horas: float | None = None) -> list[SinWorker]:
        """Más antigua primero. Un tablero o tarjeta ilegible se salta con una línea de log."""
        horas = _horas() if horas is None else horas
        now, known, found, seen = self.clock(), self._conocidos(), [], set()
        for board in sorted({ln.board for ln in self.lanes.values() if getattr(ln, "board", "")}):
            try:
                h = self.hermes_for(board)
            except Exception as exc:
                log.warning("asignaciones: tablero %s no disponible: %s", board, exc)
                continue
            for status in STATUSES:
                try:
                    listed = h.list_status_all(status)
                except Exception as exc:
                    log.warning("asignaciones: no se pudo listar %s de %s: %s", status, board, exc)
                    continue
                for t in listed:
                    tid, who = t.get("id"), str(t.get("assignee") or "").strip()
                    if not tid or not who or who.lower() in known or (board, tid) in seen \
                            or t.get("status", status) != status:
                        continue
                    created = t.get("created_at")
                    if isinstance(created, (int, float)) and created and now - created < horas * 3600:
                        continue
                    try:
                        show = h.show(tid)
                    except Exception as exc:
                        log.info("asignaciones: %s no leída: %s", tid, exc)
                        show = None
                    task = (show or {}).get("task") or t
                    if task.get("status", status) != status:
                        continue
                    since = desde_cuando(t, show)
                    if not since or now - since < horas * 3600:
                        continue
                    seen.add((board, tid))
                    title = task.get("title") or t.get("title") or tid
                    text = f"{title}\n{task.get('body') or t.get('body') or ''}"
                    found.append(SinWorker(tid, board, who, title, status, since,
                                           sugerir_carril(self.lanes, board, text)))
        return sorted(found, key=lambda p: (p.since, p.tid))


class AsignacionesAviso:
    """`send(text, markup)` publica en el canal de la Integradora. Solo lectura sobre el kanban."""

    def __init__(self, lanes: dict, hermes_for: Callable, send: Callable[[str, dict | None], object],
                 state_path: Path | str = STATE_PATH, clock: Callable[[], float] = time.time):
        self.send, self.clock, self.state_path = send, clock, Path(state_path)
        self.asignaciones = Asignaciones(lanes, hermes_for, clock)

    def _cargar(self) -> dict:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _guardar(self, data: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def publicar(self) -> list[str]:
        """Un mensaje con las tarjetas aún no avisadas, agrupadas por asignado. Sin ninguna no manda nada."""
        found = self.asignaciones.encontrar()
        now, avisadas = self.clock(), self._cargar()
        nuevas = [p for p in found
                  if (a := avisadas.get(f"{p.board}/{p.tid}")) is None or not isinstance(a, dict)
                  or a.get("status") != p.status or now - float(a.get("ts") or 0) >= REPEAT_DAYS * 86400]
        if not nuevas:
            return []
        por: dict[str, list[SinWorker]] = {}
        for p in nuevas:
            por.setdefault(p.assignee, []).append(p)
        n, horas = len(nuevas), _horas()
        lines = [f"🧭 {n} tarjeta{'s' if n != 1 else ''} asignada{'s' if n != 1 else ''} a alguien que no ejecuta "
                 f"nada, más de {horas:g} h"]
        for who, items in por.items():
            nota = " (perfil de consola, sin worker)" if who.lower() in CONSOLE_PROFILES else " (no es un carril)"
            lines.append(f"\n{who}{nota}")
            lines += [f"• [{p.board}] " + p.linea(now) for p in items]
        lines.append("\n➡️ Próximo paso: reasignarlas al carril sugerido (aquí solo se propone) · Lo da: la Integradora")
        self.send(truncate("\n".join(lines), 3800), None)
        for p in nuevas:  # después de enviar: si falla el envío, mañana se vuelve a intentar
            avisadas[f"{p.board}/{p.tid}"] = {"status": p.status, "ts": now}
        self._guardar(avisadas)
        return [p.tid for p in nuevas]
