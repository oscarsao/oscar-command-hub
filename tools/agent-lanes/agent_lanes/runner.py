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

from . import proc as _proc
from .config import ROOT, Lane
from .hermes import GUARD_LIMIT_MARK, looks_like_guard_limit
from .notices import (MessageStore, TaskNotices, files_label, fit_reason, money, next_step, normalize_questions,
                      question_text, questions_block, render, status_line, test_label)
from .preflight import preflight_warnings
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


# Motivos de verificación que dependen de la config (lanes.yaml / cuerpo de la tarea), no de algo que el worker arregle
# reintentando: el aviso no ofrece [🔄 Reintentar].
CONFIG_REASONS = ("toca rutas vetadas", "Destino-Ops no permitido")
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
                 messages: MessageStore | None = None, links: Callable | None = None, decisions=None,
                 tree: Callable | None = None,
                 repo_guard: Callable[[Lane], str | None] | None = None,
                 kill: Callable[[int], None] = _proc.kill_tree,
                 created_at: Callable[[int], float | None] = _proc.created_at):
        self.lane = lane
        self._kill = kill
        self._created_at = created_at
        self._tree = tree  # deps.TreeReader: líneas 🔗/⏸/↳ (padre e hijas) en cada aviso; None = sin árbol
        # Candado de repos (git_ops.github_slug_problem): motivo por el que NO se reclama, o None. Solo carriles de
        # código; el ERROR se registra una vez por arranque (este objeto vive lo que vive el runner).
        self._repo_guard = repo_guard
        self._repo_warned = False
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
        self._preflighted: set[str] = set()  # tareas ya avisadas por el chequeo previo (una vez por arranque)
        self._subs_dropped: set[str] = set()  # tareas con la suscripción de Hermes ya quitada (listado o claim)
        self._reasked: set[str] = set()  # tareas relanzadas UNA vez porque el worker repitió una pregunta ya respondida

    def notify(self, state: str, tid: str, task: dict | None, status: str, *, alert: bool = False,
               bullets: list[str] | None = None, branch_link: bool = True, changed_files=None,
               buttons: bool = False, block_kind: str | None = None, questions=None, summary: str | None = None,
               for_oscar: str | None = None, yes_no: bool = True, q_index: int = 0,
               target_override: tuple[str, str] | None = None) -> None:
        """Estado de la tarea en su único mensaje. `status` es texto público: nunca rutas, stderr ni trazas.
        `buttons`: añade los botones de decisión del estado (si hay bot de carriles). `for_oscar`: explicación llana
        del worker, sustituye al "Qué:" técnico; `summary`: último resumen del worker (lo usa 💬 Explícame más)."""
        task = {"id": tid, **(task or {})}
        # Origen-Telegram routes the notice back to that topic (contract with W3b); lane.telegram is the fallback.
        target = target_override or telegram_target(task.get("body"))  # override = tema Atención (config de confianza)
        mirror = dm_mirror(self._decisions, target, alert, state)
        for attempt in (1, 2):  # un aviso que falla (red, Telegram) se reintenta UNA vez
            try:
                links = self._links(self.lane, tid, branch=branch_link, changed_files=changed_files) if self._links else []
                tree = self._tree(self.lane.board, tid) if self._tree else None
                text = render(state, tid, task.get("title"), self.lane.name, status, links, bullets,
                              body=task.get("body"), for_oscar=for_oscar, tree=tree,
                              next_step=next_step(state, block_kind, ops=self.lane.kind == "ops", buttons=buttons))
                markup = None
                if buttons and self._decisions:
                    markup = self._decisions.markup(state, task=task, lane=self.lane, block_kind=block_kind,
                                                    questions=questions, changed_files=changed_files,
                                                    summary=summary, for_oscar=for_oscar, yes_no=yes_no,
                                                    q_index=q_index)
                self._notices.publish(tid, text, target, self.lane.telegram, alert=alert, reply_markup=markup,
                                      followup=list(getattr(bullets, "overflow", None) or ()) or None,
                                      **({"mirror_to": mirror} if mirror else {}))
                return
            except Exception as exc:
                log.warning("aviso a Telegram falló (intento %d/2): %s", attempt, exc)

    def _drop_hermes_subs(self, tid: str) -> None:
        """Hermes suscribe el hilo de origen al crear la tarea desde Telegram; su notificador duplicaría los avisos."""
        if tid in self._subs_dropped:
            return
        try:
            dropped = self.hermes.drop_telegram_subs(tid)
            self._subs_dropped.add(tid)
            if dropped:
                log.info("%s: quitadas suscripciones de Hermes %s (avisa el runner)", tid, dropped)
        except Exception as exc:  # best effort: never blocks the claim
            log.warning("%s: no se pudieron quitar las suscripciones de Hermes: %s", tid, exc)

    def _state_path(self, tid: str) -> Path:
        return self.state_dir / f"{tid}.json"

    def _read_state(self, tid: str) -> dict:
        try:
            state = json.loads(self._state_path(tid).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return state if isinstance(state, dict) else {}

    def _live_worker(self, state: dict) -> int | None:
        """PID del `claude` apuntado en el estado si SIGUE vivo y es el mismo proceso (no un PID reutilizado)."""
        pid = state.get("worker_pid")
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return None
        if not self.pid_alive(pid):
            return None
        recorded = state.get("worker_created")
        now = self._created_at(pid)
        if recorded is not None and now is not None and abs(float(recorded) - now) > 2:
            return None  # el PID lo tiene ahora otro proceso
        return pid

    def reconcile(self) -> list[str]:
        """At startup: block this lane's `running` tasks whose runner is gone, instead of waiting for the TTL.

        Only this runner consumes the lane's assignee, so a running task without a live owner PID is orphaned.
        """
        orphans = []
        for task in self.hermes.list_status(self.lane.name, "running"):
            tid = task["id"]
            path = self._state_path(tid)
            state = self._read_state(tid)
            pid = state.get("pid")
            if pid and self.pid_alive(int(pid)):
                continue
            # schtasks /End mata al runner pero no a su `claude`: sin runner no habría quien recoja su resultado, y
            # dejarlo seguir lo haría escribir en el worktree a la vez que el reintento (t_9d33b178, t_761326d7).
            worker = self._live_worker(state)
            if worker:
                log.warning("%s: mato el worker huérfano (pid %s) del runner anterior", tid, worker)
                self._kill(worker)
            session = state.get("session_id")
            self._block(tid, "transient", "runner reiniciado: la tarea quedó en running sin proceso vivo"
                        + (f" (session_id={session}; se puede retomar con --resume)" if session else ""),
                        task, public="runner reiniciado con la tarea en curso")
            path.unlink(missing_ok=True)
            orphans.append(tid)
        return orphans

    def jobs(self) -> list[tuple[str, Callable[[], str]]]:
        """Work for one pass: up to max_parallel ready tasks, as (task_id, callable) for the Service."""
        if self._repo_guard and self.lane.kind == "implement":
            problem = self._repo_guard(self.lane)
            if problem:
                if not self._repo_warned:
                    log.error("candado de repos: %s; el carril no reclama tareas", problem)
                    self._repo_warned = True
                return []
        ready = [t for t in self.hermes.list_ready(self.lane.name) if t["id"] not in self.exclude]
        for t in ready:  # antes del claim: el notificador de Hermes ya duplicó un aviso en la ventana claim -> baja
            self._drop_hermes_subs(t["id"])
        return [(t["id"], lambda t=t: self.process(t)) for t in ready[: self.lane.max_parallel]]

    def run_once(self) -> dict[str, str]:
        """One pass, sequential. Returns {task_id: outcome}."""
        return {tid: job() for tid, job in self.jobs()}

    def _block(self, tid: str, kind: str, reason: str, task: dict | None = None, *, public: str,
               questions: list | None = None, summary: str | None = None, for_oscar: str | None = None,
               notify_kind: str | None = None, changed_files=None) -> str:
        """`reason` (detalle técnico) va a la tarjeta y al log; a Telegram solo `public` (+ preguntas si needs_input)."""
        if for_oscar:  # también a la tarjeta: renotify y /decisiones lo recuperan del motivo del bloqueo
            reason = f"{reason}\n{FOR_OSCAR_PREFIX} {' '.join(str(for_oscar).split())}"
        log.warning("%s bloqueada (%s): %s", tid, kind, reason)
        blocked = self.hermes.block(tid, kind, fit_reason(reason))
        if not blocked:
            log.error("no se pudo bloquear %s (%s) en el kanban: la tarea sigue en running", tid, kind)
        task = task or self._active.get(tid)
        # Botones solo si el bloqueo quedó hecho: Reintentar/Responder hacen `unblock`.
        if kind == "needs_input":
            hint = "responde con un botón o en la tarjeta" if (blocked and self._decisions) else \
                "responde en este hilo o a Hermes"
            # Varias preguntas: en secuencia, una por paso en la misma tarjeta ("Pregunta 1/3"); ver decisions.
            many = len(normalize_questions(questions)) > 1
            step = f"Pregunta 1/{len(normalize_questions(questions))}" if many else None
            self.notify("needs_input", tid, task, status_line(public, step, hint), alert=True,
                        bullets=questions_block((questions or [])[:1] if many else questions), buttons=bool(blocked),
                        questions=questions, changed_files=changed_files,
                        summary=summary, for_oscar=for_oscar)
        else:
            self.notify("blocked", tid, task, status_line("bloqueada", public, "detalle en la tarjeta"), alert=True,
                        buttons=bool(blocked), block_kind=notify_kind or kind)
        return f"blocked:{kind}"

    def process(self, task: dict) -> str:
        tid = task["id"]
        lane = self.lane
        # Ya hay un `claude` vivo sobre esta tarea (runner anterior caído, otro proceso): no se lanza un segundo sobre
        # el mismo worktree (t_761326d7). Sin reclamar: la tarea sigue ready y se reintenta en la próxima pasada.
        alive = self._live_worker(self._read_state(tid))
        if alive and alive != os.getpid():
            log.warning("%s: ya hay un worker vivo (pid %s) sobre la tarea; no se lanza otro", tid, alive)
            return "worker_vivo"
        if not self.hermes.claim(tid, lane.claim_ttl_seconds):
            log.info("%s: claim perdido (otro runner o ya no está ready)", tid)
            return "claim_lost"
        deadline = self.clock() + lane.max_runtime_seconds
        session_id = str(uuid.uuid4())
        state_path = self._state_path(tid)
        # Estado justo tras el claim y antes de cualquier llamada lenta (notify-list de Hermes): sin él, `lanes.py
        # status` veía la tarea en running sin PID y la daba por huérfana (incidente 27-09, tarjeta t_bdee05fe).
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"task_id": tid, "lane": lane.name, "pid": os.getpid(),
                                          "session_id": session_id, "started": time.time()}), encoding="utf-8")
        self._active[tid] = task
        try:
            self._drop_hermes_subs(tid)
            return self._work(task, tid, session_id, deadline)
        finally:
            self._active.pop(tid, None)
            state_path.unlink(missing_ok=True)

    def _work(self, task: dict, tid: str, session_id: str, deadline: float) -> str:
        lane = self.lane
        ops = lane.kind == "ops"
        where = "workspace ops (sin git)" if ops else f"rama lane/{tid}"
        self.hermes.comment(tid, f"LANE claim por {lane.name} · session_id={session_id} · {where}")

        repeated = ""  # nota con las respuestas previas si el worker insiste en una pregunta ya respondida
        with Heartbeat(lambda: self.hermes.heartbeat(tid), lane.heartbeat_seconds):
            try:
                feedback = self.hermes.review_feedback(tid)  # non-empty when the review lane reopened it
                if feedback:
                    task = {**task, "review_feedback": feedback}
                # Respuestas de Oscar a un needs_input anterior (botones de Telegram): el worker debe verlas.
                answers = self.hermes.oscar_answers(tid) if hasattr(self.hermes, "oscar_answers") else []
                if answers:
                    task = {**task, "oscar_answers": answers}
                # Notas del coordinador en la tarjeta ("NO vuelvas a preguntar X"): el worker las ve junto a las respuestas.
                notes = self.hermes.coordinator_notes(tid) if hasattr(self.hermes, "coordinator_notes") else []
                if notes:
                    task = {**task, "coordinator_notes": notes}
                # Aviso previo (antes de gastar la pasada): lo que pide la tarjeta y los límites del carril no
                # permiten. Solo informa, una vez por tarea y arranque; no bloquea ni toca la config.
                warns = [] if tid in self._preflighted else self._preflight(lane, tid, task)
                self.notify("running", tid, task,
                            status_line("en curso", f"aplicando cambios de revisión (ronda {len(feedback)})"
                                        if feedback else None), branch_link=bool(feedback) and not ops,
                            alert=bool(warns), bullets=[f"⚠️ {w}" for w in warns] or None)
                cwd = self.git.prepare_worktree(lane, tid, task) if ops else self.git.prepare_worktree(lane, tid)
                if not ops and hasattr(self.git, "sync_base"):
                    # Antes de repetir nada: la lane al día con origin/<base>. Con conflicto se lo pasamos al worker
                    # (t_985109b9 ×7 reintentos inútiles por una base que se había movido).
                    conflict = self.git.sync_base(lane, cwd)
                    if conflict:
                        task = {**task, "merge_conflict": conflict}
                outcome = self.worker.run(lane, task, cwd, session_id, timeout=max(60.0, deadline - self.clock()))
                resumes = 0
                while not outcome.ok and resumes < lane.max_resumes and deadline - self.clock() > 60:
                    resumes += 1
                    log.info("%s: resume %d tras %s", tid, resumes, outcome.subtype)
                    outcome = self.worker.resume(lane, cwd, session_id, timeout=max(60.0, deadline - self.clock()), task_id=tid)
                # El worker reformuló una pregunta que Oscar ya contestó: se relanza UNA vez con las respuestas; si
                # insiste, pasa a Oscar con las respuestas previas a la vista (la 2ª llamada solo anota, no relanza).
                for _ in range(2):
                    outcome, repeated = self._reask_if_answered(lane, tid, cwd, session_id, deadline, outcome)
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
                if result.get("limitation") == "guard" or looks_like_guard_limit(result.get("summary"), detail):
                    return self._block_console(tid, task, detail, result)
                detail += repeated
                if ops and result.get("proposed_actions"):  # la acción exacta queda en la tarjeta para Oscar
                    detail += "\nAcciones propuestas (no ejecutadas):\n" + "\n".join(
                        f"- [{a.get('id')}] {a.get('description')} · {a.get('tool')} {a.get('arguments')}"
                        for a in result["proposed_actions"])
                return self._block(tid, "needs_input", f"El worker necesita decisión:\n{detail}", task,
                                   public="necesita tu decisión" + (" (repite una pregunta ya respondida; "
                                                                    "respuestas previas en la tarjeta)" if repeated else ""),
                                   questions=asked, summary=result.get("summary"),
                                   for_oscar=result.get("for_oscar"), changed_files=result.get("changed_files"))
            if result["status"] != "done":
                return self._block(tid, "transient", f"worker status={result['status']}: {result.get('summary', '')}",
                                   task, public="el worker no pudo completarla")

            check = self.verifier(lane, tid, cwd, result, task=task) if ops else self.verifier(lane, tid, cwd, result)
            if not check.ok:
                config = [r for r in check.reasons if r.startswith(CONFIG_REASONS)]
                if config:  # t_985109b9 ×7, t_0d94921e ×4: reintentar da siempre lo mismo; hay que cambiar la config
                    return self._block(
                        tid, "transient", "verificación mecánica fallida por CONFIGURACIÓN: " + "; ".join(config),
                        task, notify_kind="config",
                        public="la verificación falla por configuración (" + "; ".join(config)[:300] + "). Reintentar no "
                        "sirve: Oscar/el carril hub debe cambiar lanes.yaml (forbidden_paths, dest_roots, claims) o "
                        "corregir la tarea (quitar esas rutas/destinos) y entonces reabrirla")
                return self._block(tid, "transient", "verificación mecánica fallida: " + "; ".join(check.reasons),
                                   task, public="verificación mecánica fallida")
            if ops:
                return self._to_review_ops(tid, task, session_id, cwd, resumes, outcome, result)

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

    def _preflight(self, lane: Lane, tid: str, task: dict) -> list[str]:
        """Avisos de permisos que faltan (ver preflight.py); quedan también como comentario de la tarjeta."""
        self._preflighted.add(tid)
        try:
            warns = preflight_warnings(lane, task)
            if warns:
                self.hermes.comment(tid, "LANE aviso previo: " + "; ".join(warns))
            return warns
        except Exception as exc:  # el chequeo nunca impide trabajar
            log.warning("%s: chequeo previo falló: %s", tid, exc)
            return []

    def _reask_if_answered(self, lane: Lane, tid: str, cwd: str, session_id: str, deadline: float, outcome):
        """(outcome, nota). Si el worker pide decisión sobre algo que Oscar YA contestó en la tarjeta (otra redacción),
        no se bloquea: se reanuda una vez con las respuestas. Si insiste, la nota las lista para la tarjeta."""
        res = outcome.structured if getattr(outcome, "ok", False) else None
        finder = getattr(self.hermes, "answered_for", None)
        if not res or res.get("status") != "needs_input" or not finder:
            self._reasked.discard(tid)
            return outcome, ""
        asked = normalize_questions(res.get("questions"))
        found = finder(tid, asked) if asked else None
        if not found:
            return outcome, ""
        pairs = "\n".join(f"- {q} -> {a}" for q, a in found)
        if tid in self._reasked or deadline - self.clock() <= 60:
            return outcome, "\nRespuestas previas de Oscar a lo mismo:\n" + pairs
        self._reasked.add(tid)
        self.hermes.comment(tid, "LANE: el worker repitió una pregunta ya respondida; se relanza una vez con las respuestas.")
        prompt = ("Oscar YA respondió a lo que preguntas (aunque con otras palabras). No vuelvas a preguntarlo: "
                  "aplica estas decisiones, termina la tarea y devuelve el JSON del schema.\n\n" + pairs)
        return self.worker.resume(lane, cwd, session_id, timeout=max(60.0, deadline - self.clock()), task_id=tid,
                                  prompt=prompt), ""

    def _block_console(self, tid: str, task: dict | None, detail: str, result: dict) -> str:
        """Limitación PERMANENTE del guard (no es una pregunta): bloqueo needs_input con motivo estructurado. Ni la
        autocuración ni Reintentar la devuelven al carril; el aviso (una vez) dice qué comando usar."""
        reason = f"{GUARD_LIMIT_MARK} el worker no puede hacerlo con el guard del carril:\n{detail}"
        log.warning("%s bloqueada (limitacion_guard): %s", tid, reason)
        blocked = self.hermes.block(tid, "needs_input", fit_reason(reason))
        if not blocked:
            log.error("no se pudo bloquear %s (limitacion_guard) en el kanban: la tarea sigue en running", tid)
        self.notify("blocked", tid, task or self._active.get(tid), status_line(
            "requiere consola", f"el guard no deja hacerlo; en una consola interactiva: /tomar-tarea {tid}",
            "detalle en la tarjeta"), alert=True, buttons=bool(blocked), block_kind="console",
            summary=result.get("summary"), for_oscar=result.get("for_oscar"))
        return "blocked:limitacion_guard"

    def _to_review_ops(self, tid, task, session_id, cwd, resumes, outcome, result) -> str:
        """Ops nunca se marca done sola: siempre review, y Oscar valida con [✅ Validar / 🔁 Pedir cambios]."""
        metadata = {"lane": self.lane.name, "kind": "ops", "session_id": session_id, "workspace": cwd,
                    "resumes": resumes, "cost_usd": outcome.cost_usd, "verified": True,
                    **{k: result.get(k) for k in ("evidence", "proposed_actions", "next_steps", "risks")}}
        ok, err = self.hermes.request_review(tid, result.get("summary", "")[:1500], metadata)
        if not ok:
            return self._block(tid, "transient", f"request-review rechazado: {err}", task,
                               public="el kanban rechazó el paso a review")
        n = len(result.get("evidence") or [])
        paths = [str(ev.get("path") or (ev.get("value") if ev.get("type") == "path" else "") or "")
                 for ev in result.get("evidence") or [] if isinstance(ev, dict)]
        self.notify("review", tid, task, status_line(
            "pendiente de tu validación", f"{n} evidencia{'s' if n != 1 else ''}",
            money(outcome.cost_usd)), branch_link=False, buttons=True, alert=True,
            bullets=[f"📁 {p}" for p in paths if p][:3])
        return "review"
