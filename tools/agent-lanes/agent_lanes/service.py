"""One runner process for every lane: global worker cap + single-instance lock (the 27-09 OOM)."""
from __future__ import annotations

import json
import logging
import os
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
        for lane in self.lanes:
            try:
                found = lane.jobs()
            except Exception as exc:  # one broken lane (hermes/git error) must not stop the others
                log.error("%s: no se pudieron listar tareas: %s", getattr(lane, "name", lane), exc)
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
