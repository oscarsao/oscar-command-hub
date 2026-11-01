"""Vistas por paso y «Lo tuyo» de CommandCenter: /pendientes, /pendiente, /personal y /agrupar.

Métodos movidos tal cual desde commands.py (sin cambios de comportamiento).
"""
from __future__ import annotations

import html

from . import agrupar as _agrupar
from . import solapes
from . import lo_tuyo as _lo_tuyo
from . import personal as _personal
from . import titulos
from .notices import (DECISION_CARD_STATUSES, OSCAR_ASSIGNEE, TEXT_MAX, normalize_questions,
                      truncate)
from .comandos_comun import (CARD_TITLE_MAX, MAX_CARDS, TASK_ID_RE, _plural, log)

class PendientesMixin:
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

    # --- /pendiente: bandeja única «Lo tuyo» (lo_tuyo.py) ----------------------------------------------------------

    def lo_tuyo_items(self, brand: str | None = None) -> list[dict]:
        """Todo lo que espera a Oscar, de las fuentes de siempre. Una fuente que falla no vacía la bandeja."""
        def board_of(lane):
            return getattr(self.lanes.get(lane), "board", "")

        def tuyo() -> list[dict]:
            snoozed = self._snoozed()
            mine = [c for c in self.board_tasks(brand) if c["task"].get("assignee") == OSCAR_ASSIGNEE
                    and titulos.step_of(c["task"]) in titulos.OSCAR_STEPS and c["task"]["id"] not in snoozed]
            return _lo_tuyo.de_tarjetas(mine + self.oscar_cards(brand)[0])

        def cola() -> list[dict]:
            queue = self.guard_queue()
            return _lo_tuyo.de_cola_guard(queue.entries(), board_of) if queue and not brand else []

        def externos() -> list[dict]:
            ext = self.cola_externos()
            return _lo_tuyo.de_externos(ext.items()) if ext and not brand else []

        fuentes = (("preguntas", lambda: _lo_tuyo.de_pendientes(self.pending_decisions(brand), "pregunta")),
                   ("atascadas", lambda: _lo_tuyo.de_pendientes(self.stuck_tasks(brand), "atascada")),
                   ("por aprobar", lambda: _lo_tuyo.de_pendientes(self.ready_to_approve(brand), "aprobar")),
                   ("validaciones de ops", lambda: [] if brand else _lo_tuyo.de_validar_ops(self.lanes, self.hermes_for)),
                   ("cola del integrador", cola), ("PR de sesiones", externos), ("tarjetas tuyas", tuyo),
                   ("estado del integrador", lambda: [] if brand else _lo_tuyo.de_estado(self.estado_integrador())))
        out: list[dict] = []
        for name, fn in fuentes:
            try:
                out += fn()
            except Exception as exc:
                log.warning("/pendiente: %s no leído: %s", name, exc)
        return out

    def cmd_pendiente(self, where, args: str, brand: str | None) -> None:
        """UN mensaje con todo lo tuyo agrupado por prioridad y un botón por línea (cada botón abre la ficha de siempre)."""
        aj = _lo_tuyo.load_ajustes(getattr(self, "lo_tuyo_settings", None))
        text, shown = _lo_tuyo.render(self.lo_tuyo_items(brand), aj, self._now(), f" · {brand}" if brand else "")
        self._send(where, text, html=False, markup=self._lo_tuyo_markup(shown))

    def _lo_tuyo_markup(self, shown: list[dict]) -> dict | None:
        """Un token por botón (cada uno de un solo uso e independiente), todos en el mismo teclado de dos columnas."""
        from .decisions import ESTADO_MAS, LT_OPEN
        buttons = []
        for n, it in enumerate(shown, 1):
            text = f"{n} {_lo_tuyo.verbo(it)}"
            if it.get("url"):
                buttons.append({"text": text, "url": it["url"]})
                continue
            rec = {"task_id": "pendiente", "board": it.get("board") or "", "lane": "", "kind": it["kind"], "tid": it["tid"]}
            action = ESTADO_MAS if it["kind"] == "integrador" else LT_OPEN
            buttons.append(self.desk.store.issue(rec, [[{"text": text, "action": action}]])[1]["inline_keyboard"][0][0])
        return {"inline_keyboard": [buttons[i:i + 2] for i in range(0, len(buttons), 2)]} if buttons else None

    def semillas(self) -> list[dict]:
        """Siembra del tema 🎯 Decisiones (F1-03, FL-02/FL-12): el mismo backlog que /pendiente y `lanes.py pendiente`
        (lo_tuyo), en orden, con los botones de cada cosa. Los teclados se emiten solo al publicar (markup diferido).
        - tarjeta tuya: ✅ Hecho / ⏳ Aparcar 7 d / 🧑‍💻 Integrador / 🗄 Archivar (CARD_*; releen la tarjeta antes);
        - cola del integrador: 🧑‍💻 Que lo resuelva el integrador / ✅ Ya está resuelta;
        - PR de sesión: su enlace; paso del integrador: 📖 Ver más; el resto: la ficha de siempre con sus botones."""
        from . import canal_avisos
        from .decisions import (COLA_RESUELTA, ESTADO_MAS, LT_OPEN, TO_INTEGRADOR, seed_card_keyboard_spec)
        aj = _lo_tuyo.load_ajustes(getattr(self, "lo_tuyo_settings", None))
        now = self._now()
        store = self.desk.store

        def markup_de(it: dict):
            tid, board, verbo = it["tid"], it.get("board") or "", _lo_tuyo.verbo(it)
            if it.get("url"):
                return {"inline_keyboard": [[{"text": verbo, "url": it["url"]}]]}
            if it["kind"] == "tuyo" and it.get("status") in (None, *DECISION_CARD_STATUSES):
                # Hecho/Aparcar/Integrador/Archivar solo actúan en ready/blocked (_card_act); el resto abre la ficha
                rec = self.desk.card_rec_for({"task_id": tid, "board": board, "title": it.get("title")})
                return lambda: store.issue(rec, seed_card_keyboard_spec())[1]
            if it["kind"] == "cola":
                rec = {"task_id": tid, "board": board, "lane": "", "title": it.get("title") or ""}
                return lambda: store.issue(rec, [[{"text": "🧑‍💻 Que lo resuelva el integrador", "action": TO_INTEGRADOR}],
                                                 [{"text": "✅ Ya está resuelta", "action": COLA_RESUELTA}]])[1]
            rec = {"task_id": "pendiente", "board": board, "lane": "", "kind": it["kind"], "tid": tid}
            action = ESTADO_MAS if it["kind"] == "integrador" else LT_OPEN
            return lambda: store.issue(rec, [[{"text": verbo, "action": action}]])[1]

        return [canal_avisos.semilla(it, markup_de(it), _lo_tuyo.verbo(it), now)
                for it in _lo_tuyo.ordenar(self.lo_tuyo_items(None), aj, now)]

    def lt_open(self, rec: dict, where: dict) -> bool:
        """Botón de una línea de /pendiente: la ficha de esa cosa, con los botones que le tocan hoy (siempre fresca)."""
        dest, tid, board = (where["chat_id"], where["thread_id"]), rec.get("tid") or "", rec.get("board") or ""
        if rec.get("kind") == "tuyo":
            card = next((c for c in self.board_tasks() if c["task"]["id"] == tid), None)
            if card:
                self._send_card(dest, card, self._now())
                return True
        elif TASK_ID_RE.match(tid):
            try:
                show = self.hermes_for(board).show(tid) if board else None
            except Exception:
                show = None
            found = (board, show) if show and show.get("task") else self._find(tid)
            if found:
                self._send_ficha(dest, *found)
                return True
        self._send(dest, f"No encuentro {tid}: puede que ya se haya resuelto", html=False)
        return True

    # --- /personal: backlog personal fuera del kanban de carriles (personal.py) ---------------------------------------

    def personal_candidatas(self) -> list[dict]:
        aj = _personal.load_ajustes(getattr(self, "personal_settings", None))
        cards = [c for c in self.board_tasks() if not aj.boards_origen or c["board"] in aj.boards_origen]
        path = self._state_file("personal_state", _personal.DESCARTADAS_FILE)
        excluidas = self._snoozed() | (_personal.Descartadas(path).ids() if path else set())
        return _personal.candidatas(cards, aj, self._now(), excluidas)

    def cmd_personal(self, where, args: str, brand: str | None) -> None:
        """Propone mover a Oscar sus tarjetas personales (no son decisiones de carril) al board `personal`. No mueve nada
        hasta que pulse [✅ Mover]; [✋ Dejarlas] las excluye de futuras propuestas."""
        from .decisions import PERS_KEEP, PERS_MOVE
        aj = _personal.load_ajustes(getattr(self, "personal_settings", None))
        cands = self.personal_candidatas()
        if not cands:
            self._send(where, "Backlog personal: no hay tarjetas tuyas que mover 🎉", html=False)
            return
        now, tanda, tope = self._now(), cands[:aj.max_por_tanda], MAX_CARDS * 2
        lines = [f"🗃 Backlog personal · {len(cands)} tarjetas a tu nombre que no son decisiones de carril",
                 f"Propongo moverlas al board «{aj.board}». No se mueve nada hasta que pulses ✅."
                 + (f" Esta tanda: las {len(tanda)} más antiguas." if len(cands) > len(tanda) else "")]
        for c in tanda[:tope]:
            t = c["task"]
            lines.append(f"• {t['id']} · {c['board']} · {truncate(t.get('title'), 50)}"
                         + (f" · hace {int(max(0, now - c['since']) // 86400)} d" if c.get("since") else ""))
        if len(tanda) > tope:
            lines.append(f"… y {len(tanda) - tope} más en esta tanda")
        rec = {"task_id": "personal", "board": "", "lane": "",
               "cards": [{"id": c["task"]["id"], "board": c["board"]} for c in tanda]}
        markup = self.desk.store.issue(rec, [[{"text": f"✅ Mover {len(tanda)} a «{aj.board}»", "action": PERS_MOVE},
                                              {"text": "✋ Dejarlas donde están", "action": PERS_KEEP}]])[1]
        self._send(where, "\n".join(lines)[:TEXT_MAX], html=False, markup=markup)

    def personal_step(self, action: str, rec: dict, where: dict) -> bool:
        """[✅ Mover] / [✋ Dejarlas] de /personal. Solo se traslada lo que SIGUE siendo candidata (releída al pulsar)."""
        from .decisions import PERS_KEEP
        dest = (where["chat_id"], where["thread_id"])
        ids = [m["id"] for m in rec.get("cards") or () if isinstance(m, dict) and m.get("id")]
        if action == PERS_KEEP:
            path = self._state_file("personal_state", _personal.DESCARTADAS_FILE)
            if path is None:
                self._send(dest, "No puedo recordar la elección (sin carpeta .state)", html=False)
                return False
            _personal.Descartadas(path, self._now).add(ids)
            self._send(dest, f"✋ Vale: {len(ids)} tarjetas se quedan donde están y no vuelvo a proponerlas", html=False)
            return True
        aj = _personal.load_ajustes(getattr(self, "personal_settings", None))
        vivas = {c["task"]["id"]: c for c in self.personal_candidatas()}
        todo = [vivas[i] for i in ids if i in vivas]
        moved, failed = [], []
        for c in todo:
            ok, detail = _personal.mover(c, self.hermes_for, aj)
            (moved if ok else failed).append((c["task"]["id"], detail))
            if len(failed) >= 3 and not moved:   # el board destino no existe o falla: no insistir 60 veces
                break
        lines = [f"🗃 Movidas a «{aj.board}»: {len(moved)}"
                 + (f" · ya no eran candidatas: {len(ids) - len(todo)}" if len(ids) > len(todo) else "")]
        lines += [f"⚠️ {tid}: {why}" for tid, why in failed[:5]]
        if failed and not moved:
            lines.append(f"No he movido nada. Si el board «{aj.board}» no existe, hay que crearlo en Hermes (necesita tu "
                         "OK); luego repite /personal.")
        self._send(dest, "\n".join(lines), html=False)
        return bool(moved) or not failed

    # --- /agrupar: propone grupos de tarjetas abiertas (agrupar.py); nada se aplica sin botón -----------------------

    def _agrupar_enlazadas(self):
        cache: dict = {}

        def enlazadas(a, b) -> bool:
            return solapes._enlazadas(solapes.Card(a.tid, a.board, a.lane, a.status, a.title),
                                      solapes.Card(b.tid, b.board, b.lane, b.status, b.title), self.hermes_for, cache)
        return enlazadas

    def cmd_agrupar(self, where, args: str, brand: str | None) -> None:
        """/agrupar [proyecto|todo]: un mensaje por grupo con sus botones y, al final, la bandeja de Oscar sin botones."""
        aj = _agrupar.load_ajustes(getattr(self, "agrupar_settings", None))
        if not aj.activo:
            self._send(where, "/agrupar está apagado (agrupar.yaml: activo: false)", html=False)
            return
        arg = args.strip().lower()
        repo = None
        if arg and arg not in ("todo", "todos"):
            lane = self.lanes.get(f"claude-{arg}")
            if lane is None:
                self._send(where, "Proyecto desconocido: usa /agrupar, /agrupar todo o /agrupar <proyecto> "
                                  "(migrateam, oscarhq, hub…)", html=False)
                return
            repo = solapes._norm_repo(getattr(lane, "repo", ""))
        items = self.board_tasks(brand if not arg else None)
        if repo is not None:
            items = [i for i in items if solapes._norm_repo(
                getattr(self.lanes.get(i["task"].get("assignee")), "repo", "")) == repo]
        cards, bandeja = _agrupar.preparar(items, self.lanes, aj)
        descartes_path = getattr(getattr(self.desk, "fusion", None), "descartes_path", None) \
            or getattr(self, "descartes_path", None)
        preguntas = {}
        try:
            for p in self.pending_decisions(brand):
                qs = normalize_questions(p.questions)
                if qs:
                    preguntas[p.tid] = qs[0].get("question") or ""
        except Exception as exc:
            log.warning("/agrupar: preguntas pendientes no leídas: %s", exc)
        grupos = _agrupar.proponer(cards, aj, solapes.load_descartes(descartes_path),
                                   enlazadas=self._agrupar_enlazadas(), preguntas=preguntas)
        if not grupos and not bandeja:
            self._send(where, "Agrupar: no hay nada que agrupar 🎉", html=False)
            return
        mostrados = grupos[:aj.max_grupos]
        self._send(where, f"🧩 Agrupar · {len(grupos)} grupos propuestos"
                   + (f" (muestro {len(mostrados)})" if len(grupos) > len(mostrados) else "")
                   + (f" · {len(bandeja)} tarjetas en la bandeja de Oscar" if bandeja else "")
                   + ". Nada se aplica sin botón.", html=False)
        for n, g in enumerate(mostrados, 1):
            rec = _agrupar.registro(g)
            markup = self.desk.store.issue(rec, _agrupar.teclado(g))[1] if rec else None
            self._send(where, _agrupar.texto_grupo(g, n, len(grupos), aj)[:TEXT_MAX], html=False, markup=markup)
        if bandeja:
            self._send(where, _agrupar.texto_bandeja(bandeja, aj)[:TEXT_MAX], html=False)

    def agrupar_step(self, action: str, rec: dict, where: dict) -> bool:
        """[📦 Mismo lote] / [📌 Una sola pregunta] de /agrupar (Fusionar, Encadenar y Dejar son los de fusion.py)."""
        return _agrupar.on_button(action, rec, where, self.desk, self.hermes_for)
