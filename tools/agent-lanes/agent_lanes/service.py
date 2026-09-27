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

    def __init__(self, lanes: list, max_workers: int = 1):
        self.lanes = lanes
        self.max_workers = max(1, int(max_workers))

    def run_pass(self) -> dict[str, str]:
        jobs = []
        for lane in self.lanes:
            try:
                jobs += lane.jobs()
            except Exception as exc:  # one broken lane (hermes/git error) must not stop the others
                log.error("%s: no se pudieron listar tareas: %s", getattr(lane, "name", lane), exc)
        if not jobs:
            return {}
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
