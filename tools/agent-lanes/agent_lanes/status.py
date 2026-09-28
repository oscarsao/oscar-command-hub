"""`lanes status`: per lane free/busy (running + live runner PID), ready/review counts, last finished task."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import Lane


def _state_pid(state_dir: Path, tid: str) -> int | None:
    try:
        return int(json.loads((state_dir / f"{tid}.json").read_text(encoding="utf-8")).get("pid"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _when(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d-%m %H:%M")
    except (TypeError, ValueError, OSError):
        return "?"


def runner_line(lock: Path, pid_alive: Callable[[int], bool], drain: dict | None = None) -> str:
    """Estado del runner-servicio desde .state/runner.lock (+ drenaje en curso, si lo hay)."""
    try:
        pid = int(json.loads(Path(lock).read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return "runner: no arrancado (sin .state/runner.lock)"
    line = f"runner: {'VIVO' if pid_alive(pid) else 'PARADO'} (pid {pid})"
    if drain:
        line += f" · drenando desde {_when(drain.get('since'))} (no reclama)"
    return line


def runner_alive(lock: Path, pid_alive: Callable[[int], bool]) -> bool:
    try:
        return pid_alive(int(json.loads(Path(lock).read_text(encoding="utf-8"))["pid"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False


def lane_rows(lanes: dict[str, Lane], *, hermes_for: Callable[[str], object], state_dir: Path,
              pid_alive: Callable[[int], bool], runner_is_alive: bool | None = None) -> list[dict]:
    """`runner_is_alive`: una tarea en running sin PID en .state con el runner VIVO está "arrancando" (claim hecho,
    estado aún sin escribir) o es una toma interactiva; solo con el runner parado es "huérfano" de verdad (t_bdee05fe).
    None = no se sabe (compatibilidad): se trata como parado."""
    rows = []
    for name, lane in lanes.items():
        if lane.kind not in ("implement", "ops"):
            continue
        h = hermes_for(lane.board)
        running = h.list_status(name, "running")
        live = [t["id"] for t in running if (pid := _state_pid(Path(state_dir), t["id"])) and pid_alive(pid)]
        if not running:
            state = "libre"
        elif live:
            state = "ocupado"
        elif runner_is_alive:
            state = "arrancando"  # claim recién hecho (o consola interactiva): el runner vivo no lo da por perdido
        else:
            state = "huérfano"  # running in the kanban but no live runner: reconcile will block it on start
        done = h.list_status(name, "done", sort="completed-desc")
        last = f"{done[0]['id']} {_when(done[0].get('completed_at'))} {done[0].get('title', '')[:40]}" if done else "-"
        rows.append({"lane": name, "board": lane.board, "state": state, "running": [t["id"] for t in running],
                     "ready": len(h.list_status(name, "ready")), "review": len(h.list_status(name, "review")),
                     "last": last})
    return rows


def format_rows(rows: list[dict], runner_line: str) -> str:
    head = f"{'CARRIL':18} {'BOARD':10} {'ESTADO':10} {'READY':>5} {'REVIEW':>6}  EN CURSO / ÚLTIMA TERMINADA"
    lines = [runner_line, head, "-" * len(head)]
    for r in rows:
        current = ",".join(r["running"]) if r["running"] else r["last"]
        lines.append(f"{r['lane']:18} {r['board']:10} {r['state']:10} {r['ready']:>5} {r['review']:>6}  {current}")
    return "\n".join(lines)
