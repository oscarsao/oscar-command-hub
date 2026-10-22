"""Comandos del bot de carriles (@pildora_carriles_bot): decidir desde UN sitio sin ir cambiando de grupo.

Deterministas (sin LLM) y solo para operadores (Oscar y las personas de people.yaml con `operador: true`, hoy María);
a cualquier otro: NOT_OWNER (qué hacer en vez de un "no"). Funcionan en su DM y en los grupos,
también como /cmd@<bot>. Un /cmd@otro_bot (p. ej. el de Hermes) se ignora.

    /hoy                 el resumen de las 8:00 bajo demanda (brief/brief_diario.py, mismo render y datos)
    /decisiones          bandeja: cabecera con [✅ Aceptar todo lo recomendado] + una tarjeta por tarea en needs_input,
                         y "📌 Tus tarjetas de decisión" (asignadas a oscar con [DECISIÓN…]/[SEMANA…]/[IDEA…])
    /aprobar             tareas listas para integrar con [✅ Aprobar] [🔁 Pedir cambios] [🗄 Aparcar]
    /tareas [marca]      en curso y en cola por carril (migrateam | pildora | nextjobs), un solo mensaje
    /cola [marca]        tarea en curso y siguientes por prioridad en cada carril
    /prioridad t_x N     cambia la prioridad de una tarjeta (N = 0-99, sube o baja)
    /tarea t_xxx         ficha de la tarea con los botones de su estado
    /hazlo <texto>       crea una tarea pequeña (prioridad alta, tope 15 min): marca y carril salen del texto; con
                         duda, botones para elegir carril. El resultado vuelve a donde lo pediste
    /estado [marca]      en un solo mensaje: en curso, lo que espera a Oscar y lo pendiente de desplegar
    /falta [marca]       checklist de lo que frena una salida (lote, ramas fuera, SQL, variables, deploy), con el
                         siguiente paso arriba y botones donde ya existe la acción (falta.py)
    /desplegar <proyecto>  ficha de lo fusionado y sin desplegar con [🚀 Desplegar] → "¿Seguro?" → ✅ Sí (doble toque;
                         el despliegue es el mismo int_deploy del integrador, nunca otra vía)
    /promover migrateam  prepara el PR develop→master y pide la doble confirmación (promote.py)
    /lote <proyecto>     monta ya el lote de las ramas aprobadas
    /salud               runner y carriles, gateway de Hermes, servicios y alertas (del monitor), develop↔master de
                         MigraTeam, RAM/CPU y decisiones pendientes; sin peticiones HTTP (agent_lanes/health.py)
    /ram                 RAM ahora y los 5 procesos que más gastan (el monitor lo manda solo al DM cada 4 h)
    /sesion [nombre]     abre una sesión de correcciones: el texto libre del DM se acumula como puntos numerados;
                         /sesion fin [tablero] crea UNA tarjeta [CORRECCIONES] (con preguntas de clarificación y mejoras
                         de Claude), /sesion cancelar la descarta y /sesion estado cuenta los puntos (sesion.py)
    /flujo <texto>       Claude propone hasta 8 tarjetas encadenadas (carril, dependencias, límites); no se crea nada
                         hasta pulsar [Crear todo]. Las que piden merge/deploy/envío externo van a nombre de Oscar
                         con [CONFIRMAR] (flujo.py)

/decisiones también lista las 🧊 atascadas (tareas de carril que Hermes pasó a triage por bloquearse dos veces por
lo mismo) con [🔄 Reintentar] (las devuelve a ready sin reescribirlas) y cuenta las tarjetas a nombre de Oscar sin
etiqueta de decisión.

Dentro de un tema de operaciones se filtran por la marca del tema (lanes.yaml: t230 MigraTeam, t231 Píldora); los
orígenes genéricos (t5 Operaciones · General, el DM) y el General del grupo ven todo.

Grupos con privacy mode: Telegram solo entrega a este bot los comandos con sufijo (/decisiones@<bot>) o los que
se eligen en el menú (setMyCommands). Sin sufijo, un /hoy puede ir a parar solo al bot de Hermes.
"""
from __future__ import annotations

import contextlib
import hashlib
import html
import json
import logging
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from . import bandeja as _bandeja
from . import tipo_peticion, titulos
from .aviso import UrgentBlockWatcher, mention
from .card_options import PREPARED_AUTHOR, PREPARED_PREFIX, CardSpec, card_message, parse_card
from .decisions import DEPLOY_ASK, DEPLOY_NO, FALTA, FLUJO, HAZLO, OWNER_TELEGRAM_ID
from .deps import (ShowCache, child_bucket, dependency_order, family, pending_parents, tree_lines,
                   waiting_line)
from .notices import (BOARD_BRANDS, BRANDS, DECISION_CARD_STATUSES, OSCAR_ASSIGNEE, TEXT_MAX, card_url,
                      decision_since, hours_ago, is_decision_card, markup_token, next_step, normalize_questions,
                      render, status_line, truncate)

log = logging.getLogger("agent_lanes")
URGENT_EVERY = 120  # s mínimos entre pasadas del aviso inmediato de bloqueos de prioridad alta

COMMANDS = (
    ("ajustes", "Ajustes del hub con botones: avisos, atascos, lotes y carriles"),
    ("hoy", "Resumen del día (el de las 8:00) ahora"),
    ("integrador", "Estado del integrador, PR de sesiones interactivas y bloqueos de guard"),
    ("encolar", "Encola un PR de una sesión interactiva para tu OK: /encolar <repo> <PR>"),
    ("decisiones", "Bandeja de decisiones pendientes con botones"),
    ("atencion", "Todo lo que espera a Oscar, con botones para resolverlo aquí"),
    ("aprobar", "Tareas listas para integrar"),
    ("tareas", "En curso y en cola por carril: /tareas [migrateam|pildora|nextjobs]"),
    ("cola", "Qué corre y qué sigue en cada carril, por prioridad: /cola [marca]"),
    ("prioridad", "Sube o baja una tarea en la cola: /prioridad t_xxx <0-99|sube|baja>"),
    ("tarea", "Ficha de una tarea con sus botones: /tarea t_xxx"),
    ("preparar", "Claude redacta contexto y 3 opciones de una tarjeta de decisión: /preparar t_xxx | todas"),
    ("hazlo", "Crea una tarea pequeña (carril según el texto): /hazlo <texto>"),
    ("pedir", "Pide algo con tipo (bug o feature) y preguntas: /pedir <texto>"),
    ("estado", "En curso, esperando a Oscar y pendiente de desplegar: /estado [marca]"),
    ("falta", "Qué frena una salida, con el siguiente paso arriba: /falta [marca]"),
    ("desplegar", "Despliega lo fusionado de un proyecto, con doble confirmación: /desplegar oscarhq"),
    ("promover", "Promover MigraTeam a producción (doble confirmación): /promover migrateam"),
    ("lote", "Montar ya el lote de un proyecto: /lote migrateam|oscarhq"),
    ("salud", "Estado de runner, Hermes, servicios, MigraTeam y equipo"),
    ("ram", "Consumo de RAM ahora y qué procesos más gastan"),
    ("sesion", "Correcciones agrupadas: /sesion [nombre] abre; fin | cancelar | estado"),
    ("flujo", "Una petición -> cadena de hasta 8 tarjetas (se crean al pulsar): /flujo <texto>"),
    ("pendientes", "Tus pendientes (pasos «tuyo» y «decidir») con botones"),
    ("epicas", "Vista de épicas (fuera de la cola de decisiones)"),
    ("retenidas", "Vista de tarjetas retenidas"),
    ("backlog", "Vista del backlog del bot del equipo"),
)
COMMAND_SCOPES = ("default", "all_private_chats", "all_group_chats")
HELP_ALIASES = ("start", "ayuda", "help")
NOT_OWNER = ("Este comando es solo para operadores. Pide a Oscar o a María que lo haga, o escribe tu petición en "
             "texto libre y se la paso")
# Comandos sin marca clara o con efecto directo en repos/sesiones de Oscar: un operador acotado por marca no los usa.
SOLO_TODAS_LAS_MARCAS = ("integrador", "encolar", "preparar", "pedir", "sesion", "flujo", "ajustes")
BRAND_ARGS = {"migrateam": "MigraTeam", "pildora": "Píldora", "píldora": "Píldora", "nextjobs": "NextJobs"}
TASK_ID_RE = re.compile(r"^t_[0-9a-f]{8}$")
STATUS_TEXT = {"running": ("running", "en curso"), "ready": ("ready", "en cola"), "review": ("review", "en review"),
               "done": ("done", "terminada"), "blocked": ("blocked", "bloqueada"), "todo": ("todo", "pendiente"),
               "triage": ("triage", "en triage"), "archived": ("archived", "archivada")}
TOP_DECISIONS = 5    # /decisiones muestra las 5 más importantes; el resto con [📋 Ver todas]
VIEW_STATUSES = ("triage", "todo", "ready", "running", "blocked", "review")  # vistas por paso: tarjetas no cerradas
MAX_CARDS = 20    # tarjetas por /decisiones o /aprobar (el resto se cuenta en la cabecera)
CARD_TITLE_MAX = 60  # título de una tarjeta de decisión de Oscar en /decisiones
EXTRA_BOARDS = ("default",)  # además de los tableros de los carriles: donde viven muchas tarjetas de Oscar
HAZLO_PRIORITY = 10  # prioridad alta (kanban: más alto = antes)
HAZLO_MINUTES = 15
HAZLO_TITLE_MAX = 70
# Palabras (con límite de palabra, en minúsculas) que delatan el carril de un /hazlo. "píldora" a secas no decide: hay
# dos carriles de esa marca (oscarhq y scraper).
LANE_HINTS = {
    "claude-migrateam": ("migrateam", "migra team"),
    "claude-oscarhq": ("oscar hq", "oscarhq", "oscar-hq", "crewai", "crews"),
    "claude-scraper": ("scraper", "icp", "signal engine"),
    "claude-nextjobs": ("nextjobs", "next jobs", "autoapply"),
    "claude-hub": ("command hub", "command-hub", "agent-lanes", "carriles", "runner", "bot de trabajos", "monitor",
                   "hermes"),
}
PREPARE_MAX = 10  # /preparar todas: tope de tarjetas por orden (0,5 $ como máximo cada una)
READY_PER_LANE = 8  # tareas en cola listadas por carril en /tareas
COLA_PER_LANE = 5  # siguientes tareas por carril en /cola
PRIORITY_MAX = 99
PRIORITY_STEP = 1  # lo que suben o bajan «sube»/«baja» de /prioridad

_CMD_RE = re.compile(r"^/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?:\s+(.*))?$", re.S)


def parse_command(text: str | None, bot_username: str | None = None) -> tuple[str, str] | None:
    """"/decisiones@pildora_carriles_bot x" -> ("decisiones", "x"). None si no es un comando nuestro."""
    m = _CMD_RE.match((text or "").strip())
    if not m:
        return None
    cmd, at, args = m.group(1).lower(), m.group(2), (m.group(3) or "").strip()
    if at and (not bot_username or at.lower() != bot_username.lower()):
        return None  # dirigido a otro bot (Hermes)
    if cmd not in {c for c, _ in COMMANDS} | set(HELP_ALIASES):
        return None
    return cmd, args


def command_target(text: str | None, bot_username: str | None = None) -> tuple[str, bool, bool] | None:
    """"/foo@pildora_carriles_bot x" -> ("foo", conocido, con_sufijo_nuestro). None si no es un comando o va dirigido a
    otro bot (Hermes). Distingue los desconocidos para poder contestarlos con ayuda."""
    m = _CMD_RE.match((text or "").strip())
    if not m:
        return None
    cmd, at = m.group(1).lower(), m.group(2)
    if at and (not bot_username or at.lower() != bot_username.lower()):
        return None
    return cmd, cmd in ({c for c, _ in COMMANDS} | set(HELP_ALIASES)), bool(at)


def scope_brand(chat_id, thread_id, lanes: dict, generic_origins=()) -> str | None:
    """Marca del tema donde se escribió el comando (None = todo): la de los carriles cuyo destino es ese tema."""
    key = (str(chat_id), str(thread_id or 0))
    generic = set(generic_origins or ())
    if key in generic or (key[0], "*") in generic:
        return None
    brands = {BRANDS.get(n) for n, l in lanes.items() if getattr(l, "kind", "implement") == "implement"
              and l.telegram and tuple(l.telegram) == key}
    brands.discard(None)
    return brands.pop() if len(brands) == 1 else None


def register_commands(notifier) -> list[str]:
    """setMyCommands (descripciones en español) en los tres scopes. Devuelve los scopes registrados."""
    ok = []
    for scope in COMMAND_SCOPES:
        try:
            if notifier.set_my_commands(list(COMMANDS), scope):
                ok.append(scope)
        except Exception as exc:
            log.warning("setMyCommands %s falló: %s", scope, exc)
    return ok


def question_key(questions) -> tuple:
    """Clave para deduplicar: preguntas normalizadas (casefold, espacios colapsados) con sus opciones."""
    norm = lambda s: " ".join(str(s).split()).casefold()  # noqa: E731
    return tuple((norm(q["question"]), tuple(norm(o) for o in q["options"]), q["recommended"])
                 for q in normalize_questions(questions))


def group_pending(pendings: list) -> list[list]:
    """Agrupa las tareas con exactamente las mismas preguntas Y el mismo progreso (mismas ya respondidas, misma
    pregunta en curso), en el orden de la primera aparición."""
    groups: dict[tuple, list] = {}
    for p in pendings:
        qk = question_key(p.questions)
        key = (qk, getattr(p, "q_index", 0), tuple(getattr(p, "answered", ()) or ())) if qk else ("__solo__", p.tid)
        groups.setdefault(key, []).append(p)
    return list(groups.values())


def pending_of(p) -> list[dict]:
    """Preguntas que le quedan a una decisión (las ya respondidas en esta ronda no cuentan)."""
    answered = set(getattr(p, "answered", ()) or ())
    return [q for i, q in enumerate(normalize_questions(p.questions)) if i not in answered]


def fully_recommended(questions) -> bool:
    qs = normalize_questions(questions)
    return bool(qs) and all(q["recommended"] is not None for q in qs)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def panel_url(base_url: str | None) -> str | None:
    """Raíz del panel del kanban desde KANBAN_BASE_URL (base simple, o plantilla con {board}/{id} -> esquema+host)."""
    base = (base_url or "").strip()
    if not base:
        return None
    if "{" in base:
        parts = urlsplit(base)
        return f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else None
    return base.rstrip("/") + "/"


def _prio(task: dict) -> int | None:
    try:
        return int(task.get("priority"))
    except (TypeError, ValueError):
        return None


def _elapsed(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


def _progress_label(p) -> str | None:
    """"Pregunta 2/3" de una decisión con varias preguntas (None con una)."""
    n = len(getattr(p, "questions", None) or ())
    if n < 2 or not getattr(p, "yes_no", True):  # escalada de review: sin secuencia
        return None
    return f"Pregunta {getattr(p, 'q_index', 0) + 1}/{n}"


class CommandCenter:
    def __init__(self, notifier, desk, *, lanes: dict, hermes_for: Callable[[str], object], messages, links=None,
                 generic_origins=(), bot_username: str | None = None, base_url: str | None = None,
                 owner_id: str | None = None, brief: Callable[[str | None], str] | None = None,
                 renotifier=None, now: Callable[[], float] = time.time,
                 health: Callable[[], str] | None = None, preparer: Callable[[dict], str | None] | None = None,
                 sesiones=None, suggester: Callable[[dict], dict | None] | None = None,
                 flow_planner: Callable[[str, dict], list[dict]] | None = None,
                 bandeja: _bandeja.Bandeja | None = None, asker: Callable[[str, str], str | None] | None = None):
        self.bandeja = bandeja or _bandeja.Bandeja()  # umbrales de la bandeja (bandeja.yaml; el runner pasa load())
        self.notifier = notifier
        self.desk = desk
        self.lanes = lanes
        self.hermes_for = hermes_for
        self.messages = messages
        self.links = links
        self.generic_origins = set(generic_origins or ())
        self.bot_username = bot_username
        self.base_url = base_url if base_url is not None else getattr(links, "kanban_base_url", None)
        self.owner_id = str(owner_id or getattr(desk, "owner_id", None) or OWNER_TELEGRAM_ID)
        self._brief = brief
        self._renotifier = renotifier
        self._now = now
        self._shows = ShowCache(ttl=30)  # árbol padre/hijas: una lectura por tarea y comando, no una por línea
        self._preparer = preparer  # /preparar: ctx -> bloque Contexto/Opciones/Recomendada (None = card_prepare.prepare)
        self._sesiones = sesiones  # /sesion: sesion.Sesiones (None = .state/sesiones junto a callbacks/)
        self._flow_planner = flow_planner  # /flujo: (texto, {carril: descripción}) -> tarjetas (None = flujo.propose)
        self._suggester = suggester  # /sesion fin: sesión -> {sección: [viñetas]} (None = sesion.suggest)
        self._asker = asker  # texto libre del DM: (pregunta, estado) -> respuesta (explain.answer); None = solo la ayuda
        self._ask_lock = threading.Lock()  # una pregunta libre a la vez (cada una cuesta una llamada a haiku)
        self._health = health  # /salud: texto HTML ya montado (tests); None = health.build con lo real
        self._tipo = None  # /pedir: tipo_peticion.TipoPeticion (perezoso); `peticiones` = registro de A1, si lo hay
        self.peticiones = None

    # --- entrada --------------------------------------------------------------------------------------

    def handle(self, msg: dict) -> bool:
        """True si el mensaje era un comando nuestro (respondido o rechazado)."""
        parsed = parse_command(msg.get("text"), self.bot_username)
        if not parsed:
            return False
        cmd, args = parsed
        where = self._where(msg)
        actor = (msg.get("from") or {}).get("id")
        if not self.es_operador(actor):
            self._send(where, NOT_OWNER, html=False)
            return True
        brand = scope_brand(where[0], where[1], self.lanes, self.generic_origins)
        try:
            veto = self._veto_marca(actor, cmd, args, brand)
        except Exception as exc:  # ante la duda, un operador acotado no ejecuta
            log.warning("/%s: no pude comprobar la marca: %s", cmd, exc)
            veto = "No pude comprobar tu permiso de marca; pídeselo a Oscar o a María"
        if veto:
            self._send(where, veto, html=False)
            return True
        actuando = getattr(self.desk, "actuando", None)  # firma las acciones con el nombre del operador (no Oscar)
        try:
            with actuando(actor) if actuando else contextlib.nullcontext():
                if cmd in HELP_ALIASES:
                    self._send(where, self.help_text(), html=False)
                else:
                    getattr(self, f"cmd_{cmd}")(where, args, brand)
        except Exception as exc:
            log.warning("/%s falló: %s", cmd, exc)
            self._send(where, f"No pude completar /{cmd}; detalle en el log", html=False)
        return True

    def es_operador(self, user_id) -> bool:
        """Oscar o un operador de people.yaml (`operador: true`): mismo acceso a los comandos."""
        if str(user_id) == self.owner_id:
            return True
        check = getattr(self.desk, "es_operador", None)
        return bool(check and check(user_id))

    def _veto_marca(self, actor, cmd: str, args: str, scope: str | None) -> str | None:
        """Operador acotado por marca (people.yaml `brands` sin "*"): texto de rechazo si el comando apunta a una marca
        que no es suya o a algo sin marca (integrador, sesiones, tareas del hub...); None si puede seguir. Los
        listados pasan: sus botones se comprueban al pulsarlos (DecisionDesk._callback)."""
        restringido = getattr(self.desk, "restringido", None)
        if not restringido or not restringido(actor):
            return None
        desk = self.desk

        def check(marca) -> str | None:
            return None if desk.puede_en(actor, marca) else desk.aviso_marca(actor, marca)

        if cmd in SOLO_TODAS_LAS_MARCAS:
            return desk.aviso_marca(actor, None)
        if cmd in ("desplegar", "promover", "lote"):
            arg = args.strip()
            if cmd == "desplegar":
                found = self._deploy_target(arg)
                name = found[0].name if found else None
            elif cmd == "promover":
                name = f"claude-{(arg.split() or [''])[0].lower()}"
            else:
                batches = getattr(getattr(desk, "integrator", None), "batches", None)
                found = batches.resolve(arg.lower() or (scope or "").lower()) if batches and (arg or scope) else None
                name = found[0].name if found else None
            return check(BRANDS.get(name))
        if cmd == "hazlo":
            lane = self.infer_lane(args, scope)
            # Sin carril claro, el comando ofrece botones de carril: se comprueban al pulsarlos.
            return check(BRANDS.get(lane)) if lane else None
        if cmd in ("prioridad", "tarea"):
            tid = (args.split() or [""])[0].lower()
            found = self._find(tid) if TASK_ID_RE.match(tid) else None
            if not found:
                return None  # el comando dirá que no la encuentra
            board, show = found
            marca = BRANDS.get((show.get("task") or {}).get("assignee")) or BOARD_BRANDS.get(board)
            return check(marca)
        return None

    @staticmethod
    def _where(msg: dict) -> tuple[str, str]:
        chat = str((msg.get("chat") or {}).get("id"))
        # Solo en temas de foro: en un grupo normal message_thread_id puede ser un hilo de respuestas.
        thread = str(msg.get("message_thread_id") or 0) if msg.get("is_topic_message") else "0"
        return chat, thread

    def _send(self, where: tuple[str, str], text: str, *, html: bool = True, markup: dict | None = None):
        return self.notifier.send_to(where[0], where[1], text, html=html,
                                     **({"reply_markup": markup} if markup else {}))

    @staticmethod
    def help_text() -> str:
        return ("Comandos del bot de trabajos:\n" + "\n".join(f"/{c} · {d}" for c, d in COMMANDS)
                + "\n\nCon /hazlo la tarea se crea ya, con prioridad alta y tope de 15 minutos; te aviso al terminar."
                + "\nCon /sesion abres una sesión: lo que escribas aquí se acumula y /sesion fin lo agrupa en UNA tarjeta."
                + "\n/desplegar pide dos toques (🚀 y ✅ Sí); /promover también pide doble confirmación.")

    # --- datos ----------------------------------------------------------------------------------------

    def renotifier(self):
        if self._renotifier is None:
            from .renotify import Renotifier
            self._renotifier = Renotifier(self.lanes, hermes_for=self.hermes_for, notifier=self.notifier,
                                          messages=self.messages, links=self.links, desk=self.desk,
                                          out=lambda s: log.debug("comandos: %s", s))
        return self._renotifier

    def _filter(self, pendings: list, brand: str | None) -> list:
        return [p for p in pendings if not brand or BRANDS.get(p.lane.name) == brand]

    def pending_decisions(self, brand: str | None = None) -> list:
        """Tareas de carril en needs_input (la misma lista para /decisiones y los recordatorios), más antigua primero."""
        found = [p for p in self.renotifier().collect(statuses=("blocked",))
                 if p.state == "needs_input" and titulos.is_live(p.task)]  # épicas/retenidas/backlog: vistas aparte
        return sorted(self._filter(found, brand), key=lambda p: (p.since or 0, p.tid))

    def stuck_tasks(self, brand: str | None = None) -> list:
        """🧊 Tareas de carril que Hermes pasó a triage por bloquearse dos veces por lo mismo (block_loop_detected):
        desde ahí `unblock` falla y nadie las ve. Más antigua primero; las cuentan también los recordatorios."""
        found = [p for p in self.renotifier().collect(statuses=("triage",)) if p.state == "stuck" and not p.guard
                 and titulos.is_live(p.task)]
        return sorted(self._filter(found, brand), key=lambda p: (p.since or 0, p.tid))

    # --- cola del integrador (limitaciones del guard) y resumen de las 9:00 (t_f8f58acb) ------------------------

    def _state_file(self, attr: str, name: str) -> Path | None:
        """Como urgent_tick: `attr` explícito (tests) o `.state/<name>` junto al almacén de mensajes real."""
        path = getattr(self, attr, None)
        root = getattr(self.messages, "root", None)
        if path is None and root and Path(root).parent.name == ".state":
            path = Path(root).parent / name
        return Path(path) if path else None

    def guard_pending(self) -> list:
        """Bloqueos por LIMITACION-GUARD (bloqueados y los que Hermes pasó a triage por repetirse)."""
        return [p for p in self.renotifier().collect(statuses=("blocked", "triage")) if p.guard]

    def guard_queue(self):
        from .guard_route import GuardQueue
        path = self._state_file("guard_state", "integrador_cola.json")
        if path is None:
            return None
        if getattr(self, "_guard_queue", None) is None:
            self._guard_queue = GuardQueue(path, self._now)
            self.desk.guard_queue = self._guard_queue  # ⏳ Aplazar (DecisionDesk._guard_snooze) lo usa
        return self._guard_queue

    def guard_tick(self) -> list[str]:
        """Cola del integrador: anuncia los bloqueos de guard nuevos y escala a Oscar los que llevan 2 h sin resolverse.
        Una pasada cada 2 min como mucho; sin `.state` (o sin `guard_state`) no hace nada."""
        from .guard_route import GuardNotifier, GuardRouter
        queue, now = self.guard_queue(), self._now()
        if queue is None or now - getattr(self, "_guard_at", 0) < URGENT_EVERY:
            return []
        self._guard_at = now
        ren = self.renotifier()
        gn = GuardNotifier(self.notifier, self.desk, ren.attention, self.owner_id, self._now)
        return GuardRouter(self.guard_pending, gn.to_integrator, gn.to_oscar, queue, now=self._now).tick()

    def daily_tick(self) -> str | None:
        """Resumen de las 9:00 por carril (resumen_carriles). Barato fuera de la franja: solo mira la hora."""
        from . import resumen_carriles as rc
        path = self._state_file("daily_state", "resumen_carriles.json")
        hour = self.vis_ajustes().hora_parte
        if path is None or rc.due_date(datetime.fromtimestamp(self._now(), rc.MADRID), set(), hour) is None:
            return None
        att = self.renotifier().attention
        who = mention((att or {}).get("oscar_mention") or (att or {}).get("mention"))

        def send(text: str, with_button: bool) -> bool:
            markup = self.desk.see_all_markup(None) if with_button else None
            kw = {"reply_markup": markup} if markup else {}
            chat, thread = (str(att["chat"]), str(att.get("thread") or "0")) if att else (self.owner_id, None)
            return bool(self.notifier.send_to(chat, thread, text, html=False, **kw))

        queue = self.guard_queue()
        return rc.DailyLaneSummary(
            lambda: rc.collect_rows(self.lanes, self.hermes_for), lambda: self.pending_decisions() + self.stuck_tasks(),
            lambda: len(queue.entries()) if queue else 0, send, path, who=who,
            now=lambda: datetime.fromtimestamp(self._now(), rc.MADRID), hour=hour,
            extra=lambda: self.integrador_resumen()[0]).tick()

    # --- visibilidad del integrador (t_5624611d): estado, PR de sesiones interactivas, «ver más» ---------------

    def vis_ajustes(self):
        from . import integrador_visibilidad as iv
        from .ajustes_hub import aplicar_visibilidad  # el YAML y, encima, lo editado con /ajustes
        return aplicar_visibilidad(iv.load_ajustes(getattr(self, "vis_settings", None)))  # se relee en cada uso

    def estado_integrador(self):
        from . import integrador_visibilidad as iv
        return iv.leer_estado(self._state_file("estado_path", iv.ESTADO_FILE))

    def cola_externos(self):
        from . import integrador_visibilidad as iv
        path = self._state_file("externos_state", iv.EXTERNOS_FILE)
        return iv.ColaExternos(path, self._now) if path else None

    def integrador_resumen(self) -> tuple[str, bool]:
        """(texto corto, hay «ver más»): estado del integrador (o su falta) y los PR de sesiones interactivas."""
        from . import integrador_visibilidad as iv
        aj, now = self.vis_ajustes(), self._now()
        text, mas = iv.resumen(self.estado_integrador(), aj, now)
        cola = self.cola_externos()
        ext = iv.vista_externos(cola.items() if cola else {}, now)
        return "\n".join(x for x in (text, ext) if x), mas

    # --- seguimiento de peticiones req_<n> (t_211c9902): /estado, «Ver más» y segundo parte de las 9:00 ----------------

    def registro_peticiones(self):
        """Registro de peticiones: el inyectado en `self.peticiones` si sabe listar; si no, `.state/peticiones.json`
        (None si no hay `.state`, p. ej. en tests sin él)."""
        from .peticiones import Peticiones
        if hasattr(self.peticiones, "listar"):
            return self.peticiones
        path = self._state_file("peticiones_state", "peticiones.json")
        return Peticiones(path, now=self._now) if path else None

    def _show_card(self, tid: str) -> dict | None:
        """`show` de una tarjeta de petición en el primer tablero que la tiene (el registro guarda el id, no el tablero)."""
        boards = dict.fromkeys([l.board for l in self.lanes.values() if l.board] + list(EXTRA_BOARDS))
        for board in boards:
            try:
                show = self._shows.show(self.hermes_for(board), tid)
            except Exception:
                continue
            if (show or {}).get("task"):
                return show
        return None

    def peticion_items(self, proyecto: str | None = None) -> list:
        """[(petición, Estado derivado)] leyendo las tarjetas enlazadas; una ilegible no tumba el resto."""
        from .peticiones import derivar
        store, ahora = self.registro_peticiones(), self._now()
        if store is None:
            return []
        return [(r, derivar(r, {t: self._show_card(t) for t in r["tarjetas"]}, ahora)) for r in store.listar(proyecto)]

    def peticiones_tick(self) -> str | None:
        """Segundo parte de las 9:00: por petición, junto al de carriles (no lo repite ni sale sin novedad)."""
        from . import resumen_carriles as rc
        from .formato_aviso import paginas
        from .resumen_peticiones import DailyRequestSummary
        path = self._state_file("peticiones_daily_state", "resumen_peticiones.json")
        if path is None or rc.due_date(datetime.fromtimestamp(self._now(), rc.MADRID), set()) is None:
            return None
        att = self.renotifier().attention
        chat, thread = (str(att["chat"]), str(att.get("thread") or "0")) if att else (self.owner_id, None)
        return DailyRequestSummary(
            self.peticion_items, lambda t: bool(self.notifier.send_to(chat, thread, t, html=False)), path,
            now=lambda: datetime.fromtimestamp(self._now(), rc.MADRID)).tick()

    def cmd_integrador(self, where, args: str, brand: str | None) -> None:
        from .decisions import ESTADO_MAS
        from .guard_route import queue_view
        queue = self.guard_queue()
        text, mas = self.integrador_resumen()
        markup = None
        if mas:
            rec = {"task_id": "estado-integrador", "board": "", "lane": ""}
            markup = self.desk.store.issue(rec, [[{"text": "📖 Ver más", "action": ESTADO_MAS}]])[1]
        self._send(where, text + "\n\n" + queue_view(queue.entries() if queue else {}, self._now()), html=False,
                   markup=markup)

    def estado_mas(self, where: dict) -> bool:
        """Botón «Ver más» de /integrador: todos los pasos del estado, en un mensaje nuevo."""
        from . import integrador_visibilidad as iv
        text = iv.detalle(self.estado_integrador(), self.vis_ajustes(), self._now())
        self._send((where["chat_id"], where["thread_id"]), text, html=False)
        return True

    def repos_encolables(self) -> dict[str, str]:
        """alias -> owner/repo que se pueden encolar: los del YAML o, sin ellos, los repos de los carriles."""
        aj = self.vis_ajustes()
        if aj.repos:
            return dict(aj.repos)
        out = {}
        for name, lane in self.lanes.items():
            slug = self.links.repo_slug(lane) if self.links else None
            if slug:
                out[name.removeprefix("claude-").lower()] = slug
        return out

    def cmd_encolar(self, where, args: str, brand: str | None) -> None:
        """/encolar <repo> <PR>: mete un PR de una sesión interactiva en la cola, con el botón de Oscar (sin él no se
        fusiona nada). Aquí no se fusiona ni se despliega."""
        from . import integrador_visibilidad as iv
        from .decisions import EXT_DROP, EXT_OK
        repos = self.repos_encolables()
        parsed = iv.parse_encolar(args)
        cola = self.cola_externos()
        if not parsed or parsed[0] not in repos:
            self._send(where, "Uso: /encolar <repo> <PR>. Repos: " + (", ".join(sorted(repos)) or "(ninguno configurado)"),
                       html=False)
            return
        if cola is None:
            self._send(where, "No puedo encolar: no hay carpeta .state configurada.", html=False)
            return
        alias, number = parsed
        slug = repos[alias]
        cp = self.desk._gh("pr", "view", str(number), "--repo", slug, "--json", "number,title,headRefOid,state")
        try:
            pr = json.loads(cp.stdout or "{}") if cp.returncode == 0 else {}
        except ValueError:
            pr = {}
        if pr.get("state") != "OPEN" or not re.fullmatch(r"[0-9a-f]{40}", pr.get("headRefOid") or ""):
            self._send(where, f"No encuentro el PR #{number} abierto en {slug}.", html=False)
            return
        key, it = cola.add(alias, slug, number, pr.get("title") or "", pr["headRefOid"])
        rec = {"task_id": key, "board": "", "lane": "", "ext": key}
        markup = self.desk.store.issue(rec, [[{"text": "✅ Dar OK a fusionar", "action": EXT_OK},
                                              {"text": "🗄 Quitar", "action": EXT_DROP}]])[1]
        self._send(where, f"🔀 En la cola del integrador: {alias} #{number} · {truncate(it['title'], 80)} · "
                          f"{it['sha'][:9]}\n➡️ Próximo paso: pulsa «Dar OK» (vale solo para este commit; si el PR "
                          "cambia, vuelve a pedirlo) · Lo da: Oscar. Sin tu OK no se fusiona nada.", html=False,
                   markup=markup)

    def externo_step(self, action: str, rec: dict, where: dict) -> bool:
        """Botones de /encolar. El OK se ata al commit que Oscar vio; si el PR cambió, se avisa y no vale."""
        from .decisions import EXT_OK
        cola, key = self.cola_externos(), rec.get("ext") or ""
        it = (cola.items() if cola else {}).get(key)
        dest = (where["chat_id"], where["thread_id"])
        if not it:
            self._send(dest, f"{key}: ya no está en la cola.", html=False)
            return True
        if action != EXT_OK:
            cola.quitar(key)
            self._send(dest, f"🗄 {key} fuera de la cola; no se ha tocado el PR.", html=False)
            return True
        cp = self.desk._gh("pr", "view", str(it["pr"]), "--repo", it["slug"], "--json", "headRefOid,state")
        try:
            pr = json.loads(cp.stdout or "{}") if cp.returncode == 0 else {}
        except ValueError:
            pr = {}
        if pr.get("state") != "OPEN" or not cola.dar_ok(key, pr.get("headRefOid") or ""):
            self._send(dest, f"⚠️ {key} cambió desde que lo viste (o ya no está abierto): vuelve a lanzar "
                             f"/encolar {it['repo']} {it['pr']} y confirma el commit nuevo.", html=False)
            return True
        self._send(dest, f"✅ OK registrado para {key} ({it['sha'][:9]}). El integrador puede fusionarlo; "
                         "el OK vale solo para este commit.", html=False)
        return True

    def decision_cards(self, brand: str | None = None) -> list[dict]:
        """Tarjetas de decisión de Oscar (no son de carril): asignadas a `oscar`, en ready/blocked y con etiqueta
        [DECISIÓN…]/[SEMANA…]/[IDEA…] en el título, en los tableros de los carriles + default. Más antigua primero.
        [{board, task, since}]. Con `brand` (tema de una marca), solo las de su tablero (default = "Otros")."""
        return self.oscar_cards(brand)[0]

    def oscar_cards(self, brand: str | None = None) -> tuple[list[dict], int]:
        """(tarjetas de decisión, nº de tarjetas a nombre de Oscar SIN etiqueta de decisión) en una sola pasada por
        los tableros: mismos estados y tableros, sin duplicados por id."""
        boards = dict.fromkeys([l.board for l in self.lanes.values() if l.board] + list(EXTRA_BOARDS))
        snoozed = self._snoozed()
        found: dict[str, dict] = {}
        untagged: set[str] = set()
        for board in boards:
            if brand and BOARD_BRANDS.get(board, "Otros") != brand:
                continue
            h = self.hermes_for(board)
            for status in DECISION_CARD_STATUSES:
                try:
                    tasks = h.list_status(OSCAR_ASSIGNEE, status)
                except Exception as exc:  # un tablero ilegible no vacía la bandeja
                    log.warning("tarjetas de Oscar en %s (%s) no leídas: %s", board, status, exc)
                    continue
                for t in tasks:
                    t = {"assignee": OSCAR_ASSIGNEE, "status": status, **t}
                    if not t.get("id") or t["id"] in found or t["id"] in snoozed:
                        continue
                    if not titulos.is_live(t):  # épica/retenida/backlog bot: ni cola, ni recordatorios, ni «sin etiqueta»
                        continue
                    if is_decision_card(t):
                        found[t["id"]] = {"board": board, "task": t, "since": decision_since(t)}
                        untagged.discard(t["id"])
                    else:
                        untagged.add(t["id"])
        cards = sorted(found.values(), key=lambda c: (c["since"] or 0, c["task"]["id"]))
        cfg = self.bandeja
        if cfg.dedupe:
            cards = _bandeja.dedupe(cards)  # la más antigua de cada grupo gana; las repetidas se enlazan en ella
        cards, low = _bandeja.split_low_priority(cards, cfg, self._now())
        self._auto_snooze(low)
        return _bandeja.rank(cards), len(untagged)

    def _auto_snooze(self, low: list[dict]) -> None:
        """Aplaza solas las de prioridad baja con mucha espera, con UN aviso al DM de Oscar. Reutiliza CardSnoozes: una
        aplazada ya no sale en /decisiones ni en los recordatorios (y por eso el aviso no se repite)."""
        snoozes = getattr(self.desk, "snoozes", None)
        if not low or not snoozes:
            return
        done = []
        for c in low:
            try:
                snoozes.add(c["task"]["id"], self.bandeja.autosnooze_aplazar_dias)
                done.append(c)
            except OSError as exc:
                log.warning("%s: no se pudo aplazar sola: %s", c["task"]["id"], exc)
        if not done:
            return
        days = self.bandeja.autosnooze_aplazar_dias
        lines = [f"⏳ Aplazadas solas {len(done)} tarjeta{'s' if len(done) != 1 else ''} de prioridad baja "
                 f"(más de {self.bandeja.autosnooze_dias:g} d esperando); vuelven en {days:g} d:"]
        lines += [f"• {c['task']['id']} · {_bandeja.origin(c)} · {truncate(c['task'].get('title'), 50)}" for c in done[:10]]
        if len(done) > 10:
            lines.append(f"… y {len(done) - 10} más")
        try:
            self.notifier.send_to(self.owner_id, None, "\n".join(lines), html=False)
        except Exception as exc:
            log.info("aviso de aplazadas solas no entregado: %s", exc)

    def _snoozed(self) -> set[str]:
        """Tarjetas aplazadas con [⏳ Aplazar 7 d]: no salen en /decisiones ni en los recordatorios hasta que pasa el plazo."""
        snoozes = getattr(self.desk, "snoozes", None)
        try:
            return snoozes.hidden() if snoozes else set()
        except Exception as exc:
            log.warning("tarjetas aplazadas no leídas: %s", exc)
            return set()

    def ready_to_approve(self, brand: str | None = None) -> list:
        found = [p for p in self.renotifier().collect(statuses=("done",)) if p.state == "done"]
        return sorted(self._filter(found, brand), key=lambda p: (p.since or 0, p.tid))

    def _links_for(self, lane, tid: str, changed_files=None) -> list:
        return self.links(lane, tid, changed_files=changed_files) if self.links else []

    def _tree(self, board: str | None, tid: str, **kw) -> list[str]:
        """Líneas 🔗/⏸/↳ de una tarjeta (deps.tree_lines, nunca lanza)."""
        if not board:
            return []
        try:
            h = self.hermes_for(board)
        except Exception:
            return []
        return tree_lines(h, tid, cache=self._shows, **kw)

    def _mirror(self, tid: str, sent, markup) -> None:
        """Registra la tarjeta como copia del aviso de la tarea: una decisión en cualquiera edita todas."""
        if self.messages and sent:
            self.messages.add_mirror(tid, sent, token=markup_token(markup), bot=getattr(self.notifier, "bot_id", None))

    # --- /hoy -----------------------------------------------------------------------------------------

    def cmd_hoy(self, where, args: str, brand: str | None) -> None:
        if self._brief is not None:
            text = self._brief(brand)
        else:
            from brief import brief_diario  # perezoso: solo el runner-servicio lo necesita
            text = brief_diario.build(hermes_for=self.hermes_for, brand_name=brand)
        self._send(where, text)

    # --- /decisiones ----------------------------------------------------------------------------------

    def cmd_decisiones(self, where, args: str, brand: str | None, show_all: bool = False) -> None:
        """Las TOP_DECISIONS más importantes, cada una en su mensaje con botones, y "N más · ver todas". Importancia
        (decide Oscar): primero las que bloquean agentes (preguntas y 🧊 atascadas, la más antigua antes), luego las
        tarjetas de decisión por antigüedad. Con ≤5 en total, o con `show_all`, la bandeja completa de siempre."""
        now = self._now()
        pendings = self.pending_decisions(brand)
        stuck = self.stuck_tasks(brand)
        cards, untagged = self.oscar_cards(brand)
        total = len(group_pending(pendings)) + len(stuck) + len(cards)
        if total > self.bandeja.top and not show_all:
            self._top_decisions(where, pendings, stuck, cards, now, brand, total)
            return
        scope = f" · {brand}" if brand else ""
        if not pendings and not stuck and not cards:
            if untagged:  # no son decisiones, pero que no se olviden
                self._send(where, html.escape(f"Nada pendiente de ti{scope} 🎉") + "\n" + self.untagged_line(untagged))
            else:
                self._send(where, f"Nada pendiente de ti{scope} 🎉", html=False)
            return
        if pendings:
            self._agent_questions(where, pendings, now, scope)
        if stuck:
            self._stuck_section(where, stuck, now, scope)
        if cards:
            self._cards_section(where, cards, now, scope)
        if untagged:
            self._send(where, self.untagged_line(untagged))

    def cmd_atencion(self, where, args: str, brand: str | None) -> None:
        """Todo lo que espera a Oscar (preguntas, 🧊 atascadas y tarjetas de decisión) con sus botones, sin recorte."""
        self.cmd_decisiones(where, args, brand, show_all=True)

    # --- vistas por paso del título (titulos.py): fuera de la cola de Telegram ------------------------------------

    def board_tasks(self, brand: str | None = None) -> list[dict]:
        """[{board, task, since}] de todas las tarjetas vivas del kanban (sin hechas ni archivadas) de los tableros de
        los carriles + default, sin duplicados por id. Un tablero ilegible no vacía la vista."""
        found: dict[str, dict] = {}
        for board in dict.fromkeys([l.board for l in self.lanes.values() if l.board] + list(EXTRA_BOARDS)):
            if brand and BOARD_BRANDS.get(board, "Otros") != brand:
                continue
            h = self.hermes_for(board)
            for status in VIEW_STATUSES:
                try:
                    tasks = h.list_status_all(status)
                except Exception as exc:
                    log.warning("vista del kanban: %s/%s no leído: %s", board, status, exc)
                    continue
                for t in tasks:
                    t = {"status": status, **t}
                    if t.get("id") and t["id"] not in found:
                        found[t["id"]] = {"board": board, "task": t, "since": decision_since(t)}
        return sorted(found.values(), key=lambda c: (c["since"] or 0, c["task"]["id"]))

    def _step_view(self, where, brand: str | None, paso: str, titulo: str) -> None:
        e = html.escape
        rows = [c for c in self.board_tasks(brand) if titulos.step_of(c["task"]) == paso]
        scope = f" · {brand}" if brand else ""
        if not rows:
            self._send(where, f"{titulo}{scope}: nada 🎉", html=False)
            return
        lines = [f"<b>{e(titulo)} ({len(rows)}){e(scope)}</b>"]
        for c in rows[:MAX_CARDS]:
            t = c["task"]
            url = card_url(self.base_url, c["board"], t["id"])
            ref = f'<a href="{e(url, quote=True)}">{t["id"]}</a>' if url else f"<code>{t['id']}</code>"
            lines.append(f"• {ref} · {e(truncate(t.get('title'), CARD_TITLE_MAX))}")
        if len(rows) > MAX_CARDS:
            lines.append(f"… y {len(rows) - MAX_CARDS} más en el panel")
        self._send(where, "\n".join(lines))

    def cmd_epicas(self, where, args: str, brand: str | None) -> None:
        self._step_view(where, brand, titulos.EPICA, "🏔 Épicas")

    def cmd_retenidas(self, where, args: str, brand: str | None) -> None:
        self._step_view(where, brand, titulos.RETENIDA, "⏸ Retenidas")

    def cmd_backlog(self, where, args: str, brand: str | None) -> None:
        self._step_view(where, brand, titulos.BACKLOG_BOT, "🗃 Backlog del bot del equipo")

    def cmd_pendientes(self, where, args: str, brand: str | None) -> None:
        """«Pendientes de Oscar»: assignee oscar con paso tuyo o decidir, una tarjeta por mensaje con sus botones. Las de
        Oscar sin paso reconocible salen aparte como «sin clasificar» con aviso (nunca se ocultan)."""
        now = self._now()
        mine = [c for c in self.board_tasks(brand) if c["task"].get("assignee") == OSCAR_ASSIGNEE]
        snoozed = self._snoozed()
        pend = [c for c in mine if titulos.step_of(c["task"]) in titulos.OSCAR_STEPS and c["task"]["id"] not in snoozed]
        loose = [c for c in mine if titulos.step_of(c["task"]) is None and c["task"]["id"] not in snoozed]
        scope = f" · {brand}" if brand else ""
        if not pend and not loose:
            self._send(where, f"Nada pendiente de ti{scope} 🎉", html=False)
            return
        if pend:
            self._send(where, f"🙋 Pendientes de Oscar ({len(pend)}){scope}", html=False)
            for c in pend[:MAX_CARDS]:
                self._send_card(where, c, now)
            if len(pend) > MAX_CARDS:
                self._send(where, f"Muestro {MAX_CARDS}; el resto, en el panel", html=False)
        if loose:
            e = html.escape
            lines = [f"⚠️ {_plural(len(loose), 'tarjeta tuya', 'tarjetas tuyas')} sin clasificar: el título no acaba en "
                     f"«· paso» ({e(', '.join(titulos.PASOS))})"]
            lines += [f"• <code>{c['task']['id']}</code> · {e(truncate(c['task'].get('title'), CARD_TITLE_MAX))}"
                      for c in loose[:MAX_CARDS]]
            self._send(where, "\n".join(lines))

    def _top_decisions(self, where, pendings: list, stuck: list, cards: list[dict], now: float, brand: str | None,
                       total: int) -> None:
        scope = f" · {brand}" if brand else ""
        top = self.bandeja.top
        n_q = min(len(group_pending(pendings)), top)
        n_s = min(len(stuck), top - n_q)
        n_c = top - n_q - n_s
        if n_q:
            self._agent_questions(where, pendings, now, scope, limit=n_q)
        if n_s:
            self._stuck_section(where, stuck[:n_s], now, scope)
        shown = cards[:n_c]
        for c in shown:
            self._send_card(where, c, now)
        self._accept_cards_line(where, shown)
        # El resto, resumido por origen ("MigraTeam 4 · Otros 3"): se ve entero con [📋 Ver todas].
        rest = cards[n_c:]
        by_origin = f" ({_bandeja.group_counts(rest)})" if rest else ""
        self._send(where, f"➕ {total - top} más{by_origin} · ver todas", html=False,
                   markup=self.desk.see_all_markup(brand))

    def _accept_cards_line(self, where, cards: list[dict]) -> None:
        """[✅ Aceptar las recomendadas (N)] del lote de tarjetas enseñadas, si hay 2 o más con recomendada."""
        members = []
        for c in cards:
            spec = self.card_spec(c)
            if spec.has_options and spec.recommended is not None:
                members.append({"task_id": c["task"]["id"], "board": c["board"], "title": c["task"].get("title") or "",
                                "options": spec.options, "recommended": spec.recommended})
        if len(members) >= 2:
            self._send(where, f"⭐ {len(members)} tarjetas con recomendada", html=False,
                       markup=self.desk.card_accept_all_markup(members))

    def _stuck_section(self, where, stuck: list, now: float, scope: str) -> None:
        """🧊 Atascadas: cabecera + una tarjeta por tarea con [🔄 Reintentar] [🗄 Aparcar]."""
        head = (f"🧊 Atascadas ({len(stuck)}){scope} · Hermes las paró por bloquearse dos veces por lo mismo; "
                "🔄 Reintentar las devuelve a la cola sin reescribirlas")
        if len(stuck) > MAX_CARDS:
            head += f"\nMuestro {MAX_CARDS}; el resto, en el panel"
        self._send(where, head, html=False)
        for p in stuck[:MAX_CARDS]:
            status = status_line(p.status, f"hace {hours_ago(p.since, now)} h")
            text = render("stuck", p.tid, p.task.get("title"), p.lane.name, status, self._links_for(p.lane, p.tid),
                          body=p.task.get("body"), for_oscar=p.for_oscar,
                          next_step=next_step("stuck", p.block_kind))
            markup = self.desk.markup("stuck", task=p.task, lane=p.lane, block_kind=p.block_kind)
            self._mirror(p.tid, self._send(where, text, markup=markup), markup)

    def card_spec(self, c: dict) -> CardSpec:
        """Opciones de una tarjeta: de su cuerpo o de un comentario PREPARADO-POR-CLAUDE (lee `show`; sin él, sin opciones)."""
        t = c["task"]
        try:
            show = self._shows.show(self.hermes_for(c["board"]), t["id"])
        except Exception as exc:
            log.info("%s: tarjeta no leída para sus opciones: %s", t.get("id"), exc)
            show = {}
        body = ((show.get("task") or {}).get("body")) or t.get("body")
        return parse_card(body, show.get("comments") or ())

    def _send_card(self, where, c: dict, now: float) -> None:
        """Una tarjeta de decisión = un mensaje con contexto, opciones escritas y botones."""
        spec = self.card_spec(c)
        text = card_message(c, spec, self.base_url, now)
        if c.get("duplicates"):  # avisos repetidos: una sola tarjeta, con sus gemelas enlazadas
            text += f"\n<i>Repetida en: {html.escape(', '.join(c['duplicates'][:5]))}</i>"
        veto = self.bandeja.veto_activo and spec.has_options and spec.recommended is not None and spec.reversible
        if veto:
            text += "\n" + self._veto_line(c["task"]["id"], spec)
        markup = self.desk.card_markup(c, spec.options if spec.has_options else [], spec.recommended, text,
                                       veto=bool(veto))
        self._mirror(c["task"]["id"], self._send(where, text, markup=markup), markup)

    def _veto_line(self, tid: str, spec: CardSpec) -> str:
        """Línea del plazo de veto de una tarjeta reversible (arranca la primera vez que se enseña)."""
        cfg = self.bandeja
        due = self.desk.vetos.arm(tid) + cfg.veto_horas * 3600
        when = time.strftime("%d/%m %H:%M", time.localtime(due))
        verbo = "se aplicará sola" if cfg.veto_modo == _bandeja.VETO_EJECUTAR else "te avisaré (aún no se aplica sola)"
        return f"⏱ Reversible: si no decides antes del {when}, {verbo} la recomendada ({'ABC'[spec.recommended]}) · ⛔ Vetar"

    def urgent_tick(self) -> list[str]:
        """Aviso inmediato de los bloqueos de tarjetas con prioridad >= 8 (aviso.UrgentBlockWatcher). Se engancha al
        tic del runner vía `veto_tick` (runner.py no se toca). Sin `urgent_state` no hace nada, salvo con el almacén de
        mensajes real (`.state/messages`), donde guarda `.state/urgent_avisos.json`. Una pasada cada 2 min como mucho."""
        state = getattr(self, "urgent_state", None)
        root = getattr(self.messages, "root", None)
        if state is None and root and Path(root).parent.name == ".state":
            state = Path(root).parent / "urgent_avisos.json"
        now = self._now()
        if state is None or now - getattr(self, "_urgent_at", 0) < URGENT_EVERY:
            return []
        self._urgent_at = now
        ren = self.renotifier()
        return UrgentBlockWatcher(lambda: ren.collect(statuses=("blocked",)), ren.alert_urgent, state).tick()

    def veto_tick(self) -> list[str]:
        """Tarjetas reversibles cuyo plazo de veto ha vencido sin respuesta ni veto. Modo `aviso`: solo se lo cuenta a
        Oscar (no ejecuta nada). Modo `ejecutar`: aplica la recomendada. Devuelve los ids tratados. Nunca lanza."""
        try:
            self.urgent_tick()
        except Exception as exc:
            log.warning("avisos urgentes: %s", exc)
        for tick in (self.guard_tick, self.daily_tick, self.peticiones_tick):  # cola del integrador y partes de las 9:00
            try:
                tick()
            except Exception as exc:
                log.warning("%s: %s", tick.__name__, exc)
        cfg, book = self.bandeja, getattr(self.desk, "vetos", None)
        if not cfg.veto_activo or book is None:
            return []
        try:
            due = book.due(cfg.veto_horas)
            if not due:
                return []
            pending = {c["task"]["id"]: c for c in self.oscar_cards()[0]}
        except Exception as exc:
            log.warning("veto: no se pudo comprobar: %s", exc)
            return []
        treated, lines = [], []
        for tid in due:
            c = pending.get(tid)
            spec = self.card_spec(c) if c else None
            if not c or not spec.reversible or not spec.has_options or spec.recommended is None:
                book.set_state(tid, "cerrada")  # ya decidida, aplazada o ya no es reversible: nada que hacer
                continue
            title = truncate(c["task"].get("title"), 60)
            answer = spec.answer_for(spec.recommended)
            if cfg.veto_modo == _bandeja.VETO_EJECUTAR:
                member = {"task_id": tid, "board": c["board"], "title": c["task"].get("title") or "",
                          "options": spec.options, "recommended": spec.recommended}
                ok = self.desk.apply_recommended(member)
                book.set_state(tid, "ejecutada" if ok else "armed")
                if not ok:
                    continue
                lines.append(f"⏱ Plazo de veto vencido: apliqué la recomendada en {tid} · {title}: {truncate(answer, 80)}")
            else:
                book.set_state(tid, "avisada")
                lines.append(f"⏱ Plazo de veto vencido en {tid} · {title}. Modo aviso: no he aplicado nada; "
                             f"habría elegido {truncate(answer, 80)}")
            treated.append(tid)
        if lines:
            try:
                self.notifier.send_to(self.owner_id, None, "\n".join(lines), html=False)
            except Exception as exc:
                log.info("aviso de veto no entregado: %s", exc)
        return treated

    def _cards_section(self, where, cards: list[dict], now: float, scope: str) -> None:
        """Cabecera + una tarjeta por mensaje (hasta MAX_CARDS; el resto, en el panel)."""
        head = f"📌 Tus tarjetas de decisión ({len(cards)}){scope}"
        if len(cards) > MAX_CARDS:
            head += f"\nMuestro {MAX_CARDS}; el resto, en el panel"
        self._send(where, head, html=False)
        for c in cards[:MAX_CARDS]:
            self._send_card(where, c, now)
        self._accept_cards_line(where, cards[:MAX_CARDS])

    # --- /preparar ------------------------------------------------------------------------------------

    def _prepare_ctx(self, c: dict) -> dict:
        t = c["task"]
        h = self.hermes_for(c["board"])
        show = self._shows.show(h, t["id"])
        task = show.get("task") or {}
        ctx = {"task_id": t["id"], "title": task.get("title") or t.get("title"), "body": task.get("body") or "",
               "comments": show.get("comments") or []}
        for pid in (show.get("parents") or [])[:1]:
            pid = pid if isinstance(pid, str) else (pid or {}).get("id")
            try:
                ptask = (h.show(pid) or {}).get("task") or {}
            except Exception:
                continue
            ctx.update(parent_title=ptask.get("title"), parent_body=ptask.get("body"))
        return ctx

    def cmd_preparar(self, where, args: str, brand: str | None) -> None:
        """/preparar t_xxx | todas: Claude (sonnet, sin herramientas, ≤0,5 $ por tarjeta) redacta Contexto + 3 opciones +
        recomendada y se guarda como comentario PREPARADO-POR-CLAUDE (el cuerpo no se toca). Luego reenvía la tarjeta
        con botones. Solo tarjetas de decisión sin opciones; `todas` tiene tope PREPARE_MAX."""
        arg = args.strip().split()[0].lower() if args.strip() else ""
        cards = self.oscar_cards(brand)[0]
        if arg == "todas":
            todo = [c for c in cards if not self.card_spec(c).has_options]
        elif TASK_ID_RE.match(arg):
            todo = [c for c in cards if c["task"]["id"] == arg]
            if not todo:
                self._send(where, f"{arg} no es una tarjeta de decisión pendiente tuya", html=False)
                return
            if self.card_spec(todo[0]).has_options:
                self._send(where, f"{arg} ya tiene opciones; aquí van:", html=False)
                self._send_card(where, todo[0], self._now())
                return
        else:
            self._send(where, "Uso: /preparar t_xxxxxxxx  o  /preparar todas", html=False)
            return
        if not todo:
            self._send(where, "No hay tarjetas sin opciones que preparar 🎉", html=False)
            return
        skipped = max(0, len(todo) - PREPARE_MAX)
        todo = todo[:PREPARE_MAX]
        self._send(where, f"Preparando {len(todo)} tarjeta{'s' if len(todo) != 1 else ''}"
                          + (f" (tope {PREPARE_MAX}; quedan {skipped} para otra vez)" if skipped else "")
                          + "… tarda un rato", html=False)
        preparer = self._preparer
        if preparer is None:
            from .card_prepare import prepare as preparer
        failed = []
        for c in todo:
            tid = c["task"]["id"]
            try:
                block = preparer(self._prepare_ctx(c))
                h = self.hermes_for(c["board"])
                ok = bool(block) and h.comment(tid, f"{PREPARED_PREFIX}\n{block}"[:3000], author=PREPARED_AUTHOR)
            except Exception as exc:
                log.warning("/preparar %s falló: %s", tid, exc)
                ok = False
            if not ok:
                failed.append(tid)
                continue
            self._shows._data.clear()  # que el mensaje nuevo lea el comentario recién escrito
            self._send_card(where, c, self._now())
        if failed:
            self._send(where, "No pude preparar: " + ", ".join(failed), html=False)

    def untagged_line(self, n: int) -> str:
        """"📋 N tarjetas más a tu nombre sin etiqueta · panel" (enlace a la raíz del panel del kanban)."""
        e = html.escape
        url = panel_url(self.base_url)
        panel = f'<a href="{e(url, quote=True)}">panel</a>' if url else "panel"
        return f"📋 {_plural(n, 'tarjeta más', 'tarjetas más')} a tu nombre sin etiqueta · {panel}"

    # --- /hazlo ---------------------------------------------------------------------------------------

    def _implement_lanes(self) -> list[str]:
        return [n for n, l in self.lanes.items() if getattr(l, "kind", "implement") == "implement"]

    def infer_lane(self, text: str, brand: str | None) -> str | None:
        """Carril de un /hazlo: el único cuyas palabras clave salen en el texto; si no hay ninguna, el único carril de la
        marca del tema. None si no se puede decidir (ninguno, varios o marca con dos carriles)."""
        low = " ".join((text or "").casefold().split())
        named = [n for n in self._implement_lanes()
                 if any(re.search(rf"(?<![\w-]){re.escape(h)}(?![\w-])", low) for h in LANE_HINTS.get(n, ()))]
        if len(named) == 1:
            return named[0]
        if named:
            return None
        in_brand = [n for n in self._implement_lanes() if brand and BRANDS.get(n) == brand]
        return in_brand[0] if len(in_brand) == 1 else None

    def cmd_hazlo(self, where, args: str, brand: str | None) -> None:
        text = args.strip()
        if not text:
            self._send(where, "Uso: /hazlo <qué hay que hacer>. Ej.: /hazlo MigraTeam: corrige el typo del pie",
                       html=False)
            return
        lane = self.infer_lane(text, brand)
        if lane:
            self._send(where, self.create_quick(lane, text, where))
            return
        names = [n for n in self._implement_lanes() if not brand or BRANDS.get(n) == brand] or self._implement_lanes()
        rec = {"task_id": "hazlo", "board": "", "lane": "", "kind": "hazlo", "text": text, "lanes": names}
        spec = [[{"text": f"{BRANDS.get(n, 'Sistema')} · {n}", "action": HAZLO, "index": i}]
                for i, n in enumerate(names)] + [[{"text": "🚫 Cancelar", "action": HAZLO, "index": -1}]]
        markup = self.desk.store.issue(rec, spec)[1]
        self._send(where, "¿En qué carril lo pongo?\n" + truncate(text, 300), html=False, markup=markup)

    def create_quick(self, lane_name: str, text: str, where) -> str:
        """Crea la tarjeta del /hazlo en el carril y devuelve el aviso (HTML) para Oscar."""
        lane = self.lanes[lane_name]
        chat, thread = where
        body = (f"## Objetivo\n{text.strip()}\n\n## Encargo rápido (/hazlo desde Telegram)\n"
                f"- Tarea pequeña: tope de {HAZLO_MINUTES} minutos de trabajo. Si no cabe, no la empieces: "
                "devuelve needs_input proponiendo cómo trocearla.\n\n"
                f"Origen-Telegram: chat={chat} thread={thread}\n")
        slot = int(self._now() // 600)  # reenvío del mismo update: misma tarjeta, no dos
        key = "hazlo-" + hashlib.sha1(f"{lane_name}|{chat}|{thread}|{slot}|{text}".encode()).hexdigest()[:16]
        tid = self.hermes_for(lane.board).create(truncate(text.splitlines()[0], HAZLO_TITLE_MAX), body, lane_name,
                                                 priority=HAZLO_PRIORITY, key=key, created_by="oscar")
        return (f"✅ Creada <code>{html.escape(tid)}</code> en {html.escape(lane_name)} · prioridad alta, tope "
                f"{HAZLO_MINUTES} min. Te aviso al terminar.")

    def hazlo_choice(self, rec: dict, button: dict, where: dict) -> bool:
        """Botón de carril de un /hazlo con duda. True = hecho (tarjeta creada o cancelado); False = fallo (vuelven
        los botones)."""
        idx, names = button.get("index"), rec.get("lanes") or []
        if idx == -1:
            self.notifier.edit(where["chat_id"], where["message_id"], "🚫 Cancelado, no he creado nada", html=False)
            return True
        if not isinstance(idx, int) or not 0 <= idx < len(names) or names[idx] not in self.lanes:
            return False
        text = self.create_quick(names[idx], rec.get("text") or "", (where["chat_id"], where["thread_id"]))
        self.notifier.edit(where["chat_id"], where["message_id"], text)
        return True

    # --- /pedir ---------------------------------------------------------------------------------------

    @property
    def tipo(self):
        if self._tipo is None:
            self._tipo = tipo_peticion.TipoPeticion(self.notifier, self.desk.store, crear_tarjeta=self._pedir_card,
                                                    registro=self.peticiones, now=self._now)
        return self._tipo

    def cmd_pedir(self, where, args: str, brand: str | None) -> None:
        text = args.strip()
        if not text:
            self._send(where, "Uso: /pedir <qué necesitas>. Te pregunto si es algo que falla o algo nuevo y lo que haga "
                       "falta. Ej.: /pedir MigraTeam: el login falla con Google", html=False)
            return
        self.tipo.start(where, text, lane=self.infer_lane(text, brand), brand=brand)

    def pedir_step(self, rec: dict, button: dict, where: dict, text: str | None = None) -> bool:
        """Botón (o respuesta escrita de ✍️) de un /pedir. False = vuelven los botones."""
        return self.tipo.step(rec, button, where, self.desk._who(), text)

    def _pedir_card(self, st: dict, title: str, body: str, priority: int | None) -> str:
        """Tarjeta del /pedir: en el carril por palabras clave (como /hazlo) o, sin carril claro, [IDEA] para Oscar."""
        lane = st.get("lane")
        board, assignee = (self.lanes[lane].board, lane) if lane in self.lanes else ("default", OSCAR_ASSIGNEE)
        key = tipo_peticion.card_key(st["confirmado_por"], tuple(st["where"]), st["texto"], self._now())
        tid = self.hermes_for(board).create(title, body, assignee, priority=priority, key=key, created_by="oscar")
        return (f"✅ Creada <code>{html.escape(tid)}</code> en {html.escape(assignee)} · "
                f"{html.escape(st['tipo'])}" + (f" {html.escape(st['urgencia'])}" if st.get("urgencia") else "")
                + ". Te aviso al terminar.")

    # --- /sesion --------------------------------------------------------------------------------------

    @property
    def sesiones(self):
        if self._sesiones is None:
            from .sesion import Sesiones
            self._sesiones = Sesiones(Path(self.desk.store.root).parent / "sesiones", now=self._now)
        return self._sesiones

    def _known_boards(self) -> list[str]:
        return list(dict.fromkeys([l.board for l in self.lanes.values() if l.board] + list(EXTRA_BOARDS)))

    def _session_board(self, marca: str | None) -> str:
        """Tablero de la tarjeta: el del carril de la marca; sin marca (o sin carril), el default."""
        for n in self._implement_lanes():
            if marca and BRANDS.get(n) == marca and self.lanes[n].board:
                return self.lanes[n].board
        return EXTRA_BOARDS[0]

    def _expire_session(self, where) -> None:
        ses = self.sesiones.expire()
        if ses:
            self._send(where, f"⌛ La sesión «{ses['nombre']}» caducó a las 12 h con {len(ses['puntos'])} punto(s) sin "
                              "cerrar. No he creado ninguna tarjeta; el borrador queda guardado en .state/sesiones.",
                       html=False)

    def session_note(self, msg: dict) -> bool:
        """Texto libre del dueño en su DM: con una sesión abierta lo anota como punto y devuelve True (sin ayuda del #16)."""
        where = self._where(msg)
        self._expire_session(where)
        text = (msg.get("text") or "").strip()
        if not text:
            return False
        try:
            n = self.sesiones.add(text)
        except Exception as exc:
            log.warning("sesión: no pude anotar el punto: %s", type(exc).__name__)
            self._send(where, "No pude anotar el punto en la sesión; cierra con /sesion fin o mira el log", html=False)
            return True
        if n is None:
            return False
        self._send(where, f"📝 Punto {n} anotado. /sesion fin para cerrar · /sesion estado", html=False)
        return True

    def capture(self, msg: dict) -> bool:
        """Foto o documento del dueño en su DM: se descarga a .state/adjuntos/ y se anota en la sesión /sesion abierta o
        crea una tarjeta [CAPTURA] con la ruta. True si había adjunto (se responda lo que se responda)."""
        from . import capturas
        picked = capturas.pick(msg)
        if not picked:
            return False
        where = self._where(msg)
        file_id, size, ext = picked
        try:
            if size is not None and size > capturas.MAX_BYTES:
                raise capturas.AttachmentError("pesa más de 10 MB")
            dest = Path(self.desk.store.root).parent / "adjuntos" / capturas.safe_name(msg, ext, self._now())
            try:
                self.notifier.download_file(file_id, dest, capturas.MAX_BYTES)
            except ValueError:
                raise capturas.AttachmentError("pesa más de 10 MB") from None
            except Exception as exc:
                log.warning("captura: descarga fallida: %s", type(exc).__name__)
                raise capturas.AttachmentError("no pude descargarlo; reenvíalo en un rato") from None
            caption = capturas.caption_of(msg)
            self._expire_session(where)
            if self.sesiones.current():
                n = self.sesiones.add(capturas.session_point(dest.resolve(), caption))
                self._send(where, f"📎 Captura anotada como punto {n} de la sesión. /sesion fin para cerrar", html=False)
                return True
            chat = where[0]
            key = "captura-" + hashlib.sha1(f"{chat}|{msg.get('message_id')}".encode()).hexdigest()[:16]
            tid = self.hermes_for(self._session_board(self._scope_brand(where))).create(
                truncate("[CAPTURA] " + caption, HAZLO_TITLE_MAX), capturas.card_body(dest.resolve(), caption),
                OSCAR_ASSIGNEE, key=key, created_by="oscar")
            self._send(where, f"Recibida, tarjeta <code>{html.escape(tid)}</code>")
        except capturas.AttachmentError as exc:
            self._send(where, f"No guardé la captura: {exc}", html=False)
        except Exception as exc:
            log.warning("captura: falló: %s", type(exc).__name__)
            self._send(where, "No pude procesar la captura; detalle en el log", html=False)
        return True

    def _scope_brand(self, where) -> str | None:
        return scope_brand(where[0], where[1], self.lanes, self.generic_origins)

    def cmd_sesion(self, where, args: str, brand: str | None) -> None:
        """/sesion [nombre] | fin [tablero] | cancelar | estado. Una sola sesión abierta; caduca a las 12 h."""
        self._expire_session(where)
        first, _, rest = args.strip().partition(" ")
        sub, rest = first.casefold(), rest.strip()
        cur = self.sesiones.current()
        if sub == "estado":
            self._send(where, f"📝 Sesión «{cur['nombre']}»: {len(cur['puntos'])} punto(s). /sesion fin para cerrarla"
                       if cur else "No hay ninguna sesión abierta. /sesion [nombre] abre una", html=False)
        elif sub == "cancelar":
            if cur:
                self.sesiones.finish(cur, cancelled=True)
            self._send(where, f"🚫 Sesión «{cur['nombre']}» descartada" if cur else "No hay ninguna sesión abierta",
                       html=False)
        elif sub == "fin":
            self._session_end(where, cur, rest)
        elif cur:
            self._send(where, f"Ya hay una sesión abierta («{cur['nombre']}», {len(cur['puntos'])} punto(s)). "
                              "Ciérrala con /sesion fin o descártala con /sesion cancelar", html=False)
        else:
            marca = BRAND_ARGS.get(sub)  # "/sesion migrateam Revisión del lunes": la marca elige el tablero
            ses = self.sesiones.start(rest if marca else args, marca or brand)
            self._send(where, f"📝 Sesión «{ses['nombre']}» abierta" + (f" · {ses['marca']}" if ses["marca"] else "")
                       + ". Escribe tus correcciones como mensajes sueltos; /sesion fin las agrupa en una tarjeta "
                         "(caduca a las 12 h)", html=False)

    def _session_end(self, where, ses: dict | None, board_arg: str) -> None:
        if not ses:
            self._send(where, "No hay ninguna sesión abierta", html=False)
            return
        if not ses["puntos"]:
            self._send(where, "La sesión no tiene puntos; escribe alguno o usa /sesion cancelar", html=False)
            return
        board = board_arg.split()[0].casefold() if board_arg else self._session_board(ses.get("marca"))
        if board not in self._known_boards():
            self._send(where, "Tablero desconocido; usa uno de: " + ", ".join(self._known_boards()), html=False)
            return
        self._send(where, f"Cerrando «{ses['nombre']}» ({len(ses['puntos'])} puntos)… Claude redacta preguntas y mejoras",
                   html=False)
        from . import sesion
        try:
            sections = (self._suggester or sesion.suggest)(ses)
        except Exception as exc:
            log.warning("sesión %s: sugerencias fallidas: %s", ses["id"], type(exc).__name__)
            sections = None
        tid = self.hermes_for(board).create(truncate(f"[CORRECCIONES] {ses['nombre']}", HAZLO_TITLE_MAX),
                                            sesion.card_body(ses, sections), OSCAR_ASSIGNEE,
                                            key="sesion-" + ses["id"], created_by="oscar")
        self.sesiones.finish(ses)
        self._send(where, f"✅ Tarjeta <code>{html.escape(tid)}</code> creada en {html.escape(board)} con "
                          f"{len(ses['puntos'])} punto(s)" + ("" if sections else " (sin sugerencias de Claude)"))

    # --- /flujo ---------------------------------------------------------------------------------------

    def cmd_flujo(self, where, args: str, brand: str | None) -> None:
        """/flujo <texto>: Claude (sonnet, sin herramientas, ≤0,5 $) propone hasta 8 tarjetas encadenadas; se muestran
        numeradas con [Crear todo] / [Cancelar]. No se crea nada hasta pulsar."""
        text = args.strip()
        if not text:
            self._send(where, "Uso: /flujo <petición>. Ej.: /flujo añadir exportación a CSV en MigraTeam con tests y doc",
                       html=False)
            return
        from . import flujo
        lanes = {n: f"{BRANDS.get(n, 'Sistema')} (tablero {self.lanes[n].board or 'default'})"
                 for n in self._implement_lanes()}
        self._send(where, "Claude está troceando la petición…", html=False)
        cards = (self._flow_planner or flujo.propose)(text, lanes)
        if not cards:
            self._send(where, "No pude proponer un flujo válido (Claude no respondió o salió un plan inconsistente). "
                              "Reformula la petición o usa /hazlo", html=False)
            return
        rec = {"task_id": "flujo", "board": "", "lane": "", "kind": "flujo", "cards": cards, "text_hash": text,
               "origin": [where[0], where[1]], "slot": int(self._now() // 600)}
        spec = [[{"text": "✅ Crear todo", "action": FLUJO, "index": 1}, {"text": "🚫 Cancelar", "action": FLUJO, "index": 0}]]
        markup = self.desk.store.issue(rec, spec)[1]
        self._send(where, flujo.render(cards), html=False, markup=markup)

    def flujo_choice(self, rec: dict, button: dict, where: dict) -> bool:
        """Botón de la propuesta de /flujo. True = hecho (creado o cancelado); False = hubo fallos (vuelven los botones;
        reintentar no duplica: cada tarjeta lleva clave idempotente)."""
        if button.get("index") != 1:
            self.notifier.edit(where["chat_id"], where["message_id"], "🚫 Flujo cancelado, no he creado nada", html=False)
            return True
        from . import flujo
        origin = tuple(rec.get("origin") or ()) or None
        fid = flujo.flow_id(rec.get("text_hash") or "", *(origin or ("", "")), rec.get("slot") or 0)
        results = flujo.create_all(rec.get("cards") or [], fid, self.lanes, self.hermes_for, origin)
        by_n = {c["n"]: c for c in rec.get("cards") or []}
        lines = []
        for n, tid, info in results:
            c = by_n[n]
            lines.append(f"{n}. {c['titulo']} → " + (f"{tid} ({'a tu nombre, [CONFIRMAR]' if c['confirmar'] else c['carril']})"
                                                     if tid else f"⚠️ {info}"))
        failed = any(tid is None for _, tid, _ in results)
        head = "⚠️ Flujo a medias; pulsa [Crear todo] otra vez para completar (no duplica):" if failed \
            else f"✅ Flujo creado · {len(results)} tarjeta(s):"
        self.notifier.edit(where["chat_id"], where["message_id"], head + "\n" + "\n".join(lines), html=False)
        return not failed

    # --- /estado --------------------------------------------------------------------------------------

    def estado_peticiones(self, where, proyecto: str | None) -> None:
        """Vista por petición de /estado (A2): una línea por req, atascadas primero; «Ver más» abre la cadena."""
        from .formato_aviso import REQ_MAS, estado_texto, ordenar, paginas
        items = self.peticion_items(proyecto)
        text = estado_texto(items, self._now(), proyecto)
        for page in paginas(text):
            self._send(where, page, html=False)
        for req, est in ordenar(items)[:5]:  # «Ver más» de las 5 primeras (las que piden atención)
            if est.estado != "hecha":
                markup = self.desk.store.issue({"task_id": "estado", "board": "", "lane": "", "req": req["id"]},
                                               [[{"text": f"💬 Ver más · {req['id']}", "action": REQ_MAS}]])[1]
                self._send(where, f"{req['id']} · {truncate(req['titulo'], 50)}", html=False, markup=markup)

    def req_choice(self, rec: dict, button: dict, where: dict) -> bool:
        """Botón «Ver más» de /estado: manda la cadena entera de la petición (paginada, sin recortar)."""
        from .formato_aviso import cadena_texto, paginas
        from .peticiones import derivar
        store, ahora = self.registro_peticiones(), self._now()
        req = store.get(rec.get("req")) if store else None
        if not req:
            return False
        shows = {t: self._show_card(t) for t in req["tarjetas"]}
        dest = (str(where["chat_id"]), str(where.get("thread_id") or "0"))
        for page in paginas(cadena_texto(req, derivar(req, shows, ahora), shows, ahora)):
            self._send(dest, page, html=False)
        return True

    # --- /ajustes: configuración editable del hub (ajustes_hub.py; regla «nada hardcodeado») ----------------

    def _quien(self) -> str:
        """Nombre de quien actúa en este hilo (operador de people.yaml) o Oscar."""
        return getattr(getattr(self.desk, "_tl", None), "name", None) or "Oscar"

    def _ajustes_screen(self, where, screen: tuple, *, key: str | None = None, grupo: str | None = None) -> None:
        text, items, spec = screen
        markup = self.desk.store.issue({"task_id": "ajustes", "board": "", "lane": "",
                                        "ajustes": {"items": items, "key": key, "grupo": grupo}}, spec)[1]
        self._send(where, text, html=False, markup=markup)

    def cmd_ajustes(self, where, args: str, brand: str | None) -> None:
        """/ajustes -> botones; /ajustes <clave> <valor> -> modo avanzado; /ajustes historial [clave]."""
        from . import ajustes_hub as ah, ajustes_ui as ui
        store, parts = ah.almacen(), args.split(None, 1)
        if not parts:
            return self._ajustes_screen(where, ui.menu())
        if parts[0].lower() == "historial":
            return self._send(where, ui.historial(store, parts[1].strip() if len(parts) > 1 else None), html=False)
        if parts[0] not in ah.SETTINGS:
            return self._send(where, f"No existe el ajuste «{truncate(parts[0], 40)}». Usa /ajustes y elige con los botones.",
                              html=False)
        if len(parts) == 1:
            return self._ajustes_screen(where, ui.detalle(store, parts[0]), key=parts[0], grupo=ah.SETTINGS[parts[0]].grupo)
        self._ajustes_save(where, parts[0], parts[1])

    def _ajustes_save(self, where, key: str, raw) -> None:
        from . import ajustes_hub as ah
        spec = ah.SETTINGS[key]
        try:
            aviso = ah.almacen().set(key, raw, self._quien())
        except ah.AjusteInvalido as exc:
            return self._send(where, f"❌ No lo he guardado: {exc}", html=False)
        text = f"✅ {spec.etiqueta}: {spec.describe(ah.almacen().valor(key))}" + (f"\n{aviso}" if aviso else "")
        self._send(where, text, html=False)

    def ajustes_choice(self, rec: dict, button: dict, where: dict) -> bool:
        """Botones de /ajustes: grupo -> ajuste -> valor (se guarda al pulsar) o «Valor por defecto»."""
        from . import ajustes_hub as ah, ajustes_ui as ui
        ctx = rec.get("ajustes") or {}
        items, idx = ctx.get("items") or [], button.get("index")
        if not isinstance(idx, int) or not 0 <= idx < len(items):
            return False
        item, store, key, grupo = items[idx], ah.almacen(), ctx.get("key"), ctx.get("grupo")
        dest = (str(where["chat_id"]), str(where.get("thread_id") or "0"))
        if key is None and grupo is None:  # menú de grupos
            if item not in (*ah.GRUPOS, ah.AVANZADO):
                return False
            self._ajustes_screen(dest, ui.grupo(store, item), grupo=item)
        elif key is None:  # lista de un grupo: ajuste elegido o «Grupos»
            if item == ui.ATRAS:
                self._ajustes_screen(dest, ui.menu())
            elif item in ah.SETTINGS:
                self._ajustes_screen(dest, ui.detalle(store, item), key=item, grupo=grupo)
            else:
                return False
        elif key not in ah.SETTINGS:
            return False
        elif item == ui.ATRAS:
            self._ajustes_screen(dest, ui.grupo(store, grupo), grupo=grupo)
        elif item == ui.RESET:
            spec = ah.SETTINGS[key]
            hecho = store.reset(key, self._quien())
            self._send(dest, f"↩️ {spec.etiqueta}: vuelve al valor por defecto ({spec.describe(spec.default)})." if hecho
                       else f"{spec.etiqueta} ya estaba en su valor por defecto.", html=False)
        else:
            self._ajustes_save(dest, key, item)
        return True

    def cmd_estado(self, where, args: str, brand: str | None) -> None:
        from .peticiones import PROYECTOS
        proyecto = next((p for p in PROYECTOS if args.strip().lower() == p.lower()), None)
        if (self.registro_peticiones() is not None and not args.strip()) or proyecto:
            self.estado_peticiones(where, proyecto)
            if proyecto and not BRAND_ARGS.get(proyecto.lower()):
                return  # Hub, Signal… no son marcas de la vista antigua
        if args:
            brand = BRAND_ARGS.get(args.split()[0].lower())
            if not brand:
                self._send(where, "Marca desconocida: usa /estado migrateam, /estado pildora o /estado nextjobs",
                           html=False)
                return
        self._send(where, self.estado_text(brand))

    def undeployed(self, brand: str | None) -> list[str] | None:
        """Líneas de lo fusionado y sin desplegar (estado del integrador) y develop↔master de MigraTeam. None = sin
        integrador activo (no se sabe)."""
        integ = getattr(self.desk, "integrator", None)
        if not integ:
            return None
        out = []
        for name, policy in integ.settings.policies.items():
            lane = self.lanes.get(name)
            if lane is None or policy.deploy == "none" or (brand and BRANDS.get(name) != brand):
                continue
            for tid, st in integ._merged_states(lane, ""):
                out.append(f"{name} · PR #{st.get('pr', '?')} ({tid}) fusionado, sin desplegar")
        mig = self.lanes.get("claude-migrateam")
        if mig and (not brand or brand == "MigraTeam"):
            from . import health
            drift = health.migrateam_drift(mig.repo)
            if drift != health.NA:
                out.append("MigraTeam · " + drift)
        return out

    def estado_text(self, brand: str | None) -> str:
        e = html.escape
        out = ["📊 <b>Estado</b>" + (f" · {e(brand)}" if brand else "")]
        running = []
        for name, lane in self.lanes.items():
            if lane.kind not in ("implement", "ops") or (brand and BRANDS.get(name) != brand):
                continue
            for t in self.hermes_for(lane.board).list_status(name, "running"):
                running.append(f"▶️ {self._task_ref(lane, t, True)} · {e(name)}")
        out.append(f"\n<b>En curso ({len(running)})</b>")
        out += running[:10] or ["nada en curso"]
        if len(running) > 10:
            out.append(f"  +{len(running) - 10} más en /tareas")
        stuck = self.stuck_tasks(brand)
        waiting = [(len(self.pending_decisions(brand)), "decisiones de agentes", "/decisiones"),
                   (len(stuck), "atascadas", "/decisiones"),
                   (len(self.ready_to_approve(brand)), "listas para integrar", "/aprobar"),
                   (len(self.decision_cards(brand)), "tarjetas de decisión", "/decisiones")]
        out.append("\n<b>Esperan a Oscar</b>")
        out += [f"❓ {n} {label} · {cmd}" for n, label, cmd in waiting if n] or ["nada 🎉"]
        out += [f"🧊 {self._task_ref(p.lane, p.task, True)} · {e(p.lane.name)} · 🔄 Reintentar en /decisiones"
                for p in stuck[:5]]
        if len(stuck) > 5:
            out.append(f"  +{len(stuck) - 5} atascadas más en /decisiones")
        pend = self.undeployed(brand)
        out.append("\n<b>Pendiente de desplegar</b>")
        out += [e(l) for l in pend] if pend else (["nada"] if pend is not None else ["sin integrador activo: no lo sé"])
        return "\n".join(out)[:TEXT_MAX]

    # --- /falta ---------------------------------------------------------------------------------------

    def falta_data(self, brand: str | None) -> tuple[list[str], list[dict]] | None:
        """(pasos HTML en orden, acciones con botón). Orden: fusionar → ramas fuera → SQL → variables → desplegar →
        PRs sueltos. None = sin integrador activo (no se sabe)."""
        from .batch import OPEN_STATUSES, brand_of
        from .falta import env_pending, plain_reason
        from .integrator import (DEPLOY_BUTTON, INT_BATCH_MERGE, INT_MIGRATION_APPLIED, MIGRATION_KINDS,
                                 pending_migration)
        integ = getattr(self.desk, "integrator", None)
        if not integ or not integ.settings.enabled:
            return None
        e = html.escape
        merge, excl, sql, env, deploy, loose, targets = [], [], [], [], [], [], []
        env_tids: dict[str, tuple] = {}  # tid -> (lane, etiqueta): tarjetas cuyo cuerpo puede pedir variables
        for name, policy in integ.settings.policies.items():
            lane = self.lanes.get(name)
            if lane is None or (brand and BRANDS.get(name) != brand):
                continue
            who = "" if brand else f" · {name.removeprefix('claude-')}"
            if policy.batch:
                for b in integ.batches.all():
                    if b.get("lane") != name or b.get("status") not in OPEN_STATUSES:
                        continue
                    n = len(b.get("members") or ())
                    stale = b.get("status") == "stale"
                    merge.append(f"🔀 Fusionar el lote PR #{b.get('pr')} ({n} rama{'s' if n != 1 else ''}){e(who)}"
                                 + (f" — está desfasado: /lote {brand_of(policy)} lo monta de nuevo" if stale else ""))
                    if not stale and b.get("rec"):
                        targets.append({"action": INT_BATCH_MERGE, "rec": b["rec"], "text": f"🔀 Fusionar lote #{b.get('pr')}"})
                    if b.get("migration") in MIGRATION_KINDS:
                        sql.append(f"🗄 Aplicar el SQL/migración ({e(b['migration'])}) del lote #{b.get('pr')}"
                                   " cuando lo fusiones y antes de desplegar")
                    for x in b.get("excluded") or ():
                        excl.append(f"⏭ #{x.get('pr')} quedó fuera del lote: {e(plain_reason(x.get('reason')))}")
                    for m in b.get("members") or ():
                        env_tids[m["tid"]] = (lane, f"lote #{b.get('pr')}")
                waiting = len(integ.batches._candidates(lane))
                if waiting:
                    merge.append(f"⏳ {waiting} rama{'s' if waiting != 1 else ''} aprobada{'s' if waiting != 1 else ''} "
                                 f"esperando lote{e(who)} → /lote {brand_of(policy)}")
            for tid, st in integ._merged_states(lane, ""):
                mig, pr = pending_migration(st), st.get("pr", "?")
                rec = {"kind": "integrate", "task_id": tid, "board": lane.board, "lane": name,
                       "title": st.get("title") or "", "pr_number": st.get("pr"), "pr_url": st.get("pr_url") or "",
                       "merge_sha": st.get("merge_sha"), "migration": mig}
                env_tids[tid] = (lane, f"PR #{pr}")
                if mig in MIGRATION_KINDS:
                    sql.append(f"🗄 Aplicar el SQL/migración ({e(mig)}) de PR #{pr} ({e(tid)}){e(who)} y marcarla")
                    targets.append({"action": INT_MIGRATION_APPLIED, "rec": rec, "text": f"✅ Migración aplicada #{pr}"})
                elif policy.deploy in DEPLOY_BUTTON:
                    deploy.append(f"🚀 Desplegar PR #{pr} ({e(tid)}){e(who)}: fusionado y sin desplegar")
                    targets.append({"action": DEPLOY_ASK, "rec": {**rec, "migration": None}, "text": f"🚀 Desplegar #{pr}"})
                else:
                    deploy.append(f"📦 PR #{pr} ({e(tid)}){e(who)} fusionado y sin aplicar")
            for path in sorted(integ.state_dir.glob("t_*.json")):
                st = integ._state(path.stem)
                if st.get("lane") == name and st.get("status") == "offered" and not st.get("batch"):
                    loose.append(f"🔀 PR #{st.get('pr', '?')} ({e(path.stem)}){e(who)} abierto, esperando tu "
                                 "[🔀 Fusionar] en su ficha" + (" · PR del hub" if name == "claude-hub" else ""))
        for tid, (lane, label) in env_tids.items():
            try:
                body = ((self._shows.show(self.hermes_for(lane.board), tid) or {}).get("task") or {}).get("body")
            except Exception as exc:
                log.warning("/falta: no se pudo leer %s: %s", tid, exc)
                continue
            vars_ = env_pending(body)
            if vars_:
                env.append("🔑 Poner en Railway (" + e(label) + "): "
                           + ", ".join(f"<code>{e(n)}</code>" + (f"=<code>{e(v)}</code>" if v else "") for n, v in vars_))
        return merge + excl + sql + env + deploy + loose, targets[:6]

    def falta_text(self, brand: str | None) -> tuple[str, dict | None]:
        """(mensaje HTML, teclado). Un solo mensaje; botones solo donde la acción ya existe."""
        from .falta import falta_message
        data = self.falta_data(brand)
        if data is None:
            return "🧭 <b>Qué falta</b>\nEl integrador está apagado: no sé qué PRs ni lotes hay.", None
        steps, targets = data
        text = falta_message(brand, steps)[:TEXT_MAX]
        if not targets:
            return text, None
        rec = {"task_id": "falta", "board": "", "lane": "", "kind": "falta", "targets": targets}
        spec = [[{"text": t["text"], "action": FALTA, "index": i}] for i, t in enumerate(targets)]
        return text, self.desk.store.issue(rec, spec)[1]

    def cmd_falta(self, where, args: str, brand: str | None) -> None:
        if args:
            brand = BRAND_ARGS.get(args.split()[0].lower())
            if not brand:
                self._send(where, "Marca desconocida: usa /falta migrateam, /falta pildora o /falta nextjobs",
                           html=False)
                return
        text, markup = self.falta_text(brand)
        self._send(where, text, markup=markup)

    def falta_choice(self, rec: dict, button: dict, where: dict, desk) -> bool:
        """Botón de /falta: ejecuta la acción que ya existía (fusionar lote, migración aplicada, desplegar)."""
        idx, targets = button.get("index"), rec.get("targets") or []
        if not isinstance(idx, int) or not 0 <= idx < len(targets):
            return False
        t = targets[idx]
        if t["action"] == DEPLOY_ASK:
            return self.deploy_step(DEPLOY_ASK, t["rec"], where, desk)
        return bool(desk.integrator) and desk.integrator.on_button(t["action"], t["rec"], where, desk)

    # --- texto libre ----------------------------------------------------------------------------------

    @property
    def can_answer(self) -> bool:
        return self._asker is not None

    def free_text(self, msg: dict) -> bool:
        """Pregunta suelta del dueño en su DM (ni comando, ni /sesion abierta): responde con el estado real. Solo
        informa: nunca ejecuta nada. False = no actúa (sin asker configurado: queda la ayuda de siempre)."""
        text = (msg.get("text") or "").strip()
        if not self._asker or not text:
            return False
        where = self._where(msg)
        if not self._ask_lock.acquire(blocking=False):
            self._send(where, "Sigo con tu pregunta anterior; dame un momento.", html=False)
            return True
        try:
            try:
                plain = lambda s: html.unescape(re.sub(r"<[^>]+>", "", s))
                brand = scope_brand(where[0], where[1], self.lanes, self.generic_origins)
                context = plain(self.falta_text(brand)[0]) + "\n\n" + plain(self.estado_text(brand))
                answer = self._asker(text, context)
            except Exception as exc:
                log.warning("texto libre: no pude responder: %s", type(exc).__name__)
                answer = None
            self._send(where, f"💬 {answer}\n\n(Solo información: para actuar usa /falta o los botones.)" if answer
                       else "💬 No pude generar la explicación ahora; prueba con /falta o /estado.", html=False)
        finally:
            self._ask_lock.release()
        return True

    # --- /salud ---------------------------------------------------------------------------------------

    def cmd_salud(self, where, args: str, brand: str | None) -> None:
        self._send(where, self._health() if self._health is not None else self.health_text())

    def cmd_ram(self, where, args: str, brand: str | None) -> None:
        """RAM bajo demanda; el monitor manda el mismo informe al DM cada 4 h (ya no hay aviso continuo)."""
        from . import health
        self._send(where, health.ram_report(), html=False)

    def health_text(self) -> str:
        """Todo local (health.py): .state, `hermes kanban list`, ficheros del monitor y git sin fetch."""
        from . import ciego, drain, health
        from .runner import STATE_DIR, pid_alive
        from .status import lane_rows, runner_alive, runner_line
        lock = drain.LOCK_FILE
        mig = self.lanes.get(health.MIGRATEAM_LANE)
        now = self._now()
        return health.build(
            runner=lambda: runner_line(lock, pid_alive, drain.active(), drain.BUSY_FILE),
            rows=lambda: lane_rows(self.lanes, hermes_for=self.hermes_for, state_dir=STATE_DIR, pid_alive=pid_alive,
                                   runner_is_alive=runner_alive(lock, pid_alive)),
            gateway=lambda: health.gateway_line(health.HERMES_GATEWAY_STATE, pid_alive, now),
            monitor=lambda: health.monitor_lines(health.MONITOR_STATE_DIR, now),
            drift=lambda: health.migrateam_drift(mig.repo if mig else ""),
            machine=health.machine_line,
            pending=lambda: (len(self.pending_decisions()), len(self.stuck_tasks()), len(self.decision_cards())),
            now=now, blind=lambda: ciego.resumen(ciego.FILE, now))

    def _agent_questions(self, where, pendings: list, now: float, scope: str, limit: int = MAX_CARDS) -> None:
        """Preguntas de los agentes (tareas de carril en needs_input): cabecera + una tarjeta con botones por tarea.
        `limit`: tarjetas que se muestran (el "aceptar todo" de la cabecera sigue cubriendo todas las pendientes)."""
        groups = group_pending(pendings)
        eligible = [p for p in pendings if fully_recommended(pending_of(p))]
        head = (f"❓ {_plural(len(pendings), 'decisión', 'decisiones')} · la más antigua hace "
                f"{hours_ago(pendings[0].since, now)} h{scope}")
        dups = sum(len(g) - 1 for g in groups if len(g) > 1)
        if dups:
            head += f"\n{_plural(dups, 'pregunta repetida agrupada', 'preguntas repetidas agrupadas')}"
        markup = None
        if eligible:
            head += f"\n{len(eligible)} con opción recomendada en todas sus preguntas"
            # Todas: al pulsar se aplican las elegibles y las demás se nombran como "sin tocar".
            markup = self.desk.accept_all_markup([self.desk.member(p.task, p.lane, p.questions,
                                                                   q_index=getattr(p, "q_index", 0))
                                                  for p in pendings])
        else:
            head += "\nNinguna tiene opción recomendada: decide en cada tarjeta"
        if limit >= MAX_CARDS and len(groups) > MAX_CARDS:
            head += f"\nMuestro {MAX_CARDS} tarjetas; el resto, en el panel"
        self._send(where, head, html=False, markup=markup)
        for group in groups[:limit]:
            if len(group) == 1:
                self._decision_card(where, group[0], now)
            else:
                self._group_card(where, group, now)

    def _decision_card(self, where, p, now: float) -> None:
        status = status_line("necesita tu decisión", _progress_label(p), f"hace {hours_ago(p.since, now)} h")
        text = render("needs_input", p.tid, p.task.get("title"), p.lane.name, status,
                      self._links_for(p.lane, p.tid), p.bullets, body=p.task.get("body"),
                      for_oscar=getattr(p, "for_oscar", None), tree=self._tree(p.lane.board, p.tid))
        markup = self.desk.markup("needs_input", task=p.task, lane=p.lane, questions=p.questions,
                                  summary=p.summary, for_oscar=getattr(p, "for_oscar", None),
                                  yes_no=getattr(p, "yes_no", True), q_index=getattr(p, "q_index", 0))
        self._mirror(p.tid, self._send(where, text, markup=markup), markup)

    def _group_card(self, where, group: list, now: float) -> None:
        e = html.escape
        step = _progress_label(group[0])
        lines = [f"❓ Misma pregunta en {len(group)} tareas · la respuesta se aplica a todas"
                 + (f" · {step}" if step else "")]
        for p in group:
            card = card_url(self.base_url, p.lane.board, p.tid)
            tid = f'<a href="{e(card, quote=True)}">{e(p.tid)}</a>' if card else e(p.tid)
            lines.append(f"• {tid} · {e(BRANDS.get(p.lane.name) or p.lane.name)} · "
                         f"{e(truncate(p.task.get('title'), 48))} · hace {hours_ago(p.since, now)} h")
        lines += [e(b) for b in group[0].bullets]
        text = "\n".join(lines)
        if len(text) > TEXT_MAX:
            text = text[:TEXT_MAX]
        yes_no = all(getattr(p, "yes_no", True) for p in group)
        q_index = getattr(group[0], "q_index", 0)
        members = [self.desk.member(p.task, p.lane, p.questions, yes_no, q_index=q_index) for p in group]
        markup = self.desk.group_markup(members, group[0].questions, yes_no, q_index=q_index)
        self._send(where, text, markup=markup)

    # --- /aprobar -------------------------------------------------------------------------------------

    def cmd_aprobar(self, where, args: str, brand: str | None) -> None:
        ready = self.ready_to_approve(brand)
        scope = f" · {brand}" if brand else ""
        if not ready:
            self._send(where, f"Nada listo para integrar{scope}", html=False)
            return
        # Orden recomendado: cada tarea después de las que necesita (kanban link padre -> hijo), luego antigüedad.
        waits = {}
        for p in ready:
            try:
                waits[p.tid] = pending_parents(self.hermes_for(p.lane.board), p.tid)
            except Exception as exc:
                log.warning("/aprobar: dependencias de %s no leídas: %s", p.tid, exc)
                waits[p.tid] = []
        ready = dependency_order(ready, lambda p: p.tid, lambda p: [w["id"] for w in waits[p.tid]])
        head = f"✅ {_plural(len(ready), 'tarea lista', 'tareas listas')} para integrar{scope}"
        if any(waits.values()):
            head += "\nOrden recomendado: las marcadas con ⏸ necesitan antes el código de otra tarea."
        self._send(where, head, html=False)
        for p in ready[:MAX_CARDS]:
            text = render("done", p.tid, p.task.get("title"), p.lane.name, p.status,
                          self._links_for(p.lane, p.tid, p.changed_files), body=p.task.get("body"),
                          for_oscar=p.for_oscar,
                          tree=self._tree(p.lane.board, p.tid, with_waiting=False))  # ⏸ ya va debajo
            if waits[p.tid]:
                text += "\n" + html.escape(waiting_line(waits[p.tid])) + " · puedes aprobarla, pero no se fusionará antes"
            markup = self.desk.markup("done", task=p.task, lane=p.lane, summary=p.summary,
                                      changed_files=p.changed_files, for_oscar=p.for_oscar)
            self._mirror(p.tid, self._send(where, text, markup=markup), markup)

    # --- /tareas --------------------------------------------------------------------------------------

    def cmd_lote(self, where, args: str, brand: str | None) -> None:
        """/lote <marca>: monta ya el lote de las ramas aprobadas (lo mismo que hace solo a la hora del lote)."""
        batches = getattr(getattr(self.desk, "integrator", None), "batches", None)
        brands = batches.brands() if batches else []
        if not brands:
            self._send(where, "El lote diario no está activo (integrador apagado o ningún carril con batch).",
                       html=False)
            return
        arg = args.strip().lower() or (brand or "").lower() or (brands[0] if len(brands) == 1 else "")
        found = batches.resolve(arg) if arg else None
        if not found:
            self._send(where, "Dime de qué proyecto: /lote " + "|".join(brands), html=False)
            return
        lane, policy = found
        self._send(where, f"Montando el lote de {arg}… pasa los gates y los tests, puede tardar unos minutos.",
                   html=False)

        def run() -> None:
            try:
                text = batches.assemble(lane, policy, manual=True)
            except Exception as exc:
                log.warning("/lote %s falló: %s", arg, exc)
                text = "No pude montar el lote; detalle en el log."
            self._send(where, text, html=False)

        self._spawn(run)

    def cmd_promover(self, where, args: str, brand: str | None) -> None:
        """/promover migrateam: prepara el PR develop → master y pide la doble confirmación (promote.py)."""
        promoter = getattr(self.desk, "promoter", None)
        if not promoter:
            self._send(where, "La promoción a producción no está activa (integrador apagado o sin sección promote).",
                       html=False)
            return
        key = (args.strip().split() or [""])[0].lower()
        if key not in promoter.configs:
            self._send(where, "Dime qué promover: /promover " + "|".join(promoter.configs), html=False)
            return
        self._send(where, f"Preparando la promoción de {key}… compruebo el PR, los checks y producción (solo lectura).",
                   html=False)
        dest = {"chat_id": where[0], "thread_id": where[1]}
        self._spawn(lambda: promoter.start(key, dest))

    # --- /desplegar -----------------------------------------------------------------------

    def _deploy_target(self, arg: str):
        """(carril, política) con deploy `railway_up` cuyo nombre, marca o palabra clave es `arg`; None si no hay."""
        from .integrator import DEPLOY_BUTTON
        integ = getattr(self.desk, "integrator", None)
        if not integ or not integ.settings.enabled:
            return None
        want = re.sub(r"[^a-z0-9 -]+", "", (arg or "").lower()).strip()
        for name, policy in integ.settings.policies.items():
            lane = self.lanes.get(name)
            if lane is not None and policy.deploy in DEPLOY_BUTTON and want and want in (
                    name, name.removeprefix("claude-"), *LANE_HINTS.get(name, ())):
                return lane, policy
        return None

    def cmd_desplegar(self, where, args: str, brand: str | None) -> None:
        """/desplegar <proyecto>: ficha de cada fusión sin desplegar con [🚀 Desplegar]; hace falta un 2.º toque."""
        from .integrator import DEPLOY_BUTTON, pending_migration
        integ = getattr(self.desk, "integrator", None)
        arg = args.strip()
        if not integ or not integ.settings.enabled:
            self._send(where, "El integrador está apagado: no hay nada que desplegar desde aquí.", html=False)
            return
        found = self._deploy_target(arg)
        if not found:
            names = sorted(n.removeprefix("claude-") for n, p in integ.settings.policies.items()
                           if p.deploy in DEPLOY_BUTTON)
            self._send(where, "Dime el proyecto: /desplegar " + ("|".join(names) or "(ninguno despliega con botón)")
                       + ". MigraTeam despliega staging al fusionar y producción va por /promover.", html=False)
            return
        lane, policy = found
        merged = integ._merged_states(lane, "")
        if not merged:
            self._send(where, f"Nada que desplegar en {lane.name}: no hay fusiones pendientes.", html=False)
            return
        for tid, st in merged[:5]:
            if pending_migration(st):
                self._send(where, f"⏸ {tid} · PR #{st.get('pr', '?')} lleva una migración pendiente: no se despliega "
                                  "desde aquí (aplícala a mano y márcala en su ficha).", html=False)
                continue
            rec = {"kind": "integrate", "task_id": tid, "board": lane.board, "lane": lane.name,
                   "title": st.get("title") or "", "pr_number": st.get("pr"), "pr_url": st.get("pr_url") or "",
                   "merge_sha": st.get("merge_sha"), "migration": None}
            markup = self.desk.store.issue(rec, [[{"text": "🚀 Desplegar", "action": DEPLOY_ASK}]])[1]
            self._send(where, f"🚀 {tid} · {truncate(st.get('title'), 60)}\nPR #{st.get('pr', '?')} fusionado "
                              f"({(st.get('merge_sha') or '')[:7]}) y sin desplegar en {lane.name}.", html=False,
                       markup=markup)

    def deploy_step(self, action: str, rec: dict, where: dict, desk) -> bool:
        """1.er toque de /desplegar: edita la ficha con "¿Seguro?" y [✅ Sí, desplegar] (= int_deploy) / [✖ No]."""
        from .integrator import DEPLOY_BUTTON, INT_DEPLOY
        if action == DEPLOY_NO:
            self.notifier.edit(where["chat_id"], where["message_id"], "✖ No despliego nada.", html=False)
            return True
        policy = desk.integrator.settings.policies.get(rec.get("lane")) if desk.integrator else None
        if not policy or policy.deploy not in DEPLOY_BUTTON or not rec.get("merge_sha"):
            return False
        markup = desk.store.issue(rec, [[{"text": "✅ Sí, desplegar", "action": INT_DEPLOY}],
                                        [{"text": "✖ No", "action": DEPLOY_NO}]])[1]
        self.notifier.edit(where["chat_id"], where["message_id"],
                           f"⚠️ ¿Seguro? Esto despliega {rec.get('lane')} (PR #{rec.get('pr_number')}, "
                           f"{(rec.get('merge_sha') or '')[:7]}) con "
                           f"{'Dokploy' if policy.deploy == 'dokploy' else '`railway up`'}.", html=False,
                           reply_markup=markup)
        return True


    def _spawn(self, fn: Callable[[], None]) -> None:  # aparte para que los tests lo ejecuten en línea
        threading.Thread(target=fn, name="lote", daemon=True).start()

    def cmd_tareas(self, where, args: str, brand: str | None) -> None:
        if args:
            brand = BRAND_ARGS.get(args.split()[0].lower())
            if not brand:
                self._send(where, "Marca desconocida: usa /tareas migrateam, /tareas pildora o /tareas nextjobs",
                           html=False)
                return
        self._send(where, self.tasks_text(brand))

    def cmd_cola(self, where, args: str, brand: str | None) -> None:
        if args:
            brand = BRAND_ARGS.get(args.split()[0].lower())
            if not brand:
                self._send(where, "Marca desconocida: usa /cola migrateam, /cola pildora o /cola nextjobs", html=False)
                return
        self._send(where, self.queue_text(brand))

    def queue_text(self, brand: str | None) -> str:
        """Por carril: la tarea en curso (desde cuándo) y las siguientes en cola por prioridad actual."""
        e = html.escape
        out = ["📋 <b>Cola por carril</b>" + (f" · {e(brand)}" if brand else "")]
        for name, lane in self.lanes.items():
            if lane.kind not in ("implement", "ops") or (brand and BRANDS.get(name) != brand):
                continue
            h = self.hermes_for(lane.board)
            running = h.list_status(name, "running")
            ready = sorted(h.list_status(name, "ready"), key=lambda t: -(_prio(t) or 0))
            out.append(f"\n<b>{e(name)}</b>" + (f" · {e(BRANDS[name])}" if name in BRANDS else ""))
            for t in running:
                since = t.get("started_at")
                when = f" · desde hace {_elapsed(self._now() - float(since))}" if since else ""
                out.append(f"▶️ <code>{e(t['id'])}</code> · {e(truncate(t.get('title'), 50))}{when}")
            if not running:
                out.append("▶️ libre")
            for i, t in enumerate(ready[:COLA_PER_LANE], 1):
                out.append(f"{i}. <code>{e(t['id'])}</code> · {e(truncate(t.get('title'), 50))} · "
                           f"prioridad {_prio(t) or 0}")
            if len(ready) > COLA_PER_LANE:
                out.append(f"  +{len(ready) - COLA_PER_LANE} más en cola")
            elif not ready:
                out.append("sin cola")
        return "\n".join(out)[:TEXT_MAX]

    def cmd_prioridad(self, where, args: str, brand: str | None) -> None:
        usage = "Uso: /prioridad t_xxxxxxxx <0-99 | sube | baja>"
        parts = args.split()
        if len(parts) != 2 or not TASK_ID_RE.match(parts[0].lower()):
            self._send(where, usage, html=False)
            return
        tid, val = parts[0].lower(), parts[1].lower()
        if val not in ("sube", "baja") and not (val.isascii() and val.isdigit()):
            self._send(where, usage, html=False)
            return
        found = self._find(tid)
        if not found:
            self._send(where, f"No encuentro {tid} en los tableros de los carriles", html=False)
            return
        board, show = found
        task = show["task"]
        if task.get("status") in ("done", "archived"):
            self._send(where, f"{tid} ya está cerrada: no tiene cola que reordenar", html=False)
            return
        cur = _prio(task) or 0
        new = cur + PRIORITY_STEP if val == "sube" else cur - PRIORITY_STEP if val == "baja" else int(val)
        new = max(0, min(PRIORITY_MAX, new))
        if not self.hermes_for(board).set_priority(tid, new):
            self._send(where, f"No pude cambiar la prioridad de {tid}; detalle en el log", html=False)
            return
        self._send(where, f"✅ {tid} · prioridad {cur} → {new}", html=False)

    def tasks_text(self, brand: str | None, *, with_links: bool = True) -> str:
        e = html.escape
        out = ["🛣 <b>Tareas en curso y en cola</b>" + (f" · {e(brand)}" if brand else "")]
        sections = []
        for name, lane in self.lanes.items():
            if lane.kind not in ("implement", "ops") or (brand and BRANDS.get(name) != brand):
                continue
            h = self.hermes_for(lane.board)
            running, ready = h.list_status(name, "running"), h.list_status(name, "ready")
            items = [("▶️", lane, t) for t in running] + [("⏳", lane, t) for t in ready[:READY_PER_LANE]]
            sections.append((name, lane, items, len(ready)))
        # Una hija cuyo padre también sale en la lista va debajo de él (aunque sea de otro carril del tablero).
        shown = [it for _, _, items, _ in sections for it in items]
        parent_of = self._shown_parents(shown)
        kids: dict[str, list] = {}
        for it in shown:
            if it[2]["id"] in parent_of:
                kids.setdefault(parent_of[it[2]["id"]], []).append(it)

        def emit(it, depth: int = 0) -> None:
            emoji, lane, t = it
            out.append(("   " * depth + "↳ " if depth else "") + f"{emoji} " + self._task_ref(lane, t, with_links))
            for kid in kids.get(t["id"], ()):  # _shown_parents deja un bosque (sin ciclos): termina
                emit(kid, depth + 1)

        for name, lane, items, n_ready in sections:
            out.append(f"\n<b>{e(name)}</b>" + (f" · {e(BRANDS[name])}" if name in BRANDS else ""))
            if not items:
                out.append("libre, sin cola")
            top = [it for it in items if it[2]["id"] not in parent_of]
            for it in top:
                emit(it)
            if items and not top:
                out.append("(sus tareas van bajo su padre)")
            if n_ready > READY_PER_LANE:
                out.append(f"  +{n_ready - READY_PER_LANE} más en cola")
        text = "\n".join(out)
        if len(text) > TEXT_MAX and with_links:  # sin enlaces antes que cortar el HTML a medias
            return self.tasks_text(brand, with_links=False)
        return text[:TEXT_MAX]

    def _shown_parents(self, items: list) -> dict[str, str]:
        """{hija: padre} entre las tareas listadas (mismo tablero). Una lectura `show` cacheada por tarea; si no se
        puede leer, la tarea sale suelta. Un ciclo se rompe quitando un enlace (el resultado es siempre un bosque)."""
        by_id = {t["id"]: lane for _, lane, t in items}
        out: dict[str, str] = {}
        for _, lane, t in items:
            try:
                parents = self._shows.show(self.hermes_for(lane.board), t["id"]).get("parents") or []
            except Exception:
                continue
            for p in parents:
                pid = p if isinstance(p, str) else (p or {}).get("id")
                if pid in by_id and pid != t["id"] and by_id[pid].board == lane.board:
                    out[t["id"]] = pid
                    break
        for tid in list(out):
            seen, cur = {tid}, out.get(tid)
            while cur in out:
                if cur in seen:
                    out.pop(cur, None)  # el nodo donde se cierra el ciclo pierde su enlace
                    break
                seen.add(cur)
                cur = out[cur]
        return out

    def _task_ref(self, lane, t: dict, with_links: bool) -> str:
        e = html.escape
        url = card_url(self.base_url, lane.board, t["id"]) if with_links else None
        ref = f'<a href="{e(url, quote=True)}">{e(t["id"])}</a>' if url else f"<code>{e(t['id'])}</code>"
        return f"{ref} · {e(truncate(t.get('title'), 60))}"

    # --- /tarea t_xxx ---------------------------------------------------------------------------------

    def cmd_tarea(self, where, args: str, brand: str | None) -> None:
        tid = (args.split() or [""])[0].lower()
        if not TASK_ID_RE.match(tid):  # nunca pasar a la CLI algo que no sea un id (p. ej. "--board x")
            self._send(where, "Uso: /tarea t_xxxxxxxx", html=False)
            return
        found = self._find(tid)
        if not found:
            self._send(where, f"No encuentro {tid} en los tableros de los carriles", html=False)
            return
        board, show = found
        text, markup = self.task_card(board, show)
        tree = self.tree_text(board, show)
        if tree and len(text) + 1 + len(tree) <= TEXT_MAX:
            text += "\n" + tree
        sent = self._send(where, text, markup=markup)
        if markup:
            self._mirror(tid, sent, markup)

    def _find(self, tid: str) -> tuple[str, dict] | None:
        for board in dict.fromkeys(l.board for l in self.lanes.values() if l.board):
            try:
                show = self.hermes_for(board).show(tid)
            except Exception:
                continue
            if show and show.get("task"):
                return board, show
        return None

    def tree_text(self, board: str, show: dict, budget: int = TEXT_MAX // 2) -> str:
        """Árbol de /tarea en HTML: padre(s), la tarea con sus hermanas y, debajo, sus hijas; cada una con su estado y
        enlace al panel. "" si la tarea no tiene enlaces. Nunca corta el HTML: si no cabe en `budget`, quita líneas."""
        task = show.get("task") or {}
        tid = task.get("id") or ""
        try:
            fam = family(self.hermes_for(board), tid, show, cache=self._shows)
        except Exception:
            return ""
        if not (fam["parents"] or fam["children"]):
            return ""
        e = html.escape

        def ref(b: dict, *, me: bool = False) -> str:
            url = card_url(self.base_url, board, b["id"])
            idt = f'<a href="{e(url, quote=True)}">{e(b["id"])}</a>' if url else f"<code>{e(b['id'])}</code>"
            label = STATUS_TEXT.get(b["status"], (b["status"], b["status"]))[1]
            title = e(truncate(b.get("title"), 48))
            return (f"{idt} · <b>{title}</b>" if me else f"{idt} · {title}") + \
                f" · {child_bucket(b['status'])} {e(label)}" + (" (esta)" if me else "")

        def part(key: str, cap: int, fmt, indent: str) -> list[str]:
            shown = fam[key][:cap]
            n = fam[f"n_{key}"] - len(shown)
            return [fmt(b) for b in shown] + ([f"{indent}+{n} más en el panel"] if n > 0 else [])

        me = {"id": tid, "title": task.get("title") or "", "status": str(task.get("status") or "?")}
        pad = "  " if fam["parents"] else ""
        for cap in (10, 5, 3, 1, 0):  # menos filas por lista hasta que quepa; nunca se corta el HTML
            lines = ["🌳 <b>Árbol</b>"]
            lines += part("parents", max(cap, 1), lambda b: f"🔗 Padre: {ref(b)}", "")
            lines += part("siblings", cap, lambda b: f"{pad}• {ref(b)}", pad)
            lines.append(f"{pad}👉 {ref(me, me=True)}")
            lines += part("children", cap, lambda b: f"{pad}   ↳ {ref(b)}", pad + "   ")
            text = "\n".join(lines)
            if len(text) <= budget:
                return text
        return ""

    def task_card(self, board: str, show: dict) -> tuple[str, dict | None]:
        """Ficha con el render de los avisos y los botones de su estado (needs_input, bloqueo transitorio, lista)."""
        task = show["task"]
        tid, status = task["id"], task.get("status")
        lane = self.lanes.get(task.get("assignee"))
        lane = lane if lane is not None and lane.kind == "implement" else None
        if lane and status in ("blocked", "done"):
            plan = self.renotifier()._plan_blocked if status == "blocked" else self.renotifier()._plan_done
            p = plan(lane, show)
            if p:
                text = render(p.state, tid, task.get("title"), lane.name, p.status,
                              self._links_for(lane, tid, p.changed_files), p.bullets, body=task.get("body"),
                              for_oscar=p.for_oscar)
                markup = self.desk.markup(p.state, task=task, lane=lane, block_kind=p.block_kind,
                                          questions=p.questions, summary=p.summary, changed_files=p.changed_files,
                                          for_oscar=p.for_oscar, yes_no=p.yes_no, q_index=p.q_index)
                return text, markup
        state, label = STATUS_TEXT.get(status, (status or "?", status or "?"))
        lane_name = lane.name if lane else (task.get("assignee") or board)
        links = self._links_for(lane, tid) if lane else (
            [("🗂 Tarjeta", url)] if (url := card_url(self.base_url, board, tid)) else [])
        return render(state, tid, task.get("title"), lane_name, label, links, body=task.get("body")), None
