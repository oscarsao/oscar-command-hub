"""Un mensaje de Telegram por tarea: se crea al empezar y se edita en cada cambio de estado.

Solo lo que necesita a Oscar (needs_input, lista para merge, bloqueo) sale como mensaje NUEVO con notificación;
el anterior se borra para que el tema no acumule mensajes. Nada técnico (rutas, stderr, trazas) llega a Telegram:
los llamantes pasan una frase pública y el detalle va al log y a la tarjeta del kanban.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from urllib.parse import quote, urlsplit

log = logging.getLogger("agent_lanes")

EMOJI = {"running": "▶️", "review": "🔍", "done": "✅", "changes": "🔁", "needs_input": "❓", "blocked": "⛔"}
TITLE_MAX = 60
QUESTIONS_MAX_ITEMS = 5
QUESTIONS_MAX_CHARS = 300  # total del bloque de viñetas, no por viñeta
QUESTION_MAX_CHARS = 110   # cada viñeta, para que quepan al menos 2 dentro de los 300


def truncate(text: str | None, n: int) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def money(usd) -> str | None:
    try:
        v = float(usd)
    except (TypeError, ValueError):
        return None
    return f"{v:.1f}".replace(".", ",") + " $" if v > 0 else None


def files_label(n: int | None) -> str | None:
    if n is None:
        return None
    return f"{n} archivo" + ("" if n == 1 else "s")


def test_label(exit_code) -> str | None:
    if exit_code is None:
        return None
    return "test OK" if exit_code == 0 else f"test exit {exit_code}"


def status_line(*parts) -> str:
    return " · ".join(str(p) for p in parts if p)


def questions_block(questions) -> list[str]:
    """Máximo 5 viñetas y 300 caracteres en total (contando saltos de línea)."""
    lines: list[str] = []
    for q in list(questions or [])[:QUESTIONS_MAX_ITEMS]:
        line = "• " + truncate(q, QUESTION_MAX_CHARS)
        if len("\n".join(lines + [line])) > QUESTIONS_MAX_CHARS:
            break
        lines.append(line)
    return lines


# --- URLs -------------------------------------------------------------------------------------------

_SCP_RE = re.compile(r"^(?:[\w.-]+@)?([\w.-]+):(?!//)(.+)$")  # git@github.com:owner/repo.git


def github_repo_url(remote_url: str | None) -> str | None:
    """https://github.com/<owner>/<repo> desde un remote ssh/https (sin credenciales). None si no es GitHub."""
    url = (remote_url or "").strip()
    if not url:
        return None
    if "://" in url:
        parts = urlsplit(url)
        host, path = (parts.hostname or ""), parts.path  # hostname descarta user:token@
    else:
        m = _SCP_RE.match(url)
        if not m:
            return None
        host, path = m.group(1), m.group(2)
    if host.lower() not in ("github.com", "www.github.com"):
        return None
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if path.count("/") != 1 or not all(path.split("/")):
        return None
    return f"https://github.com/{path}"


def compare_url(remote_url: str | None, base: str, task_id: str) -> str | None:
    repo = github_repo_url(remote_url)
    if not repo or not base:
        return None
    return f"{repo}/compare/{quote(base, safe='/')}...{quote('lane/' + task_id, safe='/')}"


def card_url(base_url: str | None, board: str, task_id: str) -> str | None:
    """KANBAN_BASE_URL: plantilla con {board}/{id}, o base a la que se añade /tasks/{board}/{id}. Sin base: None."""
    base = (base_url or "").strip()
    if not base:
        return None
    if "{id}" in base or "{board}" in base:
        return base.replace("{board}", quote(board or "")).replace("{id}", quote(task_id))
    return f"{base.rstrip('/')}/tasks/{quote(board or '')}/{quote(task_id)}"


class LinkBuilder:
    """Enlaces de una tarea: rama en GitHub (remote del repo del carril, cacheado) y tarjeta del kanban."""

    def __init__(self, kanban_base_url: str | None = None, runner=subprocess.run):
        self.kanban_base_url = kanban_base_url
        self._run = runner
        self._remotes: dict[tuple[str, str], str | None] = {}

    def _remote_url(self, repo: str, remote: str) -> str | None:
        key = (repo, remote)
        if key not in self._remotes:
            try:
                cp = self._run(["git", "-C", repo, "remote", "get-url", remote], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
                self._remotes[key] = cp.stdout.strip() if cp.returncode == 0 else None
            except (OSError, subprocess.SubprocessError):
                self._remotes[key] = None
        return self._remotes[key]

    def __call__(self, lane, task_id: str, *, branch: bool = True) -> list[tuple[str, str]]:
        links = []
        if branch and lane.repo:
            url = compare_url(self._remote_url(lane.repo, lane.remote), lane.base, task_id)
            if url:
                links.append(("rama", url))
        card = card_url(self.kanban_base_url, lane.board, task_id)
        if card:
            links.append(("tarjeta", card))
        return links


# --- render -----------------------------------------------------------------------------------------

def render(state: str, task_id: str, title: str | None, lane: str, status: str,
           links: list[tuple[str, str]] | None = None, bullets: list[str] | None = None) -> str:
    """HTML (parse_mode=HTML): cabecera, línea de estado, viñetas opcionales (solo needs_input), enlaces."""
    e = html.escape
    lines = [f"{EMOJI.get(state, '•')} {e(task_id)} · {e(truncate(title, TITLE_MAX))} · {e(lane)}", e(status)]
    lines += [e(b) for b in bullets or ()]
    if links:
        lines.append(" · ".join(f'<a href="{e(url, quote=True)}">{e(label)}</a>' for label, url in links))
    return "\n".join(lines)


# --- almacén de message_id --------------------------------------------------------------------------

class MessageStore:
    """{task_id: {chat_id, thread_id, message_id}}. Con `root`, un JSON por tarea (sobrevive a reinicios);
    sin él, en memoria (tests)."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else None
        self._mem: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _path(self, tid: str) -> Path:
        return self.root / f"{tid}.json"

    def get(self, tid: str) -> dict | None:
        if self.root is None:
            return self._mem.get(tid)
        try:
            return json.loads(self._path(tid).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def put(self, tid: str, rec: dict) -> None:
        if self.root is None:
            self._mem[tid] = dict(rec)
            return
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self._path(tid).with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(rec), encoding="utf-8")
            os.replace(tmp, self._path(tid))


class TaskNotices:
    """Publica el estado de una tarea en su único mensaje."""

    def __init__(self, notifier, store: MessageStore | None = None):
        self.notifier = notifier
        self.store = store or MessageStore()

    def publish(self, tid: str, text: str, target=None, lane_target=None, *, alert: bool = False) -> None:
        rec = self.store.get(tid)
        if rec and not alert:
            if self.notifier.edit(rec["chat_id"], rec["message_id"], text):
                return
            log.info("%s: no se pudo editar el mensaje %s; envío uno nuevo", tid, rec["message_id"])
        sent = self.notifier.send(text, target, lane_target, silent=not alert)
        if not sent:
            return
        if rec and alert:  # el aviso nuevo sustituye al mensaje de progreso: un mensaje por tarea
            try:
                self.notifier.delete(rec["chat_id"], rec["message_id"])
            except Exception as exc:
                log.info("%s: no se pudo borrar el mensaje anterior: %s", tid, exc)
        self.store.put(tid, sent)
