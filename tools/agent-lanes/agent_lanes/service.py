"""One runner process for every lane: global worker cap + single-instance lock (the 27-09 OOM)."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .runner import pid_alive as _pid_alive

log = logging.getLogger("agent_lanes")


class Service:
    """Each pass collects the jobs of every lane (implementers and review) and runs them with at most
    `max_workers` claude processes alive at once. Default 1: one worker in total on this PC."""

    def __init__(self, lanes: list, max_workers: int = 1, busy_path: Path | None = None):
        self.lanes = lanes
        self.max_workers = max(1, int(max_workers))
        # .state/busy.json mientras dura una pasada con trabajo: `lanes.py status` y /salud ven también al carril
        # review (no reclama ni escribe estado por tarea) y no dan "libre" con un worker en marcha (t_bdee05fe).
        self.busy_path = Path(busy_path) if busy_path else None
        # Fallos al listar tareas en la última pasada (texto del error): los lee el vigilante de runner ciego.
        self.list_errors: list[str] = []
        # Plaza reservada (Lane.reserved_slots): los carriles que la piden corren en su propio pool, fuera de
        # max_workers, y arrancan aunque un pase con todos los generales ocupados siga en curso.
        self._reserved_lock = threading.Lock()
        self._reserved_pool: ThreadPoolExecutor | None = None
        self._reserved_flying: dict[str, object] = {}  # tid -> Future
        self._reserved_owner: dict[str, object] = {}   # tid -> carril que lo lanzó

    @staticmethod
    def _slots(lane) -> int:
        return int(getattr(getattr(lane, "lane", None), "reserved_slots", 0) or 0)

    def reserved_pass(self) -> list[str]:
        """Lanza (sin esperar) las tareas de los carriles con plaza reservada: nunca más de `reserved_slots` vivas a la
        vez por carril, cuente lo que cuente max_workers. Devuelve los ids lanzados."""
        started = []
        for lane in self.lanes:
            slots = self._slots(lane)
            if slots <= 0:
                continue
            with self._reserved_lock:
                self._reserved_flying = {t: f for t, f in self._reserved_flying.items() if not f.done()}
                free = slots - sum(1 for t in self._reserved_flying if self._reserved_owner.get(t) is lane)
                flying = set(self._reserved_flying)
            if free <= 0:
                continue
            try:
                found = [(t, j) for t, j in lane.jobs() if t not in flying]
            except Exception as exc:  # un carril roto no para al resto
                log.error("%s: no se pudieron listar tareas: %s", getattr(getattr(lane, "lane", None), "name", lane), exc)
                self.list_errors.append(str(exc))
                continue
            for tid, job in found[:free]:
                with self._reserved_lock:
                    if self._reserved_pool is None:
                        total = sum(self._slots(l) for l in self.lanes)
                        self._reserved_pool = ThreadPoolExecutor(max_workers=total, thread_name_prefix="lane-reserved")
                    self._reserved_flying[tid] = self._reserved_pool.submit(job)
                    self._reserved_owner[tid] = lane
                started.append(tid)
        return started

    def wait_reserved(self) -> dict[str, str]:
        """Espera a las tareas de plaza reservada en vuelo (modo --once y tests)."""
        out = {}
        for tid, fut in list(self._reserved_flying.items()):
            try:
                out[tid] = fut.result()
            except Exception as exc:
                log.exception("%s: job falló: %s", tid, exc)
                out[tid] = f"error:{exc}"
        return out

    def start_reserved_ticker(self, interval: float, paused: Callable[[], bool] = lambda: False):
        """Hilo que mira cada `interval` s si el carril reservado tiene trabajo, aunque el bucle principal esté esperando
        a los workers generales. `paused()` (reinicio ordenado) detiene los lanzamientos."""
        if not any(self._slots(l) for l in self.lanes):
            return None

        def loop():
            while True:
                try:
                    if not paused():
                        self.reserved_pass()
                except Exception as exc:
                    log.error("plaza reservada: %s", exc)
                time.sleep(interval)

        thread = threading.Thread(target=loop, name="lane-reserved-ticker", daemon=True)
        thread.start()
        return thread

    def _mark_busy(self, jobs: list[tuple[str, str]]) -> None:
        if not self.busy_path:
            return
        try:
            self.busy_path.parent.mkdir(parents=True, exist_ok=True)
            self.busy_path.write_text(json.dumps({"pid": os.getpid(), "since": time.time(),
                                                  "jobs": [{"task": t, "lane": l} for t, l in jobs]}),
                                      encoding="utf-8")
        except OSError as exc:
            log.info("busy.json no escrito: %s", exc)

    def run_pass(self) -> dict[str, str]:
        jobs, named = [], []
        self.list_errors = []
        for lane in self.lanes:
            if self._slots(lane):  # su plaza es aparte (reserved_pass): no gasta max_workers
                continue
            try:
                found = lane.jobs()
            except Exception as exc:  # one broken lane (hermes/git error) must not stop the others
                log.error("%s: no se pudieron listar tareas: %s", getattr(lane, "name", lane), exc)
                self.list_errors.append(str(exc))
                continue
            jobs += found
            name = getattr(getattr(lane, "lane", None), "name", "?")
            named += [(tid, name) for tid, _ in found]
        if not jobs:
            return {}
        self._mark_busy(named)
        try:
            return self._run(jobs)
        finally:
            if self.busy_path:
                self.busy_path.unlink(missing_ok=True)

    def _run(self, jobs: list) -> dict[str, str]:
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="lane-job") as pool:
            futures = {tid: pool.submit(job) for tid, job in jobs}
        results = {}
        for tid, fut in futures.items():
            try:
                results[tid] = fut.result()
            except Exception as exc:
                log.exception("%s: job falló: %s", tid, exc)
                results[tid] = f"error:{exc}"
        return results


def acquire_single_instance(lock_path: Path, pid_alive: Callable[[int], bool] = _pid_alive) -> bool:
    """True if this process now owns the runner lock; False if another live runner holds it."""
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if lock_path.exists():
        try:
            owner = int(json.loads(lock_path.read_text(encoding="utf-8")).get("pid", 0))
        except (OSError, ValueError, json.JSONDecodeError):
            owner = 0
        if owner and owner != os.getpid() and pid_alive(owner):
            return False
    lock_path.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8")
    return True
