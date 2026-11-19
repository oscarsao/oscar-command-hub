"""Varios ordenadores sobre el mismo kanban (auditoría del segundo worker, 19-11): lo que corre en el runner de CADA equipo.

- `LeaseReaper` (H6): ticker que llama a `release_stale` para liberar las reservas caducadas. Con el kanban `hermes` no
  hace falta (su dispatcher ya lo hacía) y va apagado; con `supabase` NADIE más lo hace. Varios runners a la vez es seguro
  (`FOR UPDATE SKIP LOCKED`): cada tarjeta se libera una sola vez.
- `worker_overrides` (H10): ajustes POR EQUIPO dentro del `lanes.yaml` versionado, para no editarlo a mano en cada PC:

      runner:
        max_workers: 6                 # el del equipo principal
        workers:                       # por id de worker (HUB_WORKER_ID)
          pc-b:    {max_workers: 2, lanes: [claude-hub, claude-scraper]}
          pc-ange: {max_workers: 1}

  `lanes` limita qué carriles corre ese equipo con `--all` (sin la clave, todos). `max_workers` solo manda si no se pasa
  `--max-workers` por línea de comandos.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Callable

log = logging.getLogger("agent_lanes")

DEFAULT_MAX_ATTEMPTS = 3  # a la 3ª reserva caducada de la misma tarjeta pasa a triage (como hub.release_stale)
DEFAULT_SECONDS = 60.0
ENV_MAX_ATTEMPTS = "RELEASE_STALE_MAX_ATTEMPTS"
ENV_SECONDS = "RELEASE_STALE_SECONDS"


def _num(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def reaper_settings(backend: str, settings: dict, env: dict | None = None) -> dict:
    """{enabled, max_attempts, seconds}. Apagado con el backend hermes y con `release_stale_max_attempts: 0`; encendido
    por defecto con supabase. Orden: entorno/.env > `runner.release_stale_*` de lanes.yaml > valores de fábrica."""
    env = {**(env or {}), **{k: v for k, v in os.environ.items() if k in (ENV_MAX_ATTEMPTS, ENV_SECONDS)}}
    attempts = int(_num(env.get(ENV_MAX_ATTEMPTS) or settings.get("release_stale_max_attempts"), DEFAULT_MAX_ATTEMPTS))
    seconds = _num(env.get(ENV_SECONDS) or settings.get("release_stale_seconds"), DEFAULT_SECONDS)
    return {"enabled": backend == "supabase" and attempts > 0, "max_attempts": max(0, attempts),
            "seconds": max(5.0, seconds)}


class LeaseReaper:
    """Pasada periódica en el bucle del runner. Nunca lanza: un fallo de la base solo se registra (el siguiente tick lo
    reintenta). `alert(texto)` solo se usa cuando una tarjeta llega a triage por reservas caducadas repetidas."""

    def __init__(self, kanban, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS, seconds: float = DEFAULT_SECONDS,
                 alert: Callable[[str], None] | None = None, clock: Callable[[], float] = time.monotonic):
        self.kanban, self.max_attempts, self.interval = kanban, int(max_attempts), float(seconds)
        self.alert, self._clock = alert, clock
        self._last: float | None = None

    def tick(self) -> list[dict]:
        now = self._clock()
        if self._last is not None and now - self._last < self.interval:
            return []
        self._last = now
        try:
            rows = self.kanban.release_stale(self.max_attempts) or []
        except Exception as exc:  # noqa: BLE001 - el segador nunca tumba el bucle
            log.warning("segador de reservas: %s", exc)
            return []
        if not rows and getattr(self.kanban, "last_error", None):
            log.warning("segador de reservas: %s", self.kanban.last_error)
        for r in rows:
            log.warning("%s: reserva de %s caducada sin latido (intento %s/%s) -> %s", r.get("task_id"),
                        r.get("worker_id"), r.get("attempts"), self.max_attempts, r.get("status"))
        triaged = [r for r in rows if r.get("status") == "triage"]
        if triaged and self.alert:
            try:
                self.alert("🧊 " + ", ".join(f"{r['task_id']} ({r.get('worker_id')})" for r in triaged)
                           + f": su reserva caducó {self.max_attempts} veces seguidas; pasa a triage para que alguien la mire")
            except Exception as exc:  # noqa: BLE001
                log.warning("aviso del segador falló: %s", exc)
        return rows


def worker_overrides(settings: dict, worker: str | None) -> dict:
    """Ajustes de ESTE equipo (`runner.workers.<id>`): {max_workers: int|None, lanes: set|None}. Sin id (backend
    hermes) o sin entrada, no hay cambios."""
    entry = ((settings.get("workers") or {}).get(worker) or {}) if worker else {}
    if not isinstance(entry, dict):
        entry = {}
    try:
        cap = int(entry["max_workers"]) if entry.get("max_workers") is not None else None
    except (TypeError, ValueError):
        cap = None
    lanes = entry.get("lanes")
    return {"max_workers": cap if cap and cap > 0 else None,
            "lanes": {str(x) for x in lanes} if isinstance(lanes, (list, tuple, set)) and lanes else None}
