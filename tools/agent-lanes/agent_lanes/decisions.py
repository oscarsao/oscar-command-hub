"""Decisiones de Oscar con un toque desde Telegram: botones inline en los avisos y escucha de pulsaciones.

Solo con CARRILES_BOT_TOKEN (bot propio de los carriles). Con el bot de Hermes no hay botones ni escucha: Hermes
ya consume getUpdates de su bot y dos consumidores del mismo bot se pisan (409 Conflict).

Flujo de una pulsación:
  1. El hilo de escucha recibe el callback_query y responde answerCallbackQuery al momento (<3 s).
  2. La acción (hermes / gh) corre en un hilo aparte, nunca en el pool de workers (puede estar ocupado 2 h).
  3. El aviso se edita con lo que pasó de verdad y sin botones; si la acción falla, vuelven los botones.

callback_data = "<token>:<n>" (≤64 bytes). El token apunta a .state/callbacks/<token>.json con la tarea, el
tablero, el carril y los botones; un token se consume una sola vez (doble toque = una sola acción).
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from html import escape as _esc
from pathlib import Path
from typing import Callable

from . import bloqueos
from . import proc as _proc
from .bandeja import VetoBook
from .config import ROOT
from .card_options import LETTERS as CARD_LETTERS
from .hermes import (ANSWER_PREFIX, HERMES_ANSWER_PREFIX, OSCAR_AUTHOR, REVIEW_AUTHOR, answer_progress,
                     is_guard_limit, last_needs_input_block, resume)
from .explain import explain as _explain
from .free_text import FREE_ACTIONS, FreeText
from .people import People, load_people
from .notices import (DECISION_CARD_STATUSES, OPTION_LETTERS, card_url, normalize_questions, questions_block,
                      render, status_line, truncate, with_default_options)
from .tipo_peticion import A_ASK as PEDIR_ASK, KIND as PEDIR_KIND
from .reconcile import INTEGRATOR_PROFILE, REC_ARCHIVE, REC_KEEP
from .review import CHANGES_PREFIX

log = logging.getLogger("agent_lanes")

# Un JSON por teclado enviado (callback_data corto -> tarea/acción). Lo escriben el runner y `lanes.py renotify`;
# lo lee la escucha del runner-servicio.
CALLBACKS_DIR = ROOT / ".state" / "callbacks"
OWNER_TELEGRAM_ID = "6744452215"  # Oscar; configurable con OWNER_TELEGRAM_ID en .env
NOT_OWNER = ("Esto es solo para operadores. Pide a Oscar o a María que lo decida, o escribe tu petición en texto libre "
             "y se la paso")
GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"
CALLBACK_DATA_MAX = 64  # bytes, límite de Telegram
PARK_PROFILE = "oscar"
PR_FOOTER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"

APPROVE, CHANGES, PARK, RETRY, OPTION, OTHER = "approve", "changes", "park", "retry", "option", "other"
GUARD_SNOOZE = "guard_snooze"  # ⏳ Aplazar un bloqueo de guard escalado a Oscar: 24 h más en la cola del integrador
TO_INTEGRADOR = "to_integrador"  # 🧑‍💻 Que lo resuelva el integrador: Oscar no puede actuar desde Telegram
UNTRIAGE = "untriage"      # 🧊 Sacar de triage: specify_triage_task sin reescribir la tarjeta (nunca unblock)
EXPLAIN = "explain"        # 💬 Explícame más: responde con una explicación llana; no consume el teclado
DETAIL = "detail"          # 🔎 Más detalle (aviso de bloqueo): motivo técnico y últimos comentarios; no consume el teclado
ACCEPT_ALL = "accept_all"  # cabecera de /decisiones: aplica la opción recomendada en todas las que la tienen
HAZLO = "hazlo"            # /hazlo con duda: el botón elige el carril donde crear la tarea
FLUJO = "flujo"            # /flujo: [Crear todo] / [Cancelar] sobre la propuesta de tarjetas encadenadas
DEPLOY_ASK = "deploy_ask"  # /desplegar: 1.er toque = pide confirmar; el 2.º (✅ Sí) es el int_deploy del integrador
DEPLOY_NO = "deploy_no"    # /desplegar: ✖ No en la confirmación
ACCEPT_CARD = "accept_card"  # ✅ Acepto todas las recomendadas: las preguntas pendientes de ESTA tarjeta, de una vez
SEE_ALL = "see_all"        # "N más · ver todas" de /decisiones
FALTA = "falta"            # /falta: botón de un paso (fusionar lote, migración aplicada, desplegar)
# Tarjetas de decisión de Oscar (/decisiones): su propio juego de acciones; no tocan las rutas de las tareas de carril.
CARD_OPTION = "card_option"   # A/B/C: anota RESPUESTA-OSCAR con la opción y completa la tarjeta
CARD_REC = "card_rec"         # ✅ La recomendada (misma ruta que CARD_OPTION con el índice recomendado)
CARD_DONE = "card_done"       # ✅ Hecho (tarjeta sin opciones)
CARD_SNOOZE = "card_snooze"   # ⏳ Aplazar 7 d: comentario + oculta de /decisiones y recordatorios 7 días
CARD_PARK = "card_park"       # 🗄 Aparcar: comentario + archive
CARD_ASK = "card_ask"         # ✍️ Responder: force_reply; el texto es la respuesta
CARD_VETO = "card_veto"       # ⛔ Vetar: la tarjeta reversible deja de ejecutarse sola (no consume el teclado)
CARD_ACCEPT_ALL = "card_accept_all"  # ✅ Aceptar las recomendadas del lote de tarjetas de /decisiones
SNOOZE_DAYS = 7
BUTTON_OPTION_MAX = 22  # caracteres del texto de la opción en su botón ("A · Aprobar el PR")
REPLY_ACTIONS = (CHANGES, OTHER, CARD_ASK, PEDIR_ASK)  # piden texto a Oscar con force_reply
EXPLAIN_BUTTON = {"text": "💬 Explícame más", "action": EXPLAIN}
DEFAULT_OPTION_LABELS = ("✅ Sí, adelante", "❌ No")  # botones de una pregunta sin opciones (DEFAULT_OPTIONS)
ALL_LABEL = "todas las preguntas"  # ✍️ de una escalada de review (= hermes.LEGACY_ALL_LABELS)
TEAM_NOTICE_TEXT = "Esto lo lleva el asistente del equipo: escríbele a él. Yo solo gestiono los trabajos de los carriles."
EXPLAIN_FAILED ="💬 No pude generar la explicación ahora; mira la tarjeta o responde con ✍️"
DETAIL_FAILED = "🔎 No pude leer el detalle ahora; mira la tarjeta del kanban"


def detail_text(show: dict, cfg: dict) -> str:
    """Texto de [🔎 Más detalle] a partir del `show` de la tarjeta: último motivo de bloqueo + últimos comentarios,
    recortado a `umbrales.detalle_max` caracteres."""
    umbrales = cfg.get("umbrales") or {}
    maximo, n_com = int(umbrales.get("detalle_max") or 3000), int(umbrales.get("detalle_comentarios") or 3)
    blocks = [ev for ev in show.get("events") or [] if ev.get("kind") == "blocked"]
    reason = str(((blocks[-1].get("payload") or {}).get("reason") or "") if blocks else "").strip()
    comments = [str(c.get("body") or "").strip() for c in (show.get("comments") or [])][-n_com:] if n_com > 0 else []
    parts = [f"Motivo del bloqueo:\n{reason}" if reason else "Sin motivo de bloqueo registrado en la tarjeta."]
    parts += [f"Comentario:\n{c}" for c in comments if c]
    return truncate("\n\n".join(parts), maximo)


def keyboard_spec(state: str, *, block_kind: str | None = None, questions=None,
                  yes_no: bool = True, ops: bool = False, index: int = 0,
                  accept_card: bool = True, retried: bool = False) -> list[list[dict]] | None:
    """Botones por estado: filas de {text, action, index}. None = aviso sin botones (en curso, en review...).
    `ops`: el carril ops no tiene carril review ni PR; su review la valida Oscar directamente.
    `index`: pregunta en curso (con varias preguntas van en secuencia: una por paso, en la misma tarjeta).
    Las opciones van escritas en el mensaje (A, B, C...): los botones solo dicen la letra, ⭐ Recomendada y ✍️ Otra.
    `accept_card`: con varias preguntas y recomendada en todas las pendientes, añade ✅ Acepto todas las recomendadas.
    `retried`: Oscar ya pulsó Reintentar y volvió a fallar; añade ✍️ Responder para corregir y reanudar desde Telegram."""
    park = {"text": "🗄 Aparcar", "action": PARK}
    if state == "review" and ops:
        return [[{"text": "✅ Validar", "action": APPROVE}, {"text": "🔁 Pedir cambios", "action": CHANGES}, park]]
    if state == "done":
        return [[{"text": "✅ Aprobar", "action": APPROVE}, {"text": "🔁 Pedir cambios", "action": CHANGES}, park]]
    if state == "needs_input":
        rows = []
        # Una pregunta sin opciones recibe [✅ Sí, adelante] [❌ No]: siempre se puede decidir con un toque.
        qs = with_default_options(questions, yes_no)
        q = qs[min(max(index, 0), len(qs) - 1)] if qs else None
        if q and q.get("default_options"):
            rows.append([{"text": label, "action": OPTION, "index": i} for i, label in enumerate(DEFAULT_OPTION_LABELS)])
        elif q and q["options"]:  # botones de la pregunta en curso; la siguiente sale al responderla
            rows.append([{"text": OPTION_LETTERS[i], "action": OPTION, "index": i}
                         for i in range(min(len(q["options"]), len(OPTION_LETTERS)))])
            other = {"text": "✍️ Otra", "action": OTHER}
            rows.append([{"text": "⭐ Recomendada", "action": OPTION, "index": q["recommended"]}, other]
                        if q["recommended"] is not None else [other])
            rows.append([park, dict(EXPLAIN_BUTTON)])
            pending = qs[min(max(index, 0), len(qs) - 1):]
            if accept_card and yes_no and len(qs) > 1 and all(x["recommended"] is not None for x in pending):
                rows.append([{"text": "✅ Acepto todas las recomendadas", "action": ACCEPT_CARD}])
            return rows
        rows.append([{"text": "✍️ Otra respuesta", "action": OTHER}, park])
        rows.append([dict(EXPLAIN_BUTTON)])
        return rows
    if state == "blocked" and block_kind == "review_stuck":  # tarea en review que no se pudo bloquear: decide Oscar
        return [[{"text": "✅ Aprobar", "action": APPROVE}, {"text": "🔁 Pedir cambios", "action": CHANGES}, park]]
    if state == "blocked" and block_kind == "console_escalated":  # 2 h en la cola del integrador sin resolverse
        return [[{"text": "🧑‍💻 Que lo resuelva el integrador", "action": TO_INTEGRADOR}],
                [{"text": "⏳ Aplazar", "action": GUARD_SNOOZE}]]
    if state == "blocked" and block_kind in ("config", "console"):  # reintentar no arregla la config ni el guard: solo aparcar
        return [[park], [{"text": "🧑‍💻 Que lo resuelva el integrador", "action": TO_INTEGRADOR}]]
    if state == "blocked" and block_kind in ("transient", "estructural"):
        # Botones por clase desde bloqueos.yaml; el estructural nunca lleva Reintentar (repetiría el error en bucle).
        clase = bloqueos.ESTRUCTURAL if block_kind == "estructural" else bloqueos.TRANSITORIO
        by_id = {"retry": {"text": "🔄 Reintentar", "action": RETRY}, "park": park,
                 "integrador": {"text": "🧑‍💻 Que lo resuelva el integrador", "action": TO_INTEGRADOR},
                 "detalle": {"text": "🔎 Más detalle", "action": DETAIL}}
        ids = bloqueos.botones_ids(clase)
        rows = [[by_id[b] for b in ids if b != "detalle"], [by_id["detalle"]] if "detalle" in ids else []]
        rows = [r for r in rows if r]
        return rows + [[{"text": "✍️ Responder", "action": OTHER}]] if retried and clase == bloqueos.TRANSITORIO else rows
    if state == "stuck":  # 🧊 en triage por bloqueo repetido (block_loop_detected): unblock no vale, _untriage la saca
        return [[{"text": "🧊 Sacar de triage", "action": UNTRIAGE}, {"text": "✍️ Responder", "action": OTHER},
                 {"text": "🗄 Archivar", "action": PARK}]]
    return None


def card_keyboard_spec(n_options: int, recommended: int | None, options=None, veto: bool = False) -> list[list[dict]]:
    """Botones de una tarjeta de decisión. Con opciones: un botón por opción con su texto corto ("A · Aprobar el PR";
    sin los textos, solo la letra), la recomendada, aplazar y aparcar (+ ⛔ Vetar si la tarjeta es reversible y corre
    el plazo de veto). La respuesta libre ("A, pero con X") es ✍️ Responder. Sin opciones: botones genéricos."""
    snooze = {"text": f"⏳ Aplazar {SNOOZE_DAYS} d", "action": CARD_SNOOZE}
    park = {"text": "🗄 Aparcar", "action": CARD_PARK}
    ask = {"text": "✍️ Responder", "action": CARD_ASK}
    if n_options < 2:
        return [[{"text": "✅ Hecho", "action": CARD_DONE}, snooze], [park, ask]]
    n = min(n_options, len(CARD_LETTERS))
    texts = list(options or ())
    rows = [[{"text": f"{CARD_LETTERS[i]} · {truncate(texts[i], BUTTON_OPTION_MAX)}"
              if i < len(texts) and texts[i] else CARD_LETTERS[i], "action": CARD_OPTION, "index": i}]
            for i in range(n)]
    if isinstance(recommended, int) and 0 <= recommended < n:
        rows.append([{"text": f"✅ La recomendada ({CARD_LETTERS[recommended]})", "action": CARD_REC,
                      "index": recommended}])
    rows.append([snooze, park])
    rows.append([ask] + ([{"text": "⛔ Vetar", "action": CARD_VETO}] if veto else []))
    return rows


class CardSnoozes:
    """Tarjetas aplazadas: .state/card_snooze.json {task_id: hasta (epoch)}. /decisiones y los recordatorios las
    ocultan hasta entonces (hermes `schedule` no admite fecha: dejaría la tarjeta fuera de ready/blocked para siempre)."""

    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()

    def _load(self) -> dict[str, float]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {str(k): float(v) for k, v in raw.items() if isinstance(v, (int, float))}

    def add(self, task_id: str, days: float = SNOOZE_DAYS) -> float:
        """Aplaza `days` días desde ahora y devuelve el epoch hasta el que se oculta."""
        now = self._clock()
        until = now + days * 86400
        with self._lock:
            data = {k: v for k, v in self._load().items() if v > now}
            data[task_id] = until
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        return until

    def hidden(self) -> set[str]:
        with self._lock:
            now = self._clock()
            return {k for k, v in self._load().items() if v > now}


def _clamp(index, qs) -> int:
    try:
        i = int(index or 0)
    except (TypeError, ValueError):
        i = 0
    return min(max(i, 0), max(len(qs) - 1, 0))


class RetryBook:
    """Reintentos pulsados con 🔄 Reintentar: .state/retry_marks.json {task_id: {at, key}}. `key` = huella del motivo del
    bloqueo previo. Si el runner vuelve a bloquear la tarea dentro del plazo, el aviso dice que el reintento no
    funcionó y si es el mismo motivo (antes el botón desaparecía y reaparecía sin explicación)."""
    TTL = 24 * 3600

    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()

    @staticmethod
    def key(reason: str | None) -> str:
        """Huella del motivo: sin la línea "Para Oscar", espacios normalizados y primeros 80 caracteres."""
        lines = [l for l in (reason or "").splitlines() if not l.strip().startswith("Para Oscar")]
        return " ".join(" ".join(lines).split())[:80]

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}
        now = self._clock()
        return {k: v for k, v in raw.items() if isinstance(v, dict) and now - float(v.get("at") or 0) < self.TTL}

    def mark(self, task_id: str, reason: str | None) -> None:
        with self._lock:
            data = self._load()
            data[task_id] = {"at": self._clock(), "key": self.key(reason)}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            return self._load().get(task_id)

    def clear(self, task_id: str) -> None:
        """Cierra el ciclo de reintento (salió bien, falló al reencolar, o Oscar respondió/aparcó)."""
        with self._lock:
            data = self._load()
            if task_id not in data:
                return
            del data[task_id]
            tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)

    def verdict(self, task_id: str, reason: str | None) -> str | None:
        """Frase pública si la tarea se reintentó y vuelve a estar bloqueada ("mismo motivo" u "otro motivo"); None si
        no hubo reintento reciente. El registro se conserva: el teclado lo usa para ofrecer ✍️ Responder."""
        mark = self.get(task_id)
        if not mark:
            return None
        if mark.get("key") and self.key(reason) == mark["key"]:
            return "🔁 el reintento no funcionó: sigue atascada por el mismo motivo"
        return "🔁 el reintento no funcionó: ahora se bloquea por otro motivo"


class CallbackStore:
    """.state/callbacks/<token>.json (un teclado) y reply-<chat>_<msg>.json (force_reply pendiente)."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _write(self, path: Path, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def issue(self, rec: dict, spec: list[list[dict]]) -> tuple[str, dict]:
        """Guarda el registro y devuelve (token, reply_markup)."""
        token = secrets.token_urlsafe(9)  # 12 caracteres
        buttons, rows = [], []
        for row in spec:
            out = []
            for b in row:
                data = f"{token}:{len(buttons)}"
                assert len(data.encode()) <= CALLBACK_DATA_MAX
                buttons.append({"action": b["action"], "index": b.get("index"), "text": b["text"]})
                out.append({"text": b["text"], "callback_data": data})
            rows.append(out)
        self._write(self.root / f"{token}.json", {**rec, "buttons": buttons, "rows": [len(r) for r in rows],
                                                  "created": time.time()})
        self.prune()
        return token, {"inline_keyboard": rows}

    def live(self):
        """(token, registro) de cada teclado aún sin usar (no las respuestas force_reply pendientes)."""
        for p in self.root.glob("*.json"):
            rec = None if p.name.startswith("reply-") else self._read(p)
            if rec:
                yield p.stem, rec

    def prune(self, max_age_days: float = 30) -> None:
        """Borra teclados y respuestas pendientes viejos (Telegram no deja editar mensajes de >48 h igualmente)."""
        cutoff = time.time() - max_age_days * 86400
        for p in self.root.glob("*.*"):
            try:
                if p.suffix in (".json", ".used") and p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass

    def get(self, token: str) -> dict | None:
        if not re.fullmatch(r"[\w-]{6,32}", token or ""):
            return None
        return self._read(self.root / f"{token}.json")

    def consume(self, token: str) -> bool:
        """Uso único: el primero que renombra gana; un segundo toque (o el otro botón) recibe False."""
        try:
            os.replace(self.root / f"{token}.json", self.root / f"{token}.used")
            return True
        except OSError:
            return False

    def restore(self, token: str) -> None:
        try:
            os.replace(self.root / f"{token}.used", self.root / f"{token}.json")
        except OSError:
            pass

    def markup(self, token: str, rec: dict) -> dict:
        """Teclado original de un registro (para devolver los botones si la acción falla)."""
        buttons, rows, i = rec.get("buttons") or [], [], 0
        for n in rec.get("rows") or [len(buttons)]:
            rows.append([{"text": b["text"], "callback_data": f"{token}:{i + k}"} for k, b in enumerate(buttons[i:i + n])])
            i += n
        return {"inline_keyboard": rows}

    def _reply_path(self, chat_id, message_id) -> Path:
        return self.root / f"reply-{str(chat_id).lstrip('-')}_{int(message_id)}.json"

    def put_reply(self, chat_id, message_id, data: dict) -> None:
        self._write(self._reply_path(chat_id, message_id), data)

    def pop_reply(self, chat_id, message_id) -> dict | None:
        path = self._reply_path(chat_id, message_id)
        taken = path.with_suffix(".taken")
        try:
            os.replace(path, taken)
        except OSError:
            return None
        data = self._read(taken)
        taken.unlink(missing_ok=True)
        return data


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="lane-decision", daemon=True).start()


class DecisionDesk:
    """Construye los teclados de los avisos y ejecuta lo que Oscar pulsa."""

    def __init__(self, notifier, store: CallbackStore, *, lanes: dict, hermes_for: Callable[[str], object],
                 links=None, owner_id: str = OWNER_TELEGRAM_ID, runner=_proc.run, gh_exe: str = GH_EXE,
                 spawn: Callable[[Callable[[], None]], None] = _spawn, now: Callable[[], datetime] = datetime.now,
                 messages=None, explainer: Callable[[dict], str | None] | None = None,
                 snoozes: CardSnoozes | None = None, free_text: dict | None = None):
        self.notifier = notifier
        self.store = store
        self.lanes = lanes
        self.hermes_for = hermes_for
        self.links = links
        self.owner_id = str(owner_id)
        self._run = runner
        self.gh_exe = gh_exe
        self._spawn = spawn
        self._now = now
        self.reconciler = None  # reconcile.Reconciler: botones rec_archive / rec_keep
        self.integrator = None  # carril Integrador (INTEGRATOR_ENABLED): botones int_* de fusionar/desplegar
        self.messages = messages  # MessageStore: todas las copias de cada aviso (sincronización tema <-> DM)
        self.promoter = None      # promote.Promoter: /promover y botones promo_* (producción de MigraTeam)
        self.commands = None      # CommandCenter: /hoy, /decisiones, /aprobar, /tareas, /tarea
        self.team_notice: dict | None = None  # telegram.team_notice de lanes.yaml (enabled, chats, text); None = apagado
        self.collab = None        # CollabIntake: DM de colaboradores (people.yaml) -> tarjeta [IDEA]; None = como antes
        self.people: People | None = None  # people.yaml; None = se lee la primera vez que hace falta (el runner lo fija)
        self._tl = threading.local()       # quién actúa en este hilo (nombre de un operador que no es Oscar)
        self._help_at: dict[str, float] = {}  # chat -> instante de la última ayuda (enfriamiento)
        self.tree = None          # deps.TreeReader: líneas 🔗/⏸/↳ al redibujar un aviso (opcional)
        # 💬 Explícame más: rec -> texto llano (claude -p --model haiku sin herramientas); inyectable en tests.
        self.explainer = explainer or (lambda rec: _explain(rec, runner=self._run))
        # Aplazadas 7 días: junto a los teclados (.state/), así los tests con tmp_path no escriben en el .state real.
        self.snoozes = snoozes or CardSnoozes(Path(store.root).parent / "card_snooze.json")
        self.retries = RetryBook(Path(store.root).parent / "retry_marks.json")  # reintentos pulsados (aviso de resultado)
        self.vetos = VetoBook(Path(store.root).parent / "card_veto.json")  # plazo de veto de las tarjetas reversibles
        # Texto libre = respuesta a la decisión pendiente del tema (`telegram.free_text` de lanes.yaml); None = apagado.
        self.free = FreeText(self, **free_text) if free_text else None

    # --- operadores (Oscar + people.yaml `operador: true`) ---------------------------------------------

    def _people(self) -> People:
        if self.people is None:
            self.people = load_people(self.owner_id)
        return self.people

    def es_operador(self, user_id) -> bool:
        """Mismo acceso que Oscar: él (OWNER_TELEGRAM_ID) o quien tenga `operador: true` en people.yaml."""
        if str(user_id) == self.owner_id:
            return True
        return self._people().es_operador(user_id)

    def operador_nombre(self, user_id) -> str | None:
        """Nombre de un operador que NO es Oscar (para firmar sus acciones); None si es Oscar o no es operador."""
        if str(user_id) == self.owner_id or not self.es_operador(user_id):
            return None
        person = self._people().lookup(user_id)
        return person.name if person else None

    @contextmanager
    def actuando(self, user_id):
        """Las acciones de este hilo se firman con el nombre del operador (si no es Oscar)."""
        previo = getattr(self._tl, "name", None)
        self._tl.name = self.operador_nombre(user_id)
        try:
            yield
        finally:
            self._tl.name = previo

    def _en_nombre_de(self, user_id, fn: Callable[[], None]) -> Callable[[], None]:
        """`fn` para otro hilo (_spawn): conserva quién la pidió."""
        if not self.operador_nombre(user_id):
            return fn

        def run() -> None:
            with self.actuando(user_id):
                fn()
        return run

    def _who(self) -> str:
        return getattr(self._tl, "name", None) or "Oscar"

    def _by(self) -> str:
        name = getattr(self._tl, "name", None)
        return f" · por {name}" if name else ""

    def _trace(self, h, tid: str) -> None:
        """Rastro de quién decidió cuando no es Oscar. Comentario aparte: las respuestas `RESPUESTA-OSCAR: p → r` se
        parsean y no admiten texto extra. La autoría sigue siendo la de Oscar (la leen las listas blancas)."""
        name = getattr(self._tl, "name", None)
        if name:
            h.comment(tid, f"DECIDIDO-POR: {name} (operador) desde Telegram", author=OSCAR_AUTHOR)

    # --- teclados -------------------------------------------------------------------------------------

    def markup(self, state: str, *, task: dict, lane, block_kind: str | None = None, questions=None,
               summary: str | None = None, changed_files=None, for_oscar: str | None = None,
               yes_no: bool = True, q_index: int = 0) -> dict | None:
        """`yes_no=False`: escalada de review (sin [✅ Sí, adelante] [❌ No] por defecto). `q_index`: pregunta en curso
        (las anteriores ya están respondidas en el kanban; ver renotify._plan_blocked)."""
        qs = with_default_options(questions, yes_no)
        q_index = _clamp(q_index, qs)
        spec = keyboard_spec(state, block_kind=block_kind, questions=questions, yes_no=yes_no,
                             ops=getattr(lane, "kind", "") == "ops", index=q_index,
                             retried=bool(self.retries.get(task["id"])))
        if not spec:
            return None
        # ✍️ responde SOLO la pregunta en curso (antes, con varias, valía para "todas las preguntas" y Oscar no lo
        # veía: volvía a contestar la 1ª).
        rec = {"task_id": task["id"], "board": lane.board, "lane": lane.name, "title": task.get("title") or "",
               "body": (task.get("body") or "")[:4000], "question": qs[q_index] if qs else None,
               "summary": (summary or "")[:1500], "changed_files": list(changed_files or [])[:50],
               "n_questions": len(qs), "questions": qs, "q_index": q_index,
               **({"for_oscar": str(for_oscar)[:600]} if for_oscar else {})}
        if len(qs) > 1 and not yes_no:
            # Escalada de review: las "preguntas" son cambios pedidos, no decisiones en secuencia; ✍️ las responde
            # todas a la vez (answer_progress la cuenta como todas respondidas).
            rec["other_label"] = ALL_LABEL
        return self.store.issue(rec, spec)[1]

    @staticmethod
    def member(task: dict, lane, questions=None, yes_no: bool = True, q_index: int = 0) -> dict:
        """Registro mínimo de una tarea dentro de un teclado de grupo o de "aceptar todo"."""
        qs = with_default_options(questions, yes_no)
        q_index = _clamp(q_index, qs)
        return {"task_id": task["id"], "board": lane.board, "lane": lane.name, "title": task.get("title") or "",
                "body": (task.get("body") or "")[:1500], "question": qs[q_index] if qs else None,
                "questions": qs, "n_questions": len(qs), "q_index": q_index}

    def group_markup(self, members: list[dict], questions, yes_no: bool = True, q_index: int = 0) -> dict | None:
        """Una tarjeta para la misma pregunta en varias tareas: la respuesta se aplica a todas."""
        return self.store.issue(*self._group_rec(members, questions, yes_no, q_index))[1]

    @staticmethod
    def _group_rec(members: list[dict], questions, yes_no: bool, q_index: int) -> tuple[dict, list]:
        qs = with_default_options(questions, yes_no)
        q_index = _clamp(q_index, qs)
        spec = keyboard_spec("needs_input", questions=questions, yes_no=yes_no, index=q_index, accept_card=False)
        rec = {**members[0], "group": members, "question": qs[q_index] if qs else None, "questions": qs,
               "n_questions": len(qs), "q_index": q_index, "yes_no": yes_no,
               **({"other_label": ALL_LABEL} if len(qs) > 1 and not yes_no else {})}
        return rec, spec

    def accept_all_markup(self, members: list[dict]) -> dict:
        """[✅ Aceptar todo lo recomendado] sobre `members` (se filtran al pulsar: recomendada en TODAS sus preguntas)."""
        rec = {"task_id": "bandeja", "board": "", "lane": "", "accept_all": members}
        return self.store.issue(rec, [[{"text": "✅ Aceptar todo lo recomendado", "action": ACCEPT_ALL}]])[1]

    def card_markup(self, card: dict, options: list[str], recommended: int | None, head: str,
                    veto: bool = False) -> dict:
        """Teclado de la tarjeta de decisión `card` ({board, task}); `head` = su mensaje HTML (se reedita al pulsar)."""
        t = card["task"]
        rec = {"task_id": t["id"], "board": card["board"], "lane": "", "card": True, "title": t.get("title") or "",
               "options": list(options[:len(CARD_LETTERS)]), "head": head, "recommended": recommended}
        return self.store.issue(rec, card_keyboard_spec(len(options), recommended, options, veto))[1]

    def card_accept_all_markup(self, members: list[dict]) -> dict:
        """[✅ Aceptar las recomendadas (N)] sobre el lote de tarjetas: cada miembro {task_id, board, options,
        recommended}; al pulsar solo se aplican las que siguen pendientes."""
        rec = {"task_id": "bandeja", "board": "", "lane": "", "card_accept_all": members}
        return self.store.issue(rec, [[{"text": f"✅ Aceptar las recomendadas ({len(members)})",
                                        "action": CARD_ACCEPT_ALL}]])[1]

    @staticmethod
    def expand_free_text(text: str, options: list[str]) -> str:
        """Respuesta libre "A, pero con X" -> "A) <texto de la opción> — pero con X". Si no empieza por una letra
        válida, se anota tal cual."""
        text = (text or "").strip()
        # La letra debe ir sola o seguida de puntuación ("A, pero…"): "A mí me da igual" no elige la A.
        m = re.match(r"^([A-Ca-c])(?:\s*[,.:;)\-–—]\s*|\s*$)(.*)$", text, re.S)
        if not m or CARD_LETTERS.index(m.group(1).upper()) >= len(options):
            return text
        idx, rest = CARD_LETTERS.index(m.group(1).upper()), m.group(2).strip()
        return f"{CARD_LETTERS[idx]}) {options[idx]}" + (f" — {rest}" if rest else "")

    def see_all_markup(self, brand: str | None) -> dict:
        """[📋 Ver todas] bajo la línea "N más" de /decisiones: reenvía la bandeja completa."""
        rec = {"task_id": "bandeja", "board": "", "lane": "", "see_all": True, "brand": brand}
        return self.store.issue(rec, [[{"text": "📋 Ver todas", "action": SEE_ALL}]])[1]

    # --- entrada: updates de getUpdates ---------------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        if update.get("callback_query"):
            self._callback(update["callback_query"])
        elif update.get("message"):
            self._message(update["message"])

    @staticmethod
    def _mask(chat_id) -> str:
        """Id de chat para el log: solo los últimos 4 dígitos (si tiene más de 4)."""
        digits = "".join(ch for ch in str(chat_id) if ch.isdigit())
        return "***" + digits[-4:] if len(digits) > 4 else "***"  # ids cortos: enteros fuera

    def _log_update(self, chat_id, kind: str, name: str | None, result: str) -> None:
        """Una línea INFO por update: chat enmascarado, tipo, comando/acción y resultado. Nunca el texto ni tokens."""
        safe = re.sub(r"[^A-Za-z0-9_]", "", str(name or ""))[:32] or "-"
        log.info("update chat=%s tipo=%s cmd=%s resultado=%s", self._mask(chat_id), kind, safe, result)

    def _message(self, msg: dict) -> None:
        from .commands import command_target  # import diferido: commands importa este módulo
        text = msg.get("text") or ""
        chat = msg.get("chat") or {}
        is_cmd = text.lstrip().startswith("/")
        kind = "comando" if is_cmd else ("texto" if text else "otro")
        if not text and (msg.get("photo") or msg.get("document")):
            kind = "adjunto"
        target = command_target(text, getattr(self.commands, "bot_username", None)) if is_cmd else None
        name = target[0] if target else None
        is_owner = self.es_operador((msg.get("from") or {}).get("id"))  # operador = Oscar o people.yaml `operador: true`
        if self.collab and not is_owner and self.collab.claims(msg, self.owner_id):
            # DM privado de alguien que no es operador: nunca llega a los comandos ni a _reply (hermes: segundos).
            self._log_update(chat.get("id"), kind, None, "respondido")  # sin nombre: lo escribe un desconocido
            self._spawn(lambda: self.collab.handle(msg))
            return
        if self.commands and target and target[1]:
            self._log_update(chat.get("id"), kind, name, "respondido" if is_owner else "rechazado")
            # Los comandos llaman a la CLI de hermes (segundos): fuera del hilo de escucha.
            self._spawn(lambda: self.commands.handle(msg) or self._reply(msg))
            return
        if self._reply(msg):  # respuesta a un force_reply pendiente
            self._log_update(chat.get("id"), kind, name, "respondido")
            return
        result = "ignorado"
        owner_dm = is_owner and not (msg.get("from") or {}).get("is_bot")
        if (kind == "adjunto" and owner_dm and self.commands and chat.get("type") == "private"
                and getattr(self.commands, "capture", None)):
            self._log_update(chat.get("id"), kind, None, "respondido")  # descarga + hermes: fuera del hilo de escucha
            self._spawn(lambda: self.commands.capture(msg))
            return
        if (owner_dm and not is_cmd and self.commands and chat.get("type") == "private"
                and self.commands.session_note(msg)):  # /sesion abierta: el texto es un punto, no pide ayuda
            self._log_update(chat.get("id"), kind, None, "respondido")
            return
        if (is_owner and not (msg.get("from") or {}).get("is_bot") and not is_cmd and text.strip() and self.free
                and self.free.handle(msg)):  # texto libre con una decisión pendiente en este tema
            self._log_update(chat.get("id"), kind, None, "respondido")
            return
        if (owner_dm and not is_cmd and kind == "texto" and chat.get("type") == "private"
                and getattr(self.commands, "can_answer", False)):
            self._log_update(chat.get("id"), kind, None, "respondido")  # claude -p tarda segundos: fuera del hilo
            self._spawn(lambda: self.commands.free_text(msg))  # pregunta suelta: estado real, nunca ejecuta nada
            return
        if not is_owner and self._team_notice(msg, chat):
            result = "respondido"
        elif owner_dm and self._wants_help(msg, is_cmd, target):
            result = self._help(msg, name if is_cmd else None)
        self._log_update(chat.get("id"), kind, name, result)

    @staticmethod
    def _wants_help(msg: dict, is_cmd: bool, target) -> bool:
        """Ayuda solo para el dueño: texto libre en su DM, o un /comando desconocido dirigido a este bot (en grupo, solo
        con el sufijo @bot explícito: sin él puede ser para Hermes)."""
        private = (msg.get("chat") or {}).get("type") == "private"
        if is_cmd:
            return bool(target) and not target[1] and (private or target[2])
        return private and bool(msg.get("text"))

    TEAM_NOTICE_COOLDOWN = 60.0  # s: un aviso al equipo por chat en ese plazo

    def _team_notice(self, msg: dict, chat: dict) -> bool:
        """Grupo del equipo (telegram.team_notice en lanes.yaml, APAGADO por defecto): si le escriben a ESTE bot
        (@mención o respuesta a un mensaje suyo), avisa de que esto lo lleva el asistente del equipo. Lo que hablan
        entre ellos no se contesta. True = avisó."""
        cfg = self.team_notice or {}
        chat_id = str(chat.get("id"))
        if not cfg.get("enabled") or chat_id not in {str(c) for c in cfg.get("chats") or ()}:
            return False
        text = msg.get("text") or ""
        username = (getattr(self.commands, "bot_username", None) or "").lstrip("@").lower()
        bot_id = getattr(self.notifier, "bot_id", None)
        replied = ((msg.get("reply_to_message") or {}).get("from") or {}).get("id")
        mentioned = bool(username) and f"@{username}" in text.lower()
        if not (mentioned or (bot_id and replied is not None and str(replied) == str(bot_id))):
            return False
        stamp = time.monotonic()
        last = self._help_at.get(f"team:{chat_id}")
        if last is not None and stamp - last < self.TEAM_NOTICE_COOLDOWN:
            return False
        self._help_at[f"team:{chat_id}"] = stamp
        thread = str(msg.get("message_thread_id") or 0) if msg.get("is_topic_message") else "0"
        try:
            self.notifier.send_to(chat_id, thread, cfg.get("text") or TEAM_NOTICE_TEXT, html=False,
                                  reply_to=msg.get("message_id"))
        except Exception as exc:
            log.warning("no pude enviar el aviso al equipo: %s", exc)
            return False
        return True

    HELP_COOLDOWN = 30.0 # s: un único aviso de ayuda por chat en ese plazo (sin ráfagas ni bucles)

    def _help(self, msg: dict, cmd: str | None) -> str:
        """Responde con la ayuda: qué entendió, comandos y /hazlo. 'respondido' | 'ignorado' (enfriamiento)."""
        from .commands import CommandCenter
        chat_id = str((msg.get("chat") or {}).get("id"))
        stamp = time.monotonic()
        last = self._help_at.get(chat_id)
        if last is not None and stamp - last < self.HELP_COOLDOWN:
            return "ignorado"
        self._help_at[chat_id] = stamp
        if cmd:
            seen = f"Entendí /{re.sub(r'[^A-Za-z0-9_]', '', cmd)[:32]}, pero no es un comando que conozca."
        else:
            seen = "Recibí tu mensaje, pero no es un comando y no tengo una respuesta pendiente tuya."
        text = (seen + "\n\n" + CommandCenter.help_text()
                + "\n\nPara pedirme algo escribe /hazlo <texto>, por ejemplo: /hazlo revisar el aviso de despliegue.")
        thread = str(msg.get("message_thread_id") or 0) if msg.get("is_topic_message") else "0"
        try:
            self.notifier.send_to(chat_id, thread, text, html=False)
        except Exception as exc:
            log.warning("no pude enviar la ayuda: %s", exc)
            return "ignorado"
        return "respondido"

    @staticmethod
    def _where(msg: dict) -> dict:
        return {"chat_id": str((msg.get("chat") or {}).get("id")), "thread_id": str(msg.get("message_thread_id") or 0),
                "message_id": msg.get("message_id")}

    def _callback(self, cq: dict) -> None:
        answer = self.notifier.answer_callback
        chat = ((cq.get("message") or {}).get("chat") or {}).get("id")
        actor = (cq.get("from") or {}).get("id")
        if not self.es_operador(actor):
            answer(cq.get("id"), NOT_OWNER, alert=True)
            self._log_update(chat, "callback", None, "rechazado")
            return
        token, _, n = (cq.get("data") or "").partition(":")
        rec = self.store.get(token)
        buttons = (rec or {}).get("buttons") or []
        if not rec or not n.isdigit() or int(n) >= len(buttons):
            answer(cq.get("id"), "Esta decisión ya no está activa")
            self._log_update(chat, "callback", None, "ignorado")
            return
        button = buttons[int(n)]
        where = self._where(cq.get("message") or {})
        self._log_update(chat, "callback", button.get("action"), "respondido")
        name = self.operador_nombre(actor)
        if name:
            log.info("%s: botón %s pulsado por %s (operador)", rec.get("task_id"), button.get("action"), name)
        if button["action"] == EXPLAIN:  # no consume el teclado: los demás botones siguen valiendo
            answer(cq.get("id"), "Te lo explico en un momento")
            self._spawn(lambda: self._explain(rec, where))
            return
        if button["action"] == DETAIL:  # tampoco consume
            answer(cq.get("id"), "Te paso el detalle")
            self._spawn(lambda: self._detail(rec, where))
            return
        if button["action"] == CARD_VETO:  # tampoco consume: solo apaga la ejecución automática
            answer(cq.get("id"), "Vetada: no se aplicará sola")
            self._spawn(lambda: self._veto(token, rec, where))
            return
        if button["action"] in REPLY_ACTIONS:
            answer(cq.get("id"), "Responde al mensaje que te envío")
            self._spawn(lambda: self._ask(token, rec, button, where))
            return
        if not self.store.consume(token):
            answer(cq.get("id"), "Ya está en marcha")
            return
        answer(cq.get("id"), "Hecho, lo aplico")
        self._spawn(self._en_nombre_de(actor, lambda: self._safe(token, rec, where,
                                                                 lambda: self._act(rec, button, where))))

    def _reply(self, msg: dict) -> bool:
        """Respuesta de Oscar a un force_reply: pedir cambios o respuesta libre. True si había una pendiente."""
        actor = (msg.get("from") or {}).get("id")
        if not self.es_operador(actor):
            return False
        replied = msg.get("reply_to_message") or {}
        text = (msg.get("text") or "").strip()
        if not replied or not text:
            return False
        chat_id = (msg.get("chat") or {}).get("id")
        pending = self.store.pop_reply(chat_id, replied.get("message_id"))
        if not pending:
            return False
        token, rec, button, where = pending["token"], pending["rec"], pending["button"], pending["where"]
        if not self.store.consume(token):  # ya se decidió con otro botón
            self._cleanup_messages(chat_id, [replied.get("message_id"), msg.get("message_id")])
            return True
        self._spawn(self._en_nombre_de(actor, lambda: self._safe(
            token, rec, where, lambda: self._apply_reply(rec, button, where, text),
            after=lambda: self._cleanup_messages(chat_id, [replied.get("message_id"), msg.get("message_id")]))))
        return True

    # --- acciones -------------------------------------------------------------------------------------

    def _safe(self, token: str, rec: dict, where: dict, fn: Callable[[], bool], after=None) -> None:
        """Ejecuta la acción; si falla, devuelve los botones para que Oscar pueda repetir."""
        # Tokens de las copias ANTES de actuar: la acción puede emitir teclados nuevos para la misma tarea
        # (p. ej. [🚀 Desplegar] tras fusionar) y esos deben seguir vivos. Grupo y "aceptar todo" retiran por
        # miembro dentro de la acción, solo en los que salieron bien.
        before = set() if (rec.get("group") or rec.get("accept_all") or self._free(rec)) else self._tokens(rec)
        try:
            ok = fn()
        except Exception as exc:
            log.warning("%s: la decisión de Oscar falló: %s", rec.get("task_id"), exc)
            self._edit(rec, where, "blocked", "no se pudo aplicar la decisión; detalle en el log")
            ok = False
        if ok:
            for tok in before - {token}:
                self.store.consume(tok)
        if not ok:
            self.store.restore(token)
            try:
                self._edit(rec, where, None, None, markup=self.store.markup(token, rec), keep_text=True)
            except Exception as exc:
                log.warning("%s: no se pudieron devolver los botones: %s", rec.get("task_id"), exc)
        if after:
            try:
                after()
            except Exception:
                pass

    @staticmethod
    def _free(rec: dict) -> bool:
        """Selector o eco del texto libre: mensajes propios, sin copias del aviso de la tarea."""
        return any(rec.get(f) for f in FREE_ACTIONS)

    def _tokens(self, rec: dict) -> set[str]:
        """Tokens de teclado de todas las copias del aviso de una tarea (tema, DM, bandeja)."""
        if not self.messages:
            return set()
        return {m["token"] for m in self.messages.all_messages(rec["task_id"]) if m.get("token")}

    def _retire(self, rec: dict) -> None:
        """Tras decidir una tarea desde un grupo o "aceptar todo": sus botones en las demás copias dejan de valer."""
        for tok in self._tokens(rec):
            self.store.consume(tok)

    def _still_pending(self, rec: dict) -> bool:
        """¿Sigue bloqueada esperando a Oscar? (grupo y "aceptar todo": no responder dos veces)."""
        return self._pending_show(rec) is not None

    def _pending_show(self, rec: dict) -> dict | None:
        """`show` de la tarea si sigue esperando a Oscar: blocked needs_input, o en triage (block_loop_detected) con su
        último bloqueo needs_input ({} si el kanban no se puede consultar: fakes). None si ya no espera decisión."""
        h = self._hermes(rec)
        if not hasattr(h, "show"):
            return {}
        try:
            show = h.show(rec["task_id"])
        except Exception:
            return None
        status = (show.get("task") or {}).get("status")
        if status not in ("blocked", "triage"):
            return None
        kinds = [(e.get("payload") or {}).get("kind") for e in show.get("events") or () if e.get("kind") == "blocked"]
        if status == "triage":  # Hermes la paró por repetir la misma pregunta: sigue esperando la respuesta
            return show if kinds and kinds[-1] == "needs_input" else None
        return show if (not kinds or kinds[-1] == "needs_input") else None

    def _progress(self, rec: dict, current: int) -> tuple[dict[int, str], int | None]:
        """Respuestas de la ronda según el kanban (+ la que se acaba de anotar) y la siguiente pendiente. Sin `show`
        legible (fakes, error de la CLI) se avanza en orden desde la pregunta en curso."""
        qs = normalize_questions(rec.get("questions") or ([rec["question"]] if rec.get("question") else []))
        answers: dict[int, str] | None = None
        h = self._hermes(rec)
        if hasattr(h, "show"):
            try:
                answers = answer_progress(h.show(rec["task_id"]), qs)[0]
            except Exception as exc:
                log.info("%s: progreso de respuestas no leído: %s", rec.get("task_id"), exc)
        if answers is None:
            answers = {i: "" for i in range(current)}
        answers.setdefault(current, "")
        return answers, next((i for i in range(len(qs)) if i not in answers), None)

    def _hermes(self, rec: dict):
        return self.hermes_for(rec["board"])

    def _lane(self, rec: dict):
        return self.lanes.get(rec["lane"])

    def _act(self, rec: dict, button: dict, where: dict) -> bool:
        action = button["action"]
        if action in FREE_ACTIONS:  # selector / Deshacer del texto libre
            return bool(self.free) and self.free.on_button(action, rec, button, where)
        if rec.get("kind") == PEDIR_KIND:  # /pedir: tipo, preguntas y modo OASP (tipo_peticion.py)
            return bool(self.commands) and self.commands.pedir_step(rec, button, where)
        if rec.get("card"):  # tarjeta de decisión de Oscar (/decisiones)
            return self._card_act(rec, button, where)
        if action.startswith("int_"):  # 🔀 Fusionar / 🚀 Desplegar: solo con el Integrador activo
            return bool(self.integrator) and self.integrator.on_button(action, rec, where, self)
        if action in (REC_ARCHIVE, REC_KEEP):  # propuesta de obsolescencia (reconcile.py)
            return bool(self.reconciler) and self.reconciler.on_button(action, rec, where, self)
        if action == TO_INTEGRADOR:
            return self._to_integrador(rec, where)
        if action == GUARD_SNOOZE:
            return self._guard_snooze(rec, where)
        if action.startswith("promo_"):  # 🚀 Promover MigraTeam: Revisado / Sí, promover (promote.py)
            if action == "promo_start":
                if not self.promoter:
                    return False
                key = str(rec.get("key") or "")
                reissue = getattr(self.integrator, "reissue_promote_markup", None)
                if reissue:  # el token del fijado ya está gastado: se repone para poder pulsar de nuevo
                    reissue()
                self.promoter.start(key, where)
                return True
            return bool(self.promoter) and self.promoter.on_button(action, rec, where, self)
        if action == FALTA:  # /falta: reenvía a la acción que ya existía
            return bool(self.commands) and self.commands.falta_choice(rec, button, where, self)
        if action == HAZLO:  # /hazlo con duda: el botón elige carril
            return bool(self.commands) and self.commands.hazlo_choice(rec, button, where)
        if action == FLUJO:  # /flujo: crear o descartar la propuesta
            return bool(self.commands) and self.commands.flujo_choice(rec, button, where)
        if action in (DEPLOY_ASK, DEPLOY_NO):  # /desplegar: confirmación previa al int_deploy
            return bool(self.commands) and self.commands.deploy_step(action, rec, where, self)
        if action == CARD_ACCEPT_ALL:
            return self._card_accept_all(rec, where)
        if action == ACCEPT_ALL:
            return self._accept_all(rec, where)
        if action == ACCEPT_CARD:
            return self._accept_all({"accept_all": [rec]}, where, card_where=where)
        if action == SEE_ALL:
            return self._see_all(rec, where)
        if rec.get("group"):
            return self._group_act(rec, button, where)
        if action == APPROVE:
            return self._approve(rec, where)
        if action == PARK:
            return self._park(rec, where)
        if action == RETRY:
            return self._retry(rec, where)
        if action == UNTRIAGE:
            return self._untriage(rec, where)
        if action == OPTION:
            return self._option_answer(rec, where, button.get("index"))
        return False

    # --- tarjetas de decisión de Oscar ---------------------------------------------------------------

    @staticmethod
    def _has_comment(show: dict | None, body: str) -> bool:
        return any(c.get("author") == OSCAR_AUTHOR and (c.get("body") or "").strip() == body.strip()
                   for c in (show or {}).get("comments") or ())

    def _card_act(self, rec: dict, button: dict, where: dict, text: str | None = None) -> bool:
        """Botón de una tarjeta de decisión. Anota RESPUESTA-OSCAR (misma autoría que las demás respuestas de Oscar) y
        completa la tarjeta; aplazar y aparcar comentan y no cuentan como respuesta. Idempotente: si la tarjeta ya no
        está en ready/blocked no hace nada, y un comentario idéntico no se repite si hay que reintentar."""
        action, tid, h = button["action"], rec["task_id"], self._hermes(rec)
        show = None
        if hasattr(h, "show"):
            try:
                show = h.show(tid)
            except Exception as exc:
                log.info("%s: tarjeta no leída antes de decidir: %s", tid, exc)
        status = ((show or {}).get("task") or {}).get("status")
        if status and status not in DECISION_CARD_STATUSES:
            self._edit(rec, where, None, "ya estaba decidida (mira la tarjeta)")
            return True
        stamp = self._now().strftime("%Y-%m-%d %H:%M")

        def note(body: str) -> bool:
            if self._has_comment(show, body):
                return True
            if h.comment(tid, body[:3000], author=OSCAR_AUTHOR):
                self._trace(h, tid)
                return True
            self._edit(rec, where, "blocked", "no se pudo guardar la respuesta en la tarjeta")
            return False

        if action == CARD_SNOOZE:
            until = self.snoozes.add(tid, SNOOZE_DAYS)
            label = datetime.fromtimestamp(until).strftime("%d/%m")
            if not note(f"APLAZADA-OSCAR hasta {label} ({SNOOZE_DAYS} d, desde Telegram){self._by()}"):
                return False
            self._edit(rec, where, None, f"⏳ aplazada hasta el {label}")
            return True
        if action == CARD_PARK:
            if not note(f"Aparcada por {self._who()} desde Telegram"):
                return False
            archive = getattr(h, "archive", None)
            if not archive or not archive(tid):
                self._edit(rec, where, "blocked", "no se pudo aparcar la tarjeta (mira el panel)")
                return False
            self._edit(rec, where, None, "🗄 aparcada")
            return True
        opts = rec.get("options") or []
        if action in (CARD_OPTION, CARD_REC):
            idx = button.get("index")
            if not isinstance(idx, int) or isinstance(idx, bool) or not 0 <= idx < len(opts):
                return False
            answer = f"{CARD_LETTERS[idx]}) {opts[idx]}" + (" (la recomendada)" if action == CARD_REC else "")
        elif action == CARD_DONE:
            answer = "Hecho"
        elif action == CARD_ASK and text:
            answer = self.expand_free_text(text, opts)
        else:
            return False
        if not note(f"{HERMES_ANSWER_PREFIX} {answer}"):
            return False
        ok, err = h.complete(tid, f"Decidido por {self._who()} desde Telegram: {answer}"[:1500],
                             {"decided_by": OSCAR_AUTHOR, "at": stamp})
        if not ok:
            log.warning("%s: complete de la tarjeta falló: %s", tid, err)
            self._edit(rec, where, "blocked", "respuesta anotada, pero no se pudo cerrar la tarjeta (mira el panel)")
            return False
        self._edit(rec, where, None, f"✅ Elegida: {truncate(answer, 200)}")
        return True

    def card_rec_for(self, member: dict) -> dict:
        """Registro de tarjeta de decisión (el de `card_markup`) reconstruido desde un miembro del lote o del veto."""
        tid = member["task_id"]
        return {"task_id": tid, "board": member.get("board") or "", "lane": "", "card": True,
                "title": member.get("title") or "", "options": list(member.get("options") or ()),
                "head": f"📌 <code>{_esc(tid)}</code> · <b>{_esc(truncate(member.get('title'), 80))}</b>"}

    def apply_recommended(self, member: dict, where: dict | None = None) -> bool:
        """Aplica la opción recomendada de una tarjeta (misma ruta que ✅ La recomendada)."""
        idx = member.get("recommended")
        if not isinstance(idx, int) or isinstance(idx, bool):
            return False
        return self._card_act(self.card_rec_for(member), {"action": CARD_REC, "index": idx}, where)

    def _card_accept_all(self, rec: dict, where: dict) -> bool:
        """✅ Aceptar las recomendadas del lote: cada tarjeta pendiente recibe su ⭐; las ya decididas se saltan solas
        (_card_act es idempotente) y las que fallan no paran a las demás."""
        done, failed = [], []
        for m in rec.get("card_accept_all") or ():
            try:
                ok = self.apply_recommended(m)
            except Exception as exc:
                log.warning("%s: aceptar la recomendada falló: %s", m.get("task_id"), exc)
                ok = False
            (done if ok else failed).append(m["task_id"])
            if ok:
                self._retire(m)
        self._edit_text(where, f"✅ Aplicada la recomendada en {len(done)} tarjeta{'s' if len(done) != 1 else ''}"
                        + (f" ({', '.join(done)})" if done else "")
                        + (f" · fallaron: {', '.join(failed)}" if failed else ""))
        return bool(done) or not failed

    def _veto(self, token: str, rec: dict, where: dict) -> None:
        """⛔ Vetar: la tarjeta reversible ya no se ejecuta sola. No consume el teclado: A/B/C siguen valiendo."""
        tid = rec["task_id"]
        self.vetos.set_state(tid, "vetoed")
        try:
            self._hermes(rec).comment(tid, "VETO-OSCAR: no se aplica sola la recomendada; decide Oscar", author=OSCAR_AUTHOR)
            self._edit(rec, where, None, "⛔ vetada: no se aplicará sola", markup=self.store.markup(token, rec))
        except Exception as exc:
            log.warning("%s: veto anotado solo en local: %s", tid, exc)

    def _option_answer(self, rec: dict, where, idx) -> bool:
        return self._option_step(rec, where, idx)[0]

    def _option_step(self, rec: dict, where, idx) -> tuple[bool, int | None]:
        """(ok, siguiente pregunta pendiente o None si ya no queda ninguna)."""
        q = rec.get("question") or {}
        opts = q.get("options") or []
        if not isinstance(idx, int) or not 0 <= idx < len(opts):
            return False, None
        if (rec.get("n_questions") or 1) > 1:
            return self._answer_step(rec, where, opts[idx])
        return self._answer(rec, where, q.get("question") or "", opts[idx]), None

    def _group_act(self, rec: dict, button: dict, where: dict, text: str | None = None) -> bool:
        """Misma pregunta en varias tareas: la decisión se aplica a cada una que siga pendiente."""
        action, done, skipped = button["action"], [], []
        if action not in (OPTION, OTHER, PARK) or (action == OTHER and text is None):
            return False
        nexts, advanced = set(), []
        for m in rec["group"]:
            if not self._still_pending(m):
                skipped.append(m["task_id"])
                continue
            nxt = None
            if action == OPTION:
                ok, nxt = self._option_step(m, None, button.get("index"))
            elif action == OTHER and (m.get("n_questions") or 1) > 1 and not rec.get("other_label"):
                ok, nxt = self._answer_step(m, None, text)
            elif action == OTHER:
                label = rec.get("other_label") or (m.get("question") or {}).get("question") or "pregunta del worker"
                ok = self._answer(m, None, label, text)
            else:
                ok = self._park(m, None)
            (done if ok else skipped).append(m["task_id"])
            if not ok:
                continue
            nexts.add(nxt)
            if nxt is None:
                self._retire(m)
            else:  # sigue en secuencia: sus copias ya muestran la siguiente pregunta (teclado nuevo)
                advanced.append({**m, "q_index": nxt, "question": (m.get("questions") or [None] * (nxt + 1))[nxt]})
        if not done:
            self._edit_text(where, "❓ Nada aplicado: " + (", ".join(skipped) or "-") + " ya no esperaba(n) decisión")
            return False
        if advanced and len(advanced) == len(done) and len(nexts) == 1:
            return self._group_next(rec, where, advanced, nexts.pop(), button, text)
        answer = ""
        if action == OPTION:
            opts = (rec.get("question") or {}).get("options") or []
            idx = button.get("index")
            answer = f": {truncate(opts[idx], 80)}" if isinstance(idx, int) and 0 <= idx < len(opts) else ""
        elif text:
            answer = f": {truncate(text, 80)}"
        verb = "aparcada" if action == PARK else "respondida"
        self._edit_text(where, f"💬 {verb} en {len(done)} tarea{'s' if len(done) != 1 else ''} ({', '.join(done)})"
                        + answer + (f" · sin tocar: {', '.join(skipped)}" if skipped else ""))
        return True

    def _see_all(self, rec: dict, where: dict) -> bool:
        if not self.commands:
            return False
        self._edit_text(where, "📋 La lista completa va debajo ⬇️")
        self.commands.cmd_decisiones((where["chat_id"], where["thread_id"]), "", rec.get("brand"), show_all=True)
        return True

    def _accept_all(self, rec: dict, where: dict, card_where: dict | None = None) -> bool:
        """Cada tarea con recomendada en TODAS sus preguntas PENDIENTES: un comentario "Respuesta de Oscar" por
        pregunta pendiente (las que Oscar ya contestó no se pisan) y un único desbloqueo (lo mismo que pulsar la
        opción ⭐). Una tarea con alguna pendiente sin recomendada queda intacta: desbloquear con media respuesta haría
        que el worker se volviera a bloquear. Las ya decididas se saltan.
        `card_where`: ✅ Acepto todas las recomendadas de UNA tarjeta; se edita ese mensaje (y sus copias) en vez de
        sustituirlo por el resumen, y si no se pudo aplicar los botones vuelven (False)."""
        done, skipped, failed = [], [], []
        for m in rec.get("accept_all") or ():
            qs = normalize_questions(m.get("questions"))
            show = self._pending_show(m) if qs else None
            answered = answer_progress(show, qs)[0] if show else {}
            todo = [q for i, q in enumerate(qs) if i not in answered]
            if show is None or any(q["recommended"] is None for q in todo):
                skipped.append(m["task_id"])
                continue
            h, tid = self._hermes(m), m["task_id"]
            if not all(h.comment(tid, f"{ANSWER_PREFIX} {q['question']} → {q['options'][q['recommended']]}"[:3000],
                                 author=OSCAR_AUTHOR) for q in todo):
                failed.append(tid)
                self._edit(m, None, "blocked", "no se pudo guardar la respuesta en la tarjeta")
                continue
            self._trace(h, tid)
            answers = " · ".join(truncate(q["options"][q["recommended"]], 40) for q in todo)
            self._edit(m, card_where, "answered", f"💬 respondida (lo recomendado): {truncate(answers, 120)}")
            if not self._resume(h, tid):  # en triage (bloqueo repetido) unblock falla: requeue_triage
                self._edit(m, card_where, "answered", "💬 respuesta anotada · no se pudo desbloquear (mira la tarjeta)")
            done.append(tid)
            self._retire(m)
        if card_where:
            return bool(done)
        parts = [f"✅ Aplicado lo recomendado en {len(done)} tarea{'s' if len(done) != 1 else ''}"
                 + (f" ({', '.join(done)})" if done else "")]
        if skipped:
            parts.append(f"sin tocar: {', '.join(skipped)}")
        if failed:
            parts.append(f"fallaron: {', '.join(failed)}")
        self._edit_text(where, " · ".join(parts))
        return bool(done) or not failed

    def _answer_step(self, rec: dict, where: dict | None, answer: str) -> tuple[bool, int | None]:
        """Varias preguntas, EN SECUENCIA: anota la respuesta a la pregunta en curso y, si queda alguna pendiente
        (según el kanban), la MISMA tarjeta y sus copias (tema, DM, bandeja) pasan a la siguiente con sus botones
        ("Pregunta 2/3"). Con la última, desbloquea una sola vez (retomar con media respuesta haría que el worker
        volviera a bloquearse, y Hermes manda a triage los bloqueos repetidos). (ok, siguiente índice o None)."""
        tid, h = rec["task_id"], self._hermes(rec)
        q = rec.get("question") or {}
        cur = _clamp(rec.get("q_index"), rec.get("questions") or [None])
        if not h.comment(tid, f"{ANSWER_PREFIX} {q.get('question') or ''} → {answer}"[:3000], author=OSCAR_AUTHOR):
            self._edit(rec, where, "blocked", "no se pudo guardar la respuesta en la tarjeta")
            return False, None
        self._trace(h, tid)
        _, nxt = self._progress(rec, cur)
        qs = rec.get("questions") or []
        total = rec.get("n_questions") or len(qs) or 1
        if nxt is None:
            self._edit(rec, where, "answered", f"💬 respondida ({total}/{total}): {truncate(answer, 100)}")
            if not self._resume(h, tid) and self._stuck(rec):
                self._edit(rec, where, "answered", "💬 respuesta anotada · no se pudo desbloquear (mira la tarjeta)")
            return True, None
        step = {**rec, "q_index": nxt, "question": qs[nxt]}
        token, markup = self.store.issue(step, keyboard_spec("needs_input", questions=qs, index=nxt))
        status = status_line(f"💬 {cur + 1}ª: {truncate(answer, 60)}", f"Pregunta {nxt + 1}/{total}",
                             "responde con un botón")
        self._edit(step, where, "needs_input", status, markup=markup, bullets=questions_block(qs[nxt:nxt + 1]))
        if self.messages:  # las copias llevan ya el teclado nuevo: que retirarlas lo retire también
            try:
                self.messages.set_token(tid, token)
            except Exception as exc:
                log.info("%s: no se pudo registrar el teclado nuevo: %s", tid, exc)
        return True, nxt

    def _group_next(self, rec: dict, where: dict | None, members: list[dict], nxt: int, button: dict,
                    text: str | None) -> bool:
        """Tarjeta de grupo con varias preguntas: respondida la pregunta en curso en todas sus tareas, la misma tarjeta
        pasa a la siguiente ("Pregunta k/N") con sus botones."""
        qs = rec.get("questions") or []
        total = rec.get("n_questions") or len(qs) or 1
        _, markup = self.store.issue(*self._group_rec(members, qs, rec.get("yes_no", True), nxt))
        if button["action"] == OPTION:
            opts = (rec.get("question") or {}).get("options") or []
            idx = button.get("index")
            answer = truncate(opts[idx], 60) if isinstance(idx, int) and 0 <= idx < len(opts) else ""
        else:
            answer = truncate(text or "", 60)
        cur = _clamp(rec.get("q_index"), qs)
        base = getattr(self.links, "kanban_base_url", None)
        refs = []  # HTML: cada tarea enlaza su tarjeta (con html=False se perdían los enlaces del aviso original)
        for m in members:
            url = card_url(base, m.get("board") or "", m["task_id"])
            refs.append(f'<a href="{_esc(url, quote=True)}">{_esc(m["task_id"])}</a>' if url else _esc(m["task_id"]))
        lines = [f"💬 {cur + 1}ª respondida en {len(members)} tareas ({', '.join(refs)}): {_esc(answer)}",
                 f"❓ Pregunta {nxt + 1}/{total} · la respuesta se aplica a todas",
                 *(_esc(q) for q in questions_block(qs[nxt:nxt + 1]))]
        if where:
            self.notifier.edit(where["chat_id"], where["message_id"], "\n".join(lines), reply_markup=markup)
        return True

    def _resume(self, h, tid: str) -> str | None:
        """Devuelve la tarea al carril: `unblock`; si falla porque Hermes la pasó a triage (bloqueo repetido,
        block_loop_detected), `requeue_triage` la saca sin reescribirla. Estado resultante, o None si fallan ambas."""
        status = resume(h, tid)
        if status and status != "ready":
            log.info("%s: unblock falló; sacada de triage sin reescribirla (queda en %s)", tid, status)
        return status

    def _stuck(self, rec: dict) -> bool:
        """Tras un desbloqueo fallido: ¿sigue parada (blocked/triage)? Si otra vía ya la devolvió al carril (la
        autocuración, una respuesta vía Hermes), no hay error que enseñar. Sin `show` legible: sí (se avisa)."""
        h = self._hermes(rec)
        try:
            status = (h.show(rec["task_id"]).get("task") or {}).get("status")
        except Exception:
            return True
        return status in ("blocked", "triage", None)

    def _approve(self, rec: dict, where: dict) -> bool:
        lane, tid = self._lane(rec), rec["task_id"]
        if lane is not None and lane.kind == "ops":
            return self._validate_ops(rec, where)
        slug = self.links.repo_slug(lane) if (self.links and lane) else None
        if not slug:
            self._edit(rec, where, "blocked", "no se pudo abrir el PR: el repo del carril no está en GitHub")
            return False
        pr = self._find_pr(slug, tid) or self._create_pr(slug, lane, rec)
        if not pr:
            self._edit(rec, where, "blocked", "no se pudo abrir el PR; detalle en el log")
            return False
        number, url = pr
        stamp = self._now().strftime("%Y-%m-%d %H:%M")
        if not self._hermes(rec).comment(tid, f"APROBADO-OSCAR {stamp} · PR {url}{self._by()}", author=OSCAR_AUTHOR):
            log.warning("%s: PR %s abierto pero no se pudo comentar la aprobación en la tarjeta", tid, url)
        if self.integrator:  # registro local PR + commit aprobado: lo único de lo que se fía el Integrador
            try:
                self.integrator.record_approval(lane, tid, number)
            except Exception as exc:
                log.warning("%s: no se pudo registrar la aprobación para el integrador: %s", tid, exc)
        # Nunca se fusiona aquí: el merge es del carril Integrador.
        self._edit(rec, where, "approved", f"✅ aprobada · PR #{number}", extra_links=[(f"PR #{number}", url)])
        return True

    def _validate_ops(self, rec: dict, where: dict) -> bool:
        """Carril ops: sin PR ni Integrador. Validar = `complete` de la tarea en review + comentario de Oscar.
        No ejecuta ninguna acción propuesta: eso queda como siguiente paso (ver README, carril ops)."""
        tid, h = rec["task_id"], self._hermes(rec)
        stamp = self._now().strftime("%Y-%m-%d %H:%M")
        ok, err = h.complete(tid, f"Validado por {self._who()} desde Telegram", {"validated_by": OSCAR_AUTHOR, "at": stamp})
        if not ok:
            log.warning("%s: complete (validación ops) falló: %s", tid, err)
            self._edit(rec, where, "blocked", "no se pudo validar: la tarea ya no está en review (mira la tarjeta)")
            return False
        h.comment(tid, f"VALIDADO-OSCAR {stamp} (carril ops){self._by()}", author=OSCAR_AUTHOR)
        self._edit(rec, where, "approved", "✅ validada")
        return True

    def _gh(self, *args: str):
        return self._run([self.gh_exe, *args], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=120)

    def _find_pr(self, slug: str, tid: str) -> tuple[int, str] | None:
        cp = self._gh("pr", "list", "--repo", slug, "--head", f"lane/{tid}", "--state", "open",
                      "--json", "number,url")
        if cp.returncode != 0:
            log.warning("%s: gh pr list falló: %s", tid, (cp.stderr or "").strip()[:300])
            return None
        try:
            prs = json.loads(cp.stdout or "[]")
        except json.JSONDecodeError:
            return None
        return (int(prs[0]["number"]), prs[0]["url"]) if prs else None

    def _create_pr(self, slug: str, lane, rec: dict) -> tuple[int, str] | None:
        tid = rec["task_id"]
        card = card_url(getattr(self.links, "kanban_base_url", None), rec["board"], tid)
        body = "\n\n".join(p for p in (
            rec.get("summary") or f"Tarea {tid}.",
            "Aprobado por Oscar desde Telegram.",
            f"Tarjeta: {card}" if card else f"Tarjeta: {tid} (tablero {rec['board']})",
            PR_FOOTER) if p)
        cp = self._gh("pr", "create", "--repo", slug, "--head", f"lane/{tid}", "--base", lane.base,
                      "--title", rec.get("title") or tid, "--body", body)
        if cp.returncode != 0:
            log.warning("%s: gh pr create falló: %s", tid, (cp.stderr or "").strip()[:300])
            return None
        m = re.search(r"https://github\.com/\S+/pull/(\d+)", cp.stdout or "")
        return (int(m.group(1)), m.group(0)) if m else None

    def _park(self, rec: dict, where: dict) -> bool:
        tid, h = rec["task_id"], self._hermes(rec)
        self.retries.clear(tid)
        self._edit(rec, where, "parked", "🗄 aparcada")
        if not h.assign(tid, PARK_PROFILE):
            self._edit(rec, where, "blocked", "no se pudo aparcar (¿la tarea está en curso?)")
            return False
        h.comment(tid, f"Aparcada por {self._who()}", author=OSCAR_AUTHOR)
        return True

    def _to_integrador(self, rec: dict, where: dict) -> bool:
        """Lo que Oscar no puede hacer desde Telegram (consola, guard, config) pasa al integrador: la tarjeta se
        reasigna a su perfil y queda dicho en ella quién la tiene."""
        tid, h = rec["task_id"], self._hermes(rec)
        if not h.assign(tid, INTEGRATOR_PROFILE):
            self._edit(rec, where, "blocked", "no se pudo pasar al integrador (¿la tarea está en curso?)")
            return False
        h.comment(tid, f"Pasada al integrador ({INTEGRATOR_PROFILE}) por {self._who()}: requiere consola",
                  author=OSCAR_AUTHOR)
        self._edit(rec, where, "parked", f"🧑‍💻 la tiene el integrador ({INTEGRATOR_PROFILE}); queda en su tarjeta")
        return True

    def _guard_snooze(self, rec: dict, where: dict) -> bool:
        """⏳ Aplazar: la tarjeta sigue en la cola del integrador y no vuelve a Oscar hasta dentro de 24 h."""
        queue = getattr(self, "guard_queue", None)  # la pone CommandCenter.guard_router (guard_route.GuardQueue)
        if queue is None or not queue.snooze(rec["task_id"]):
            self._edit(rec, where, "blocked", "no se pudo aplazar: ya no está en la cola del integrador")
            return False
        self._edit(rec, where, "parked", "⏳ aplazada 24 h; sigue en la cola del integrador (/integrador)")
        return True

    def _retry(self, rec: dict, where: dict) -> bool:
        if self._guard_limited(rec):  # limitación permanente del guard: devolverla al carril repite el mismo bloqueo
            self._edit(rec, where, "blocked", f"requiere consola: /tomar-tarea {rec['task_id']} (el guard del carril no lo permite)")
            return False
        h, tid = self._hermes(rec), rec["task_id"]
        self._mark_retry(h, tid)  # para decir después si el reintento salió o volvió a fallar por lo mismo
        # Se edita ANTES de desbloquear: el runner puede cogerla enseguida y su "▶️ en curso" debe quedar encima.
        self._edit(rec, where, "requeued", "🔄 reencolada · si vuelve a fallar te aviso aquí con el motivo")
        # Bloqueada dos veces por lo mismo, Hermes la pasa a triage y `unblock` falla: _resume la saca de triage sin
        # reescribirla (también desde un aviso ⛔ antiguo de cuando aún estaba bloqueada).
        status = self._resume(h, tid)
        if status:
            if status != "ready":
                self._edit(rec, where, "requeued", f"🔄 sacada de triage · queda en {status} (espera a otra tarea)")
            return True
        self.retries.clear(tid)
        self._edit(rec, where, "blocked", "❌ el reintento falló: no se pudo reencolar, la tarea ya no está bloqueada ni "
                   "en triage (quizá otra vía ya la movió: mira la tarjeta)")
        return False

    def _untriage(self, rec: dict, where: dict) -> bool:
        """🧊 Sacar de triage: `requeue_triage` (specify_triage_task sin título ni cuerpo); nunca `unblock`, que Hermes
        rechaza en triage. Siempre contesta: éxito, «ya estaba resuelta» o el fallo con su causa (False: `_safe`
        devuelve los botones y el texto con la causa se queda). Idempotente: una segunda pulsación no hace nada."""
        h, tid = self._hermes(rec), rec["task_id"]
        try:
            status = (h.show(tid).get("task") or {}).get("status")
        except Exception:
            status = None  # sin poder leerla se intenta igualmente: requeue_triage solo actúa sobre tareas en triage
        if status and status != "triage":
            self._edit(rec, where, "requeued", f"ya estaba resuelta (ahora en {status}); no hago nada más")
            return True
        if self._guard_limited(rec):
            self._edit(rec, where, "stuck", f"requiere consola: /tomar-tarea {tid} (el guard del carril no lo permite)")
            return False
        self._edit(rec, where, "requeued", "🔄 sacando de triage…")
        new = h.requeue_triage(tid)
        if not new:
            cause = getattr(h, "last_error", None) or "Hermes no la dejó salir de triage"
            self._edit(rec, where, "stuck", f"⚠️ no se pudo sacar de triage: {cause}. Puedes volver a pulsar.")
            return False
        self._edit(rec, where, "requeued", "🔄 sacada de triage" + ("" if new == "ready" else f" · queda en {new}"))
        return True

    def _mark_retry(self, h, tid: str) -> None:
        try:
            reason = next(((e.get("payload") or {}).get("reason") for e in reversed(h.show(tid).get("events") or ())
                           if e.get("kind") == "blocked"), None)
            self.retries.mark(tid, reason)
        except Exception as exc:  # sin la huella el reintento sigue: solo se pierde el "mismo motivo"
            log.info("%s: huella del bloqueo no guardada: %s", tid, exc)

    def _guard_limited(self, rec: dict) -> bool:
        try:
            block = last_needs_input_block(self._hermes(rec).show(rec["task_id"]))
        except Exception:  # sin poder comprobarlo, el reintento sigue como siempre
            return False
        return is_guard_limit(((block or {}).get("payload") or {}).get("reason"))

    def _answer(self, rec: dict, where: dict, question: str, answer: str) -> bool:
        tid, h = rec["task_id"], self._hermes(rec)
        if not h.comment(tid, f"{ANSWER_PREFIX} {question} → {answer}"[:3000], author=OSCAR_AUTHOR):
            self._edit(rec, where, "blocked", "no se pudo guardar la respuesta en la tarjeta")
            return False
        self._trace(h, tid)
        self.retries.clear(tid)
        self._edit(rec, where, "answered", f"💬 respondida: {truncate(answer, 120)}")
        if not self._resume(h, tid) and self._stuck(rec):  # en triage (bloqueo repetido): requeue_triage
            self._edit(rec, where, "answered", "💬 respuesta anotada · no se pudo desbloquear (mira la tarjeta)")
        return True

    def answered_elsewhere(self, rec: dict, status: str) -> None:
        """La tarea se respondió fuera de los botones (RESPUESTA-OSCAR vía Hermes): sus teclados dejan de valer (un
        toque tardío recibe "ya no está activa") y todas las copias del aviso (tema, DM, bandeja) pasan a `status`
        sin botones."""
        self._retire(rec)
        self._edit(rec, None, "answered", status)

    def _explain(self, rec: dict, where: dict) -> None:
        """💬 Explícame más: explicación llana como respuesta al aviso pulsado, en el mismo chat/tema."""
        try:
            text = self.explainer(rec)  # en un grupo, rec = primer miembro + la pregunta compartida
        except Exception as exc:
            log.warning("%s: explicación fallida: %s", rec.get("task_id"), exc)
            text = None
        body = f"💬 {rec.get('task_id')} · {text}" if text else EXPLAIN_FAILED
        try:
            self.notifier.send_to(where["chat_id"], where["thread_id"], body, html=False,
                                  reply_to=where.get("message_id"))
        except Exception as exc:
            log.warning("%s: no se pudo enviar la explicación: %s", rec.get("task_id"), exc)

    def _detail(self, rec: dict, where: dict) -> None:
        """🔎 Más detalle: motivo técnico del último bloqueo y últimos comentarios de la tarjeta, en el propio chat."""
        tid = rec.get("task_id")
        try:
            show = self._hermes(rec).show(tid) or {}
            body = f"🔎 {tid}\n{detail_text(show, bloqueos.load_config())}"
        except Exception as exc:
            log.warning("%s: detalle no leído: %s", tid, exc)
            body = DETAIL_FAILED
        try:
            self.notifier.send_to(where["chat_id"], where["thread_id"], body, html=False,
                                  reply_to=where.get("message_id"))
        except Exception as exc:
            log.warning("%s: no se pudo enviar el detalle: %s", tid, exc)

    def _ask(self, token: str, rec: dict, button: dict, where: dict) -> None:
        tid = rec["task_id"]
        if rec.get("kind") == PEDIR_KIND:  # /pedir: la pregunta en curso, sin tarjeta que enlazar
            prompt = rec.get("prompt") or "Tu respuesta:"
        elif button["action"] == CHANGES:
            prompt = f"¿Qué cambio pides para {tid}?"
        else:
            q = ((rec.get("question") or {}).get("question") or rec.get("summary")
                 or (rec.get("title") if rec.get("card") else "") or "")
            n = rec.get("n_questions") or 1
            step = f" (Pregunta {(rec.get('q_index') or 0) + 1}/{n})" if n > 1 and not rec.get("other_label") else ""
            prompt = f"Tu respuesta para {tid}{step}:" + (f"\n{truncate(q, 300)}" if q else "")
        card = None if rec.get("kind") == PEDIR_KIND else card_url(
            getattr(self.links, "kanban_base_url", None), rec.get("board") or "", tid)
        if card:  # texto plano: Telegram enlaza la URL sola
            prompt += f"\nTarjeta: {card}"
        try:
            sent = self.notifier.send_to(where["chat_id"], where["thread_id"], prompt, html=False,
                                         reply_to=where["message_id"],
                                         # selective=False: con True Telegram lo muestra al autor del mensaje
                                         # citado, que es el propio bot. Solo Oscar cuenta: se filtra por from.id.
                                         reply_markup={"force_reply": True, "selective": False,
                                                       "input_field_placeholder": "Escribe aquí"})
        except Exception as exc:
            log.warning("%s: no se pudo pedir la respuesta: %s", tid, exc)
            return
        if sent and sent.get("message_id"):
            self.store.put_reply(where["chat_id"], sent["message_id"],
                                 {"token": token, "rec": rec, "button": button, "where": where})

    def _apply_reply(self, rec: dict, button: dict, where: dict, text: str) -> bool:
        if rec.get("kind") == PEDIR_KIND:
            return bool(self.commands) and self.commands.pedir_step(rec, button, where, text)
        if rec.get("card"):
            return self._card_act(rec, button, where, text)
        if rec.get("group"):
            return self._group_act(rec, button, where, text)
        tid, h = rec["task_id"], self._hermes(rec)
        if button["action"] == OTHER:
            if (rec.get("n_questions") or 1) > 1 and not rec.get("other_label"):  # en secuencia: la pregunta en curso
                return self._answer_step(rec, where, text)[0]
            q = rec.get("other_label") or (rec.get("question") or {}).get("question") or "pregunta del worker"
            return self._answer(rec, where, q, text)
        # Pedir cambios: igual que el carril review (comentario CAMBIOS de REVIEW_AUTHOR + reopen-review).
        body = f"{CHANGES_PREFIX} pedidos por Oscar desde Telegram:\n- {text}"
        if not h.comment(tid, body[:3000], author=REVIEW_AUTHOR):
            self._edit(rec, where, "blocked", "no se pudo guardar el cambio pedido en la tarjeta")
            return False
        self._edit(rec, where, "changes", "🔁 cambios pedidos")
        if not h.reopen_review(tid):
            # Aprobada = done, y la CLI 0.21.4 no reabre tareas done: el cambio queda anotado para cuando se reabra.
            self._edit(rec, where, "changes", "🔁 cambios anotados · reábrela desde el panel para que vuelva al carril")
        return True

    # --- mensaje --------------------------------------------------------------------------------------

    def _edit(self, rec: dict, where: dict | None, state: str | None, status: str | None, *, extra_links=(),
              markup: dict | None = None, keep_text: bool = False, bullets=None) -> None:
        """Edita el mensaje pulsado (`where`) y TODAS las demás copias del aviso de la tarea (tema, DM, bandeja):
        una decisión tomada en cualquiera se ve en todas. `where` None = solo las copias (grupo, aceptar todo)."""
        if keep_text:  # solo devolver los botones
            self.notifier.edit_markup(where["chat_id"], where["message_id"], markup)
            return
        if rec.get("group") or rec.get("accept_all") or self._free(rec):  # grupo / cabecera / selector: texto propio
            self._edit_text(where, status or "")
            return
        if rec.get("card"):  # tarjeta de decisión: su mensaje HTML + lo que pasó, sin botones
            text = (rec.get("head") or "") + "\n" + _esc(status or "")
        else:
            lane = self._lane(rec)
            links = self.links(lane, rec["task_id"], changed_files=rec.get("changed_files")) if (self.links and lane) else []
            tree = self.tree(rec.get("board"), rec["task_id"]) if self.tree else None
            text = render(state, rec["task_id"], rec.get("title"), rec["lane"], status, [*links, *extra_links], bullets,
                          body=rec.get("body"), for_oscar=rec.get("for_oscar"), tree=tree)
        extra = {"reply_markup": markup} if markup else {}
        seen = set()
        if where:
            self.notifier.edit(where["chat_id"], where["message_id"], text, **extra)
            seen.add((str(where["chat_id"]), str(where["message_id"])))
        for msg in (self.messages.all_messages(rec["task_id"]) if self.messages else ()):
            key = (str(msg["chat_id"]), str(msg["message_id"]))
            if key in seen:
                continue
            seen.add(key)
            try:
                self.notifier.edit(msg["chat_id"], msg["message_id"], text, **extra)
            except Exception as exc:
                log.info("%s: no se pudo sincronizar la copia %s: %s", rec["task_id"], msg["message_id"], exc)

    def _edit_text(self, where: dict | None, text: str) -> None:
        """Texto plano en un mensaje concreto (cabecera de la bandeja, tarjeta de grupo), sin botones."""
        if where:
            self.notifier.edit(where["chat_id"], where["message_id"], text, html=False)

    def _cleanup_messages(self, chat_id, message_ids) -> None:
        """Borra la pregunta force_reply y la respuesta de Oscar: el hilo no acumula mensajes (requiere admin)."""
        for mid in message_ids:
            if mid:
                try:
                    self.notifier.delete(str(chat_id), mid)
                except Exception:
                    pass


class UpdatePoller:
    """Hilo de long-polling de getUpdates con offset persistido en .state/tg_offset."""

    def __init__(self, notifier, desk: DecisionDesk, offset_path: Path | str, *, timeout: int = 25,
                 sleep: Callable[[float], None] = time.sleep):
        self.notifier = notifier
        self.desk = desk
        self.offset_path = Path(offset_path)
        self.timeout = timeout
        self._sleep = sleep
        self._stop = threading.Event()

    def _offset(self) -> int | None:
        try:
            return int(self.offset_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _save(self, offset: int) -> None:
        self.offset_path.parent.mkdir(parents=True, exist_ok=True)
        self.offset_path.write_text(str(offset), encoding="utf-8")

    def poll_once(self) -> int:
        updates = self.notifier.get_updates(self._offset(), self.timeout)
        for update in sorted(updates, key=lambda u: u.get("update_id", 0)):
            # Offset antes de procesar: un update que rompe no se reintenta en bucle (los tokens son de un uso).
            self._save(int(update["update_id"]) + 1)
            try:
                self.desk.handle_update(update)
            except Exception as exc:
                log.warning("update %s de Telegram falló: %s", update.get("update_id"), exc)
        return len(updates)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # red, 409 de otro consumidor...: nunca tumba el runner; nunca con token
                log.warning("getUpdates falló: %s", exc)
                self._sleep(5)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="tg-updates", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
