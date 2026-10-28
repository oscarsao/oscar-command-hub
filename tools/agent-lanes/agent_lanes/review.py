"""Review lane: read-only claude review of lane/<id> -> `complete` (done) or `reopen-review` (back to the lane).

Never merges. Uses reopen-review, not request-changes: in hermes 0.21.4 request-changes needs a review run
claimed from `review`, and the CLI `claim` only takes `ready` tasks.
"""
from __future__ import annotations

import json
import logging

from . import avisos_revision, canal_avisos, resumen_carriles
from . import proc as _proc
import time
import uuid
from pathlib import Path
from typing import Callable

from . import telegram_lane
from .config import ROOT, Lane
from .hermes import OSCAR_AUTHOR, REVIEW_AUTHOR
from .integration import CHANNEL, link_line, render_ficha, repo_name, risk_of, risks_from_files
from .notices import (MessageStore, TaskNotices, files_label, fit_reason, money, questions_block, render, status_line,
                      test_label, truncate)
from .runner import dm_mirror
from .telegram import telegram_target
from .worker import WorkerOutcome, render_settings, run_claude

log = logging.getLogger("agent_lanes")

REVIEW_SCHEMA_PATH = ROOT / "contract" / "review.schema.json"
REVIEW_SETTINGS = ROOT / "contract" / "reviewer-settings.json"
CERRADAS = ("done", "archived")  # tarjetas que el carril de revisión no debe tocar
CHANGES_PREFIX = "CAMBIOS"
READ_ONLY_TOOLS = ("Read", "Grep", "Glob", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
                   "Bash(git status:*)")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
APPROVED_PREFIX = "APROBADO-OSCAR"  # = integrator.APPROVED_PREFIX / decisions._approve
COMPLETE_INTENTOS = 3  # intentos de `hermes kanban complete` antes de aparcar la tarjeta por fallo transitorio
COMPLETE_ESPERA = 10  # s; espera creciente: 10 s antes del 2.º intento, 20 s antes del 3.º
RECHEQUEO_ESCALADA = 120  # s entre relecturas de una tarjeta escalada (¿cabeza nueva? ¿aprobada?) al elegir trabajo
SIN_COMMITS = "la rama no tiene commits sobre la base; empuja el trabajo"


def aprobada_por_oscar(show: dict) -> bool:
    """APROBADO-OSCAR de Oscar posterior a la última petición de review (`review_requested`): la aprobó desde la
    escalada y el PR ya está abierto. Si el implementador vuelve a pedir review después, la aprobación ya no cuenta."""
    from . import aprobaciones
    events = (show or {}).get("events") or []
    last_request = max((e.get("created_at") or 0 for e in events if e.get("kind") == "review_requested"), default=0)
    tid = str(((show or {}).get("task") or {}).get("id") or "")
    # P08 (F1-07): el comentario no basta (lo puede escribir cualquiera con la CLI): hace falta el registro local
    # del bot de carriles para ese PR.
    return any(c.get("author") == OSCAR_AUTHOR and (c.get("body") or "").startswith(APPROVED_PREFIX)
               and (c.get("created_at") or 0) >= last_request
               and aprobaciones.aprobado(tid, aprobaciones.pr_de_comentario(c.get("body")))
               for c in (show or {}).get("comments") or [])


def implementation_metadata(show: dict) -> dict:
    """Metadata of the latest implementer handoff (a later closing run may carry none)."""
    for run in reversed(show.get("runs") or []):
        meta = run.get("metadata") or {}
        if meta.get("head_sha") and meta.get("branch"):
            return meta
    return {}


def review_prompt(lane: Lane, task: dict, meta: dict) -> str:
    return (
        f"Revisa la tarea {task['id']} del carril {lane.name} (repo {lane.repo}).\n"
        f"Estás en su worktree, en la rama {meta['branch']} @ {meta['head_sha']}. Base: {lane.base_ref}.\n"
        f"Mira el diff con `git diff {lane.base_ref}...HEAD` y lee los archivos que necesites. NO modifiques nada.\n\n"
        f"Resumen del implementador: {meta.get('summary') or task.get('result') or '(sin resumen)'}\n"
        f"Archivos declarados: {', '.join(meta.get('changed_files') or []) or '(ninguno)'}\n\n"
        f"# Tarea: {task.get('title', '')}\n\n{task.get('body') or ''}\n"
    )


def review_args(review_lane: Lane, session_flag: list[str]) -> list[str]:
    return [
        "claude", "-p", *session_flag,
        "--settings", str(render_settings(REVIEW_SETTINGS)),
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--permission-mode", "default",           # -p: anything not allowed below is denied
        "--allowedTools", *READ_ONLY_TOOLS,
        "--disallowedTools", *WRITE_TOOLS,
        "--append-system-prompt-file", str(review_lane.role_path),
        "--json-schema", REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"),
        "--max-budget-usd", str(review_lane.max_budget_usd),
        "--model", review_lane.model,
        "--effort", review_lane.effort,
        "--output-format", "json",
    ]


class ClaudeReviewer:
    def __init__(self, runner=_proc.run):
        self._run = runner

    def _env(self, review_lane: Lane, task: dict) -> dict:
        return {"AGENT_LANES_TASK": task["id"], "AGENT_LANES_LANE": review_lane.name, "AGENT_LANES_ROLE": "revisor"}

    def run(self, review_lane, lane, task, meta, cwd, session_id, timeout) -> WorkerOutcome:
        args = review_args(review_lane, ["--session-id", session_id, "--name", f"review-{task['id']}"])
        return run_claude(self._run, args, review_prompt(lane, task, meta), cwd, timeout, self._env(review_lane, task))

    def resume(self, review_lane, lane, task, cwd, session_id, timeout) -> WorkerOutcome:
        return run_claude(self._run, review_args(review_lane, ["--resume", session_id]),
                          "Termina la revisión y devuelve el JSON del schema.", cwd, timeout,
                          self._env(review_lane, task))


class ReviewRunner:
    def __init__(self, review_lane: Lane, lanes: dict[str, Lane], *, hermes_for: Callable[[str], object], git,
                 reviewer, verifier, notify, clock: Callable[[], float] = time.monotonic,
                 messages: MessageStore | None = None, links: Callable | None = None, decisions=None,
                 integration=None, tree: Callable | None = None, state_dir: Path | str | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.lane = review_lane
        self._sleep = sleep
        # Tareas en review que NO se pudieron bloquear (h.block solo mueve running/ready): se recuerda la cabeza ya
        # avisada para no reprocesarlas ni repetir la alerta en cada vuelta. state_dir=None: solo en memoria.
        self._unblockable: dict[str, str] = {}
        self._heads: dict[str, str] = {}
        self._aparcadas: dict[str, tuple[str, float]] = {}  # tid -> (motivo, instante): escaladas que no se relanzan
        self._state_dir = Path(state_dir) if state_dir else None
        self._tree = tree  # deps.TreeReader: líneas 🔗/⏸/↳ en los avisos; None = sin árbol
        # IntegrationRoute (tema de Integración): la tarjeta done con ✅ Aprobar va allí como ficha + copia en el DM.
        self._integration = integration
        self._decisions = decisions  # DecisionDesk (botones) solo con CARRILES_BOT_TOKEN
        self.lanes = {n: l for n, l in lanes.items() if n in review_lane.reviews}
        self.hermes_for = hermes_for
        self.git = git
        self.reviewer = reviewer
        self.verifier = verifier
        self._notices = TaskNotices(notify, messages)
        self._links = links
        self.clock = clock

    def notify(self, state: str, task: dict, status: str, *, alert: bool = False,
               bullets: list[str] | None = None, changed_files=None, buttons: bool = False,
               block_kind: str | None = None, questions=None, summary: str | None = None,
               for_oscar: str | None = None, yes_no: bool = True, test_exit=None, deps=None) -> None:
        """Edita el mensaje único de la tarea (o envía uno nuevo si `alert`). `status` es texto público."""
        target = telegram_target(task.get("body"))
        alert = avisos_revision.interceptar(state, task, status, alert, summary=summary, for_oscar=for_oscar)
        mirror = dm_mirror(self._decisions, target, alert, state)
        for attempt in (1, 2):  # un aviso que falla se reintenta UNA vez
            try:
                # Reviewed lane's destination (brand topic), else the review lane's own, else the .env default.
                reviewed = telegram_lane.lane_de_tarjeta(self.lanes, task)
                lane_target = (reviewed.telegram if reviewed else None) or self.lane.telegram
                name = reviewed.name if reviewed else self.lane.name
                links = self._links(reviewed, task["id"], changed_files=changed_files) if (self._links and reviewed) else []
                integ = self._integration if (state == "done" and reviewed) else None
                # La ficha de Integración ya lista los padres pendientes en "Depende de": sin ⏸ repetido.
                tree = (self._tree(reviewed.board, task["id"], with_waiting=not integ)
                        if (self._tree and reviewed) else None)
                if integ:
                    text = self._ficha(reviewed, task, status, links, changed_files, for_oscar, test_exit, deps, tree)
                else:
                    text = render(state, task["id"], task.get("title"), name, status, links, bullets,
                                  body=task.get("body"), for_oscar=for_oscar, tree=tree)
                markup = None
                if buttons and self._decisions and reviewed:
                    markup = self._decisions.markup(state, task=task, lane=reviewed, block_kind=block_kind,
                                                    questions=questions, summary=summary, changed_files=changed_files,
                                                    for_oscar=for_oscar, yes_no=yes_no)
                if integ:  # tema de Integración (nunca el origen) + copia espejo en el DM de Oscar
                    owner = str(getattr(self._decisions, "owner_id", "") or "") if alert else ""
                    prefix = f"✅ {task['id']} · {truncate(task.get('title'), 40)} · lista para aprobar"
                    canal_avisos.publicar(canal_avisos.de(self._decisions), self._notices, "aprobar", task["id"], text,
                                          None, integ.target, alert=alert, reply_markup=markup, mirror=owner or None,
                                          channel=CHANNEL, leave_behind=lambda sent: link_line(prefix, sent))
                    return
                canal_avisos.publicar(canal_avisos.de(self._decisions), self._notices, canal_avisos.tipo_de_estado(state),
                                      task["id"], text, target, lane_target, alert=alert, reply_markup=markup,
                                      mirror=mirror, followup=list(getattr(bullets, "overflow", None) or ()) or None)
                return
            except Exception as exc:
                log.warning("aviso a Telegram falló (intento %d/2): %s", attempt, exc)

    def _ficha(self, lane: Lane, task: dict, status: str, links, changed_files, for_oscar, test_exit, deps,
               tree=None) -> str:
        """Ficha de la tarjeta done (antes del PR): riesgos deducidos de los archivos, ya que aún no hay gates."""
        policy = self._integration.policies.get(lane.name)
        gates = ["✔ revisión aprobada"]
        if test_exit is not None:
            gates.append("✔ tests OK" if test_exit == 0 else f"⛔ tests exit {test_exit}")
        return render_ficha(risk=risk_of(policy), phase="PARA APROBAR", tid=task["id"], title=task.get("title"),
                            repo=repo_name(lane), base=lane.base, status=status, for_oscar=for_oscar, gates=gates,
                            risks=risks_from_files(policy, changed_files), deps=deps or (), links=links,
                            tree=tree)

    def _deps(self, h, tid: str, show: dict) -> list[str]:
        """Dependencias sin integrar (enlaces padre del kanban), solo si hay tema de Integración. Nunca lanza."""
        if not self._integration:
            return []
        try:
            from .deps import pending_parents
            return [f"{w['id']} · {truncate(w['title'], 40)} · {w['reason']}"
                    for w in pending_parents(h, tid, show)[:5]]
        except Exception as exc:
            log.info("%s: no se pudieron leer las dependencias: %s", tid, exc)
            return []

    def jobs(self) -> list[tuple[str, Callable[[], str]]]:
        found = []
        for name, lane in self.lanes.items():
            h = self.hermes_for(lane.board)
            # Carril de varios repos (claude-telegram): se revisa en el repo que eligió la tarjeta.
            found += [(t, telegram_lane.lane_de_tarjeta({name: lane}, {**t, "assignee": name}), h)
                      for t in h.list_status(name, "review")]
        # Escaladas sin cambios o ya aprobadas por Oscar: fuera ANTES del tope, para que no ocupen la plaza en bucle
        # (t_cce5da81: t_fc0c293d se lanzó 97 veces, una cada ~30 s, tapando el resto del carril).
        found = [(t, l, h) for t, l, h in found if not self._aparcada(h, t["id"])]
        return [(t["id"], lambda t=t, l=l, h=h: self.process(t, l, h)) for t, l, h in found[: self.lane.max_parallel]]

    def _escalada(self, tid: str) -> bool:
        path = self._unblockable_path(tid)
        return tid in self._unblockable or bool(path and path.exists())

    def _aparcada(self, h, tid: str) -> str | None:
        """Tarjeta escalada que el carril no debe relanzar: 'aprobada' (APROBADO-OSCAR posterior a la última petición
        de review: la lleva el integrador) o 'escalada' (la misma cabeza ya se avisó con botones). None = se revisa.
        Solo relee (show) las que tienen escalada guardada, y como mucho cada RECHEQUEO_ESCALADA segundos."""
        if not self._escalada(tid):
            return None
        now = self.clock()
        cached = self._aparcadas.get(tid)
        if cached and now - cached[1] < RECHEQUEO_ESCALADA:
            return cached[0]
        try:
            show = h.show(tid)
        except Exception:
            return "escalada"  # sin poder leerla, sigue como estaba: escalada (se relee en la próxima)
        if aprobada_por_oscar(show):
            motivo = "aprobada"
        else:
            motivo = "escalada" if self._already_escalated(tid, str(implementation_metadata(show).get("head_sha") or "")) \
                else None
        if motivo:
            if not cached:
                log.info("%s: escalada en review (%s); el carril review no la relanza", tid, motivo)
            self._aparcadas[tid] = (motivo, now)
        else:
            self._aparcadas.pop(tid, None)
        return motivo

    def run_once(self) -> dict[str, str]:
        return {tid: job() for tid, job in self.jobs()}

    def _block(self, h, task: dict, kind: str, reason: str, *, public: str,
               questions: list[str] | None = None) -> str:
        """`reason` (detalle técnico) va a la tarjeta y al log; a Telegram solo `public` (+ preguntas si needs_input)."""
        closed = self._cerrada(h, task["id"])  # la revisión es larga: puede haberse cerrado mientras tanto
        if closed:
            log.info("%s ya está %s: no se bloquea (%s): %s", task["id"], closed, kind, reason)
            return f"skip:{closed}"
        log.warning("%s bloqueada en review (%s): %s", task["id"], kind, reason)
        blocked = h.block(task["id"], kind, fit_reason(reason))
        if not blocked:  # block solo mueve running/ready: una tarea en review puede no quedar bloqueada
            log.error("no se pudo bloquear %s (%s) en el kanban", task["id"], kind)
            # La escalada lleva [✅ Aprobar] (abre PR): con la rama sin commits sobre la base no se pregunta a Oscar,
            # la tarjeta vuelve al carril (t_cce5da81: t_dd4f5d1c y t_faf4e4c2, «No commits between» 6 veces).
            if (devuelta := self._sin_commits(h, task)) is not None:
                return devuelta
            # 9 alertas sin botones seguidas (escalada de review): se anota la cabeza y se emite UNA con botones.
            self._remember_unblockable(task["id"], kind)
            self.notify("blocked", task, status_line(f"en review, requiere tu decisión: {public}", "detalle en la tarjeta"),
                        alert=True, buttons=True, block_kind="review_stuck", bullets=questions_block(questions))
            return f"blocked:{kind}"
        if kind == "needs_input":
            self.notify("needs_input", task, status_line(public, "responde en este hilo o a Hermes"), alert=True,
                        bullets=questions_block(questions), buttons=bool(blocked), questions=questions,
                        yes_no=False)  # escalada: las "preguntas" son cambios pedidos; "Sí" sería ambiguo
        else:
            self.notify("blocked", task, status_line("bloqueada en review", public, "detalle en la tarjeta"),
                        alert=True, buttons=bool(blocked), block_kind=kind)
        return f"blocked:{kind}"

    def _sin_commits(self, h, task: dict) -> str | None:
        """Rama lane/<id> con 0 commits sobre la base: si ya hay un PR fusionado, se cierra (integrada); si no, vuelve
        al carril con el motivo. None = tiene commits o no se sabe (se escala como siempre). Nunca lanza."""
        tid = task["id"]
        lane = telegram_lane.lane_de_tarjeta(self.lanes, task)
        ahead_fn = getattr(self.git, "commits_ahead", None)
        if lane is None or ahead_fn is None:
            return None
        try:
            if ahead_fn(lane, tid) != 0:
                return None
            merged_fn = getattr(self.git, "merged_pr", None)
            merged = merged_fn(lane, tid) if merged_fn else None
        except Exception as exc:
            log.info("%s: no se pudo contar los commits de la rama: %s", tid, exc)
            return None
        if merged:
            ok, err = h.complete(tid, f"Ya integrada: {merged} fusionado; la rama no aporta nada sobre {lane.base}",
                                 {"integrated_pr": merged})
            if ok:
                log.info("%s: rama sin commits sobre %s y PR fusionado (%s): cerrada sin preguntar", tid, lane.base,
                         merged)
                return "done:integrada"
            log.warning("%s: no se pudo cerrar la tarjeta ya integrada: %s", tid, err)
            return None
        h.comment(tid, f"{SIN_COMMITS} (lane/{tid} no tiene commits sobre {lane.remote}/{lane.base}); "
                       "no se pide decisión a Oscar", author=REVIEW_AUTHOR)
        if not h.reopen_review(tid):
            log.warning("%s: rama sin commits pero no se pudo devolver al carril; se escala", tid)
            return None
        log.info("%s: rama sin commits sobre %s: devuelta al carril sin preguntar a Oscar", tid, lane.base)
        self.notify("changes", task, status_line("la rama no tiene commits sobre la base", "vuelve al carril"))
        return "changes:sin_commits"

    def _complete_con_reintentos(self, h, tid: str, result: str, metadata: dict) -> tuple[bool, str]:
        """`complete` con hasta COMPLETE_INTENTOS intentos y espera creciente: un timeout de hermes (120 s) no debe
        aparcar la tarjeta (t_104416af, 2 h en review). Antes de cada intento se relee la tarjeta: si el complete
        anterior sí llegó a aplicarse (done/archived), no hay nada que hacer."""
        err = ""
        for intento in range(COMPLETE_INTENTOS):
            if intento:
                self._sleep(COMPLETE_ESPERA * intento)
                if (cerrada := self._cerrada(h, tid)):
                    log.info("%s: complete reintentado y la tarjeta ya está %s", tid, cerrada)
                    return True, ""
            ok, err = h.complete(tid, result, metadata)
            if ok:
                return True, ""
            log.warning("%s: complete rechazado (intento %d/%d): %s", tid, intento + 1, COMPLETE_INTENTOS, err)
        if self._cerrada(h, tid):
            return True, ""
        return False, err

    @staticmethod
    def _cerrada(h, tid: str) -> str | None:
        """Estado `done`/`archived` si la tarjeta ya está cerrada (se relee del kanban); None si no, o si no se lee."""
        try:
            status = (h.show(tid).get("task") or {}).get("status")
        except Exception:
            return None
        return status if status in CERRADAS else None

    def _unblockable_path(self, tid: str) -> Path | None:
        return self._state_dir / f"{tid}.json" if self._state_dir else None

    def _remember_unblockable(self, tid: str, kind: str) -> None:
        head = self._heads.get(tid) or ""
        self._unblockable[tid] = head
        path = self._unblockable_path(tid)
        try:
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"task_id": tid, "head_sha": head, "kind": kind, "at": time.time()}),
                                encoding="utf-8")
        except OSError as exc:
            log.warning("%s: no se pudo guardar el estado de la alerta de review: %s", tid, exc)

    def _already_escalated(self, tid: str, head: str) -> bool:
        if self._unblockable.get(tid) == head:
            return True
        path = self._unblockable_path(tid)
        try:
            return bool(path and json.loads(path.read_text(encoding="utf-8")).get("head_sha") == head)
        except (OSError, json.JSONDecodeError):
            return False

    def process(self, task: dict, lane: Lane, h) -> str:
        tid = task["id"]
        try:
            show = h.show(tid)
            task = {**show.get("task", {}), **task}
            if task.get("status") in CERRADAS:  # el integrador la cerró entre el listado y ahora: no se toca
                return f"skip:{task['status']}"
            if aprobada_por_oscar(show):  # Oscar ya la aprobó (PR abierto): es del integrador, review no la toca
                return "skip:aprobada"
            meta = implementation_metadata(show)
            self._heads[tid] = str(meta.get("head_sha") or "")
            if self._already_escalated(tid, self._heads[tid]):
                return "blocked:already_escalated"  # misma cabeza ya avisada y sin poder bloquearse: no se repite
            if (path := self._unblockable_path(tid)) and path.exists():  # cabeza nueva: la escalada vieja ya no vale
                path.unlink(missing_ok=True)
            self._unblockable.pop(tid, None)
            if not meta:
                return self._block(h, task, "transient", "review sin metadata de implementación (branch/head_sha)",
                                   public="falta la metadata de implementación")
            cwd = self.git.prepare_review_worktree(lane, tid)
            check = self.verifier(lane, tid, cwd, {"branch": meta["branch"], "head_sha": meta["head_sha"]})
            if not check.ok:
                return self._block(h, task, "transient", "re-verificación mecánica fallida: " + "; ".join(check.reasons),
                                   public="re-verificación mecánica fallida")
            deadline = self.clock() + self.lane.max_runtime_seconds
            session_id = str(uuid.uuid4())
            outcome = self.reviewer.run(self.lane, lane, task, meta, cwd, session_id, max(60.0, deadline - self.clock()))
            resumes = 0
            while not outcome.ok and resumes < self.lane.max_resumes and deadline - self.clock() > 60:
                resumes += 1
                outcome = self.reviewer.resume(self.lane, lane, task, cwd, session_id, max(60.0, deadline - self.clock()))
        except Exception as exc:
            return self._block(h, task, "transient", f"error del carril review: {exc}",
                               public="error del carril review")
        if not outcome.ok:
            return self._block(h, task, "transient", f"el revisor no terminó: {outcome.raw_error} (session_id={session_id})",
                               public="el revisor no terminó (tiempo o presupuesto agotado)")

        verdict = outcome.structured
        if verdict["status"] == "approve":
            metadata = {**meta, "review": {"status": "approve", "summary": verdict.get("summary"),
                                           "findings": verdict.get("findings"), "session_id": session_id,
                                           "cost_usd": outcome.cost_usd}}
            ok, err = self._complete_con_reintentos(h, tid, f"Aprobada por revisión: {verdict.get('summary', '')}"[:1500],
                                                    metadata)
            if not ok:
                return self._block(h, task, "transient", f"complete rechazado: {err}",
                                   public="el kanban rechazó el cierre")
            cleaned, msg = self.git.cleanup(lane, tid)
            if not cleaned:  # detalle técnico solo al log y a la tarjeta
                log.warning("%s: limpieza local pendiente: %s", tid, msg)
                h.comment(tid, f"Limpieza local pendiente: {msg}"[:1500])
            cost = sum(float(c or 0) for c in (meta.get("cost_usd"), outcome.cost_usd))
            self.notify("done", task, status_line(
                "review aprobada", files_label(len(meta.get("changed_files") or [])),
                test_label(check.test_exit), money(cost), "lista para merge",
                None if cleaned else "limpieza local pendiente"), alert=True,
                changed_files=meta.get("changed_files"), buttons=True,
                summary=verdict.get("summary") or meta.get("summary"), for_oscar=meta.get("for_oscar"),
                test_exit=check.test_exit, deps=self._deps(h, tid, show))
            return "done"

        changes = verdict.get("required_changes") or [verdict.get("summary", "")]
        # Rounds = review_reopened events (what the implementer actually got back), not comments: a comment whose
        # reopen failed must not count, nor be posted twice.
        events = show.get("events") or []
        rounds_done = sum(1 for e in events if e.get("kind") == "review_reopened")
        last_request = max((e.get("created_at") or 0 for e in events if e.get("kind") == "review_requested"), default=0)
        pending = any(c.get("author") == REVIEW_AUTHOR and c.get("body", "").startswith(CHANGES_PREFIX)
                      and (c.get("created_at") or 0) >= last_request for c in (show.get("comments") or []))
        rnd = rounds_done + 1
        if rounds_done >= self.lane.max_review_rounds:
            return self._block(h, task, "needs_input",
                               f"{rnd}ª petición de cambios: decide Oscar.\n" + "\n".join(f"- {c}" for c in changes),
                               public=f"{rnd}ª petición de cambios: decides tú", questions=changes)
        if not pending:
            body = f"{CHANGES_PREFIX} (ronda {rnd}) pedidos por revisión:\n" + "\n".join(f"- {c}" for c in changes)
            if not h.comment(tid, body[:3000], author=REVIEW_AUTHOR):
                return self._block(h, task, "transient", "no se pudo publicar el comentario de cambios; no se reabre",
                                   public="no se pudo publicar el comentario de cambios")
        if not h.reopen_review(tid):
            return self._block(h, task, "transient", "reopen-review rechazado (el comentario de cambios ya está publicado)",
                               public="el kanban rechazó la reapertura")
        # Los cambios pedidos quedan como comentario en la tarjeta; aquí solo el recuento.
        self.notify("changes", task, status_line(
            f"cambios pedidos (ronda {rnd})", f"{len(changes)} cambio" + ("" if len(changes) == 1 else "s"),
            "vuelve al carril"), changed_files=meta.get("changed_files"))
        return "changes"


REINTENTO_LIMPIEZA = 6 * 3600  # s entre intentos de limpiar la misma tarjeta tras un fallo (1.386 líneas de log, 28-10)
_limpieza_fallida: dict[str, float] = {}  # tid -> instante del último intento fallido


def sweep_done(lane: Lane, h, git, clock: Callable[[], float] = time.monotonic) -> list[str]:
    """Remove worktree + local branch of done tasks (approved by the review lane or by Oscar). Una limpieza fallida
    no se reintenta hasta pasadas REINTENTO_LIMPIEZA s: una línea de log por intento, no por pasada."""
    cleaned = []
    for task in h.list_status(lane.name, "done"):
        if (Path(lane.worktree_root) / f"lane-{task['id']}").exists():
            ultimo = _limpieza_fallida.get(task["id"])
            if ultimo is not None and clock() - ultimo < REINTENTO_LIMPIEZA:
                continue
            ok, msg = git.cleanup(lane, task["id"])
            if ok:
                _limpieza_fallida.pop(task["id"], None)
                cleaned.append(task["id"])
            else:
                _limpieza_fallida[task["id"]] = clock()
                log.warning("%s: limpieza pendiente: %s", task["id"], msg)
                resumen_carriles.anotar("limpieza", f"{task['id']}: {msg}", clave=task["id"])  # F1-04: 1 vez al día
    return cleaned
