"""Lane runner state machine: ready -> claim -> worktree -> worker (+resume) -> verify -> review | block."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Callable

from .config import Lane

log = logging.getLogger("agent_lanes")

# The claim TTL cannot be extended from the CLI (hermes 0.21.4), so the runner stops working this long
# before it expires, leaving time to block the task itself instead of letting the sweep reclaim it mid-run.
DEADLINE_MARGIN_SECONDS = 600


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
                 clock: Callable[[], float] = time.monotonic):
        self.lane = lane
        self.hermes = hermes
        self.git = git
        self.worker = worker
        self.verifier = verifier
        self._notify = notify
        self.clock = clock

    def notify(self, text: str) -> None:
        try:
            self._notify(f"[{self.lane.name}] {text}")
        except Exception as exc:
            log.warning("aviso a Telegram falló: %s", exc)

    def run_once(self) -> dict[str, str]:
        """One pass: process up to max_parallel ready tasks sequentially. Returns {task_id: outcome}."""
        results: dict[str, str] = {}
        for task in self.hermes.list_ready(self.lane.name)[: self.lane.max_parallel]:
            results[task["id"]] = self.process(task)
        return results

    def _block(self, tid: str, kind: str, reason: str) -> str:
        self.hermes.block(tid, kind, reason[:1500])
        self.notify(f"⛔ {tid} bloqueada ({kind}): {reason[:300]}")
        return f"blocked:{kind}"

    def process(self, task: dict) -> str:
        tid = task["id"]
        lane = self.lane
        if not self.hermes.claim(tid, lane.claim_ttl_seconds):
            log.info("%s: claim perdido (otro runner o ya no está ready)", tid)
            return "claim_lost"
        deadline = self.clock() + lane.claim_ttl_seconds - DEADLINE_MARGIN_SECONDS
        session_id = str(uuid.uuid4())
        self.hermes.comment(tid, f"LANE claim por {lane.name} · session_id={session_id} · rama lane/{tid}")
        self.notify(f"▶️ empieza {tid}: {task.get('title', '')[:120]}")

        with Heartbeat(lambda: self.hermes.heartbeat(tid), lane.heartbeat_seconds):
            try:
                cwd = self.git.prepare_worktree(lane, tid)
                outcome = self.worker.run(lane, task, cwd, session_id, timeout=max(60.0, deadline - self.clock()))
                resumes = 0
                while not outcome.ok and resumes < lane.max_resumes and deadline - self.clock() > 60:
                    resumes += 1
                    log.info("%s: resume %d tras %s", tid, resumes, outcome.subtype)
                    outcome = self.worker.resume(lane, cwd, session_id, timeout=max(60.0, deadline - self.clock()))
            except Exception as exc:
                return self._block(tid, "transient", f"error del runner: {exc}")

            if not outcome.ok:
                return self._block(tid, "transient",
                                   f"el worker no terminó tras {resumes} resume(s): {outcome.raw_error} "
                                   f"(session_id={session_id})")
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
                        f"test `{lane.test_cmd}` exit {check.test_exit}\n{result.get('summary', '')[:500]}")
            return "review"
