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


def lane_rows(lanes: dict[str, Lane], *, hermes_for: Callable[[str], object], state_dir: Path,
              pid_alive: Callable[[int], bool]) -> list[dict]:
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
        else:
            state = "huérfano"  # running in the kanban but no live runner: reconcile will block it on start
        done = h.list_status(name, "done", sort="completed-desc")
        last = f"{done[0]['id']} {_when(done[0].get('completed_at'))} {done[0].get('title', '')[:40]}" if done else "-"
        rows.append({"lane": name, "board": lane.board, "state": state, "running": [t["id"] for t in running],
                     "ready": len(h.list_status(name, "ready")), "review": len(h.list_status(name, "review")),
                     "last": last})
    return rows


def format_rows(rows: list[dict], runner_line: str) -> str:
    head = f"{'CARRIL':18} {'BOARD':10} {'ESTADO':9} {'READY':>5} {'REVIEW':>6}  EN CURSO / ÚLTIMA TERMINADA"
    lines = [runner_line, head, "-" * len(head)]
    for r in rows:
        current = ",".join(r["running"]) if r["running"] else r["last"]
        lines.append(f"{r['lane']:18} {r['board']:10} {r['state']:9} {r['ready']:>5} {r['review']:>6}  {current}")
    return "\n".join(lines)
