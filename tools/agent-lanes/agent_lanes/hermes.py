"""Thin wrapper over the `hermes kanban` CLI (pinned to 0.21.4) plus its contract check."""
from __future__ import annotations

import json
import re
import subprocess

from . import proc as _proc
from pathlib import Path

HERMES_EXE = Path.home() / "AppData/Local/hermes/bin/hermes.exe"
HERMES_AGENT_DIR = Path.home() / "AppData/Local/hermes/hermes-agent"
HERMES_PYTHON = HERMES_AGENT_DIR / "venv/Scripts/python.exe"
PINNED_VERSION = "0.21.4"
TRIAGE_STATUS = "triage"

# triage -> todo -> ready SIN reescribir título/cuerpo (verificado 28-09). La CLI `kanban specify` pasa por un LLM y
# reescribe la tarea: no se usa. Tablero, id y autor van por argv, nunca interpolados en el código.
_REQUEUE_TRIAGE = (
    "import sys\n"
    "from hermes_cli import kanban_db as kb, kanban_db_connect as kbc\n"
    "board, tid, author = sys.argv[1:4]\n"
    "with kbc.connect_closing(board=board) as conn:\n"
    "    ok = kb.specify_triage_task(conn, tid, author=author)\n"
    "print('ok' if ok else 'no')\n"
)
_TID_RE = re.compile(r"^t_[0-9a-f]{8}$")
_BOARD_RE = re.compile(r"^[\w-]{1,64}$")

# subcommand -> flags the runner relies on
CONTRACT = {
    "list": ["--assignee", "--status", "--json"],
    "claim": ["--ttl"],
    "heartbeat": ["--note"],
    "comment": ["--author"],
    "block": ["--kind", "needs_input", "transient"],
    "request-review": ["--summary", "--metadata"],
    "show": ["--json"],
    "complete": ["--result", "--summary", "--metadata"],
    "reopen-review": ["--reason"],
    "reassign": ["profile"],
    "assign": ["profile"],
    "unblock": ["--reason"],
    "notify-list": ["--json"],
    "notify-unsubscribe": ["--platform", "--chat-id", "--thread-id"],
}

REVIEW_AUTHOR = "lane-review"  # author of the review lane's change requests (read back by the implementer)
OSCAR_AUTHOR = "oscar-telegram"  # Oscar's decisions from Telegram buttons (answers are read back by the implementer)
ANSWER_PREFIX = "Respuesta de Oscar:"

# Respuesta de Oscar llegada por Hermes (clarify en su DM, 28-09): Hermes la apunta en la tarjeta con SU perfil
# (`default`) y este prefijo EXACTO. Nada más cuenta como respuesta: ni "Oscar decide (28-09): …" ni "Respuesta de
# Oscar: …" escritos por `default`, ni nada de los workers (agent-lanes, lane-*) o del integrador. Ver README,
# "Responder desde Hermes".
HERMES_AUTHOR = "default"
HERMES_ANSWER_PREFIX = "RESPUESTA-OSCAR:"
HERMES_ANSWER_AUTHORS = frozenset({HERMES_AUTHOR, OSCAR_AUTHOR})  # lista blanca, nunca una lista negra


def hermes_answer_text(comment: dict | None) -> str | None:
    """Texto de la respuesta si `comment` es un `RESPUESTA-OSCAR: …` válido (autor permitido, prefijo exacto al
    principio del cuerpo y texto no vacío detrás). None en cualquier otro caso."""
    c = comment or {}
    if c.get("author") not in HERMES_ANSWER_AUTHORS:
        return None
    body = c.get("body")
    if not isinstance(body, str) or not body.startswith(HERMES_ANSWER_PREFIX):
        return None
    return body[len(HERMES_ANSWER_PREFIX):].strip() or None


def _ts(value) -> float | None:
    try:
        return float(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def pending_hermes_answer(show: dict | None) -> dict | None:
    """Comentario `RESPUESTA-OSCAR:` que desbloquea la tarea: la tarea está `blocked`, su último bloqueo es
    needs_input y el comentario es ESTRICTAMENTE posterior a ese bloqueo (una respuesta de una ronda anterior no
    responde la pregunta nueva). El más reciente si hay varios. Fail-closed: sin fechas legibles, None."""
    show = show or {}
    if (show.get("task") or {}).get("status") != "blocked":
        return None
    block = next((ev for ev in reversed(show.get("events") or []) if ev.get("kind") == "blocked"), None)
    if not block or (block.get("payload") or {}).get("kind") != "needs_input":
        return None
    blocked_at = _ts(block.get("created_at"))
    if blocked_at is None:
        return None
    found = None
    for c in show.get("comments") or []:
        at = _ts(c.get("created_at"))
        if at is not None and at > blocked_at and hermes_answer_text(c):
            found = c
    return found


def oscar_answers_from(show: dict | None) -> list[str]:
    """Respuestas de Oscar en la tarjeta, en orden: las de los botones (`Respuesta de Oscar: …` de oscar-telegram) y
    las que apuntó Hermes (`RESPUESTA-OSCAR: …`), estas como "Respuesta de Oscar (vía Hermes): <texto>".
    Varias respuestas a la MISMA pregunta (Oscar la contestó otra vez): solo cuenta la última, en su posición; los
    comentarios no se borran."""
    out: list[tuple[str | None, str]] = []
    for c in (show or {}).get("comments") or []:
        body = c.get("body") or ""
        if c.get("author") == OSCAR_AUTHOR and body.startswith(ANSWER_PREFIX):
            key = answer_question(body)
            out = [(k, b) for k, b in out if key is None or k != key]
            out.append((key, body))
        elif (text := hermes_answer_text(c)) is not None:
            out.append((None, f"Respuesta de Oscar (vía Hermes): {text}"))
    return [b for _, b in out]


# --- progreso de las respuestas a un needs_input con varias preguntas ------------------------------------
# Visto el 28-09 (t_6a2fdf4d): con varias preguntas, un botón respondía solo la 1ª y todas las vistas volvían a pintar
# la tarjeta desde la 1ª; Oscar la contestó tres veces. El estado de las respuestas se deriva SIEMPRE del kanban.

ANSWER_ARROW = " → "
# Etiquetas del ✍️ antiguo: una respuesta libre que valía para todas las preguntas (o para las que quedaban).
LEGACY_ALL_LABELS = ("todas las preguntas", "resto de preguntas")


def _norm(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def answer_question(body: str | None) -> str | None:
    """Pregunta (normalizada) de un comentario `Respuesta de Oscar: <pregunta> → <respuesta>`; None si no lo es."""
    b = body or ""
    if not b.startswith(ANSWER_PREFIX) or ANSWER_ARROW not in b:
        return None
    return _norm(b[len(ANSWER_PREFIX):].split(ANSWER_ARROW, 1)[0]) or None


def last_needs_input_block(show: dict | None) -> dict | None:
    """Último bloqueo needs_input con fecha legible; None en otro caso (fail-closed).

    28-09 (bucle t_3db189f4/t_e8e76d7c): el 2º bloqueo por lo mismo Hermes NO lo registra como `blocked` sino como
    `block_loop_detected` (y pasa la tarea a triage). Si solo se mirase `blocked`, la "ronda actual" sería la anterior
    y sus respuestas contarían para la pregunta nueva -> autocuración -> relanzado -> bloqueo... en bucle. Por eso la
    fecha del bloqueo es la MÁS RECIENTE entre `blocked`, `block_loop_detected` y el comentario "BLOCKED:"."""
    show = show or {}
    events = show.get("events") or []
    block = next((ev for ev in reversed(events) if ev.get("kind") == "blocked"), None)
    if not block or (block.get("payload") or {}).get("kind") != "needs_input" or _ts(block.get("created_at")) is None:
        return None
    latest = _ts(block.get("created_at"))
    for ev in events:
        if ev.get("kind") == "block_loop_detected" and (t := _ts(ev.get("created_at"))) is not None:
            latest = max(latest, t)
    for c in show.get("comments") or []:
        if (c.get("body") or "").startswith("BLOCKED") and (t := _ts(c.get("created_at"))) is not None:
            latest = max(latest, t)
    return {**block, "created_at": latest}


def answer_progress(show: dict | None, questions) -> tuple[dict[int, str], int | None]:
    """({índice: respuesta}, índice de la siguiente pregunta pendiente o None si están todas) para `questions`
    (normalizadas: [{question, ...}]) en la ronda actual. Cuenta un comentario si es ESTRICTAMENTE posterior al
    último bloqueo needs_input y es:
      - de oscar-telegram `Respuesta de Oscar: <pregunta> → <respuesta>` (pregunta comparada normalizada; si hay
        varias a la misma pregunta, la última);
      - de oscar-telegram con la etiqueta antigua del ✍️ ("todas las preguntas" / "resto de preguntas"): todas;
      - un `RESPUESTA-OSCAR:` válido (Hermes): todas.
    Sin fechas legibles no cuenta nada (se pinta desde la 1ª, como antes)."""
    qs = list(questions or ())
    block = last_needs_input_block(show)
    answers: dict[int, str] = {}
    if block is not None and qs:
        blocked_at = _ts(block.get("created_at"))
        # Pregunta -> índice, las más largas primero: el cuerpo EMPIEZA por la pregunta conocida (una respuesta libre
        # con " → " dentro no confunde) y una pregunta que es prefijo de otra no se lleva la respuesta de la larga.
        known = sorted(((_norm(q.get("question") if isinstance(q, dict) else q), i) for i, q in enumerate(qs)),
                       key=lambda kv: -len(kv[0]))
        for c in (show or {}).get("comments") or []:
            at = _ts(c.get("created_at"))
            if at is None or at <= blocked_at:
                continue
            body = c.get("body") or ""
            if (text := hermes_answer_text(c)) is not None:
                answers.update({i: text for i in range(len(qs))})
                continue
            if c.get("author") != OSCAR_AUTHOR:
                continue
            if not body.startswith(ANSWER_PREFIX):
                continue
            rest = " ".join(body[len(ANSWER_PREFIX):].split())
            low = rest.casefold()
            hit = next(((k, i) for k, i in known if k and low.startswith(k + ANSWER_ARROW.rstrip())), None)
            if hit:
                answers[hit[1]] = rest[len(hit[0]) + len(ANSWER_ARROW):].strip()
                continue
            legacy = next((k for k in LEGACY_ALL_LABELS if low.startswith(k + ANSWER_ARROW.rstrip())), None)
            if legacy:
                answers.update({i: rest[len(legacy) + len(ANSWER_ARROW):].strip() for i in range(len(qs))})
    nxt = next((i for i in range(len(qs)) if i not in answers), None)
    return answers, nxt


def resume(h, tid: str) -> str | None:
    """Devuelve una tarea respondida al carril: `unblock`; si falla porque Hermes la pasó a triage (bloqueo repetido,
    block_loop_detected; desde ahí `unblock` falla), `requeue_triage` la saca sin reescribirla. Estado resultante
    ("ready", o "todo" si espera a un padre) o None si fallan ambas vías."""
    if h.unblock(tid):
        return "ready"
    requeue = getattr(h, "requeue_triage", None)
    return (requeue(tid) if requeue else None) or None


class HermesError(RuntimeError):
    pass


class HermesCLI:
    def __init__(self, board: str, exe: Path = HERMES_EXE, runner=_proc.run, author: str = "agent-lanes"):
        self.board = board
        self.exe = str(exe)
        self._run = runner
        self.author = author

    def _call(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return self._run([self.exe, "kanban", "--board", self.board, *args],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)

    def list_status(self, assignee: str, status: str, sort: str = "priority") -> list[dict]:
        cp = self._call("list", "--assignee", assignee, "--status", status, "--json", "--sort", sort)
        if cp.returncode != 0:
            raise HermesError(f"list failed: {cp.stderr.strip()[:300]}")
        return json.loads(cp.stdout or "[]")

    def list_ready(self, assignee: str) -> list[dict]:
        return self.list_status(assignee, "ready")

    def claim(self, task_id: str, ttl: int) -> bool:
        return self._call("claim", task_id, "--ttl", str(ttl)).returncode == 0

    def heartbeat(self, task_id: str, note: str | None = None) -> bool:
        args = ["heartbeat", task_id] + (["--note", note] if note else [])
        return self._call(*args).returncode == 0

    def comment(self, task_id: str, text: str, author: str | None = None) -> bool:
        return self._call("comment", task_id, "--author", author or self.author, text).returncode == 0

    def show(self, task_id: str) -> dict:
        cp = self._call("show", task_id, "--json")
        if cp.returncode != 0:
            raise HermesError(f"show {task_id} failed: {cp.stderr.strip()[:300]}")
        return json.loads(cp.stdout)

    def review_feedback(self, task_id: str) -> list[str]:
        """Change requests left by the review lane, oldest first (empty for a first run)."""
        comments = self.show(task_id).get("comments") or []
        return [c["body"] for c in comments if c.get("author") == REVIEW_AUTHOR]

    def oscar_answers(self, task_id: str) -> list[str]:
        """Oscar's answers to the worker's questions (Telegram buttons / free reply / RESPUESTA-OSCAR vía Hermes),
        oldest first."""
        return oscar_answers_from(self.show(task_id))

    def assign(self, task_id: str, profile: str) -> bool:
        return self._call("assign", task_id, profile).returncode == 0

    def unblock(self, task_id: str) -> bool:
        return self._call("unblock", task_id).returncode == 0

    def requeue_triage(self, task_id: str, author: str = OSCAR_AUTHOR, *, python: Path = HERMES_PYTHON) -> str | None:
        """Saca de `triage` una tarea que Hermes aparcó por bloquearse dos veces por lo mismo (block_loop_detected;
        desde ahí `unblock` falla): specify_triage_task con la API de Hermes en su venv, sin tocar título ni cuerpo.
        Devuelve el estado resultante ("ready", o "todo" si espera a un padre) o None si no estaba en triage/falló."""
        if not _TID_RE.match(task_id or "") or not _BOARD_RE.match(self.board or ""):
            return None
        try:
            cp = self._run([str(python), "-c", _REQUEUE_TRIAGE, self.board, task_id, author], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60, cwd=str(HERMES_AGENT_DIR))
        except (OSError, subprocess.SubprocessError):
            return None
        if cp.returncode != 0 or (cp.stdout or "").strip().splitlines()[-1:] != ["ok"]:
            return None
        self.comment(task_id, "Reintentada por Oscar desde Telegram: sale de triage (bloqueo repetido) sin cambios",
                     author=author)
        try:
            return (self.show(task_id).get("task") or {}).get("status") or "todo"
        except (HermesError, json.JSONDecodeError):
            return "todo"

    def complete(self, task_id: str, result: str, metadata: dict) -> tuple[bool, str]:
        cp = self._call("complete", task_id, "--result", result, "--metadata", json.dumps(metadata))
        return cp.returncode == 0, cp.stderr.strip()[:500]

    def reopen_review(self, task_id: str) -> bool:
        return self._call("reopen-review", task_id).returncode == 0

    def reassign(self, task_id: str, profile: str, reason: str) -> bool:
        return self._call("reassign", task_id, profile, "--reason", reason).returncode == 0

    def block(self, task_id: str, kind: str, reason: str) -> bool:
        # --kind must precede the positionals: hermes rejects `block <id> --kind k <reason>`.
        return self._call("block", "--kind", kind, task_id, reason).returncode == 0

    def request_review(self, task_id: str, summary: str, metadata: dict) -> tuple[bool, str]:
        cp = self._call("request-review", task_id, "--summary", summary, "--metadata", json.dumps(metadata))
        return cp.returncode == 0, cp.stderr.strip()[:500]

    def drop_telegram_subs(self, task_id: str) -> list[str]:
        """Quita las suscripciones de Telegram que `kanban_create` puso al hilo de origen (auto_subscribe_on_create):
        los avisos de una tarea de carril los da el runner, y el notificador de Hermes los duplicaba.
        Devuelve los destinos quitados ("chat:thread")."""
        cp = self._call("notify-list", task_id, "--json")
        if cp.returncode != 0:
            raise HermesError(f"notify-list {task_id} failed: {cp.stderr.strip()[:300]}")
        dropped = []
        for sub in json.loads(cp.stdout or "[]"):
            if sub.get("task_id") != task_id or sub.get("platform") != "telegram":
                continue
            chat, thread = str(sub.get("chat_id") or ""), str(sub.get("thread_id") or "")
            args = ["notify-unsubscribe", task_id, "--platform", "telegram", "--chat-id", chat]
            if thread:  # sin --thread-id hermes busca thread_id "" (su normalización del hilo vacío)
                args += ["--thread-id", thread]
            if self._call(*args).returncode == 0:
                dropped.append(f"{chat}:{thread}" if thread else chat)
        return dropped

    def check_contract(self) -> list[str]:
        """Return a list of problems; empty means the CLI matches what the runner expects."""
        problems: list[str] = []
        try:
            cp = self._run([self.exe, "--version"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"hermes no ejecutable ({exc})"]
        if PINNED_VERSION not in cp.stdout:
            problems.append(f"versión de hermes distinta de {PINNED_VERSION}: {cp.stdout.strip().splitlines()[:1]}")
        for sub, flags in CONTRACT.items():
            help_cp = self._call(sub, "--help", timeout=60)
            if help_cp.returncode != 0:
                problems.append(f"falta subcomando `kanban {sub}`")
                continue
            problems += [f"`kanban {sub}` sin {f}" for f in flags if f not in help_cp.stdout]
        cp = self._call("list", "--status", "ready", "--json")
        try:
            data = json.loads(cp.stdout or "null")
            if not isinstance(data, list) or (data and not {"id", "title", "body"} <= set(data[0])):
                problems.append("`kanban list --json` no devuelve [{id,title,body,...}]")
        except json.JSONDecodeError:
            problems.append("`kanban list --json` no devuelve JSON")
        return problems
