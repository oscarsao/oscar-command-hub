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

from . import proc as _proc
import threading
from pathlib import Path
from urllib.parse import quote, urlsplit

log = logging.getLogger("agent_lanes")

EMOJI = {"running": "▶️", "review": "🔍", "done": "✅", "changes": "🔁", "needs_input": "❓", "blocked": "⛔",
         "approved": "✅", "parked": "🗄", "requeued": "🔄", "answered": "💬"}
TITLE_MAX = 60
OBJECTIVE_MAX = 200
OPTION_MAX = 40
TEXT_MAX = 4000  # sendMessage admite 4096: margen para no cortar nunca el HTML a medias
QUESTIONS_MAX_ITEMS = 5
QUESTIONS_MAX_CHARS = 1200  # total del bloque: Oscar debe poder responder desde Telegram sin abrir la tarjeta
QUESTION_MAX_CHARS = 280   # cada viñeta; 5 x 280 < límite de Telegram (4096) con el resto del mensaje
# Marca de cada carril (segunda línea del aviso). Un carril sin marca muestra solo su nombre.
BRANDS = {"claude-migrateam": "MigraTeam", "claude-oscarhq": "Píldora", "claude-scraper": "Píldora",
          "claude-nextjobs": "NextJobs"}
OASP_MODES = ("Fast-Track", "Spec-Lite", "Pitch", "CTO-360")


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
    return "tests OK" if exit_code == 0 else f"tests exit {exit_code}"


def status_line(*parts) -> str:
    return " · ".join(str(p) for p in parts if p)


# --- preguntas del worker -----------------------------------------------------------------------------

def normalize_questions(questions) -> list[dict]:
    """`questions` del worker (strings u objetos {question, options, recommended}) -> [{question, options,
    recommended}]. Retrocompatible: un string es una pregunta sin opciones. Unas opciones fuera de contrato
    (no 2-4 textos) se descartan y la pregunta queda para responder con texto libre."""
    out = []
    for q in questions or ():
        if isinstance(q, dict):
            text = str(q.get("question") or "").strip()
            opts = q.get("options")
            opts = [truncate(str(o), OPTION_MAX) for o in opts] if isinstance(opts, list) else []
            if not 2 <= len(opts) <= 4 or not all(opts):
                opts = []
            rec = q.get("recommended")
            ok = isinstance(rec, int) and not isinstance(rec, bool) and 0 <= rec < len(opts)
            rec = rec if ok else None
        else:
            text, opts, rec = str(q or "").strip(), [], None
        if text:
            out.append({"question": text, "options": opts, "recommended": rec})
    return out


def question_text(q: dict) -> str:
    """Pregunta normalizada en texto plano, con sus opciones, para la tarjeta del kanban."""
    opts = " / ".join(f"{i + 1}) {o}" + (" (recomendada)" if i == q.get("recommended") else "")
                      for i, o in enumerate(q.get("options") or ()))
    return q["question"] + (f" [{opts}]" if opts else "")


def questions_block(questions) -> list[str]:
    """Máximo 5 viñetas y 1200 caracteres en total (contando saltos de línea)."""
    lines: list[str] = []
    for q in normalize_questions(questions)[:QUESTIONS_MAX_ITEMS]:
        line = "• " + truncate(q["question"], QUESTION_MAX_CHARS)
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


SPEC_PREFIXES = ("docs/specs/", "pitches/", "specs/")


def _clean_path(path: str) -> str:
    p = (path or "").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def is_spec_path(path: str) -> bool:
    """Spec o pitch: bajo docs/specs/, pitches/ o specs/, o un .md en la raíz del repo."""
    p = _clean_path(path)
    if not p or p.startswith("/") or ".." in p.split("/"):
        return False
    return p.startswith(SPEC_PREFIXES) or ("/" not in p and p.lower().endswith(".md"))


def spec_url(remote_url: str | None, task_id: str, changed_files) -> str | None:
    """Enlace al primer spec/pitch de `changed_files`, en la rama lane/<id>. None si no hay o no es GitHub."""
    repo = github_repo_url(remote_url)
    path = next((_clean_path(f) for f in changed_files or () if isinstance(f, str) and is_spec_path(f)), None)
    if not repo or not path:
        return None
    return f"{repo}/blob/{quote('lane/' + task_id, safe='/')}/{quote(path, safe='/')}"


def card_url(base_url: str | None, board: str, task_id: str) -> str | None:
    """KANBAN_BASE_URL: plantilla con {board}/{id}, o base a la que se añade /tasks/{board}/{id}. Sin base: None."""
    base = (base_url or "").strip()
    if not base:
        return None
    if "{id}" in base or "{board}" in base:
        return base.replace("{board}", quote(board or "")).replace("{id}", quote(task_id))
    return f"{base.rstrip('/')}/tasks/{quote(board or '')}/{quote(task_id)}"


class LinkBuilder:
    """Enlaces de una tarea: tarjeta del kanban, spec/pitch y cambios en GitHub (remote del carril, cacheado)."""

    def __init__(self, kanban_base_url: str | None = None, runner=_proc.run):
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

    def repo_slug(self, lane) -> str | None:
        """owner/repo del carril en GitHub (para `gh --repo`). None si el remote no es de GitHub."""
        url = github_repo_url(self._remote_url(lane.repo, lane.remote)) if lane.repo else None
        return url.split("github.com/", 1)[1] if url else None

    def __call__(self, lane, task_id: str, *, branch: bool = True, changed_files=None) -> list[tuple[str, str]]:
        # La tarjeta (panel del kanban) va primero: es el enlace que Oscar usa a diario; el resto es para revisar.
        links = []
        card = card_url(self.kanban_base_url, lane.board, task_id)
        if card:
            links.append(("🗂 Tarjeta", card))
        if branch and lane.repo:
            remote = self._remote_url(lane.repo, lane.remote)
            spec = spec_url(remote, task_id, changed_files)
            if spec:
                links.append(("📄 Spec/pitch", spec))
            url = compare_url(remote, lane.base, task_id)
            if url:
                links.append(("🔀 Cambios", url))
        return links


# --- render -----------------------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_OBJECTIVE_RE = re.compile(r"^\s{0,3}#{2,6}\s*objetivo\b.*$", re.I | re.M)
_OASP_RE = re.compile(r"(?<![\w-])(fast[- ]?track|spec[- ]?lite|pitch|cto[- ]?360)(?![\w-])", re.I)


def objective(body: str | None, n: int = OBJECTIVE_MAX) -> str | None:
    """Resumen de la sección `## Objetivo` del cuerpo (hasta el siguiente encabezado), sin viñetas ni markdown."""
    m = _OBJECTIVE_RE.search(body or "")
    if not m:
        return None
    lines = []
    for line in (body or "")[m.end():].splitlines():
        if _HEADING_RE.match(line):
            break
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).replace("**", "").replace("`", "").strip()
        if line:
            lines.append(line)
    return truncate(" ".join(lines), n) or None


def oasp_mode(body: str | None) -> str | None:
    m = _OASP_RE.search(body or "")
    if not m:
        return None
    key = re.sub(r"[- ]", "", m.group(1)).lower()
    return next(mode for mode in OASP_MODES if mode.replace("-", "").lower() == key)


def context_line(lane: str, body: str | None) -> str:
    return status_line(BRANDS.get(lane), lane, oasp_mode(body))


def render(state: str, task_id: str, title: str | None, lane: str, status: str,
           links: list[tuple[str, str]] | None = None, bullets: list[str] | None = None, *,
           body: str | None = None) -> str:
    """HTML (parse_mode=HTML). Unas 5 líneas, más las viñetas de needs_input:

        <emoji> t_xxx · <título ≤60>
        <Marca> · <carril> · <modo OASP si aparece en el cuerpo>
        Qué: <sección ## Objetivo, ≤200>
        <estado> · N archivos · tests OK · 1,9 $
        🗂 Tarjeta · 📄 Spec/pitch · 🔀 Cambios
    """
    e = html.escape
    head = [f"{EMOJI.get(state, '•')} {e(task_id)} · {e(truncate(title, TITLE_MAX))}", e(context_line(lane, body))]
    what = objective(body)
    if what:
        head.append("Qué: " + e(what))
    head.append(e(status))
    tail = []
    if links:
        tail.append(" · ".join(f'<a href="{e(url, quote=True)}">{e(label)}</a>' for label, url in links))
    bullets = [e(b) for b in bullets or ()]
    while bullets and len("\n".join(head + bullets + tail)) > TEXT_MAX:  # recorta viñetas, nunca el HTML
        bullets.pop()
    return "\n".join(head + bullets + tail)


# --- almacén de message_id --------------------------------------------------------------------------

class MessageStore:
    """{task_id: {chat_id, thread_id, message_id, bot}}. Con `root`, un JSON por tarea (sobrevive a reinicios);
    sin él, en memoria (tests). `bot` = id numérico del bot que lo envió (nunca el token)."""

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

    def drop(self, tid: str) -> None:
        """Olvida el mensaje de una tarea (el siguiente aviso sale como mensaje nuevo)."""
        if self.root is None:
            self._mem.pop(tid, None)
            return
        with self._lock:
            self._path(tid).unlink(missing_ok=True)


class TaskNotices:
    """Publica el estado de una tarea en su único mensaje."""

    def __init__(self, notifier, store: MessageStore | None = None):
        self.notifier = notifier
        self.store = store or MessageStore()

    def publish(self, tid: str, text: str, target=None, lane_target=None, *, alert: bool = False,
                reply_markup: dict | None = None) -> None:
        rec = self.store.get(tid)
        bot = getattr(self.notifier, "bot_id", None)
        # Un bot solo edita/borra sus propios mensajes: si el aviso anterior es de otro bot (se pasó a
        # CARRILES_BOT_TOKEN), se envía uno nuevo y el viejo se deja como está.
        mine = bool(rec) and (not rec.get("bot") or not bot or str(rec.get("bot")) == str(bot))
        extra = {"reply_markup": reply_markup} if reply_markup is not None else {}
        if rec and mine and not alert:
            if self.notifier.edit(rec["chat_id"], rec["message_id"], text, **extra):
                return
            log.info("%s: no se pudo editar el mensaje %s; envío uno nuevo", tid, rec["message_id"])
        sent = self.notifier.send(text, target, lane_target, silent=not alert, **extra)
        if not sent:
            return
        if bot:
            sent = {**sent, "bot": str(bot)}
        if rec and mine and alert:  # el aviso nuevo sustituye al mensaje de progreso: un mensaje por tarea
            try:
                self.notifier.delete(rec["chat_id"], rec["message_id"])
            except Exception as exc:
                log.info("%s: no se pudo borrar el mensaje anterior: %s", tid, exc)
        self.store.put(tid, sent)
