"""Retenciones del runner (23-10): `.state/holds/<task_id>` = «esta tarjeta la está tomando una consola; no la reclames».

Choque t_c3450ab0: `hermes kanban unblock` deja la tarjeta en ready y el runner la reclamaba al instante, antes de que
/tomar-tarea hiciera su claim. `lanes.py take` pone la retención ANTES del unblock y la quita DESPUÉS del claim; el runner
(LaneRunner.candidates) ignora las tarjetas retenidas. Una retención caduca sola (HOLD_SECONDS) por si la consola muere.
"""
from __future__ import annotations

import time
from pathlib import Path

from .config import ROOT

HOLD_DIR = ROOT / ".state" / "holds"
HOLD_SECONDS = 600


def hold(tid: str, directory: Path | None = None) -> None:
    directory = directory or HOLD_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / tid).write_text(str(time.time()), encoding="utf-8")


def release(tid: str, directory: Path | None = None) -> None:
    directory = directory or HOLD_DIR
    (directory / tid).unlink(missing_ok=True)


def held(tid: str, directory: Path | None = None, now: float | None = None) -> bool:
    directory = directory or HOLD_DIR
    try:
        placed = float((directory / tid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if (now if now is not None else time.time()) - placed > HOLD_SECONDS:
        release(tid, directory)
        return False
    return True
