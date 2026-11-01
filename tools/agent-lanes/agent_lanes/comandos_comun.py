"""Utilidades comunes de los comandos del bot: constantes, tablas de comandos y funciones sin estado.

Salen de commands.py sin cambiar comportamiento; commands.py las reexporta.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

from . import ayuda as _ayuda
from .notices import (BRANDS, normalize_questions)

log = logging.getLogger("agent_lanes")
URGENT_EVERY = 120  # s mínimos entre pasadas del aviso inmediato de bloqueos de prioridad alta

COMMANDS = (
    ("ajustes", "Ajustes del hub con botones"),
    ("hoy", "Resumen del día (el de las 8:00) ahora"),
    ("integrador", "Estado del integrador, PR de sesiones interactivas y bloqueos de guard"),
    ("encolar", "Encola el PR de una sesión interactiva: /encolar <repo> <PR>"),
    ("decisiones", "Bandeja de decisiones pendientes con botones"),
    ("atencion", "Todo lo que espera a Oscar, con botones para resolverlo aquí"),
    ("aprobar", "Tareas listas para integrar"),
    ("tareas", "En curso y en cola por carril: /tareas [migrateam|pildora|nextjobs]"),
    ("cola", "Qué corre y qué sigue en cada carril, por prioridad: /cola [marca]"),
    ("prioridad", "Sube o baja una tarea en la cola: /prioridad t_xxx <0-99|sube|baja>"),
    ("tarea", "Ficha de una tarea con sus botones: /tarea t_xxx"),
    ("preparar", "Claude prepara las opciones de una decisión: /preparar t_xxx | todas"),
    ("hazlo", "Crea una tarea pequeña (carril según el texto): /hazlo <texto>"),
    ("pedir", "Pide algo con tipo (bug o feature) y preguntas: /pedir <texto>"),
    ("estado", "En curso, esperando a Oscar y pendiente de desplegar: /estado [marca]"),
    ("falta", "Qué frena una salida, con el siguiente paso arriba: /falta [marca]"),
    ("desplegar", "Despliega lo fusionado, con doble confirmación: /desplegar oscarhq"),
    ("promover", "Promover MigraTeam a producción (doble confirmación): /promover migrateam"),
    ("lote", "Montar ya el lote de un proyecto: /lote migrateam|oscarhq"),
    ("al_dia", "Poner al día ramas, ver lo listo e integrarlo (dev): /al_dia [proyecto|todos] [dev]"),
    ("aldia", "Alias de /al_dia"),
    ("salud", "Estado de runner, Hermes, servicios, MigraTeam y equipo"),
    ("ram", "Consumo de RAM ahora y qué procesos más gastan"),
    ("avisos", "Apaga o enciende los avisos de «listo para revisar»: /avisos off [1h|3h|mañana|siempre] · /avisos on"),
    ("choques", "Choques y solapes"),
    ("paradas", "Paradas más de 24 h"),
    ("revision", "Por revisar"),
    ("limpieza", "Propuestas de cierre"),
    ("silenciar", "Calla una alerta del monitor un tiempo: /silenciar <clave> <horas>"),
    ("sesion", "Correcciones agrupadas: /sesion [nombre] abre; fin | cancelar | estado"),
    ("flujo", "Una petición → hasta 8 tarjetas encadenadas: /flujo <texto>"),
    ("pendiente", "Lo tuyo en UN mensaje, con botones: preguntas, PR y tarjetas"),
    ("personal", "Saca tu backlog personal del kanban (confirmas con botones)"),
    ("agrupar", "Propone fusionar, encadenar o juntar tarjetas abiertas: /agrupar [proyecto|todo]"),
    ("workflow", "Trabajo largo: plan y coste, nada se crea hasta aprobar: /workflow <texto>"),
    ("pendientes", "Tus pendientes (pasos «tuyo» y «decidir») con botones"),
    ("epicas", "Vista de épicas (fuera de la cola de decisiones)"),
    ("retenidas", "Vista de tarjetas retenidas"),
    ("backlog", "Vista del backlog del bot del equipo"),
)
COMMAND_SCOPES = ("default", "all_private_chats", "all_group_chats")
HELP_ALIASES = ("start", "ayuda", "help")
HERMES_BOT = "oscarsaoBot"
# Comandos propios de Hermes en Gestión (25-10). No hay forma fiable de pedírselos a Hermes desde aquí, así que es una
# lista fija: si Hermes añade alguno, añádelo aquí (o pasa otra lista en `DecisionDesk.hermes_commands`).
HERMES_COMMANDS = frozenset((
    "new", "reset", "status", "approve", "deny", "stop", "retry", "undo", "model", "usage", "compress", "title",
    "background", "sethome", "personality", "resume", "branch", "yolo", "verbose", "reasoning", "queue", "voice",
    "update", "platforms", "commands", "profile", "insights", "skills", "cron", "reload", "debug", "restart",
    "history", "save", "rollback", "fast", "tools", "toolsets", "paste", "image", "stats", "context", "clear"))
# Comandos que tardan (leen el kanban): se contesta al momento «Un momento…» y se edita con el resultado.
SLOW_COMMANDS = ("tareas", "aprobar", "atencion")
MOMENTO = "⏳ Un momento…"
NOT_OWNER = ("Este comando es solo para operadores. Pide a Oscar o a María que lo haga, o escribe tu petición en "
             "texto libre y se la paso")
# Comandos sin marca clara o con efecto directo en repos/sesiones de Oscar: un operador acotado por marca no los usa.
SOLO_TODAS_LAS_MARCAS = ("integrador", "encolar", "preparar", "pedir", "sesion", "flujo", "workflow", "ajustes",
                         "pendiente", "personal", "agrupar", "choques", "paradas", "revision", "limpieza")
BRAND_ARGS = {"migrateam": "MigraTeam", "pildora": "Píldora", "píldora": "Píldora", "nextjobs": "NextJobs"}
TASK_ID_RE = re.compile(r"^t_[0-9a-f]{8}$")
STATUS_TEXT = {"running": ("running", "en curso"), "ready": ("ready", "en cola"), "review": ("review", "en review"),
               "done": ("done", "terminada"), "blocked": ("blocked", "bloqueada"), "todo": ("todo", "pendiente"),
               "triage": ("triage", "en triage"), "archived": ("archived", "archivada")}
TOP_DECISIONS = 5    # /decisiones muestra las 5 más importantes; el resto con [📋 Ver todas]
VIEW_STATUSES = ("triage", "todo", "ready", "running", "blocked", "review")  # vistas por paso: tarjetas no cerradas
MAX_CARDS = 20    # tarjetas por /decisiones o /aprobar (el resto se cuenta en la cabecera)
FICHAS_PAUSA = 1.0  # s entre fichas de /aprobar al mismo chat (TG-07: varias seguidas daban 429)
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
COLA_PER_LANE = 5  # siguientes tareas por carril en /cola
PRIORITY_MAX = 99
BUSCA_ESTADOS = ("running", "ready", "review", "blocked", "triage", "todo", "done")  # /tarea 3d4 mira estos estados
PRIORITY_STEP = 1# lo que suben o bajan «sube»/«baja» de /prioridad

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
    """setMyCommands (descripciones en español) en los tres scopes, cada uno con su lista (los grupos sin los solo-DM,
    /ayuda en todos). Devuelve los scopes registrados."""
    ok = []
    for scope in COMMAND_SCOPES:
        try:
            if notifier.set_my_commands(_ayuda.comandos_de_ambito(COMMANDS, scope), scope):
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


