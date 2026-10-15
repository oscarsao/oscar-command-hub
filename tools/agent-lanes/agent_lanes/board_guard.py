"""Guarda de tablero (ADR-036): tarjetas abiertas cuyo assignee de carril NO lee el tablero donde están.

`hermes kanban create` sin --board cae en `default`; los carriles leen el tablero de su lanes.yaml (`board:`), así que
una tarjeta mal puesta no corre nunca y nada avisa. Esta guarda las detecta y, con `apply`, las recoloca:

- Se mueven SOLO las que no han empezado (ready/todo), sin padres ni hijas y sin claim vivo. Las demás (running,
  blocked, triage, review, con dependencias o con claim) se avisan y no se tocan.
- Mover = recrear en el tablero correcto (mismo título, cuerpo, assignee, prioridad; idempotency-key = id original),
  copiar los comentarios, enlazar la original y archivarla con "movida a <tablero>:<id>". Si algo falla a medias, la
  siguiente pasada lo completa sin duplicar (la key devuelve la misma tarjeta; los comentarios llevan marca).
- Una tarjeta de `oscar` (o de cualquier assignee que no sea un carril con tablero) nunca se mueve.

Manual: `py -3.12 lanes.py doctor-boards [--apply]` (dry-run por defecto). En el runner: cada BOARD_GUARD_MINUTES
(10 por defecto), con aplicar solo si BOARD_GUARD_APPLY=true en agent-lanes/.env; si no, avisa una vez por tarjeta.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("agent_lanes")

# Estados abiertos que se revisan (done/archived quedan fuera). MOVABLE = los que aún no han corrido.
OPEN_STATUSES = ("ready", "todo", "blocked", "triage", "review", "running")
MOVABLE = ("ready", "todo")
EXTRA_BOARDS = ("default",)
MARK = "Movida desde "  # marca del comentario de enlace en la tarjeta nueva (idempotencia de los comentarios)
CLAIM_KEYS = ("claim_lock", "claimed_by", "claim_expires", "claimed_at")


@dataclass
class Misplaced:
    tid: str
    board: str       # donde está
    lane: str        # assignee (carril)
    target: str      # tablero que lee ese carril
    status: str
    title: str


@dataclass
class Outcome:
    item: Misplaced
    action: str      # moved | would_move | warn
    new_id: str | None = None
    reason: str = ""


def lane_boards(lanes: dict) -> dict[str, str]:
    """carril -> tablero que lee (solo carriles que reclaman tarjetas; review no tiene tablero)."""
    return {n: l.board for n, l in lanes.items() if getattr(l, "board", "") and l.kind in ("implement", "ops")}


def scan(lanes: dict, hermes_for) -> list[Misplaced]:
    """Tarjetas abiertas de un carril en un tablero que ese carril no lee. Un tablero ilegible no corta el resto."""
    mapping = lane_boards(lanes)
    boards = list(dict.fromkeys([*mapping.values(), *EXTRA_BOARDS]))
    found: list[Misplaced] = []
    for board in boards:
        for status in OPEN_STATUSES:
            try:
                tasks = hermes_for(board).list_status_all(status)
            except Exception as exc:
                log.warning("guarda de tablero: %s/%s no leído: %s", board, status, exc)
                continue
            for t in tasks:
                lane = t.get("assignee")
                target = mapping.get(lane)
                if target and target != board and t.get("id"):
                    found.append(Misplaced(t["id"], board, lane, target, t.get("status") or status,
                                           t.get("title") or ""))
    return sorted(found, key=lambda m: m.tid)


def _blocker(m: Misplaced, show: dict) -> str | None:
    """Motivo para NO mover (None = se puede mover)."""
    task = show.get("task") or {}
    status = task.get("status") or m.status
    if status not in MOVABLE:
        return f"estado «{status}»: no se toca (solo ready/todo)"
    if any(task.get(k) for k in CLAIM_KEYS):
        return "tiene claim: la está trabajando alguien"
    if show.get("parents") or show.get("children"):
        return "tiene tarjetas padre/hijas (ids de otro tablero): recolocar a mano"
    if task.get("assignee") != m.lane:
        return "el assignee cambió mientras tanto"
    return None


def move(m: Misplaced, show: dict, hermes_for) -> str:
    """Recrea la tarjeta en su tablero y archiva la original. Devuelve el id nuevo."""
    task = show.get("task") or {}
    dest, src = hermes_for(m.target), hermes_for(m.board)
    new_id = dest.create(task.get("title") or m.title, task.get("body") or "", m.lane,
                         priority=task.get("priority"), key=m.tid, created_by=task.get("created_by") or None)
    link = f"{MARK}{m.board}:{m.tid} (guarda de tablero: {m.lane} lee {m.target})"
    already = any(MARK in (c.get("body") or "") for c in (dest.show(new_id).get("comments") or []))
    if not already:  # primera vez: enlace + comentarios de la original (con su autor)
        dest.comment(new_id, link)
        for c in show.get("comments") or []:
            if c.get("body"):
                dest.comment(new_id, f"[copiado de {m.board}:{m.tid}] {c['body']}", author=c.get("author") or None)
    src.comment(m.tid, f"movida a {m.target}:{new_id}")
    if not src.archive(m.tid):
        raise RuntimeError(f"no se pudo archivar {m.board}:{m.tid} (la nueva {m.target}:{new_id} ya existe)")
    return new_id


def run_guard(lanes: dict, hermes_for, *, apply: bool = False) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for m in scan(lanes, hermes_for):
        try:
            show = hermes_for(m.board).show(m.tid)
            why = _blocker(m, show)
            if why:
                outcomes.append(Outcome(m, "warn", reason=why))
            elif not apply:
                outcomes.append(Outcome(m, "would_move"))
            else:
                outcomes.append(Outcome(m, "moved", new_id=move(m, show, hermes_for)))
        except Exception as exc:
            log.warning("guarda de tablero: %s:%s falló: %s", m.board, m.tid, exc)
            outcomes.append(Outcome(m, "warn", reason=f"error: {str(exc)[:150]}"))
    return outcomes


def format_outcomes(outcomes: list[Outcome]) -> str:
    lines = []
    for o in outcomes:
        m = o.item
        head = f"{m.board}:{m.tid} «{m.title[:60]}» ({m.lane}, {m.status})"
        if o.action == "moved":
            lines.append(f"✅ {head} → movida a {m.target}:{o.new_id}")
        elif o.action == "would_move":
            lines.append(f"🔎 {head} → se movería a {m.target}")
        else:
            lines.append(f"⚠️ {head} → en {m.board} pero {m.lane} lee {m.target}; no se mueve: {o.reason}")
    return "\n".join(lines)


def notice_text(outcomes: list[Outcome], apply: bool) -> str:
    """Aviso del DM de Oscar: qué se movió y por qué (o qué hay que mover si la guarda va en dry-run)."""
    head = ("🧭 Guarda de tablero: tarjetas en un tablero que su carril no lee (no correrían nunca)."
            + ("" if apply else " Detectadas, sin mover (dry-run); aplicar: lanes.py doctor-boards --apply."))
    return head + "\n" + format_outcomes(outcomes)


class BoardGuardTicker:
    """Pasada periódica dentro del bucle del runner. Avisa una vez por tarjeta y resultado (estado en `state_path`);
    nunca lanza: un fallo de Hermes o de Telegram solo se registra."""

    def __init__(self, lanes: dict, hermes_for, *, apply: bool, send, state_path: Path, minutes: float = 10,
                 clock=time.monotonic):
        self.lanes, self.hermes_for, self.apply, self.send = lanes, hermes_for, apply, send
        self.state_path, self.interval, self._clock = state_path, minutes * 60, clock
        self._last: float | None = None

    def _seen(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def tick(self) -> list[Outcome]:
        now = self._clock()
        if self._last is not None and now - self._last < self.interval:
            return []
        self._last = now
        try:
            outcomes = run_guard(self.lanes, self.hermes_for, apply=self.apply)
            seen = self._seen()
            fresh = [o for o in outcomes if seen.get(o.item.tid) != o.action]
            if fresh:
                self.send(notice_text(fresh, self.apply))
                seen.update({o.item.tid: o.action for o in fresh})
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.state_path.write_text(json.dumps(seen), encoding="utf-8")
            return outcomes
        except Exception as exc:
            log.warning("guarda de tablero falló: %s", exc)
            return []


def main(argv: list[str]) -> int:
    import argparse
    from .config import load_lanes
    from .hermes import HermesCLI
    ap = argparse.ArgumentParser(prog="lanes.py doctor-boards",
                                 description="tarjetas abiertas en un tablero que su carril no lee")
    ap.add_argument("--apply", action="store_true", help="recolocarlas (sin esto, solo informa)")
    args = ap.parse_args(argv)
    cache: dict[str, HermesCLI] = {}
    outcomes = run_guard(load_lanes(), lambda b: cache.setdefault(b, HermesCLI(b, author="doctor-boards")),
                         apply=args.apply)
    print(format_outcomes(outcomes) if outcomes else "Todas las tarjetas abiertas están en el tablero de su carril.")
    return 0
