"""Reenvía con el bot de carriles (con botones) los avisos que esperan una decisión de Oscar y salieron antes con el
bot de Hermes, sin botones. No toca el estado de ninguna tarea: solo publica el aviso otra vez.

    py -3.12 lanes.py renotify [--lane X] [--task t_id] [--dry-run] [--force]

- blocked needs_input -> ❓ con las preguntas (metadata del último run o viñetas del motivo del bloqueo) y sus botones
- blocked transient   -> ⛔ [Reintentar] [Aparcar]
- done aprobada por el carril review, rama lane/<id> viva y sin fusionar, sin APROBADO-OSCAR ni INTEGRADO
                      -> ✅ [Aprobar] [Pedir cambios] [Aparcar]

El aviso viejo es del bot de Hermes: el bot de carriles no puede editarlo ni borrarlo, así que se envía uno NUEVO y
.state/messages apunta a él. Los teclados quedan en .state/callbacks, donde los lee el runner que corre como servicio.
Una tarea cuyo aviso ya es del bot de carriles se salta (sin --force): relanzar el comando no duplica avisos.
"""
from __future__ import annotations

import argparse
import ast
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from . import proc as _proc
from .config import Lane
from .decisions import keyboard_spec
from .hermes import OSCAR_AUTHOR
from .notices import (
    MessageStore,
    files_label,
    money,
    normalize_questions,
    questions_block,
    status_line,
    test_label,
)
from .review import ReviewRunner, implementation_metadata
from .runner import LaneRunner
from .telegram import resolve_target, telegram_target

log = logging.getLogger("agent_lanes")

APPROVED_PREFIX = "APROBADO-OSCAR"  # = integrator.APPROVED_PREFIX / decisions._approve
INTEGRATED_PREFIX = "INTEGRADO"     # = integrator.INTEGRATED_PREFIX
EMOJI = {"needs_input": "❓", "blocked": "⛔", "done": "✅"}
NEEDS_HINT = "responde con un botón o en la tarjeta"

# Motivo técnico del bloqueo (tarjeta) -> frase pública que usó el runner/review al bloquear. Nada más va a Telegram.
PUBLIC_REASONS = (
    ("runner reiniciado", "runner reiniciado con la tarea en curso"),
    ("error del runner", "error del runner"),
    ("el worker no terminó", "el worker no terminó (tiempo o presupuesto agotado)"),
    ("worker status=", "el worker no pudo completarla"),
    ("verificación mecánica fallida", "verificación mecánica fallida"),
    ("request-review rechazado", "el kanban rechazó el paso a review"),
    ("review sin metadata", "falta la metadata de implementación"),
    ("re-verificación mecánica fallida", "re-verificación mecánica fallida"),
    ("error del carril review", "error del carril review"),
    ("el revisor no terminó", "el revisor no terminó (tiempo o presupuesto agotado)"),
    ("complete rechazado", "el kanban rechazó el cierre"),
    ("no se pudo publicar el comentario de cambios", "no se pudo publicar el comentario de cambios"),
    ("reopen-review rechazado", "el kanban rechazó la reapertura"),
)
_ROUNDS_RE = re.compile(r"^(\d+)ª petición de cambios")
_OPTS_RE = re.compile(r"^(.*\S)\s+\[(1\) .*)\]$")
_OPT_RE = re.compile(r"^\d+\)\s*(.*?)(\s+\(recomendada\))?$")


def public_reason(reason: str | None) -> str | None:
    text = (reason or "").strip()
    return next((public for prefix, public in PUBLIC_REASONS if text.startswith(prefix)), None)


_REPR_Q_RE = re.compile(r"""^\{\s*['"]question['"]\s*:\s*(['"])(.*?)\1\s*[,}]""")


def _question(line: str) -> dict:
    """Inversa de notices.question_text: `¿X? [1) A / 2) B (recomendada)]` -> {question, options, recommended}.
    También el repr de un dict (bloqueos anteriores al arreglo del runner); cortado por el límite, solo la pregunta."""
    if line.startswith("{"):
        try:
            value = ast.literal_eval(line)
            if isinstance(value, dict):
                return value
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            pass
        m = _REPR_Q_RE.match(line)
        if m:
            return {"question": m.group(2)}
    m = _OPTS_RE.match(line)
    if not m:
        return {"question": line}
    options, recommended = [], None
    for i, part in enumerate(m.group(2).split(" / ")):
        om = _OPT_RE.match(part.strip())
        if not om:
            return {"question": line}
        options.append(om.group(1))
        if om.group(2):
            recommended = i
    return {"question": m.group(1), "options": options, "recommended": recommended}


def parse_questions(reason: str | None) -> list[dict]:
    """Preguntas desde el motivo del bloqueo: una por viñeta `- `; sin viñetas, el motivo entero es la pregunta."""
    lines = (reason or "").strip().splitlines()
    bullets = [l.strip()[2:].strip() for l in lines if l.strip().startswith("- ")]
    if bullets:
        return normalize_questions([_question(b) for b in bullets if b])
    return normalize_questions([(reason or "").strip()])


@dataclass
class Pending:
    tid: str
    lane: Lane
    state: str                  # needs_input | blocked | done
    task: dict
    status: str
    bullets: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    block_kind: str | None = None
    changed_files: list[str] | None = None
    summary: str | None = None


def _last_block(show: dict) -> dict | None:
    for ev in reversed(show.get("events") or []):
        if ev.get("kind") == "blocked":
            return ev.get("payload") or {}
    return None


def _git_branch_state(lane: Lane, tid: str, head_sha: str | None, run=_proc.run) -> tuple[bool, bool]:
    """(la rama lane/<id> existe en el remote, su head ya está en la base). Solo lectura: sin fetch ni checkout."""
    kw = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace", "timeout": 60}
    cp = run(["git", "-C", lane.repo, "ls-remote", "--heads", lane.remote, f"refs/heads/lane/{tid}"], **kw)
    exists = cp.returncode == 0 and bool((cp.stdout or "").strip())
    merged = False
    if exists and head_sha:
        merged = run(["git", "-C", lane.repo, "merge-base", "--is-ancestor", head_sha, lane.base_ref],
                     **kw).returncode == 0
    return exists, merged


class Renotifier:
    def __init__(self, lanes: dict[str, Lane], *, hermes_for: Callable[[str], object], notifier,
                 messages: MessageStore, links, desk,
                 branch_state: Callable[[Lane, str, str | None], tuple[bool, bool]] = _git_branch_state,
                 out: Callable[[str], None] = print):
        if not desk or not getattr(notifier, "bot_id", None):
            raise SystemExit("renotify necesita el bot de carriles (CARRILES_BOT_TOKEN): sin él no hay botones")
        self.lanes = lanes
        self.hermes_for = hermes_for
        self.notifier = notifier
        self.messages = messages
        self.links = links
        self.desk = desk
        self.branch_state = branch_state
        self.out = out
        self.bot_id = str(notifier.bot_id)
        self.review_lane = next((l for l in lanes.values() if l.kind == "review"), None)

    # --- qué hay pendiente --------------------------------------------------------------------------

    def collect(self, *, lane: str | None = None, task: str | None = None) -> list[Pending]:
        impl = {n: l for n, l in self.lanes.items() if l.kind == "implement"}
        if lane and lane not in impl:
            raise SystemExit(f"'{lane}' no es un carril de código de lanes.yaml ({', '.join(impl)})")
        found = []
        for name, ln in impl.items():
            if lane and name != lane:
                continue
            h = self.hermes_for(ln.board)
            for status, plan in (("blocked", self._plan_blocked), ("done", self._plan_done)):
                for t in h.list_status(name, status):
                    if task and t["id"] != task:
                        continue
                    try:
                        p = plan(ln, h.show(t["id"]))
                    except Exception as exc:  # una tarjeta rota no para las demás
                        self.out(f"{t['id']}: se salta ({exc})")
                        continue
                    if p:
                        found.append(p)
        return found

    def _plan_blocked(self, lane: Lane, show: dict) -> Pending | None:
        task = show["task"]
        tid = task["id"]
        block = _last_block(show)
        kind = (block or {}).get("kind")
        if kind not in ("needs_input", "transient"):
            self.out(f"{tid}: se salta (bloqueo de tipo {kind or 'desconocido'})")
            return None
        runs = show.get("runs") or []
        reason = block.get("reason") or (runs[-1].get("summary") if runs else "") or ""
        if kind == "transient":
            status = status_line("bloqueada", public_reason(reason), "detalle en la tarjeta")
            return Pending(tid, lane, "blocked", task, status, block_kind="transient")
        meta = (runs[-1].get("metadata") if runs else None) or {}
        questions = normalize_questions(meta.get("questions")) or parse_questions(reason)
        m = _ROUNDS_RE.match(reason.strip())
        public = f"{m.group(1)}ª petición de cambios: decides tú" if m else "necesita tu decisión"
        return Pending(tid, lane, "needs_input", task, status_line(public, NEEDS_HINT),
                       bullets=questions_block(questions), questions=questions)

    def _plan_done(self, lane: Lane, show: dict) -> Pending | None:
        task = show["task"]
        tid = task["id"]
        meta = implementation_metadata(show)
        review = meta.get("review") or {}
        if review.get("status") != "approve":
            return None
        comments = show.get("comments") or []
        if any((c.get("body") or "").startswith(APPROVED_PREFIX) and c.get("author") == OSCAR_AUTHOR
               for c in comments):
            return None
        if any((c.get("body") or "").startswith(INTEGRATED_PREFIX) for c in comments):
            return None
        exists, merged = self.branch_state(lane, tid, meta.get("head_sha"))
        if not exists or merged:
            self.out(f"{tid}: se salta (rama lane/{tid} {'ya fusionada' if merged else 'no está en el remote'})")
            return None
        changed = meta.get("changed_files") or []
        cost = sum(float(c or 0) for c in (meta.get("cost_usd"), review.get("cost_usd")))
        status = status_line("review aprobada", files_label(len(changed)), test_label(meta.get("runner_test_exit")),
                             money(cost), "lista para merge")
        return Pending(tid, lane, "done", task, status, changed_files=changed,
                       summary=review.get("summary") or meta.get("summary"))

    # --- envío --------------------------------------------------------------------------------------

    def run(self, *, lane: str | None = None, task: str | None = None, dry_run: bool = False,
            force: bool = False) -> tuple[list[str], list[str]]:
        sent, failed = [], []
        for p in self.collect(lane=lane, task=task):
            old = self.messages.get(p.tid)
            if old and str(old.get("bot") or "") == self.bot_id and not force:
                self.out(f"{p.tid}: se salta (ya tiene aviso del bot de carriles)")
                continue
            if dry_run:
                self._print(p)
                sent.append(p.tid)
            elif self._publish(p, old):
                self.out(f"{p.tid}: reenviado ({p.state})")
                sent.append(p.tid)
            else:
                self.out(f"{p.tid}: NO se pudo reenviar ({p.state}); detalle en el log")
                failed.append(p.tid)
        return sent, failed

    def _print(self, p: Pending) -> None:
        target = resolve_target(telegram_target(p.task.get("body")), p.lane.telegram,
                                allowed_chats=getattr(self.notifier, "allowed_chats", set()),
                                generic_origins=getattr(self.notifier, "generic_origins", set()))
        chat, thread = target or (getattr(self.notifier, "chat_id", None), getattr(self.notifier, "thread_id", None))
        spec = keyboard_spec(p.state, block_kind=p.block_kind, questions=p.questions) or []
        buttons = " | ".join(b["text"] for row in spec for b in row)
        self.out(f"[dry-run] {p.tid} · {p.lane.name} · {EMOJI[p.state]} {p.block_kind or p.state} → "
                 f"chat {chat} tema {thread or 0}\n    {p.status}\n"
                 + "".join(f"    {b}\n" for b in p.bullets) + f"    botones: {buttons}")

    def _publish(self, p: Pending, old: dict | None) -> bool:
        # Aviso viejo de otro bot (Hermes): se olvida para que TaskNotices no intente editarlo ni borrarlo.
        if old:
            self.messages.drop(p.tid)
        try:
            if p.state == "done" and self.review_lane:
                rr = ReviewRunner(self.review_lane, self.lanes, hermes_for=self.hermes_for, git=None, reviewer=None,
                                  verifier=None, notify=self.notifier, messages=self.messages, links=self.links,
                                  decisions=self.desk)
                rr.notify("done", {**p.task, "assignee": p.lane.name}, p.status, alert=True,
                          changed_files=p.changed_files, buttons=True, summary=p.summary)
            else:
                lr = LaneRunner(p.lane, hermes=self.hermes_for(p.lane.board), git=None, worker=None, verifier=None,
                                notify=self.notifier, messages=self.messages, links=self.links, decisions=self.desk)
                lr.notify(p.state, p.tid, p.task, p.status, alert=True, bullets=p.bullets, buttons=True,
                          block_kind=p.block_kind, questions=p.questions, changed_files=p.changed_files)
        except Exception as exc:
            log.warning("%s: renotify falló: %s", p.tid, exc)
        new = self.messages.get(p.tid)
        ok = bool(new) and str(new.get("bot") or "") == self.bot_id and new != old
        if not ok and old:
            self.messages.put(p.tid, old)
        return ok


def main(argv: list[str]) -> int:
    """Misma construcción que runner.py: .env, lanes.yaml, bot de carriles, MessageStore y DecisionDesk (sin escucha:
    las pulsaciones las atiende el runner-servicio, que lee los teclados de .state/callbacks)."""
    from .config import load_env, load_lanes, load_telegram_settings
    from .decisions import CALLBACKS_DIR, OWNER_TELEGRAM_ID, CallbackStore, DecisionDesk
    from .hermes import HermesCLI
    from .notices import LinkBuilder
    from .runner import MESSAGES_DIR
    from .telegram import notifier_from_env

    args = parse_args(argv)
    env, tg_settings, lanes = load_env(), load_telegram_settings(), load_lanes()
    notifier, lanes_bot = notifier_from_env(env, tg_settings)
    if not (lanes_bot and notifier.enabled):
        raise SystemExit("renotify necesita CARRILES_BOT_TOKEN y TELEGRAM_CHAT_ID en agent-lanes/.env")
    cache: dict[str, HermesCLI] = {}
    hermes_for = lambda board: cache.setdefault(board, HermesCLI(board))
    links = LinkBuilder(env.get("KANBAN_BASE_URL") or tg_settings["kanban_base_url"])
    desk = DecisionDesk(notifier, CallbackStore(CALLBACKS_DIR), lanes=lanes, hermes_for=hermes_for, links=links,
                        owner_id=env.get("OWNER_TELEGRAM_ID") or OWNER_TELEGRAM_ID)
    r = Renotifier(lanes, hermes_for=hermes_for, notifier=notifier, messages=MessageStore(MESSAGES_DIR),
                   links=links, desk=desk)
    sent, failed = r.run(lane=args.lane, task=args.task, dry_run=args.dry_run, force=args.force)
    verb = "enviaría" if args.dry_run else "reenviados"
    print(f"\n{verb}: {len(sent)} {' '.join(sent)}" + (f" · FALLIDOS: {' '.join(failed)}" if failed else ""))
    return 1 if failed else 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="lanes.py renotify", description=__doc__.split("\n\n")[0])
    ap.add_argument("--lane", help="solo este carril de código")
    ap.add_argument("--task", help="solo esta tarea (t_xxx)")
    ap.add_argument("--dry-run", action="store_true", help="imprime qué enviaría, sin enviar ni escribir nada")
    ap.add_argument("--force", action="store_true", help="reenvía aunque el aviso ya sea del bot de carriles")
    return ap.parse_args(argv)
