"""Reinicio ordenado del runner (drain), tarjeta t_bdee05fe (incidente 27-09: un `schtasks /End` con 2 workers recién
lanzados dejó uno huérfano y duplicó otro).

    py -3.12 lanes.py restart --drain [--timeout 3600]

1. Crea .state/drain.json. El runner lo ve al empezar cada vuelta del bucle principal: deja de reclamar (ni carriles,
   ni review, ni pasadas del integrador) y escribe .state/drain_ack.json con su PID. Como las pasadas son síncronas,
   ese acuse solo llega cuando han terminado todos sus workers; `idle` es False mientras un botón de Oscar tenga un
   deploy o unos gates del integrador en marcha.
2. Con el acuse ocioso del runner vivo: `schtasks /End` de la tarea "agent-lanes runner", espera a que muera su PID
   (la tarea usa MultipleInstances IgnoreNew: un /Run con el viejo aún vivo no haría nada), borra el drenaje y
   `schtasks /Run`. Comprueba que arranca un runner nuevo.
3. Sin acuse en `--timeout` segundos: borra el drenaje, NO reinicia y sale con error.

Un drain.json de más de 2 h se ignora siempre (y se borra al arrancar el runner): un `restart` que muriera a medias
no puede dejar los carriles sin reclamar indefinidamente.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

from . import proc as _proc
from .config import ROOT

log = logging.getLogger("agent_lanes")

STATE = ROOT / ".state"
DRAIN_FILE = STATE / "drain.json"
ACK_FILE = STATE / "drain_ack.json"
LOCK_FILE = STATE / "runner.lock"
BUSY_FILE = STATE / "busy.json"  # lo escribe Service mientras dura una pasada con trabajo (review incluido)
MAX_AGE_SECONDS = 2 * 3600
TASK_NAME = "agent-lanes runner"
SCHTASKS = "schtasks"


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def request(path: Path = DRAIN_FILE, *, now: float | None = None) -> dict:
    data = {"since": time.time() if now is None else now, "pid": os.getpid()}
    _write(path, data)
    return data


def active(path: Path = DRAIN_FILE, *, now: float | None = None, max_age: float = MAX_AGE_SECONDS) -> dict | None:
    """El drenaje vigente, o None (sin fichero, ilegible o de más de `max_age`: se ignora)."""
    data = _read(path)
    if not data:
        return None
    try:
        age = (time.time() if now is None else now) - float(data.get("since") or 0)
    except (TypeError, ValueError):
        return None
    return data if 0 <= age <= max_age else None


def discard_stale(path: Path = DRAIN_FILE, *, now: float | None = None, max_age: float = MAX_AGE_SECONDS) -> bool:
    """Al arrancar el runner: borra un drain.json caducado (o ilegible). True si había uno y se ignoró."""
    if not Path(path).exists() or active(path, now=now, max_age=max_age):
        return False
    log.warning("fichero de drenaje de más de %d h (o ilegible): se ignora y se borra", max_age // 3600)
    clear(path, None)
    return True


def clear(path: Path = DRAIN_FILE, ack: Path | None = ACK_FILE) -> None:
    for p in (path, ack):
        if p is not None:
            Path(p).unlink(missing_ok=True)


def write_ack(path: Path = ACK_FILE, *, pid: int | None = None, busy: list[str] | None = None,
              now: float | None = None) -> None:
    """Lo escribe el runner en cada vuelta mientras dura el drenaje (solo entre pasadas)."""
    busy = list(busy or ())
    _write(path, {"pid": os.getpid() if pid is None else pid, "at": time.time() if now is None else now,
                  "idle": not busy, "busy": busy})


def read_ack(path: Path = ACK_FILE) -> dict | None:
    return _read(path)


def runner_pid(lock: Path = LOCK_FILE) -> int | None:
    try:
        return int((_read(lock) or {}).get("pid") or 0) or None
    except (TypeError, ValueError):
        return None


class Restarter:
    """`lanes.py restart --drain`. Todo inyectable: los tests no tocan schtasks ni el .state real."""

    def __init__(self, *, pid_alive: Callable[[int], bool], drain_file: Path = DRAIN_FILE, ack_file: Path = ACK_FILE,
                 lock_file: Path = LOCK_FILE, run=_proc.run, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], float] = time.time,
                 out: Callable[[str], None] = print, task_name: str = TASK_NAME, poll: float = 5):
        self.pid_alive = pid_alive
        self.drain_file, self.ack_file, self.lock_file = Path(drain_file), Path(ack_file), Path(lock_file)
        self._run = run
        self._sleep = sleep
        self._clock = clock
        self._now = now
        self.out = out
        self.task_name = task_name
        self.poll = poll

    def _schtasks(self, verb: str) -> bool:
        cp = self._run([SCHTASKS, f"/{verb}", "/TN", self.task_name], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
        if cp.returncode != 0:
            self.out(f"schtasks /{verb} falló: {(cp.stderr or cp.stdout or '').strip()[:300]}")
        return cp.returncode == 0

    def _wait(self, cond: Callable[[], bool], seconds: float) -> bool:
        deadline = self._clock() + seconds
        while True:
            if cond():
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(self.poll)

    def restart(self, timeout: float = 3600) -> int:
        old = runner_pid(self.lock_file)
        alive = bool(old) and self.pid_alive(old)
        if alive:
            since = request(self.drain_file, now=self._now())["since"]
            self.out(f"drenando: el runner (pid {old}) deja de reclamar; espero a que acaben sus workers "
                     f"(máx. {int(timeout)} s)")

            def drained() -> bool:
                if not self.pid_alive(old):
                    return True  # murió solo: nada que esperar
                ack = read_ack(self.ack_file) or {}
                return ack.get("pid") == old and float(ack.get("at") or 0) >= since and bool(ack.get("idle"))

            if not self._wait(drained, timeout):
                busy = ", ".join((read_ack(self.ack_file) or {}).get("busy") or ()) or "workers en curso"
                clear(self.drain_file, self.ack_file)
                self.out(f"timeout de {int(timeout)} s sin quedar ocioso ({busy}): drenaje cancelado, NO reinicio")
                return 1
            if self.pid_alive(old):
                if not self._schtasks("End"):
                    clear(self.drain_file, self.ack_file)
                    return 1
                if not self._wait(lambda: not self.pid_alive(old), 60):
                    clear(self.drain_file, self.ack_file)
                    self.out(f"el runner (pid {old}) sigue vivo tras /End: no relanzo (IgnoreNew no arrancaría otro)")
                    return 1
        else:
            self.out("runner no vivo: lo arranco sin drenar")
        clear(self.drain_file, self.ack_file)
        if not self._schtasks("Run"):
            return 1

        def started() -> bool:
            pid = runner_pid(self.lock_file)
            return bool(pid) and pid != old and self.pid_alive(pid)

        # El runner coge el lock tras el test de contrato de hermes (~16 llamadas a la CLI): margen amplio.
        if not self._wait(started, 180):
            self.out("la tarea se lanzó, pero no veo un runner nuevo en 180 s: mira .state/runner.log")
            return 1
        self.out(f"runner reiniciado (pid {runner_pid(self.lock_file)})")
        return 0
