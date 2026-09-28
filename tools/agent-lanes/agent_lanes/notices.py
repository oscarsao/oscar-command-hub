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
from typing import Callable
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
FOR_OSCAR_MAX = 300  # `for_oscar` del worker: 1-2 frases llanas que sustituyen al "Qué:" técnico del aviso
# Opciones por defecto de una pregunta que llega sin `options` (p. ej. "¿Doy luz verde a la spec?"): Oscar debe
# poder decidir con un toque. La respuesta vuelve al worker como si fuera una opción del contrato.
DEFAULT_OPTIONS = ("Sí, adelante", "No")

# Tarjetas de decisión de Oscar (no son tareas de carril): asignadas a `oscar`, en ready/blocked y con una de estas
# etiquetas en el título. La misma regla para el resumen de las 8:00, /decisiones y los recordatorios.
OSCAR_ASSIGNEE = "oscar"
DECISION_TAGS = ("[DECISIÓN", "[SEMANA", "· DECISIÓN]", "[IDEA")
DECISION_CARD_STATUSES = ("ready", "blocked")
BOARD_BRANDS = {"migrateam": "MigraTeam", "oscarhq": "Píldora"}  # marca de una tarjeta de Oscar según su tablero


def is_decision_card(task: dict | None) -> bool:
    t = task or {}
    return (t.get("assignee") == OSCAR_ASSIGNEE and t.get("status") in DECISION_CARD_STATUSES
            and any(tag in (t.get("title") or "") for tag in DECISION_TAGS))


def truncate(text, n: int) -> str:
    """Una línea de como mucho n caracteres. Tolera valores que no son texto (p. ej. una pregunta del worker que
    llega como objeto {question, options, recommended}: el 28-09 un dict aquí tumbó el aviso con .split)."""
    if isinstance(text, dict) and "question" in text:
        text = text.get("question")
    s = " ".join(("" if text is None else str(text)).split())
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
    if isinstance(questions, (str, dict)):  # una sola pregunta suelta en vez de una lista
        questions = [questions]
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


def decision_since(task: dict | None, detail: dict | None = None) -> float | None:
    """Desde cuándo espera la decisión: created_at del último evento `blocked` (show --json) o, sin él, el de la
    tarea. La usan el resumen de las 8:00 (⏰ >24h), /decisiones y los recordatorios."""
    for ev in reversed((detail or {}).get("events") or ()):
        if ev.get("kind") == "blocked" and ev.get("created_at"):
            return float(ev["created_at"])
    ts = (task or {}).get("created_at")
    return float(ts) if isinstance(ts, (int, float)) and ts else None


def hours_ago(since: float | None, now: float) -> int:
    return max(0, int((now - since) // 3600)) if since else 0


def with_default_options(questions, yes_no: bool = True) -> list[dict]:
    """Preguntas normalizadas; la que llega sin opciones recibe DEFAULT_OPTIONS (sin recomendada) y la marca
    `default_options` para que los botones digan [✅ Sí, adelante] [❌ No]. `yes_no=False` (escaladas de review, donde
    las "preguntas" son cambios pedidos y "Sí" sería ambiguo): sin opciones por defecto."""
    out = []
    for q in normalize_questions(questions):
        if not q["options"] and yes_no:
            q = {**q, "options": list(DEFAULT_OPTIONS), "recommended": None, "default_options": True}
        out.append(q)
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
           body: str | None = None, for_oscar: str | None = None, tree: list[str] | None = None) -> str:
    """HTML (parse_mode=HTML). Unas 5 líneas, más el árbol (padre/hijas) y las viñetas de needs_input:

        <emoji> t_xxx · <título ≤60>
        <Marca> · <carril> · <modo OASP si aparece en el cuerpo>
        Qué: <sección ## Objetivo, ≤200>   (o "Para ti: <for_oscar>" si el worker lo explicó en llano)
        <estado> · N archivos · tests OK · 1,9 $
        🔗 Parte de: t_x · … / ⏸ Depende de: … / ↳ N subtareas: …   (`tree`, texto plano: deps.tree_lines)
        🗂 Tarjeta · 📄 Spec/pitch · 🔀 Cambios
    """
    e = html.escape
    head = [f"{EMOJI.get(state, '•')} {e(task_id)} · {e(truncate(title, TITLE_MAX))}", e(context_line(lane, body))]
    plain = truncate(for_oscar, FOR_OSCAR_MAX) if for_oscar else ""
    what = None if plain else objective(body)
    if plain:
        head.append("Para ti: " + e(plain))
    elif what:
        head.append("Qué: " + e(what))
    head.append(e(status))
    head += [e(truncate(line, 300)) for line in (tree or ())[:4]]
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

    # Espejos: otras copias del aviso de la tarea (tarjeta de /decisiones, /aprobar o /tarea, aviso en el DM de
    # Oscar). Una decisión tomada en cualquiera edita todas y retira sus botones. Registro principal intacto
    # ({chat_id, thread_id, message_id, bot, token}); las copias van en "mirrors".

    def add_mirror(self, tid: str, sent: dict | None, *, token: str | None = None, bot: str | None = None) -> None:
        if not sent or not sent.get("message_id"):
            return
        mirror = {"chat_id": str(sent["chat_id"]), "thread_id": str(sent.get("thread_id") or "0"),
                  "message_id": sent["message_id"], **({"token": token} if token else {}),
                  **({"bot": str(bot)} if bot else {})}
        rec = dict(self.get(tid) or {})
        rec["mirrors"] = [m for m in rec.get("mirrors") or ()
                          if (str(m.get("chat_id")), m.get("message_id")) != (mirror["chat_id"], mirror["message_id"])]
        rec["mirrors"].append(mirror)
        rec["mirrors"] = rec["mirrors"][-10:]
        self.put(tid, rec)

    def all_messages(self, tid: str) -> list[dict]:
        """Aviso principal + espejos de una tarea."""
        rec = self.get(tid) or {}
        out = [rec] if rec.get("message_id") else []
        return out + [m for m in rec.get("mirrors") or () if m.get("message_id")]


def markup_token(markup: dict | None) -> str | None:
    """Token de un teclado de decisiones (callback_data "<token>:<n>"); None si no hay botones."""
    for row in (markup or {}).get("inline_keyboard") or ():
        for b in row:
            data = b.get("callback_data") or ""
            if ":" in data:
                return data.split(":", 1)[0]
    return None


class TaskNotices:
    """Publica el estado de una tarea en su único mensaje (y en sus espejos)."""

    def __init__(self, notifier, store: MessageStore | None = None):
        self.notifier = notifier
        self.store = store or MessageStore()

    def publish(self, tid: str, text: str, target=None, lane_target=None, *, alert: bool = False,
                reply_markup: dict | None = None, mirror_to: str | None = None, channel: str | None = None,
                leave_behind: Callable[[dict], str] | None = None) -> None:
        """`mirror_to`: chat (DM de Oscar) que recibe además una copia del aviso, con los mismos botones. Si el aviso
        principal ya cayó en ese chat, no se duplica.

        `channel`: canal del aviso (None = tema del carril; "integration" = tema de Integración). Un aviso de otro
        canal nunca edita el mensaje del anterior: se envía uno nuevo. `leave_behind(sent)`: al pasar una alerta a otro
        canal, el mensaje anterior no se borra sino que se edita con esta línea corta (enlace al nuevo)."""
        rec = self.store.get(tid)
        bot = getattr(self.notifier, "bot_id", None)
        # Un bot solo edita/borra sus propios mensajes: si el aviso anterior es de otro bot (se pasó a
        # CARRILES_BOT_TOKEN), se envía uno nuevo y el viejo se deja como está.
        same_bot = bool(rec) and (not rec.get("bot") or not bot or str(rec.get("bot")) == str(bot))
        same_channel = (rec or {}).get("channel") == channel
        mine = same_bot and same_channel
        extra = {"reply_markup": reply_markup} if reply_markup is not None else {}
        mirrors = [m for m in (rec or {}).get("mirrors") or ()
                   if not m.get("bot") or not bot or str(m.get("bot")) == str(bot)]
        if not same_channel and not alert:
            mirrors = []  # las copias del otro canal no siguen a este aviso
        if rec and mine and not alert:
            if self.notifier.edit(rec["chat_id"], rec["message_id"], text, **extra):
                for m in mirrors:  # las copias siguen el estado de la tarea (sin notificar)
                    try:
                        self.notifier.edit(m["chat_id"], m["message_id"], text, **extra)
                    except Exception as exc:
                        log.info("%s: no se pudo editar la copia %s: %s", tid, m.get("message_id"), exc)
                return
            log.info("%s: no se pudo editar el mensaje %s; envío uno nuevo", tid, rec["message_id"])
        sent = self.notifier.send(text, target, lane_target, silent=not alert, **extra)
        if not sent:
            return
        if bot:
            sent = {**sent, "bot": str(bot)}
        token = markup_token(reply_markup)
        if token:
            sent["token"] = token
        # trail = la línea corta que quedó en el canal anterior; sigue apuntando al aviso vigente de este canal.
        trail = (rec or {}).get("trail") if same_channel else None
        if rec and same_bot and alert and not same_channel and leave_behind:
            # Cambio de canal (tema del carril -> Integración): el viejo queda como una línea con enlace al nuevo.
            trail = {k: rec.get(k) for k in ("chat_id", "thread_id", "message_id")}
        elif rec and same_bot and alert:  # el aviso nuevo sustituye al mensaje de progreso: un mensaje por tarea
            try:
                self.notifier.delete(rec["chat_id"], rec["message_id"])
            except Exception as exc:
                log.info("%s: no se pudo borrar el mensaje anterior: %s", tid, exc)
        if alert:
            # Copias de la decisión anterior: se quedan como historial pero sin botones (su token ya no vale).
            for m in mirrors:
                try:
                    if hasattr(self.notifier, "edit_markup"):
                        self.notifier.edit_markup(m["chat_id"], m["message_id"], None)
                except Exception:
                    pass
            mirrors = []
        if trail and leave_behind:
            try:
                self.notifier.edit(trail["chat_id"], trail["message_id"], leave_behind(sent))
            except Exception as exc:
                log.info("%s: no se pudo dejar la línea de enlace: %s", tid, exc)
        self.store.put(tid, {**sent, **({"mirrors": mirrors} if mirrors else {}),
                             **({"channel": channel} if channel else {}), **({"trail": trail} if trail else {})})
        if alert and mirror_to and hasattr(self.notifier, "send_to") and str(sent.get("chat_id")) != str(mirror_to):
            try:
                copy = self.notifier.send_to(str(mirror_to), None, text, **extra)
            except Exception as exc:  # DM sin /start ("chat not found", 403): no rompe el aviso del tema
                log.info("%s: no se pudo enviar la copia al DM: %s", tid, exc)
                return
            self.store.add_mirror(tid, copy, token=token, bot=bot)
