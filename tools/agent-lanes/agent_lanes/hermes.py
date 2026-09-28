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
        """Oscar's answers to the worker's questions (Telegram buttons / free reply), oldest first."""
        comments = self.show(task_id).get("comments") or []
        return [c["body"] for c in comments
                if c.get("author") == OSCAR_AUTHOR and (c.get("body") or "").startswith(ANSWER_PREFIX)]

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
