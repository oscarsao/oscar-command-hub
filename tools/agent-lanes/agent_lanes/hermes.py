"""Thin wrapper over the `hermes kanban` CLI (pinned to 0.21.4) plus its contract check."""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import subprocess
import tempfile
import time

from . import proc as _proc
from . import grandes, telegram_lane, titulos
from pathlib import Path

log = logging.getLogger("agent_lanes")

# Fallo transitorio de SQLite en kanban.db-shm (1-2 veces/día): se reintenta. WinError 4551 es OTRO problema: no.
READONLY_MARK = "is read-only for this user"
NO_RETRY_MARK = "WinError 4551"
READONLY_BACKOFF = (1.0, 2.0)  # esperas entre los 3 intentos
# Subcomandos que se pueden repetir tras un timeout sin riesgo de aplicar dos veces (lecturas y asignaciones absolutas).
TIMEOUT_IDEMPOTENT = frozenset({"list", "show", "notify-list", "heartbeat", "assign"})

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
# Aprobación de un PR: Hermes registra APROBADO-MARIA cuando la da María; vale igual que APROBADO-OSCAR (mismo autor).
APPROVED_PREFIXES = ("APROBADO-OSCAR", "APROBADO-MARIA")


def is_approval(comment: dict) -> bool:
    """¿Comentario de aprobación de un operador? Exige el autor de Oscar: un worker no puede aprobarse a sí mismo."""
    return comment.get("author") == OSCAR_AUTHOR and (comment.get("body") or "").startswith(APPROVED_PREFIXES)

# Respuesta de Oscar llegada por Hermes (clarify en su DM, 28-09): Hermes la apunta en la tarjeta con SU perfil
# (`default`) y este prefijo EXACTO. Nada más cuenta como respuesta: ni "Oscar decide (28-09): …" ni "Respuesta de
# Oscar: …" escritos por `default`, ni nada de los workers (agent-lanes, lane-*) o del integrador. Ver README,
# "Responder desde Hermes".
HERMES_AUTHOR = "default"
HERMES_ANSWER_PREFIX = "RESPUESTA-OSCAR:"
HERMES_ANSWER_AUTHORS = frozenset({HERMES_AUTHOR, OSCAR_AUTHOR})  # lista blanca, nunca una lista negra
# P07 (F1-07, transitorio hasta F2): lo que escribe Hermes (`default`) debe decir QUIÉN responde y con qué id de
# Telegram: `RESPUESTA-MARIA (id=123456789): texto`. El id tiene que ser de Oscar o de un operador de people.yaml y
# casar con el nombre. `RESPUESTA-OSCAR:` sin id solo vale escrito por el bot de carriles (`oscar-telegram`), que ya
# comprobó quién pulsó. Cualquier otra forma no desbloquea nada.
HERMES_ID_ANSWER_RE = re.compile(r"^RESPUESTA-([A-Za-zÁÉÍÓÚÑÜáéíóúñü]+)\s*\(id=(\d{5,15})\)\s*:(.*)$", re.S)
_answer_people = None  # People para validar el id (perezoso; los tests lo sustituyen con set_answer_people)


def set_answer_people(people) -> None:
    global _answer_people
    _answer_people = people


def _people_for_answers():
    global _answer_people
    if _answer_people is None:
        import os
        from .decisions import OWNER_TELEGRAM_ID  # import tardío: decisions importa este módulo
        from .people import load_people
        _answer_people = load_people(os.environ.get("OWNER_TELEGRAM_ID") or OWNER_TELEGRAM_ID)
    return _answer_people


def _plain(name: str) -> str:
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", name or "") if not unicodedata.combining(ch)).casefold()


def answer_identity(comment: dict | None) -> tuple[str, str, str] | None:
    """(nombre, user_id, texto) de un `RESPUESTA-<PERSONA> (id=…)` de Hermes válido, o None."""
    c = comment or {}
    body = c.get("body")
    if c.get("author") != HERMES_AUTHOR or not isinstance(body, str):
        return None
    m = HERMES_ID_ANSWER_RE.match(body)
    if not m or not m.group(3).strip():
        return None
    try:
        person = _people_for_answers().lookup(m.group(2))
    except Exception:
        return None
    if person is None or not (person.is_owner or person.operador):
        return None
    if _plain(m.group(1)) not in {_plain(person.key), _plain(person.name)}:
        return None  # el nombre no es el de ese id: no se fía
    return person.name, person.user_id, m.group(3).strip()


def hermes_answer_text(comment: dict | None) -> str | None:
    """Texto de la respuesta si `comment` es válido: `RESPUESTA-OSCAR: …` del bot de carriles (oscar-telegram) o
    `RESPUESTA-<PERSONA> (id=…): …` de Hermes con un id que casa con people.yaml (P07). None en cualquier otro caso:
    un `RESPUESTA-OSCAR:` de Hermes sin id ya no desbloquea (no dice quién respondió)."""
    c = comment or {}
    if c.get("author") not in HERMES_ANSWER_AUTHORS:
        return None
    body = c.get("body")
    if not isinstance(body, str):
        return None
    if c.get("author") == OSCAR_AUTHOR and body.startswith(HERMES_ANSWER_PREFIX):
        return body[len(HERMES_ANSWER_PREFIX):].strip() or None
    who = answer_identity(c)
    return who[2] if who else None


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


# Comentarios del coordinador (Hermes `default` / `coordinador`): contexto que el worker debe respetar, p. ej. "NO vuelvas a
# preguntar X". No son respuestas de Oscar (eso sigue exigiendo `RESPUESTA-OSCAR:` o el bot): solo se muestran al worker.
COORDINATOR_AUTHORS = frozenset({HERMES_AUTHOR, "coordinador"})
COORDINATOR_NOTES_MAX = 6

# Limitación PERMANENTE del guard (instalar paquetes, borrar ramas, acceso a staging, fusionar PR...): no es una pregunta
# para Oscar, nadie puede "responderla" y reintentar da siempre lo mismo. Va como needs_input con este motivo
# estructurado; ni la autocuración ni el botón Reintentar la devuelven al carril: hace falta una consola interactiva.
GUARD_LIMIT_MARK = "LIMITACION-GUARD:"
_GUARD_LIMIT_RE = re.compile(r"(?:\bguard\b|\bhook\b|sandbox|permission|permiso)", re.I)
_GUARD_DENIED_RE = re.compile(r"(?:no puedo|no tengo|bloquea|deneg|prohib|vetad|no permit|denied|blocked)", re.I)


def is_guard_limit(reason: str | None) -> bool:
    return GUARD_LIMIT_MARK in (reason or "")


def looks_like_guard_limit(*texts) -> bool:
    """Heurística de respaldo cuando el worker no marca `limitation: guard`: el texto habla del guard/hook/permisos Y de
    algo que no puede hacer."""
    t = " ".join(str(x or "") for x in texts)
    return bool(_GUARD_LIMIT_RE.search(t) and _GUARD_DENIED_RE.search(t))


def similar_text(a, b) -> bool:
    """¿Dos preguntas son la misma reformulada? Texto normalizado igual, ratio de secuencia alto o muchas palabras
    (>3 letras) en común. Conservador: un falso positivo solo relanza una vez con las respuestas a la vista."""
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    if x == y or difflib.SequenceMatcher(None, x, y).ratio() >= 0.8:
        return True
    wx, wy = {w for w in re.findall(r"\w+", x) if len(w) > 3}, {w for w in re.findall(r"\w+", y) if len(w) > 3}
    return bool(wx and wy and len(wx & wy) / len(wx | wy) >= 0.7)


def answered_pairs(show: dict | None) -> list[tuple[str, str]]:
    """[(pregunta, respuesta)] de TODA la tarjeta (cualquier ronda), en orden; las dos últimas respuestas a preguntas
    parecidas se quedan en la más reciente. Solo las de los botones (`Respuesta de Oscar: <pregunta> → <respuesta>`)."""
    out: list[tuple[str, str]] = []
    for c in (show or {}).get("comments") or []:
        body = c.get("body") or ""
        if c.get("author") == OSCAR_AUTHOR and body.startswith(ANSWER_PREFIX) and ANSWER_ARROW in body:
            q, a = body[len(ANSWER_PREFIX):].split(ANSWER_ARROW, 1)
            q, a = " ".join(q.split()), a.strip()
            if q and a:
                out = [(k, v) for k, v in out if not similar_text(k, q)]
                out.append((q, a))
    return out


def find_answered(show: dict | None, questions) -> list[tuple[str, str]] | None:
    """[(pregunta nueva, respuesta previa)] si CADA pregunta nueva ya tiene una respuesta de Oscar a una pregunta
    parecida en la tarjeta (el worker reformuló lo ya decidido); None si alguna es realmente nueva."""
    pairs = answered_pairs(show)
    found = []
    for q in questions or ():
        text = q.get("question") if isinstance(q, dict) else q
        hit = next(((text, a) for k, a in reversed(pairs) if similar_text(k, text)), None)
        if hit is None:
            return None
        found.append(hit)
    return found or None


def coordinator_notes_from(show: dict | None) -> list[str]:
    """Últimos comentarios del coordinador en la tarjeta (sin los `RESPUESTA-…`: los válidos ya van como respuestas y
    los que no se pudieron validar (P07: sin id o con un id que no casa) no se le enseñan al worker como nota)."""
    notes = [" ".join((c.get("body") or "").split())[:600] for c in (show or {}).get("comments") or []
             if c.get("author") in COORDINATOR_AUTHORS and (c.get("body") or "").strip()
             and not (c.get("body") or "").lstrip().upper().startswith("RESPUESTA-")]
    return notes[-COORDINATOR_NOTES_MAX:]


def oscar_answers_from(show: dict | None) -> list[str]:
    """Respuestas de Oscar en la tarjeta, en orden: las de los botones (`Respuesta de Oscar: …` de oscar-telegram) y
    las que apuntó Hermes (`RESPUESTA-OSCAR: …`), estas como "Respuesta de Oscar (vía Hermes): <texto>".
    Varias respuestas a la MISMA pregunta o a una reformulada (Oscar la contestó otra vez, t_a2972b36): solo cuenta
    la última, en su posición; los comentarios no se borran. Las respuestas son de la TAREA, no de un texto exacto."""
    out: list[tuple[str | None, str]] = []
    for c in (show or {}).get("comments") or []:
        body = c.get("body") or ""
        if c.get("author") == OSCAR_AUTHOR and body.startswith(ANSWER_PREFIX):
            key = answer_question(body)
            out = [(k, b) for k, b in out if key is None or not similar_text(k, key)]
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
                continue
            # Pregunta reformulada por el worker (mismo asunto, otro texto): se empareja por parecido, no por texto.
            if ANSWER_ARROW in rest:
                asked, _, reply = rest.partition(ANSWER_ARROW)
                fuzzy = next((i for k, i in known if k and similar_text(k, asked)), None)
                if fuzzy is not None and reply.strip():
                    answers[fuzzy] = reply.strip()
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


def _transient_readonly(cp) -> bool:
    text = f"{getattr(cp, 'stderr', '') or ''} {getattr(cp, 'stdout', '') or ''}"
    return READONLY_MARK in text and NO_RETRY_MARK not in text


class HermesError(RuntimeError):
    pass


class HermesCLI:
    def __init__(self, board: str, exe: Path = HERMES_EXE, runner=_proc.run, author: str = "agent-lanes",
                 sleep=time.sleep, backoff: tuple[float, ...] = READONLY_BACKOFF,
                 timeout_backoff: tuple[float, ...] | None = None):
        self._sleep = sleep
        self._backoff = backoff
        self._timeout_backoff = timeout_backoff  # None = runner.cli_timeout_backoff de lanes.yaml
        self.board = board
        self.exe = str(exe)
        self._run = runner
        self.author = author

    def _call(self, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess:
        """`hermes kanban --board <b> <args>`. Base de datos read-only: reintento (la escritura no se hizo). Timeout
        (t_cce5da81): reintento con espera progresiva solo en lo idempotente (TIMEOUT_IDEMPOTENT, `--help`); agotado,
        o en una escritura, devuelve un fallo (returncode proc.TIMEOUT_RC) en vez de lanzar, y anota el fallo
        transitorio en el hilo (lo usa la cola de decisiones). Tiempo y esperas: `runner.cli_timeout_*` de lanes.yaml."""
        cfg = _proc.cli_settings()
        timeout = timeout or cfg["timeout"]
        tbackoff = self._timeout_backoff if self._timeout_backoff is not None else cfg["backoff"]
        cmd = [self.exe, "kanban", "--board", self.board, *args]
        sub = args[0] if args else "?"
        idempotent = sub in TIMEOUT_IDEMPOTENT or "--help" in args
        ro = to = 0
        while True:
            try:
                cp = self._run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout)
            except subprocess.TimeoutExpired:
                if not idempotent or to >= len(tbackoff):
                    reason = f"hermes kanban {sub}: sin respuesta en {int(timeout)} s ({to + 1} intento(s))"
                    log.warning("%s; se sigue sin ella", reason)
                    _proc.mark_transient(reason)
                    return subprocess.CompletedProcess(cmd, _proc.TIMEOUT_RC, "", f"timeout (transitorio): {reason}")
                wait = tbackoff[to]
                to += 1
                log.warning("hermes kanban %s: sin respuesta en %ss (transitorio); reintento %d/%d en %gs",
                            sub, int(timeout), to, len(tbackoff), wait)
                self._sleep(wait)
                continue
            if cp.returncode == 0 or not _transient_readonly(cp):
                return cp
            if ro >= len(self._backoff):
                _proc.mark_transient(f"hermes kanban {sub}: base de datos read-only")
                return cp  # agotados los reintentos se devuelve el resultado original, igual que antes
            wait = self._backoff[ro]
            ro += 1
            log.warning("hermes kanban %s: base de datos read-only (transitorio); reintento %d/%d en %gs",
                        sub, ro, len(self._backoff), wait)
            self._sleep(wait)

    def list_status(self, assignee: str, status: str, sort: str = "priority") -> list[dict]:
        cp = self._call("list", "--assignee", assignee, "--status", status, "--json", "--sort", sort)
        if cp.returncode != 0:
            raise HermesError(f"list failed: {cp.stderr.strip()[:300]}")
        return json.loads(cp.stdout or "[]")

    def list_status_all(self, status: str) -> list[dict]:
        """Todas las tarjetas del tablero en un estado, de cualquier assignee (guarda de tablero)."""
        cp = self._call("list", "--status", status, "--json")
        if cp.returncode != 0:
            raise HermesError(f"list failed: {cp.stderr.strip()[:300]}")
        return json.loads(cp.stdout or "[]")

    def list_ready(self, assignee: str) -> list[dict]:
        return self.list_status(assignee, "ready")

    def create(self, title: str, body: str, assignee: str, *, priority: int | None = None, key: str | None = None,
               created_by: str | None = None, parents: list[str] | None = None) -> str:
        """Crea una tarjeta y devuelve su id. --body-file: un cuerpo que empieza por "- " no se toma como flag.
        `parents` = ids del mismo tablero de los que depende (un --parent por id)."""
        titulos.warn_title(title)  # aviso en el log si no sigue «[Proyecto] Qué · paso»; nunca bloquea
        # Enrutado automático (22-10): [Telegram] / bots de Telegram -> claude-telegram, si trabaja en este tablero.
        routed = telegram_lane.ruta(assignee, title, body)
        if routed != assignee and self.board == telegram_lane.board_destino():
            body, assignee = telegram_lane.con_repo(body, assignee), routed
        body = grandes.marcar(title, body)  # tarea grande -> «Tamaño: GRANDE» en el cuerpo (hueco grande del runner)
        fd, tmp = tempfile.mkstemp(prefix="tarjeta-", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
            args = ["create", "--assignee", assignee, "--body-file", tmp, "--created-by", created_by or self.author,
                    "--json"]
            if priority is not None:
                args += ["--priority", str(priority)]
            if key:
                args += ["--idempotency-key", key]
            for parent in parents or ():
                args += ["--parent", parent]
            cp = self._call(*args, title)
        finally:
            os.remove(tmp)
        if cp.returncode != 0:
            raise HermesError(f"create failed: {cp.stderr.strip()[:300]}")
        found = re.search(r"t_[0-9a-f]{8}", cp.stdout or "")
        if not found:
            raise HermesError(f"create sin id en la salida: {(cp.stdout or '')[:200]}")
        return found.group(0)

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

    def answered_for(self, task_id: str, questions) -> list[tuple[str, str]] | None:
        return find_answered(self.show(task_id), questions)

    def coordinator_notes(self, task_id: str) -> list[str]:
        return coordinator_notes_from(self.show(task_id))

    def assign(self, task_id: str, profile: str) -> bool:
        return self._call("assign", task_id, profile).returncode == 0

    def set_priority(self, task_id: str, priority: int) -> bool:
        return self._call("edit", task_id, "--priority", str(int(priority))).returncode == 0

    def unblock(self, task_id: str) -> bool:
        return self._call("unblock", task_id).returncode == 0

    def link(self, parent_id: str, child_id: str) -> bool:
        """Dependencia real padre -> hija (`kanban link`): la hija espera (todo) hasta que el padre esté hecho."""
        if not (_TID_RE.match(parent_id or "") and _TID_RE.match(child_id or "")):
            return False
        return self._call("link", parent_id, child_id).returncode == 0

    def requeue_triage(self, task_id: str, author: str = OSCAR_AUTHOR, *, python: Path = HERMES_PYTHON) -> str | None:
        """Saca de `triage` una tarea que Hermes aparcó por bloquearse dos veces por lo mismo (block_loop_detected;
        desde ahí `unblock` falla): specify_triage_task con la API de Hermes en su venv, sin tocar título ni cuerpo.
        Devuelve el estado resultante ("ready", o "todo" si espera a un padre) o None si no estaba en triage/falló."""
        self.last_error = None
        if not _TID_RE.match(task_id or "") or not _BOARD_RE.match(self.board or ""):
            self.last_error = "id de tarea o tablero no válidos"
            return None
        try:
            cp = self._run([str(python), "-c", _REQUEUE_TRIAGE, self.board, task_id, author], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=60, cwd=str(HERMES_AGENT_DIR))
        except (OSError, subprocess.SubprocessError):
            self.last_error = "no se pudo ejecutar Hermes"
            return None
        if cp.returncode != 0 or (cp.stdout or "").strip().splitlines()[-1:] != ["ok"]:
            self.last_error = "Hermes rechazó specify_triage_task (¿ya no está en triage?)"
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

    def archive(self, task_id: str) -> bool:
        """Archiva la tarjeta (Aparcar de una tarjeta de decisión de Oscar)."""
        return self._call("archive", task_id).returncode == 0

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

    def subscribe_telegram(self, task_id: str, chat: str, thread: str = "0") -> bool:
        """Aviso de Hermes al solicitante (`notify-subscribe … --delivery-mode notify`, decisión de Oscar 23-10).
        `drop_telegram_subs` respeta estas suscripciones (modo notify)."""
        args = ["notify-subscribe", "--platform", "telegram", "--chat-id", str(chat)]
        if str(thread) not in ("", "0"):
            args += ["--thread-id", str(thread)]
        return self._call(*args, "--delivery-mode", "notify", task_id).returncode == 0

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
            if sub.get("delivery_mode") == "notify":  # la puso `tarjeta nueva` para el solicitante: se conserva
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
