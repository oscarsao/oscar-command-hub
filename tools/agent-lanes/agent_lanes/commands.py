"""Comandos del bot de carriles (@pildora_carriles_bot): decidir desde UN sitio sin ir cambiando de grupo.

Deterministas (sin LLM) y solo para Oscar; a cualquier otro: "Solo Oscar". Funcionan en su DM y en los grupos,
también como /cmd@<bot>. Un /cmd@otro_bot (p. ej. el de Hermes) se ignora.

    /hoy                 el resumen de las 8:00 bajo demanda (brief/brief_diario.py, mismo render y datos)
    /decisiones          bandeja: cabecera con [✅ Aceptar todo lo recomendado] + una tarjeta por tarea en needs_input,
                         y "📌 Tus tarjetas de decisión" (asignadas a oscar con [DECISIÓN…]/[SEMANA…]/[IDEA…])
    /aprobar             tareas listas para integrar con [✅ Aprobar] [🔁 Pedir cambios] [🗄 Aparcar]
    /tareas [marca]      en curso y en cola por carril (migrateam | pildora | nextjobs), un solo mensaje
    /tarea t_xxx         ficha de la tarea con los botones de su estado

Dentro de un tema de operaciones se filtran por la marca del tema (lanes.yaml: t230 MigraTeam, t231 Píldora); los
orígenes genéricos (t5 Operaciones · General, el DM) y el General del grupo ven todo.

Grupos con privacy mode: Telegram solo entrega a este bot los comandos con sufijo (/decisiones@<bot>) o los que
se eligen en el menú (setMyCommands). Sin sufijo, un /hoy puede ir a parar solo al bot de Hermes.
"""
from __future__ import annotations

import html
import logging
import re
import time
from typing import Callable

from .decisions import OWNER_TELEGRAM_ID
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
)
COMMAND_SCOPES = ("default", "all_private_chats", "all_group_chats")
HELP_ALIASES = ("start", "ayuda", "help")
NOT_OWNER = "Solo Oscar"
BRAND_ARGS = {"migrateam": "MigraTeam", "pildora": "Píldora", "píldora": "Píldora", "nextjobs": "NextJobs"}
TASK_ID_RE = re.compile(r"^t_[0-9a-f]{8}$")
STATUS_TEXT = {"running": ("running", "en curso"), "ready": ("ready", "en cola"), "review": ("review", "en review"),
               "done": ("done", "terminada"), "blocked": ("blocked", "bloqueada"), "todo": ("todo", "pendiente"),
               "triage": ("triage", "en triage"), "archived": ("archived", "archivada")}
MAX_CARDS = 20       # tarjetas por /decisiones o /aprobar (el resto se cuenta en la cabecera)
CARD_TITLE_MAX = 60  # título de una tarjeta de decisión de Oscar en /decisiones
EXTRA_BOARDS = ("default",)  # además de los tableros de los carriles: donde viven muchas tarjetas de Oscar
READY_PER_LANE = 8   # tareas en cola listadas por carril en /tareas

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
    """Agrupa las tareas con exactamente las mismas preguntas (en el orden de la primera aparición)."""
    groups: dict[tuple, list] = {}
    for p in pendings:
        key = question_key(p.questions) or ("__solo__", p.tid)
        groups.setdefault(key, []).append(p)
    return list(groups.values())


def fully_recommended(questions) -> bool:
    qs = normalize_questions(questions)
    return bool(qs) and all(q["recommended"] is not None for q in qs)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


class CommandCenter:
    def __init__(self, notifier, desk, *, lanes: dict, hermes_for: Callable[[str], object], messages, links=None,
                 generic_origins=(), bot_username: str | None = None, base_url: str | None = None,
                 owner_id: str | None = None, brief: Callable[[str | None], str] | None = None,
                 renotifier=None, now: Callable[[], float] = time.time):
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
        return "Comandos del bot de trabajos:\n" + "\n".join(f"/{c} · {d}" for c, d in COMMANDS)

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

    def decision_cards(self, brand: str | None = None) -> list[dict]:
        """Tarjetas de decisión de Oscar (no son de carril): asignadas a `oscar`, en ready/blocked y con etiqueta
        [DECISIÓN…]/[SEMANA…]/[IDEA…] en el título, en los tableros de los carriles + default. Más antigua primero.
        [{board, task, since}]. Con `brand` (tema de una marca), solo las de su tablero (default = "Otros")."""
        boards = dict.fromkeys([l.board for l in self.lanes.values() if l.board] + list(EXTRA_BOARDS))
        found: dict[str, dict] = {}
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
                    if t.get("id") and t["id"] not in found and is_decision_card(t):
                        found[t["id"]] = {"board": board, "task": t, "since": decision_since(t)}
        return sorted(found.values(), key=lambda c: (c["since"] or 0, c["task"]["id"]))

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

    def cmd_decisiones(self, where, args: str, brand: str | None) -> None:
        now = self._now()
        pendings = self.pending_decisions(brand)
        cards = self.decision_cards(brand)
        scope = f" · {brand}" if brand else ""
        if not pendings and not cards:
            self._send(where, f"Nada pendiente de ti{scope} 🎉", html=False)
            return
        if pendings:
            self._agent_questions(where, pendings, now, scope)
        if cards:
            self._send(where, self.cards_text(cards, now, scope))

    def cards_text(self, cards: list[dict], now: float, scope: str = "") -> str:
        """Un mensaje HTML con las tarjetas de decisión de Oscar (sin botones en v1). Nunca corta el HTML: si no
        cabe, quita líneas y lo dice."""
        e = html.escape
        head = f"📌 <b>Tus tarjetas de decisión ({len(cards)})</b>{e(scope)}"
        lines = []
        for c in cards:
            t = c["task"]
            url = card_url(self.base_url, c["board"], t["id"])
            ref = f'<a href="{e(url, quote=True)}">{e(t["id"])}</a>' if url else f"<code>{e(t['id'])}</code>"
            lines.append(f"• {ref} · {e(truncate(t.get('title'), CARD_TITLE_MAX))} · hace {hours_ago(c['since'], now)} h")
        shown = len(lines)
        while shown and len("\n".join([head, *lines[:shown], "  +000 más en el panel"])) > TEXT_MAX:
            shown -= 1
        more = [f"  +{len(lines) - shown} más en el panel"] if shown < len(lines) else []
        return "\n".join([head, *lines[:shown], *more])

    def _agent_questions(self, where, pendings: list, now: float, scope: str) -> None:
        """Preguntas de los agentes (tareas de carril en needs_input): cabecera + una tarjeta con botones por tarea."""
        groups = group_pending(pendings)
        eligible = [p for p in pendings if fully_recommended(p.questions)]
        head = (f"❓ {_plural(len(pendings), 'decisión', 'decisiones')} · la más antigua hace "
                f"{hours_ago(pendings[0].since, now)} h{scope}")
        dups = sum(len(g) - 1 for g in groups if len(g) > 1)
        if dups:
            head += f"\n{_plural(dups, 'pregunta repetida agrupada', 'preguntas repetidas agrupadas')}"
        markup = None
        if eligible:
            head += f"\n{len(eligible)} con opción recomendada en todas sus preguntas"
            # Todas: al pulsar se aplican las elegibles y las demás se nombran como "sin tocar".
            markup = self.desk.accept_all_markup([self.desk.member(p.task, p.lane, p.questions) for p in pendings])
        else:
            head += "\nNinguna tiene opción recomendada: decide en cada tarjeta"
        if len(groups) > MAX_CARDS:
            head += f"\nMuestro {MAX_CARDS} tarjetas; el resto, en el panel"
        self._send(where, head, html=False, markup=markup)
        for group in groups[:MAX_CARDS]:
            if len(group) == 1:
                self._decision_card(where, group[0], now)
            else:
                self._group_card(where, group, now)

    def _decision_card(self, where, p, now: float) -> None:
        status = status_line("necesita tu decisión", f"hace {hours_ago(p.since, now)} h")
        text = render("needs_input", p.tid, p.task.get("title"), p.lane.name, status,
                      self._links_for(p.lane, p.tid), p.bullets, body=p.task.get("body"),
                      for_oscar=getattr(p, "for_oscar", None), tree=self._tree(p.lane.board, p.tid))
        markup = self.desk.markup("needs_input", task=p.task, lane=p.lane, questions=p.questions,
                                  summary=p.summary, for_oscar=getattr(p, "for_oscar", None),
                                  yes_no=getattr(p, "yes_no", True))
        self._mirror(p.tid, self._send(where, text, markup=markup), markup)

    def _group_card(self, where, group: list, now: float) -> None:
        e = html.escape
        lines = [f"❓ Misma pregunta en {len(group)} tareas · la respuesta se aplica a todas"]
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
        members = [self.desk.member(p.task, p.lane, p.questions, yes_no) for p in group]
        markup = self.desk.group_markup(members, group[0].questions, yes_no)
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
                                          for_oscar=p.for_oscar, yes_no=p.yes_no)
                return text, markup
        state, label = STATUS_TEXT.get(status, (status or "?", status or "?"))
        lane_name = lane.name if lane else (task.get("assignee") or board)
        links = self._links_for(lane, tid) if lane else (
            [("🗂 Tarjeta", url)] if (url := card_url(self.base_url, board, tid)) else [])
        return render(state, tid, task.get("title"), lane_name, label, links, body=task.get("body")), None
