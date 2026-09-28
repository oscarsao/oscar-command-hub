"""Review lane: read-only claude review of lane/<id> -> `complete` (done) or `reopen-review` (back to the lane).

Never merges. Uses reopen-review, not request-changes: in hermes 0.21.4 request-changes needs a review run
claimed from `review`, and the CLI `claim` only takes `ready` tasks.
"""
from __future__ import annotations

import logging
import subprocess

from . import proc as _proc
import time
import uuid
from pathlib import Path
from typing import Callable

from .config import ROOT, Lane
from .hermes import REVIEW_AUTHOR
from .notices import (MessageStore, TaskNotices, files_label, money, questions_block, render, status_line,
                      test_label)
from .telegram import telegram_target
from .worker import WorkerOutcome, render_settings, run_claude

log = logging.getLogger("agent_lanes")

REVIEW_SCHEMA_PATH = ROOT / "contract" / "review.schema.json"
REVIEW_SETTINGS = ROOT / "contract" / "reviewer-settings.json"
CHANGES_PREFIX = "CAMBIOS"
READ_ONLY_TOOLS = ("Read", "Grep", "Glob", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)",
                   "Bash(git status:*)")
WRITE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


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
                 messages: MessageStore | None = None, links: Callable | None = None, decisions=None):
        self.lane = review_lane
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
               block_kind: str | None = None, questions=None, summary: str | None = None) -> None:
        """Edita el mensaje único de la tarea (o envía uno nuevo si `alert`). `status` es texto público."""
        try:
            # Reviewed lane's destination (brand topic), else the review lane's own, else the .env default.
            reviewed = self.lanes.get(task.get("assignee"))
            lane_target = (reviewed.telegram if reviewed else None) or self.lane.telegram
            name = reviewed.name if reviewed else self.lane.name
            links = self._links(reviewed, task["id"], changed_files=changed_files) if (self._links and reviewed) else []
            text = render(state, task["id"], task.get("title"), name, status, links, bullets, body=task.get("body"))
            markup = None
            if buttons and self._decisions and reviewed:
                markup = self._decisions.markup(state, task=task, lane=reviewed, block_kind=block_kind,
                                                questions=questions, summary=summary, changed_files=changed_files)
            self._notices.publish(task["id"], text, telegram_target(task.get("body")), lane_target, alert=alert,
                                  reply_markup=markup)
        except Exception as exc:
            log.warning("aviso a Telegram falló: %s", exc)

    def jobs(self) -> list[tuple[str, Callable[[], str]]]:
        found = []
        for name, lane in self.lanes.items():
            h = self.hermes_for(lane.board)
            found += [(t, lane, h) for t in h.list_status(name, "review")]
        return [(t["id"], lambda t=t, l=l, h=h: self.process(t, l, h)) for t, l, h in found[: self.lane.max_parallel]]

    def run_once(self) -> dict[str, str]:
        return {tid: job() for tid, job in self.jobs()}

    def _block(self, h, task: dict, kind: str, reason: str, *, public: str,
               questions: list[str] | None = None) -> str:
        """`reason` (detalle técnico) va a la tarjeta y al log; a Telegram solo `public` (+ preguntas si needs_input)."""
        log.warning("%s bloqueada en review (%s): %s", task["id"], kind, reason)
        blocked = h.block(task["id"], kind, reason[:1500])
        if not blocked:  # block solo mueve running/ready: una tarea en review puede no quedar bloqueada
            log.error("no se pudo bloquear %s (%s) en el kanban", task["id"], kind)
        if kind == "needs_input":
            self.notify("needs_input", task, status_line(public, "responde en este hilo o a Hermes"), alert=True,
                        bullets=questions_block(questions), buttons=bool(blocked), questions=questions)
        else:
            self.notify("blocked", task, status_line("bloqueada en review", public, "detalle en la tarjeta"),
                        alert=True, buttons=bool(blocked), block_kind=kind)
        return f"blocked:{kind}"

    def process(self, task: dict, lane: Lane, h) -> str:
        tid = task["id"]
        try:
            show = h.show(tid)
            task = {**show.get("task", {}), **task}
            meta = implementation_metadata(show)
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
            ok, err = h.complete(tid, f"Aprobada por revisión: {verdict.get('summary', '')}"[:1500], metadata)
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
                summary=verdict.get("summary") or meta.get("summary"))
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


def sweep_done(lane: Lane, h, git) -> list[str]:
    """Remove worktree + local branch of done tasks (approved by the review lane or by Oscar)."""
    cleaned = []
    for task in h.list_status(lane.name, "done"):
        if (Path(lane.worktree_root) / f"lane-{task['id']}").exists():
            ok, msg = git.cleanup(lane, task["id"])
            if ok:
                cleaned.append(task["id"])
            else:
                log.warning("%s: limpieza pendiente: %s", task["id"], msg)
    return cleaned
