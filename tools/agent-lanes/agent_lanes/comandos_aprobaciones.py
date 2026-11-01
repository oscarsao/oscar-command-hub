"""Decisiones y aprobaciones de CommandCenter: /decisiones, /aprobar, /lote, /promover, /al_dia y /desplegar.

Métodos movidos tal cual desde commands.py (sin cambios de comportamiento).
"""
from __future__ import annotations

import html
import re
import time
from pathlib import Path

from . import bandeja as _bandeja
from . import titulos
from .aviso import UrgentBlockWatcher
from .card_options import CardSpec, card_message, parse_card
from .decisions import (DEPLOY_ASK, DEPLOY_NO)
from .deps import (dependency_order, pending_parents, tree_lines,
                   waiting_line)
from .telegram import TelegramNotifier
from .notices import (BOARD_BRANDS, BRANDS, DECISION_CARD_STATUSES, OSCAR_ASSIGNEE, TEXT_MAX, card_url,
                      decision_since, hours_ago, is_decision_card, markup_token, next_step, render, status_line, truncate)
from .comandos_comun import (CARD_TITLE_MAX, EXTRA_BOARDS, FICHAS_PAUSA, LANE_HINTS, MAX_CARDS, URGENT_EVERY, VIEW_STATUSES, _plural, _progress_label, fully_recommended, group_pending, log,
                              pending_of)

class AprobacionesMixin:
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
        self._ofertas_al_dia(where, getattr(self._ctx, "actor", None))

    def _marca_permitida(self, actor):
        """Filtro de carriles para las ofertas: un operador acotado por marca solo ve los proyectos de sus marcas."""
        restringido = getattr(self.desk, "restringido", None)
        if actor is None or not restringido or not restringido(actor):
            return None
        return lambda lane: self.desk.puede_en(actor, BRANDS.get(lane.name))

    def _ofertas_al_dia(self, where, actor=None) -> None:
        """«🔄 Poner al día» por proyecto con deriva NUEVA (una oferta por estado; al_dia.yaml), solo de las marcas del
        actor. La clave de `.state/al_dia/ofertas.json` es la misma para /hoy y para el monitor (`al_dia_tick`)."""
        try:
            for clave, texto, rec in self._al_dia().ofertas(permitido=self._marca_permitida(actor)):
                markup = self.desk.store.issue(rec, [[{"text": "🔄 Poner al día", "action": "ad_poner"}]])[1]
                self._send(where, texto, html=False, markup=markup)
        except Exception as exc:  # la oferta nunca estropea el resumen
            log.warning("ofertas de /al_dia: %s", exc)

    def al_dia_tick(self) -> None:
        """Monitor del runner (enganchado a `veto_tick`): ofrece a Oscar, por DM, «🔄 Poner al día» ante deriva nueva.
        Como mucho una pasada cada `oferta_cada_minutos` (al_dia.yaml)."""
        now = self._now()
        if now - getattr(self, "_al_dia_at", 0) < self._al_dia()._aj()["oferta_cada_minutos"] * 60:
            return
        self._al_dia_at = now
        self._ofertas_al_dia((self.owner_id, "0"))

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
        for tick in (self.guard_tick, self.daily_tick, self.peticiones_tick, self.al_dia_tick,
                     self.vigilancia_tick):  # cola, partes de las 9:00, /al_dia, tema 👥 Equipo
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
        for i, p in enumerate(ready[:MAX_CARDS]):
            if i and isinstance(self.notifier, TelegramNotifier):  # TG-07: 1 s entre fichas al mismo chat (429)
                getattr(self.notifier, "sleep", time.sleep)(FICHAS_PAUSA)
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

    # --- /al_dia (al_dia.py) ----------------------------------------------------------------

    def _al_dia(self):
        from .al_dia import AlDia
        return AlDia(getattr(self.desk, "integrator", None))

    def cmd_aldia(self, where, args: str, brand: str | None) -> None:
        self.cmd_al_dia(where, args, brand)

    def cmd_al_dia(self, where, args: str, brand: str | None) -> None:
        """/al_dia [proyecto|todos] [dev|prod]: un mensaje que se edita con el progreso y termina con el resumen y
        los botones. Esta versión solo hace `dev` (sin desplegar); `prod` llega con la tarjeta hija."""
        al_dia = self._al_dia()
        proyectos = al_dia.proyectos()
        if not proyectos:
            self._send(where, "El integrador está apagado: no hay nada que poner al día.", html=False)
            return
        from .al_dia import parse_args
        proyecto, destino, error = parse_args(args, proyectos)
        if error:
            self._send(where, error, html=False)
            return
        claves = list(proyectos) if proyecto == "todos" else [proyecto]
        sent = self._send(where, f"🔄 /al_dia {proyecto} {destino} · empiezo…", html=False)
        mid = (sent or {}).get("message_id")

        def run() -> None:
            lineas, ya_enviado = [], set()
            for clave in claves:
                try:
                    st = al_dia.diagnose(clave)
                    acciones = al_dia.poner_al_dia(st)
                    lineas.append(al_dia.resumen(st, destino) + ("\n" + "\n".join(acciones) if acciones else ""))
                    markup = al_dia.botones(self.desk, st, destino)
                except Exception as exc:  # un proyecto roto no tumba el resto
                    log.warning("/al_dia %s falló: %s", clave, exc)
                    lineas.append(f"📁 {clave}: no pude diagnosticarlo (detalle en el log)")
                    markup = None
                if markup:  # los botones de cada proyecto van en su propio mensaje (y no se repite en el resumen final)
                    self._send(where, lineas[-1], html=False, markup=markup)
                    ya_enviado.add(len(lineas) - 1)
                if mid:
                    self.notifier.edit(where[0], mid, f"🔄 /al_dia {proyecto} {destino} · {len(lineas)}/{len(claves)} "
                                                      "proyectos…", html=False)
            restantes = [bloque for n, bloque in enumerate(lineas) if n not in ya_enviado]
            texto = "✅ /al_dia terminado" + ("\n\n" + "\n\n".join(restantes) if restantes else "")
            if mid:
                self.notifier.edit(where[0], mid, texto, html=False)
            else:
                self._send(where, texto, html=False)

        self._spawn(run)

    def al_dia_choice(self, action: str, rec: dict, where: dict, desk) -> bool:
        """Botones de /al_dia: integrar todo lo listo (lote) o abrir la ficha de un PR con sus botones de siempre."""
        from .al_dia import AD_INTEGRAR, AD_PONER, AD_PR, AD_PROD, AD_PROD_OK, AD_RESOLVER
        dest = (where["chat_id"], where.get("thread_id") or "0")
        al_dia, clave = self._al_dia(), str(rec.get("key") or "")
        if action in (AD_PROD, AD_PROD_OK):  # producción: solo Oscar o "*" (el botón puede estar en un chat compartido)
            if self.desk.restringido(where.get("user_id")):
                self._send(dest, "🔒 Producción solo la despliega Oscar (o quien tenga todas las marcas).", html=False)
                return True
            return self._al_dia_prod(action, al_dia, clave, rec, where, dest)
        if action in (AD_PONER, AD_RESOLVER) and not self.desk.puede_con(where.get("user_id"), rec):
            self._send(dest, self.desk._rechazo_marca(where.get("user_id"), rec), html=False)   # veto de marca
            return True
        if action == AD_PONER:
            self.cmd_al_dia(dest, clave, None)
            return True
        if action == AD_RESOLVER:
            st = al_dia.diagnose(clave)
            if rec.get("fallo"):  # gate rojo / deploy con error / health KO
                self._send(dest, al_dia.resolver_fallo(st, rec), html=False)
                return True
            p = next((p for p in st.prs if p.tid == rec.get("task_id") and p.estado == "ci_rojo"), None)
            self._send(dest, al_dia.resolver_ci(st, p) if p else "Ese PR ya no tiene el CI en rojo.", html=False)
            return True
        if action == AD_PR:
            self.cmd_tarea(dest, str(rec.get("task_id") or ""), None)
            return True
        if action != AD_INTEGRAR:
            return False
        self._send(dest, f"Montando el lote de {clave}… pasa los gates y los tests, puede tardar unos minutos.",
                   html=False)

        def run() -> None:
            try:
                texto = al_dia.integrar_todo(clave)
            except Exception as exc:
                log.warning("/al_dia integrar %s falló: %s", clave, exc)
                texto = "No pude montar el lote; detalle en el log."
            self._send(dest, texto, html=False)

        self._spawn(run)
        return True

    def _al_dia_prod(self, action: str, al_dia, clave: str, rec: dict, where: dict, dest) -> bool:
        """`ad_prod` → «¿Seguro?»; `ad_prod_ok` → el despliegue de /desplegar (INT_DEPLOY) del integrador."""
        from .al_dia import AD_PROD
        from .integrator import INT_DEPLOY
        if action == AD_PROD:
            markup = self.desk.store.issue(rec, [[{"text": "✅ Sí, desplegar", "action": "ad_prod_ok"}],
                                                 [{"text": "✖ No", "action": DEPLOY_NO}]])[1]
            self.notifier.edit(where["chat_id"], where["message_id"], f"⚠️ ¿Seguro? Esto despliega {clave} a "
                               "producción (lo fusionado y sin desplegar).", html=False, reply_markup=markup)
            return True
        promotor = getattr(self.desk, "promoter", None)
        plan = al_dia.prod_plan(clave, tuple(getattr(promotor, "configs", ()) or ()))
        if "texto" in plan:
            self._send(dest, plan["texto"], html=False)
            return True
        sent = self._send(dest, f"🚀 Desplegando {clave}…", html=False)
        w = {"chat_id": where["chat_id"], "thread_id": where.get("thread_id") or "0",
             "message_id": (sent or {}).get("message_id"), "user_id": where.get("user_id")}
        self._spawn(lambda: self.desk.integrator.on_button(INT_DEPLOY, plan["rec"], w, self.desk))
        return True

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
