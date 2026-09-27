"""Lane runner state machine: ready -> claim -> worktree -> worker (+resume) -> verify -> review | block."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from .config import ROOT, Lane
from .telegram import telegram_target

log = logging.getLogger("agent_lanes")

STATE_DIR = ROOT / ".state" / "tasks"


def pid_alive(pid: int) -> bool:
    """Liveness without side effects (on Windows os.kill(pid, 0) would TerminateProcess)."""
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


class Heartbeat:
    """Own thread, independent of the worker process: keeps last_heartbeat_at fresh while claude runs."""

    def __init__(self, fn: Callable[[], object], interval: float):
        self._fn = fn
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="lane-heartbeat", daemon=True)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._fn()
            except Exception as exc:  # a failed beat must never kill the runner
                log.warning("heartbeat falló: %s", exc)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)


class LaneRunner:
    def __init__(self, lane: Lane, *, hermes, git, worker, verifier, notify: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic, state_dir: Path = STATE_DIR,
                 pid_alive: Callable[[int], bool] = pid_alive, exclude: set[str] | None = None):
        self.lane = lane
        self.hermes = hermes
        self.git = git
        self.worker = worker
        self.verifier = verifier
        self._notify = notify
        self.clock = clock
        self.state_dir = Path(state_dir)
        self.pid_alive = pid_alive
        self.exclude = set(exclude or ())
        self._active: dict[str, dict] = {}  # tid -> task being processed (for notice routing)

    def notify(self, text: str, task: dict | None = None) -> None:
        # Origen-Telegram routes the notice back to that topic (contract with W3b); lane.telegram is the fallback.
        target = telegram_target((task or {}).get("body"))
        try:
            self._notify(f"[{self.lane.name}] {text}", target, self.lane.telegram)
        except Exception as exc:
            log.warning("aviso a Telegram falló: %s", exc)

    def _state_path(self, tid: str) -> Path:
        return self.state_dir / f"{tid}.json"

    def reconcile(self) -> list[str]:
        """At startup: block this lane's `running` tasks whose runner is gone, instead of waiting for the TTL.

        Only this runner consumes the lane's assignee, so a running task without a live owner PID is orphaned.
        """
        orphans = []
        for task in self.hermes.list_status(self.lane.name, "running"):
            tid = task["id"]
            path = self._state_path(tid)
            state = {}
            if path.exists():
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    state = {}
            pid = state.get("pid")
            if pid and self.pid_alive(int(pid)):
                continue
            session = state.get("session_id")
            self._block(tid, "transient", "runner reiniciado: la tarea quedó en running sin proceso vivo"
                        + (f" (session_id={session}; se puede retomar con --resume)" if session else ""))
            path.unlink(missing_ok=True)
            orphans.append(tid)
        return orphans

    def jobs(self) -> list[tuple[str, Callable[[], str]]]:
        """Work for one pass: up to max_parallel ready tasks, as (task_id, callable) for the Service."""
        ready = [t for t in self.hermes.list_ready(self.lane.name) if t["id"] not in self.exclude]
        return [(t["id"], lambda t=t: self.process(t)) for t in ready[: self.lane.max_parallel]]

    def run_once(self) -> dict[str, str]:
        """One pass, sequential. Returns {task_id: outcome}."""
        return {tid: job() for tid, job in self.jobs()}

    def _block(self, tid: str, kind: str, reason: str, task: dict | None = None) -> str:
        if not self.hermes.block(tid, kind, reason[:1500]):
            log.error("no se pudo bloquear %s (%s) en el kanban: la tarea sigue en running", tid, kind)
        self.notify(f"⛔ {tid} bloqueada ({kind}): {reason[:300]}", task or self._active.get(tid))
        return f"blocked:{kind}"

    def process(self, task: dict) -> str:
        tid = task["id"]
        lane = self.lane
        if not self.hermes.claim(tid, lane.claim_ttl_seconds):
            log.info("%s: claim perdido (otro runner o ya no está ready)", tid)
            return "claim_lost"
        deadline = self.clock() + lane.max_runtime_seconds
        session_id = str(uuid.uuid4())
        state_path = self._state_path(tid)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"task_id": tid, "lane": lane.name, "pid": os.getpid(),
                                          "session_id": session_id, "started": time.time()}), encoding="utf-8")
        self._active[tid] = task
        try:
            return self._work(task, tid, session_id, deadline)
        finally:
            self._active.pop(tid, None)
            state_path.unlink(missing_ok=True)

    def _work(self, task: dict, tid: str, session_id: str, deadline: float) -> str:
        lane = self.lane
        self.hermes.comment(tid, f"LANE claim por {lane.name} · session_id={session_id} · rama lane/{tid}")
        self.notify(f"▶️ empieza {tid}: {task.get('title', '')[:120]}", task)

        with Heartbeat(lambda: self.hermes.heartbeat(tid), lane.heartbeat_seconds):
            try:
                feedback = self.hermes.review_feedback(tid)  # non-empty when the review lane reopened it
                if feedback:
                    task = {**task, "review_feedback": feedback}
                cwd = self.git.prepare_worktree(lane, tid)
                outcome = self.worker.run(lane, task, cwd, session_id, timeout=max(60.0, deadline - self.clock()))
                resumes = 0
                while not outcome.ok and resumes < lane.max_resumes and deadline - self.clock() > 60:
                    resumes += 1
                    log.info("%s: resume %d tras %s", tid, resumes, outcome.subtype)
                    outcome = self.worker.resume(lane, cwd, session_id, timeout=max(60.0, deadline - self.clock()), task_id=tid)
            except Exception as exc:
                return self._block(tid, "transient", f"error del runner: {exc}")

            if not outcome.ok:
                why = "max_runtime alcanzado" if deadline - self.clock() <= 60 else f"tras {resumes} resume(s)"
                return self._block(tid, "transient",
                                   f"el worker no terminó ({why}): {outcome.raw_error} (session_id={session_id})")
            result = outcome.structured
            if result["status"] == "needs_input":
                questions = "\n".join(f"- {q}" for q in result.get("questions") or []) or result.get("summary", "")
                return self._block(tid, "needs_input", f"El worker necesita decisión:\n{questions}")
            if result["status"] != "done":
                return self._block(tid, "transient", f"worker status={result['status']}: {result.get('summary', '')}")

            check = self.verifier(lane, tid, cwd, result)
            if not check.ok:
                return self._block(tid, "transient", "verificación mecánica fallida: " + "; ".join(check.reasons))

            metadata = {
                "lane": lane.name, "session_id": session_id, "worktree": cwd, "resumes": resumes,
                "cost_usd": outcome.cost_usd, "verified": True, "test_cmd": lane.test_cmd,
                "runner_test_exit": check.test_exit, **{k: result.get(k) for k in (
                    "branch", "head_sha", "changed_files", "tests", "next_steps", "risks")},
            }
            ok, err = self.hermes.request_review(tid, result.get("summary", "")[:1500], metadata)
            if not ok:
                return self._block(tid, "transient", f"request-review rechazado: {err}")
            self.notify(f"✅ {tid} en review · rama {result.get('branch')} @ {str(result.get('head_sha'))[:10]} · "
                        f"test exit {check.test_exit}\n{result.get('summary', '')[:500]}", task)
            return "review"
