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
from .notices import (MessageStore, TaskNotices, files_label, money, normalize_questions, question_text,
                      questions_block, render, status_line, test_label)
from .telegram import telegram_target

log = logging.getLogger("agent_lanes")

STATE_DIR = ROOT / ".state" / "tasks"
MESSAGES_DIR = ROOT / ".state" / "messages"  # message_id de Telegram por tarea (persiste entre carriles y reinicios)


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


FOR_OSCAR_PREFIX = "Para Oscar:"  # línea del motivo del bloqueo con el `for_oscar` del worker (la lee renotify)


def dm_mirror(decisions, target, alert: bool, state: str | None = None) -> str | None:
    """Chat del DM de Oscar que recibe una copia del aviso (bandeja única de decisiones), con el bot de carriles:

    - toda decisión (❓ needs_input), venga del tema que venga;
    - cualquier alerta (❓ ✅ ⛔) de una tarea pedida desde su DM (Origen-Telegram chat=<owner>).

    Lo que no es decisión (inicio, fin, técnico) de una tarea pedida desde un tema sigue solo en el tema.
    TaskNotices no manda la copia si el aviso principal ya cayó en el DM (sin duplicados)."""
    owner = str(getattr(decisions, "owner_id", "") or "")
    if not (alert and owner):
        return None
    if state == "needs_input" or (target and str(target[0]) == owner):
        return owner
    return None


class LaneRunner:
    def __init__(self, lane: Lane, *, hermes, git, worker, verifier, notify: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic, state_dir: Path = STATE_DIR,
                 pid_alive: Callable[[int], bool] = pid_alive, exclude: set[str] | None = None,
                 messages: MessageStore | None = None, links: Callable | None = None, decisions=None):
        self.lane = lane
        self._decisions = decisions  # DecisionDesk (botones) solo con CARRILES_BOT_TOKEN
        self.hermes = hermes
        self.git = git
        self.worker = worker
        self.verifier = verifier
        self._notices = TaskNotices(notify, messages)
        self._links = links
        self.clock = clock
        self.state_dir = Path(state_dir)
        self.pid_alive = pid_alive
        self.exclude = set(exclude or ())
        self._active: dict[str, dict] = {}  # tid -> task being processed (for notice routing)

    def notify(self, state: str, tid: str, task: dict | None, status: str, *, alert: bool = False,
               bullets: list[str] | None = None, branch_link: bool = True, changed_files=None,
               buttons: bool = False, block_kind: str | None = None, questions=None, summary: str | None = None,
               for_oscar: str | None = None, yes_no: bool = True) -> None:
        """Estado de la tarea en su único mensaje. `status` es texto público: nunca rutas, stderr ni trazas.
        `buttons`: añade los botones de decisión del estado (si hay bot de carriles). `for_oscar`: explicación llana
        del worker, sustituye al "Qué:" técnico; `summary`: último resumen del worker (lo usa 💬 Explícame más)."""
        task = {"id": tid, **(task or {})}
        # Origen-Telegram routes the notice back to that topic (contract with W3b); lane.telegram is the fallback.
        target = telegram_target(task.get("body"))
        mirror = dm_mirror(self._decisions, target, alert, state)
        for attempt in (1, 2):  # un aviso que falla (red, Telegram) se reintenta UNA vez
            try:
                links = self._links(self.lane, tid, branch=branch_link, changed_files=changed_files) if self._links else []
                text = render(state, tid, task.get("title"), self.lane.name, status, links, bullets,
                              body=task.get("body"), for_oscar=for_oscar)
                markup = None
                if buttons and self._decisions:
                    markup = self._decisions.markup(state, task=task, lane=self.lane, block_kind=block_kind,
                                                    questions=questions, changed_files=changed_files,
                                                    summary=summary, for_oscar=for_oscar, yes_no=yes_no)
                self._notices.publish(tid, text, target, self.lane.telegram, alert=alert, reply_markup=markup,
                                      **({"mirror_to": mirror} if mirror else {}))
                return
            except Exception as exc:
                log.warning("aviso a Telegram falló (intento %d/2): %s", attempt, exc)

    def _drop_hermes_subs(self, tid: str) -> None:
        """Hermes suscribe el hilo de origen al crear la tarea desde Telegram; su notificador duplicaría los avisos."""
        try:
            dropped = self.hermes.drop_telegram_subs(tid)
            if dropped:
                log.info("%s: quitadas suscripciones de Hermes %s (avisa el runner)", tid, dropped)
        except Exception as exc:  # best effort: never blocks the claim
            log.warning("%s: no se pudieron quitar las suscripciones de Hermes: %s", tid, exc)

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
                        + (f" (session_id={session}; se puede retomar con --resume)" if session else ""),
                        task, public="runner reiniciado con la tarea en curso")
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

    def _block(self, tid: str, kind: str, reason: str, task: dict | None = None, *, public: str,
               questions: list | None = None, summary: str | None = None, for_oscar: str | None = None) -> str:
        """`reason` (detalle técnico) va a la tarjeta y al log; a Telegram solo `public` (+ preguntas si needs_input)."""
        if for_oscar:  # también a la tarjeta: renotify y /decisiones lo recuperan del motivo del bloqueo
            reason = f"{reason}\n{FOR_OSCAR_PREFIX} {' '.join(str(for_oscar).split())}"
        log.warning("%s bloqueada (%s): %s", tid, kind, reason)
        blocked = self.hermes.block(tid, kind, reason[:1500])
        if not blocked:
            log.error("no se pudo bloquear %s (%s) en el kanban: la tarea sigue en running", tid, kind)
        task = task or self._active.get(tid)
        # Botones solo si el bloqueo quedó hecho: Reintentar/Responder hacen `unblock`.
        if kind == "needs_input":
            hint = "responde con un botón o en la tarjeta" if (blocked and self._decisions) else \
                "responde en este hilo o a Hermes"
            self.notify("needs_input", tid, task, status_line(public, hint), alert=True,
                        bullets=questions_block(questions), buttons=bool(blocked), questions=questions,
                        summary=summary, for_oscar=for_oscar)
        else:
            self.notify("blocked", tid, task, status_line("bloqueada", public, "detalle en la tarjeta"), alert=True,
                        buttons=bool(blocked), block_kind=kind)
        return f"blocked:{kind}"

    def process(self, task: dict) -> str:
        tid = task["id"]
        lane = self.lane
        if not self.hermes.claim(tid, lane.claim_ttl_seconds):
            log.info("%s: claim perdido (otro runner o ya no está ready)", tid)
            return "claim_lost"
        self._drop_hermes_subs(tid)
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

        with Heartbeat(lambda: self.hermes.heartbeat(tid), lane.heartbeat_seconds):
            try:
                feedback = self.hermes.review_feedback(tid)  # non-empty when the review lane reopened it
                if feedback:
                    task = {**task, "review_feedback": feedback}
                # Respuestas de Oscar a un needs_input anterior (botones de Telegram): el worker debe verlas.
                answers = self.hermes.oscar_answers(tid) if hasattr(self.hermes, "oscar_answers") else []
                if answers:
                    task = {**task, "oscar_answers": answers}
                self.notify("running", tid, task,
                            status_line("en curso", f"aplicando cambios de revisión (ronda {len(feedback)})"
                                        if feedback else None), branch_link=bool(feedback))
                cwd = self.git.prepare_worktree(lane, tid)
                outcome = self.worker.run(lane, task, cwd, session_id, timeout=max(60.0, deadline - self.clock()))
                resumes = 0
                while not outcome.ok and resumes < lane.max_resumes and deadline - self.clock() > 60:
                    resumes += 1
                    log.info("%s: resume %d tras %s", tid, resumes, outcome.subtype)
                    outcome = self.worker.resume(lane, cwd, session_id, timeout=max(60.0, deadline - self.clock()), task_id=tid)
            except Exception as exc:
                return self._block(tid, "transient", f"error del runner: {exc}", task, public="error del runner")

            if not outcome.ok:
                why = "max_runtime alcanzado" if deadline - self.clock() <= 60 else f"tras {resumes} resume(s)"
                return self._block(tid, "transient",
                                   f"el worker no terminó ({why}): {outcome.raw_error} (session_id={session_id})", task,
                                   public="el worker no terminó (tiempo o presupuesto agotado)")
            result = outcome.structured
            if result["status"] == "needs_input":
                asked = normalize_questions(result.get("questions")) or normalize_questions([result.get("summary", "")])
                detail = "\n".join(f"- {question_text(q)}" for q in asked) or result.get("summary", "")
                return self._block(tid, "needs_input", f"El worker necesita decisión:\n{detail}", task,
                                   public="necesita tu decisión", questions=asked, summary=result.get("summary"),
                                   for_oscar=result.get("for_oscar"))
            if result["status"] != "done":
                return self._block(tid, "transient", f"worker status={result['status']}: {result.get('summary', '')}",
                                   task, public="el worker no pudo completarla")

            check = self.verifier(lane, tid, cwd, result)
            if not check.ok:
                return self._block(tid, "transient", "verificación mecánica fallida: " + "; ".join(check.reasons),
                                   task, public="verificación mecánica fallida")

            metadata = {
                "lane": lane.name, "session_id": session_id, "worktree": cwd, "resumes": resumes,
                "cost_usd": outcome.cost_usd, "verified": True, "test_cmd": lane.test_cmd,
                "runner_test_exit": check.test_exit, **{k: result.get(k) for k in (
                    "branch", "head_sha", "changed_files", "tests", "next_steps", "risks", "for_oscar")},
            }
            ok, err = self.hermes.request_review(tid, result.get("summary", "")[:1500], metadata)
            if not ok:
                return self._block(tid, "transient", f"request-review rechazado: {err}", task,
                                   public="el kanban rechazó el paso a review")
            # El resumen completo queda en la tarjeta (request-review); aquí solo la línea de estado.
            self.notify("review", tid, task, status_line(
                "en review", files_label(len(result.get("changed_files") or [])), test_label(check.test_exit),
                money(outcome.cost_usd)), changed_files=result.get("changed_files"), for_oscar=result.get("for_oscar"))
            return "review"
