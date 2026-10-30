"""One runner process for every lane: global worker cap + single-instance lock (the 27-09 OOM)."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

from .runner import pid_alive as _pid_alive

log = logging.getLogger("agent_lanes")


@dataclass
class Flight:
    """Un worker en vuelo del planificador sin barrera (Service.tick)."""
    tid: str
    lane: object
    kind: str            # normal | big | reserved
    future: object
    serial: bool = False
    since: float = 0.0


def _lane_name(lane) -> str:
    return getattr(getattr(lane, "lane", None), "name", None) or getattr(lane, "name", "?")


def _lane_attr(lane, name: str, default):
    value = getattr(getattr(lane, "lane", None), name, default)
    return default if value is None else value


def _candidates(lane) -> list:
    """Candidatos del carril como (tid, job, big, serial). Un carril sin `candidates()` (review, ops, pruebas) ofrece lo
    que diga `jobs()` y nunca es grande."""
    if hasattr(lane, "candidates"):
        return [(c.tid, c.job, bool(c.big), bool(c.serial)) for c in lane.candidates()]
    return [(tid, job, False, False) for tid, job in lane.jobs()]


class Service:
    """Each pass collects the jobs of every lane (implementers and review) and runs them with at most
    `max_workers` claude processes alive at once. Default 1: one worker in total on this PC."""

    def __init__(self, lanes: list, max_workers: int = 1, busy_path: Path | None = None, max_big_total: int = 0,
                 cuota_pausada: Callable[[], bool] | None = None):
        self.lanes = lanes
        # Pausa por cuota de Claude (cuota.py): mientras sea verdadera no se lanza ningún worker (los en vuelo siguen).
        self.cuota_pausada = cuota_pausada
        self.max_workers = max(1, int(max_workers))
        # Planificador sin barrera (tick): huecos grandes en total (0 = sin hueco grande, todo por el carril normal).
        self.max_big_total = max(0, int(max_big_total))
        self._lock = threading.RLock()
        self._pool: ThreadPoolExecutor | None = None
        self._flights: dict[str, Flight] = {}
        self._finished: dict[str, str] = {}
        self.wake = threading.Event()  # un worker terminó: el bucle principal no espera al siguiente intervalo
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
        if self._cuota():
            return started
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

    # --- planificador sin barrera (23-10, t_c5cafff2) ----------------------------------------------------------
    # Antes: run_pass() juntaba 1 job por carril y esperaba a TODOS antes de volver a listar; una tarea grande de un
    # carril dejaba 1,5 h sin worker a las tarjetas ready de otro con plazas libres. Ahora cada tick() (no bloquea)
    # lanza lo que cabe y cada hueco se reutiliza en cuanto su job termina (`wake`). Huecos:
    #   normal    cuenta contra max_workers y contra max_parallel del carril;
    #   big       tarjeta grande: hueco propio del carril (max_big, fuera de su max_parallel); cuenta contra max_workers
    #             (RAM) y contra max_big_total; una grande que no cabe espera SIN tapar a las pequeñas;
    #   reserved  los primeros reserved_slots del carril no cuentan contra max_workers.
    # Un carril con dos tarjetas que citan serialize_paths (migraciones) no las corre a la vez.

    def _executor(self) -> ThreadPoolExecutor:
        if self._pool is None:  # el tope real lo ponen los contadores de tick(); el pool solo ha de dar hilos de sobra
            reserved = sum(int(_lane_attr(l, "reserved_slots", 0)) for l in self.lanes)
            self._pool = ThreadPoolExecutor(max_workers=self.max_workers + self.max_big_total + reserved + 2,
                                            thread_name_prefix="lane-flight")
        return self._pool

    def _reap(self) -> None:
        for tid, fl in list(self._flights.items()):
            if not fl.future.done():
                continue
            del self._flights[tid]
            try:
                self._finished[tid] = fl.future.result()
            except Exception as exc:
                log.exception("%s: job falló: %s", tid, exc)
                self._finished[tid] = f"error:{exc}"

    def pop_results(self) -> dict[str, str]:
        """Resultados de los workers terminados desde la última llamada ({task_id: salida})."""
        with self._lock:
            self._reap()
            out, self._finished = self._finished, {}
            self._write_busy()
            return out

    def wait_all(self, timeout: float | None = None) -> dict[str, str]:
        """Espera a todos los workers en vuelo (modo --once y tests) y devuelve sus resultados."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                pending = [f.future for f in self._flights.values() if not f.future.done()]
            if not pending:
                break
            for fut in pending:
                try:
                    fut.result(None if deadline is None else max(0.0, deadline - time.monotonic()))
                except Exception:
                    pass
            if deadline is not None and time.monotonic() >= deadline:
                break
        return self.pop_results()

    def busy_tasks(self) -> list[str]:
        """Ids de las tareas con worker vivo (el reinicio ordenado espera a que no quede ninguna)."""
        with self._lock:
            self._reap()
            return sorted(self._flights)

    def slots_summary(self) -> dict:
        """Huecos ocupados/total por tipo, para /salud y el panel. `lanes`: por carril {normal,big,reserved}."""
        with self._lock:
            self._reap()
            lanes: dict[str, dict] = {}
            totals = {"normal": 0, "big": 0, "reserved": 0}
            for fl in self._flights.values():
                lanes.setdefault(_lane_name(fl.lane), {"normal": 0, "big": 0, "reserved": 0})[fl.kind] += 1
                totals[fl.kind] += 1
            return {**totals, "max_workers": self.max_workers, "max_big": self.max_big_total,
                    "max_reserved": sum(int(_lane_attr(l, "reserved_slots", 0)) for l in self.lanes), "lanes": lanes}

    def _write_busy(self) -> None:
        if not self.busy_path:
            return
        if not self._flights:
            self.busy_path.unlink(missing_ok=True)
            return
        jobs = [{"task": f.tid, "lane": _lane_name(f.lane), "kind": f.kind} for f in self._flights.values()]
        slots = self.slots_summary()
        slots.pop("lanes", None)
        try:
            self.busy_path.parent.mkdir(parents=True, exist_ok=True)
            self.busy_path.write_text(json.dumps({"pid": os.getpid(), "since": time.time(), "jobs": jobs,
                                                  "slots": slots}), encoding="utf-8")
        except OSError as exc:
            log.info("busy.json no escrito: %s", exc)

    @staticmethod
    def _row(counts: dict, lane) -> dict:
        return counts["lanes"].setdefault(_lane_name(lane), {"normal": 0, "big": 0, "reserved": 0, "serial": 0})

    def _can_big(self, lane, counts: dict) -> bool:
        limit = int(_lane_attr(lane, "max_big", 1))
        return (self.max_big_total > 0 and limit > 0 and self._row(counts, lane)["big"] < limit
                and counts["big"] < self.max_big_total and self.max_workers - counts["normal"] - counts["big"] > 0)

    def _can_normal(self, lane, counts: dict) -> str | None:
        """'reserved' | 'normal' | None según el hueco normal que le quede al carril."""
        row, slots = self._row(counts, lane), int(_lane_attr(lane, "reserved_slots", 0))
        if row["normal"] + row["reserved"] >= max(int(_lane_attr(lane, "max_parallel", 1)), slots):
            return None
        if row["reserved"] < slots:
            return "reserved"
        return "normal" if self.max_workers - counts["normal"] - counts["big"] > 0 else None

    def _kind_for(self, lane, big: bool, serial: bool, counts: dict) -> str | None:
        """Hueco que le toca a un candidato, o None si ahora no cabe."""
        if serial and self._row(counts, lane)["serial"]:
            return None
        if big and self.max_big_total > 0 and int(_lane_attr(lane, "max_big", 1)) > 0:
            return "big" if self._can_big(lane, counts) else None
        return self._can_normal(lane, counts)

    def tick(self, *, only_reserved: bool = False) -> list[str]:
        """Un paso del planificador: recoge los workers terminados, lista los carriles con hueco y lanza (sin esperar) lo
        que cabe. Devuelve los ids lanzados. `only_reserved`: solo carriles con plaza reservada."""
        started: list[str] = []
        with self._lock:
            self._reap()
            if not only_reserved:
                self.list_errors = []
            if self._cuota():
                self._write_busy()
                return started
            counts: dict = {"normal": 0, "big": 0, "reserved": 0, "lanes": {}}
            for fl in self._flights.values():
                counts[fl.kind] += 1
                row = self._row(counts, fl.lane)
                row[fl.kind] += 1
                row["serial"] += 1 if fl.serial else 0
            queues: list[list] = []
            for lane in self.lanes:
                if only_reserved and int(_lane_attr(lane, "reserved_slots", 0)) <= 0:
                    continue
                if not (self._can_normal(lane, counts) or self._can_big(lane, counts)):
                    continue  # sin hueco para este carril: no se gasta una consulta a Hermes
                try:
                    found = [c for c in _candidates(lane) if c[0] not in self._flights]
                except Exception as exc:  # un carril roto (hermes/git) no para al resto
                    log.error("%s: no se pudieron listar tareas: %s", _lane_name(lane), exc)
                    self.list_errors.append(str(exc))
                    continue
                if found:
                    queues.append([lane, found])
            progress = True
            while progress:  # una por carril y vuelta: reparto justo si los huecos no llegan para todos
                progress = False
                for lane, found in queues:
                    for i, (tid, job, big, serial) in enumerate(found):
                        kind = self._kind_for(lane, big, serial, counts)
                        if kind is None:
                            continue  # no cabe (p. ej. grande sin hueco): se salta, las pequeñas de detrás sí pueden
                        del found[i]
                        fut = self._executor().submit(job)
                        fut.add_done_callback(lambda _f: self.wake.set())
                        self._flights[tid] = Flight(tid, lane, kind, fut, serial, time.time())
                        counts[kind] += 1
                        row = self._row(counts, lane)
                        row[kind] += 1
                        row["serial"] += 1 if serial else 0
                        started.append(tid)
                        progress = True
                        break
            self._write_busy()
        return started

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

    def _cuota(self) -> bool:
        """¿Pausa por cuota vigente? Un fallo al comprobarla nunca para al planificador (se asume sin pausa)."""
        try:
            return bool(self.cuota_pausada and self.cuota_pausada())
        except Exception as exc:  # noqa: BLE001
            log.warning("pausa por cuota no comprobada: %s", exc)
            return False

    def run_pass(self) -> dict[str, str]:
        if self._cuota():
            return {}
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
        if owner and owner != os.getpid():
            if pid_alive(owner):
                return False
            log.warning("lock caducado del PID %s: lo tomo", owner)
    lock_path.write_text(json.dumps({"pid": os.getpid(), "started": time.time()}), encoding="utf-8")
    return True
