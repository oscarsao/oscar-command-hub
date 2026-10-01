"""Comandos del bot de carriles (@pildora_carriles_bot): decidir desde UN sitio sin ir cambiando de grupo.

Deterministas (sin LLM) y solo para Oscar; a cualquier otro: "Solo Oscar". Funcionan en su DM y en los grupos,
también como /cmd@<bot>. Un /cmd@otro_bot (p. ej. el de Hermes) se ignora.

    /hoy                 el resumen de las 8:00 bajo demanda (brief/brief_diario.py, mismo render y datos)
    /decisiones          bandeja: cabecera con [✅ Aceptar todo lo recomendado] + una tarjeta por tarea en needs_input,
                         y "📌 Tus tarjetas de decisión" (asignadas a oscar con [DECISIÓN…]/[SEMANA…]/[IDEA…])
    /aprobar             tareas listas para integrar con [✅ Aprobar] [🔁 Pedir cambios] [🗄 Aparcar]
    /tareas [marca]      en curso y en cola por carril (migrateam | pildora | nextjobs), un solo mensaje
    /tarea t_xxx         ficha de la tarea con los botones de su estado
    /hazlo <texto>       crea una tarea pequeña (prioridad alta, tope 15 min): marca y carril salen del texto; con
                         duda, botones para elegir carril. El resultado vuelve a donde lo pediste
    /estado [marca]      en un solo mensaje: en curso, lo que espera a Oscar y lo pendiente de desplegar
    /desplegar <proyecto>  ficha de lo fusionado y sin desplegar con [🚀 Desplegar] → "¿Seguro?" → ✅ Sí (doble toque;
                         el despliegue es el mismo int_deploy del integrador, nunca otra vía)
    /promover migrateam  solo PREPARA la tarjeta de promoción develop→master (release/<fecha>) para el integrador;
                         no fusiona ni despliega nada
    /lote <proyecto>     monta ya el lote de las ramas aprobadas
    /salud               runner y carriles, gateway de Hermes, servicios y alertas (del monitor), develop↔master de
                         MigraTeam, RAM/CPU y decisiones pendientes; sin peticiones HTTP (agent_lanes/health.py)

/decisiones también lista las 🧊 atascadas (tareas de carril que Hermes pasó a triage por bloquearse dos veces por
lo mismo) con [🔄 Reintentar] (las devuelve a ready sin reescribirlas) y cuenta las tarjetas a nombre de Oscar sin
etiqueta de decisión.

Dentro de un tema de operaciones se filtran por la marca del tema (lanes.yaml: t230 MigraTeam, t231 Píldora); los
orígenes genéricos (t5 Operaciones · General, el DM) y el General del grupo ven todo.

Grupos con privacy mode: Telegram solo entrega a este bot los comandos con sufijo (/decisiones@<bot>) o los que
se eligen en el menú (setMyCommands). Sin sufijo, un /hoy puede ir a parar solo al bot de Hermes.
"""
from __future__ import annotations

import hashlib
import html
import logging
import re
import threading
import time
from typing import Callable
from urllib.parse import urlsplit

from .decisions import DEPLOY_ASK, DEPLOY_NO, HAZLO, OWNER_TELEGRAM_ID
from .deps import (ShowCache, child_bucket, dependency_order, family, pending_parents, tree_lines,
                   waiting_line)
from .notices import (BOARD_BRANDS, BRANDS, DECISION_CARD_STATUSES, OSCAR_ASSIGNEE, TEXT_MAX, card_url,
                      decision_since, hours_ago, is_decision_card, markup_token, normalize_questions, render,
                      status_line, truncate)

log = logging.getLogger("agent_lanes")

COMMANDS = (
    ("hoy", "Resumen del día (el de las 8:00) ahora"),
    ("decisiones", "Bandeja de decisiones pendientes con botones"),
    ("aprobar", "Tareas listas para integrar"),
    ("tareas", "En curso y en cola por carril: /tareas [migrateam|pildora|nextjobs]"),
    ("tarea", "Ficha de una tarea con sus botones: /tarea t_xxx"),
    ("hazlo", "Crea una tarea pequeña (carril según el texto): /hazlo <texto>"),
    ("estado", "En curso, esperando a Oscar y pendiente de desplegar: /estado [marca]"),
    ("desplegar", "Despliega lo fusionado de un proyecto, con doble confirmación: /desplegar oscarhq"),
    ("promover", "Prepara la promoción develop→master de MigraTeam para el integrador: /promover migrateam"),
    ("lote", "Montar ya el lote de un proyecto: /lote migrateam|oscarhq"),
    ("salud", "Estado de runner, Hermes, servicios, MigraTeam y equipo"),
)
COMMAND_SCOPES = ("default", "all_private_chats", "all_group_chats")
HELP_ALIASES = ("start", "ayuda", "help")
NOT_OWNER = "Solo Oscar"
BRAND_ARGS = {"migrateam": "MigraTeam", "pildora": "Píldora", "píldora": "Píldora", "nextjobs": "NextJobs"}
TASK_ID_RE = re.compile(r"^t_[0-9a-f]{8}$")
STATUS_TEXT = {"running": ("running", "en curso"), "ready": ("ready", "en cola"), "review": ("review", "en review"),
               "done": ("done", "terminada"), "blocked": ("blocked", "bloqueada"), "todo": ("todo", "pendiente"),
               "triage": ("triage", "en triage"), "archived": ("archived", "archivada")}
TOP_DECISIONS = 5    # /decisiones muestra las 5 más importantes; el resto con [📋 Ver todas]
MAX_CARDS = 20     # tarjetas por /decisiones o /aprobar (el resto se cuenta en la cabecera)
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
READY_PER_LANE = 8  # tareas en cola listadas por carril en /tareas

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
                 health: Callable[[], str] | None = None):
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
        self._health = health  # /salud: texto HTML ya montado (tests); None = health.build con lo real

    # --- entrada --------------------------------------------------------------------------------------

    def handle(self, msg: dict) -> bool:
        """True si el mensaje era un comando nuestro (respondido o rechazado)."""
        parsed = parse_command(msg.get("text"), self.bot_username)
        if not parsed:
            return False
        cmd, args = parsed
        where = self._where(msg)
        if str((msg.get("from") or {}).get("id")) != self.owner_id:
            self._send(where, NOT_OWNER, html=False)
            return True
        brand = scope_brand(where[0], where[1], self.lanes, self.generic_origins)
        try:
            if cmd in HELP_ALIASES:
                self._send(where, self.help_text(), html=False)
            else:
                getattr(self, f"cmd_{cmd}")(where, args, brand)
        except Exception as exc:
            log.warning("/%s falló: %s", cmd, exc)
            self._send(where, f"No pude completar /{cmd}; detalle en el log", html=False)
        return True

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
                + "\n/desplegar pide dos toques (🚀 y ✅ Sí); /promover solo prepara la tarjeta, el integrador fusiona.")

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
        found = [p for p in self.renotifier().collect(statuses=("blocked",)) if p.state == "needs_input"]
        return sorted(self._filter(found, brand), key=lambda p: (p.since or 0, p.tid))

    def stuck_tasks(self, brand: str | None = None) -> list:
        """🧊 Tareas de carril que Hermes pasó a triage por bloquearse dos veces por lo mismo (block_loop_detected):
        desde ahí `unblock` falla y nadie las ve. Más antigua primero; las cuentan también los recordatorios."""
        found = [p for p in self.renotifier().collect(statuses=("triage",)) if p.state == "stuck"]
        return sorted(self._filter(found, brand), key=lambda p: (p.since or 0, p.tid))

    def decision_cards(self, brand: str | None = None) -> list[dict]:
        """Tarjetas de decisión de Oscar (no son de carril): asignadas a `oscar`, en ready/blocked y con etiqueta
        [DECISIÓN…]/[SEMANA…]/[IDEA…] en el título, en los tableros de los carriles + default. Más antigua primero.
        [{board, task, since}]. Con `brand` (tema de una marca), solo las de su tablero (default = "Otros")."""
        return self.oscar_cards(brand)[0]

    def oscar_cards(self, brand: str | None = None) -> tuple[list[dict], int]:
        """(tarjetas de decisión, nº de tarjetas a nombre de Oscar SIN etiqueta de decisión) en una sola pasada por
        los tableros: mismos estados y tableros, sin duplicados por id."""
        boards = dict.fromkeys([l.board for l in self.lanes.values() if l.board] + list(EXTRA_BOARDS))
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
                    if not t.get("id") or t["id"] in found:
                        continue
                    if is_decision_card(t):
                        found[t["id"]] = {"board": board, "task": t, "since": decision_since(t)}
                        untagged.discard(t["id"])
                    else:
                        untagged.add(t["id"])
        return sorted(found.values(), key=lambda c: (c["since"] or 0, c["task"]["id"])), len(untagged)

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
        if total > TOP_DECISIONS and not show_all:
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
            self._send(where, self.cards_text(cards, now, scope, untagged=untagged))
        elif untagged:
            self._send(where, self.untagged_line(untagged))

    def _top_decisions(self, where, pendings: list, stuck: list, cards: list[dict], now: float, brand: str | None,
                       total: int) -> None:
        scope = f" · {brand}" if brand else ""
        n_q = min(len(group_pending(pendings)), TOP_DECISIONS)
        n_s = min(len(stuck), TOP_DECISIONS - n_q)
        n_c = TOP_DECISIONS - n_q - n_s
        if n_q:
            self._agent_questions(where, pendings, now, scope, limit=n_q)
        if n_s:
            self._stuck_section(where, stuck[:n_s], now, scope)
        for c in cards[:n_c]:
            t = c["task"]
            url = card_url(self.base_url, c["board"], t["id"])
            ref = f'<a href="{html.escape(url, quote=True)}">{html.escape(t["id"])}</a>' if url else f"<code>{html.escape(t['id'])}</code>"
            self._send(where, f"📌 {ref} · {html.escape(truncate(t.get('title'), CARD_TITLE_MAX))} · "
                              f"hace {hours_ago(c['since'], now)} h")
        self._send(where, f"➕ {total - TOP_DECISIONS} más · ver todas", html=False,
                   markup=self.desk.see_all_markup(brand))

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
                          body=p.task.get("body"), for_oscar=p.for_oscar)
            markup = self.desk.markup("stuck", task=p.task, lane=p.lane, block_kind=p.block_kind)
            self._mirror(p.tid, self._send(where, text, markup=markup), markup)

    def cards_text(self, cards: list[dict], now: float, scope: str = "", *, untagged: int = 0) -> str:
        """Un mensaje HTML con las tarjetas de decisión de Oscar (sin botones en v1) y, debajo, cuántas más tiene a su
        nombre sin etiqueta (con enlace al panel). Nunca corta el HTML: si no cabe, quita líneas y lo dice."""
        e = html.escape
        head = f"📌 <b>Tus tarjetas de decisión ({len(cards)})</b>{e(scope)}"
        tail = [self.untagged_line(untagged)] if untagged else []
        lines = []
        for c in cards:
            t = c["task"]
            url = card_url(self.base_url, c["board"], t["id"])
            ref = f'<a href="{e(url, quote=True)}">{e(t["id"])}</a>' if url else f"<code>{e(t['id'])}</code>"
            lines.append(f"• {ref} · {e(truncate(t.get('title'), CARD_TITLE_MAX))} · hace {hours_ago(c['since'], now)} h")
        shown = len(lines)
        while shown and len("\n".join([head, *lines[:shown], "  +000 más en el panel", *tail])) > TEXT_MAX:
            shown -= 1
        more = [f"  +{len(lines) - shown} más en el panel"] if shown < len(lines) else []
        return "\n".join([head, *lines[:shown], *more, *tail])

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

    # --- /estado --------------------------------------------------------------------------------------

    def cmd_estado(self, where, args: str, brand: str | None) -> None:
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
        waiting = [(len(self.pending_decisions(brand)), "decisiones de agentes", "/decisiones"),
                   (len(self.stuck_tasks(brand)), "atascadas", "/decisiones"),
                   (len(self.ready_to_approve(brand)), "listas para integrar", "/aprobar"),
                   (len(self.decision_cards(brand)), "tarjetas de decisión", "/decisiones")]
        out.append("\n<b>Esperan a Oscar</b>")
        out += [f"❓ {n} {label} · {cmd}" for n, label, cmd in waiting if n] or ["nada 🎉"]
        pend = self.undeployed(brand)
        out.append("\n<b>Pendiente de desplegar</b>")
        out += [e(l) for l in pend] if pend else (["nada"] if pend is not None else ["sin integrador activo: no lo sé"])
        return "\n".join(out)[:TEXT_MAX]

    # --- /salud ---------------------------------------------------------------------------------------

    def cmd_salud(self, where, args: str, brand: str | None) -> None:
        self._send(where, self._health() if self._health is not None else self.health_text())

    def health_text(self) -> str:
        """Todo local (health.py): .state, `hermes kanban list`, ficheros del monitor y git sin fetch."""
        from . import drain, health
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
            now=now)

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

    # --- /desplegar y /promover -----------------------------------------------------------------------

    def _deploy_target(self, arg: str):
        """(carril, política) con deploy `railway_up` cuyo nombre, marca o palabra clave es `arg`; None si no hay."""
        integ = getattr(self.desk, "integrator", None)
        if not integ or not integ.settings.enabled:
            return None
        want = re.sub(r"[^a-z0-9 -]+", "", (arg or "").lower()).strip()
        for name, policy in integ.settings.policies.items():
            lane = self.lanes.get(name)
            if lane is not None and policy.deploy == "railway_up" and want and want in (
                    name, name.removeprefix("claude-"), *LANE_HINTS.get(name, ())):
                return lane, policy
        return None

    def cmd_desplegar(self, where, args: str, brand: str | None) -> None:
        """/desplegar <proyecto>: ficha de cada fusión sin desplegar con [🚀 Desplegar]; hace falta un 2.º toque."""
        from .integrator import pending_migration
        integ = getattr(self.desk, "integrator", None)
        arg = args.strip()
        if not integ or not integ.settings.enabled:
            self._send(where, "El integrador está apagado: no hay nada que desplegar desde aquí.", html=False)
            return
        found = self._deploy_target(arg)
        if not found:
            names = sorted(n.removeprefix("claude-") for n, p in integ.settings.policies.items()
                           if p.deploy == "railway_up")
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
        from .integrator import INT_DEPLOY
        if action == DEPLOY_NO:
            self.notifier.edit(where["chat_id"], where["message_id"], "✖ No despliego nada.", html=False)
            return True
        policy = desk.integrator.settings.policies.get(rec.get("lane")) if desk.integrator else None
        if not policy or policy.deploy != "railway_up" or not rec.get("merge_sha"):
            return False
        markup = desk.store.issue(rec, [[{"text": "✅ Sí, desplegar", "action": INT_DEPLOY}],
                                        [{"text": "✖ No", "action": DEPLOY_NO}]])[1]
        self.notifier.edit(where["chat_id"], where["message_id"],
                           f"⚠️ ¿Seguro? Esto despliega {rec.get('lane')} (PR #{rec.get('pr_number')}, "
                           f"{(rec.get('merge_sha') or '')[:7]}) con `railway up`.", html=False, reply_markup=markup)
        return True

    def cmd_promover(self, where, args: str, brand: str | None) -> None:
        """/promover migrateam: crea la tarjeta de promoción develop→master. NO fusiona ni despliega: eso es del
        integrador y de Oscar con su ficha."""
        arg = args.strip().lower() or (brand or "").lower()
        lane = self.lanes.get("claude-migrateam")
        if arg not in ("migrateam", "claude-migrateam") or lane is None:
            self._send(where, "Solo MigraTeam tiene promoción: /promover migrateam", html=False)
            return
        from . import health
        drift = health.migrateam_drift(lane.repo)
        day = time.strftime("%Y%m%d", time.localtime(self._now()))
        chat, thread = where
        body = (f"## Objetivo\nPreparar la promoción de producción de MigraTeam: rama `release/{day}` desde develop con "
                f"la lista de PRs que suben a master y el resultado de los gates.\n\n## Encargo (/promover desde "
                f"Telegram)\n- Estado develop↔master al pedirlo: {drift}\n- SOLO preparar: no hagas merge ni push a "
                "master, ni despliegues. La fusión a master es del integrador con el OK de Oscar.\n- Si no puedes "
                "preparar la rama con tus permisos, deja la lista de PRs en `for_oscar` y devuelve needs_input.\n\n"
                f"Origen-Telegram: chat={chat} thread={thread}\n")
        tid = self.hermes_for(lane.board).create(f"Promoción MigraTeam release/{day}", body, lane.name,
                                                 priority=HAZLO_PRIORITY, key=f"promover-{day}", created_by="oscar")
        self._send(where, f"📝 Tarjeta de promoción creada: <code>{html.escape(tid)}</code> en {lane.name}. Solo "
                          "prepara la release; producción la fusiona el integrador con tu OK.\n" + html.escape(drift))

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
