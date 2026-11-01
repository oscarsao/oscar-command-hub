"""Tareas y estados de tarjeta de CommandCenter: /tareas, /cola, /tarea, fichas, respuestas y relanzar.

Métodos movidos tal cual desde commands.py (sin cambios de comportamiento).
"""
from __future__ import annotations

import contextlib
import html
import threading
from typing import Callable

from . import botonera as _bt
from . import deps
from . import tareas_movil as _tm
from .decisions import (APPROVE, CHANGES, PARK, UNTRIAGE)
from .hermes import OSCAR_AUTHOR
from .deps import (child_bucket, family)
from .notices import (BOARD_BRANDS, BRANDS, TEXT_MAX, card_url,
                      render, truncate)
from .comandos_comun import (BRAND_ARGS, BUSCA_ESTADOS, COLA_PER_LANE, PRIORITY_MAX, PRIORITY_STEP, STATUS_TEXT, TASK_ID_RE, _elapsed,
                              _prio, log)

class TarjetasMixin:
    def _spawn(self, fn: Callable[[], None]) -> None:  # aparte para que los tests lo ejecuten en línea
        threading.Thread(target=fn, name="lote", daemon=True).start()

    def cmd_tareas(self, where, args: str, brand: str | None) -> None:
        suffix = (args.split() or [""])[0].lower()
        if args and _tm.SUFFIX_RE.match(suffix):  # /tareas 3d4: búsqueda por el final del id
            self._buscar_y_enviar(where, suffix)
            return
        if args:
            brand = BRAND_ARGS.get(args.split()[0].lower())
            if not brand:
                self._send(where, "Marca desconocida: usa /tareas migrateam, /tareas pildora o /tareas nextjobs",
                           html=False)
                return
        numbers: dict[str, str] = {}
        for page in self.tasks_pages(brand, numbers):
            self._registra_lista(where, self._send(where, page), numbers)

    def _registra_lista(self, where, sent, numbers: dict[str, str]) -> None:
        """Responder a este mensaje con «3» envía la ficha de la tarea nº 3 (24 h)."""
        if sent and numbers:
            self.mapa.put(where[0], sent.get("message_id"), _tm.KIND_LISTA, {"n": dict(numbers)}, self._now())

    def cmd_cola(self, where, args: str, brand: str | None) -> None:
        if args:
            brand = BRAND_ARGS.get(args.split()[0].lower())
            if not brand:
                self._send(where, "Marca desconocida: usa /cola migrateam, /cola pildora o /cola nextjobs", html=False)
                return
        numbers: dict[str, str] = {}
        self._registra_lista(where, self._send(where, self.queue_text(brand, numbers)), numbers)

    def queue_text(self, brand: str | None, numbers: dict[str, str] | None = None) -> str:
        """Por carril: la tarea en curso (desde cuándo) y las siguientes en cola por prioridad actual. Cada línea
        lleva su número (global); `numbers` recoge {número: id} para responder con él."""
        e = html.escape
        numbers = {} if numbers is None else numbers

        def num(tid: str) -> str:
            numbers[str(len(numbers) + 1)] = tid
            return f"{len(numbers)}. "
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
                out.append(f"{num(t['id'])}▶️ <code>{e(t['id'])}</code> · {e(truncate(t.get('title'), 50))}{when}")
            if not running:
                out.append("▶️ libre")
            for t in ready[:COLA_PER_LANE]:
                out.append(f"{num(t['id'])}<code>{e(t['id'])}</code> · {e(truncate(t.get('title'), 50))} · "
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

    def tasks_text(self, brand: str | None) -> str:
        return "\n".join(self.tasks_pages(brand))

    def tasks_pages(self, brand: str | None, numbers: dict[str, str] | None = None) -> list[str]:
        """/tareas: «En marcha» (carril y tablero) y «En cola» entera; cada tarea numerada y con su `/tarea t_xxx`
        tocable (`numbers` recoge {número: id}). Si no cabe en un mensaje se reparte en varios por líneas enteras:
        nunca se recorta nada."""
        e = html.escape
        numbers = {} if numbers is None else numbers
        sections = []
        for name, lane in self.lanes.items():
            if lane.kind not in ("implement", "ops") or (brand and BRANDS.get(name) != brand):
                continue
            h = self.hermes_for(lane.board)
            sections.append((lane, h.list_status(name, "running"), h.list_status(name, "ready")))
        # Una hija cuyo padre también sale en la lista va debajo de él (aunque sea de otro carril del tablero).
        shown = [("▶️", lane, t) for lane, run, _ in sections for t in run] + \
                [("⏳", lane, t) for lane, _, rdy in sections for t in rdy]
        parent_of = self._shown_parents(shown)
        kids: dict[str, list] = {}
        for it in shown:
            if it[2]["id"] in parent_of:
                kids.setdefault(parent_of[it[2]["id"]], []).append(it)

        def emit(out: list[str], it, depth: int = 0) -> None:
            emoji, _, t = it
            pad = "   " * depth
            numbers[str(len(numbers) + 1)] = t["id"]
            out.append(pad + ("↳ " if depth else "") + f"{len(numbers)}. {emoji} {e(truncate(t.get('title'), 60))} · "
                       f"<code>{e(t['id'])}</code>")
            out.append(pad + f"/tarea {e(t['id'])}")
            for kid in kids.get(t["id"], ()):  # _shown_parents deja un bosque (sin ciclos): termina
                emit(out, kid, depth + 1)

        suffix = f" · {e(brand)}" if brand else ""
        lines = [f"▶️ <b>En marcha</b>{suffix}"]
        busy = [(lane, run) for lane, run, _ in sections if run]
        for lane, run in busy:
            lines.append(f"\n<b>{e(lane.name)}</b> · tablero {e(lane.board)} · en ejecución")
            for t in run:
                if t["id"] not in parent_of:
                    emit(lines, ("▶️", lane, t))
        if not busy:
            lines.append("Nada en marcha.")
        lines.append("\n⏳ <b>En cola</b>" + suffix)
        queued = [(lane, rdy) for lane, _, rdy in sections if rdy]
        for lane, rdy in queued:
            lines.append(f"\n<b>{e(lane.name)}</b> · tablero {e(lane.board)} · {len(rdy)} en cola")
            top = [t for t in rdy if t["id"] not in parent_of]
            for t in top:
                emit(lines, ("⏳", lane, t))
            if not top:
                lines.append("(sus tareas van bajo su padre)")
        if not queued:
            lines.append("Cola vacía.")
        return self._paginate(lines)

    @staticmethod
    def _paginate(lines: list[str]) -> list[str]:
        """Junta líneas enteras en mensajes de ≤ TEXT_MAX (cada línea es corta: nunca se parte una)."""
        pages, cur = [], ""
        for line in lines:
            if cur and len(cur) + 1 + len(line) > TEXT_MAX:
                pages.append(cur)
                cur = line.lstrip("\n")
            else:
                cur = f"{cur}\n{line}" if cur else line
        pages.append(cur)
        return pages

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
        if _tm.SUFFIX_RE.match(tid):  # /tarea 3d4: el final del id (o cualquier parte si no hay coincidencia)
            self._buscar_y_enviar(where, tid)
            return
        if not TASK_ID_RE.match(tid):  # nunca pasar a la CLI algo que no sea un id (p. ej. "--board x")
            self._send(where, "Uso: /tarea t_xxxxxxxx (o los últimos 3-8 caracteres del id)", html=False)
            return
        found = self._find(tid)
        if not found:
            self._send(where, f"No encuentro {tid} en los tableros de los carriles", html=False)
            return
        self._send_ficha(where, *found)

    def _send_ficha(self, where, board: str, show: dict) -> None:
        """Ficha de una tarea con los botones de su estado (/tarea y las líneas de /pendiente). Responderle con texto
        libre actúa sobre esa tarea (anotar, preguntar, fusionar)."""
        text, markup = self.task_card(board, show)
        tree = self.tree_text(board, show)
        if tree and len(text) + 1 + len(tree) <= TEXT_MAX:
            text += "\n" + tree
        extra, cs_rows = self._consola_ficha(board, show)  # 📍 zona · ⏳ esperando + botones de consola (H3)
        if extra and len(text) + 1 + len(extra) <= TEXT_MAX:
            text += "\n" + extra
        row = self._fusion_row(board, show)
        liberar = self._liberar_row(board, show)
        rows = [*(markup or {}).get("inline_keyboard", []), *cs_rows, *([row] if row else []),
                *([liberar] if liberar else [])]
        full = {"inline_keyboard": rows} if rows else markup
        sent = self._send(where, text, markup=full)
        if markup:  # la copia sincronizada sigue al teclado de la decisión; la fila «Fusionar con…» es aparte
            self._mirror(show["task"]["id"], sent, markup)
        if sent:
            task = show["task"]
            self.mapa.put(where[0], sent.get("message_id"), _tm.KIND_FICHA, {"tid": task["id"], "board": board},
                          self._now())

    # --- /tareas desde el móvil: número, final del id y respuestas a la ficha -----------------------------

    @property
    def mapa(self) -> _tm.MapaMensajes:
        if getattr(self, "_mapa", None) is None:
            path = self._state_file("_mapa_path", "tareas_mensajes.json")
            self._mapa = _tm.MapaMensajes(path)
        return self._mapa

    @staticmethod
    def _marca_tarea(board: str, task: dict) -> str | None:
        return BRANDS.get(task.get("assignee")) or BOARD_BRANDS.get(board)

    def _permitida(self, board: str, task: dict) -> bool:
        """El operador (hilo en curso) puede actuar en la marca de esta tarea (Oscar y "*": todas)."""
        actor = getattr(self._ctx, "actor", None)
        restringido = getattr(self.desk, "restringido", None)
        if actor is None or not restringido or not restringido(actor):
            return True
        return bool(self.desk.puede_en(actor, self._marca_tarea(board, task)))

    def _rechazo(self, board: str, task: dict) -> str:
        return self.desk.aviso_marca(getattr(self._ctx, "actor", None), self._marca_tarea(board, task))

    def _buscar(self, texto: str) -> list[tuple[str, str, dict]]:
        """[(id, tablero, tarea)] de las tarjetas cuyo id casa con `texto` (solo hex, validado antes) y que el
        operador puede ver. Lee listas por estado de cada tablero; un fallo de Hermes solo recorta la búsqueda."""
        cands: dict[str, tuple[str, dict]] = {}
        for board in dict.fromkeys(l.board for l in self.lanes.values() if l.board):
            h = self.hermes_for(board)
            for status in BUSCA_ESTADOS:
                try:
                    rows = h.list_status_all(status)
                except Exception:
                    continue
                for t in rows or ():
                    if isinstance(t, dict) and TASK_ID_RE.match(str(t.get("id"))) and self._permitida(board, t):
                        cands.setdefault(t["id"], (board, t))
        return [(i, *cands[i]) for i in _tm.coincidencias(list(cands), texto)]

    def _buscar_y_enviar(self, where, texto: str) -> None:
        """Una coincidencia: su ficha. Varias: botones «t_xxxx · título» (máx. 8). Ninguna: lo dice."""
        found = self._buscar(texto)
        if not found:
            self._send(where, f"No encuentro ninguna tarea con «{texto}» en el id", html=False)
            return
        if len(found) == 1:
            hit = self._find(found[0][0])
            if hit:
                self._send_ficha(where, *hit)
            else:
                self._send(where, f"No encuentro {found[0][0]} en los tableros de los carriles", html=False)
            return
        shown = found[:_tm.MAX_OPCIONES]
        rec = {"task_id": "buscar", "board": "", "lane": "", "tids": [i for i, _, _ in shown],
               "candidates": [{"task_id": i, "board": b, "lane": t.get("assignee") or ""} for i, b, t in shown]}
        spec = [[{"text": f"{i} · {truncate(t.get('title'), 30)}", "action": _tm.TR_PICK, "index": n}]
                for n, (i, _, t) in enumerate(shown)]
        extra = f" (hay {len(found)}; se muestran {len(shown)}: afina con más caracteres)" if len(found) > len(shown) else ""
        self._send(where, f"Varias tareas casan con «{texto}»{extra}:", html=False,
                   markup=self.desk.store.issue(rec, spec)[1])

    def ficha_markup(self, board: str, task: dict) -> dict | None:
        """Botones de la ficha por estado (los de blocked/done los pone `task_card`)."""
        status = task.get("status")
        if status in ("ready", "running"):
            spec = [[{"text": "⬆️ Prioridad", "action": _tm.TR_PRIO}, {"text": "⏸ Pausar", "action": PARK}],
                    [{"text": "🔗 Fusionar con…", "action": _tm.TR_FUSION}]]
        elif status == "review":
            spec = [[{"text": "✅ Aprobar", "action": APPROVE}, {"text": "🔁 Pedir cambios", "action": CHANGES}]]
        elif status == "triage":
            spec = [[{"text": "▶️ Sacar de triage", "action": UNTRIAGE}, {"text": "🗂 Archivar", "action": _tm.TR_ARCHIVAR}]]
        else:
            spec = []
        estado = _bt.estado_de(status)  # botonera estándar (F1-14): todo estado con tarjeta tiene salida
        if estado is None:
            return None
        spec = _bt.con_mas(spec, estado)
        rec = {"task_id": task["id"], "board": board, "lane": task.get("assignee") or "",
               "title": task.get("title") or "", "body": (task.get("body") or "")[:1500]}
        markup = self.desk.store.issue(rec, spec)[1]
        url = card_url(self.base_url, board, task["id"])
        if url:
            markup["inline_keyboard"].append([{"text": "🗂 Tarjeta", "url": url}])
        return markup

    def reply_target(self, msg: dict) -> dict | None:
        """Qué es el mensaje al que responde `msg` (lista, ficha o fusión), si lo guardamos y no ha caducado."""
        replied = (msg.get("reply_to_message") or {}).get("message_id")
        chat = (msg.get("chat") or {}).get("id")
        return self.mapa.get(chat, replied, self._now()) if replied is not None else None

    def responder(self, msg: dict, target: dict) -> None:
        """Texto libre del operador sobre una lista numerada, una ficha o un aviso de fusión (hilo aparte)."""
        where = self._where(msg)
        actor = (msg.get("from") or {}).get("id")
        text = (msg.get("text") or "").strip()
        actuando = getattr(self.desk, "actuando", None)
        self._ctx.actor = actor
        try:
            with actuando(actor) if actuando else contextlib.nullcontext():
                kind, data = target["kind"], target["data"]
                if kind == _tm.KIND_LISTA:
                    self._resp_lista(where, data, text)
                elif kind == _tm.KIND_FICHA:
                    self._resp_ficha(where, data, text)
                elif kind == _tm.KIND_FUSION:
                    self._resp_fusion(where, data, text)
        except Exception as exc:
            log.warning("respuesta a %s falló: %s", target.get("kind"), exc)
            self._send(where, "No pude completar eso; detalle en el log", html=False)

    def _resp_lista(self, where, data: dict, text: str) -> None:
        m = _tm.NUMERO_RE.match(text)
        if m:
            tid = (data.get("n") or {}).get(m.group(1))
            if not tid:
                self._send(where, f"No hay línea {m.group(1)} en esa lista (van de 1 a {len(data.get('n') or {})})",
                           html=False)
                return
            found = self._find(tid)
            if not found:
                self._send(where, f"No encuentro {tid} en los tableros de los carriles", html=False)
            elif not self._permitida(found[0], found[1]["task"]):
                self._send(where, self._rechazo(found[0], found[1]["task"]), html=False)
            else:
                self._send_ficha(where, *found)
        elif _tm.SUFFIX_RE.match(text.lower()):
            self._buscar_y_enviar(where, text.lower())
        else:
            self._send(where, "Responde con el número de la línea (p. ej. «3») o con el final del id", html=False)

    def _resp_ficha(self, where, data: dict, text: str) -> None:
        found = self._find(data.get("tid") or "")
        if not found:
            self._send(where, "Ya no encuentro esa tarea en los tableros", html=False)
            return
        board, show = found
        task = show["task"]
        if not self._permitida(board, task):
            self._send(where, self._rechazo(board, task), html=False)
            return
        if _tm.es_pregunta(text):
            self._send(where, self.estado_tarea(board, show))
            return
        h, tid = self.hermes_for(board), task["id"]
        who = self.desk._who() if hasattr(self.desk, "_who") else "Oscar"
        if not h.comment(tid, f"Nota de {who} desde Telegram: {text[:1500]}", author=OSCAR_AUTHOR):
            self._send(where, f"No pude anotarlo en {tid}; detalle en el log", html=False)
            return
        rec = {"task_id": tid, "board": board, "lane": task.get("assignee") or "", "title": task.get("title") or "",
               "nota": text[:1500]}
        spec = [[{"text": "🔁 Relanzar con esto", "action": _tm.TR_RELANZAR},
                 {"text": "📝 Solo anotar", "action": _tm.TR_ANOTAR}]]
        self._send(where, f"📝 Anotado en {tid} como comentario de {who}. ¿Relanzo la tarea con esto?", html=False,
                   markup=self.desk.store.issue(rec, spec)[1])

    def _resp_fusion(self, where, data: dict, text: str) -> None:
        tid = data.get("tid")
        word = text.split()[0].lower() if text.split() else ""
        if TASK_ID_RE.match(word):
            other = self._find(word)
            found = [(word, *other)] if other else []
        else:
            found = [(i, b, {"task": t}) for i, b, t in self._buscar(word)] if _tm.SUFFIX_RE.match(word) else []
        if len(found) != 1:
            self._send(where, "Dime una sola tarea: su id completo o el final del id que no sea ambiguo"
                       if found else f"No encuentro ninguna tarea con «{word}»", html=False)
            return
        oid, oboard, oshow = found[0][0], found[0][1], found[0][2]
        mine = self._find(tid or "")
        if not mine or oid == tid:
            self._send(where, "Necesito dos tareas distintas", html=False)
            return
        if not (self._permitida(mine[0], mine[1]["task"]) and self._permitida(oboard, oshow["task"])):
            self._send(where, self._rechazo(oboard, oshow["task"]), html=False)
            return
        who = self.desk._who() if hasattr(self.desk, "_who") else "Oscar"
        for a_board, a, b in ((mine[0], tid, oid), (oboard, oid, tid)):  # como el aviso de solape: solo deja constancia
            self.hermes_for(a_board).comment(
                a, f"{who} marcó como duplicada con {b} (Fusionar desde la ficha). Pendiente de fusionar: decide "
                   "Oscar/Hermes; no se ha movido ni cerrado nada.", author=OSCAR_AUTHOR)
        self._send(where, f"🔀 {tid} y {oid}: anotado como duplicadas en las dos tarjetas; no se fusiona nada solo",
                   html=False)

    def estado_tarea(self, board: str, show: dict) -> str:
        """Respuesta (HTML) a «¿qué falta?» sobre una ficha: estado real de la tarjeta, sin consultar a ningún modelo."""
        e = html.escape
        task = show["task"]
        label = STATUS_TEXT.get(task.get("status"), (None, task.get("status") or "?"))[1]
        out = [f"💬 <b>{e(truncate(task.get('title'), 70))}</b> · <code>{e(task['id'])}</code>",
               f"Estado: {e(label)} · carril {e(str(task.get('assignee') or board))} · prioridad {_prio(task) or 0}"]
        reasons = [(ev.get("payload") or {}).get("reason") for ev in show.get("events") or ()
                   if ev.get("kind") == "blocked"]
        if task.get("status") in ("blocked", "triage") and reasons and reasons[-1]:
            out.append("Motivo: " + e(truncate(str(reasons[-1]), 300)))
        runs = [r.get("summary") for r in show.get("runs") or () if r.get("summary")]
        if runs:
            out.append("Última ejecución: " + e(truncate(str(runs[-1]), 300)))
        comments = [c for c in show.get("comments") or () if c.get("body")]
        if comments:
            out.append(f"Último comentario ({e(str(comments[-1].get('author') or '?'))}): "
                       + e(truncate(str(comments[-1]['body']), 300)))
        return "\n".join(out)[:TEXT_MAX]

    def tarea_step(self, action: str, rec: dict, button: dict, where: dict) -> bool:
        """Botones de la ficha y de la búsqueda (tareas_movil.TR_*). True = hecho; False = vuelven los botones."""
        say = lambda text: self.notifier.edit(where["chat_id"], where["message_id"], text, html=False)
        tid, board = rec.get("task_id"), rec.get("board") or ""
        if action == _tm.TR_PICK:
            cands = rec.get("candidates") or []
            idx = button.get("index")
            if not isinstance(idx, int) or not 0 <= idx < len(cands):
                return False
            found = self._find(cands[idx]["task_id"])
            if not found:
                say("Ya no encuentro esa tarea en los tableros")
                return True
            self._send_ficha((where["chat_id"], where["thread_id"]), *found)
            return True
        h = self.hermes_for(board)
        if action == _tm.TR_PRIO:
            found = self._find(tid)
            cur = _prio(found[1]["task"]) or 0 if found else 0
            new = min(PRIORITY_MAX, cur + _tm.PRIO_PASO)
            if not h.set_priority(tid, new):
                return False
            if found:  # el botón es de un solo uso: se repone para poder subir más
                markup = self.ficha_markup(found[0], {**found[1]["task"], "status": found[1]["task"].get("status")})
                if markup:
                    self.notifier.edit_markup(where["chat_id"], where["message_id"], markup)
            self._send((where["chat_id"], where["thread_id"]), f"✅ {tid} · prioridad {cur} → {new}", html=False)
            return True
        if action == _tm.TR_ARCHIVAR:
            if not h.archive(tid):
                return False
            h.comment(tid, f"Archivada por {self.desk._who()} desde la ficha de Telegram", author=OSCAR_AUTHOR)
            say(f"🗂 {tid} archivada")
            return True
        if action == _tm.TR_FUSION:
            sent = self._send((where["chat_id"], where["thread_id"]),
                              f"🔗 ¿Con qué tarea se fusiona {tid}? Responde a este mensaje con su id completo o con "
                              "el final del id.", html=False)
            if sent:
                self.mapa.put(where["chat_id"], sent.get("message_id"), _tm.KIND_FUSION, {"tid": tid}, self._now())
            return True
        if action == _tm.TR_ANOTAR:
            say(f"📝 Solo anotado en {tid}; no he tocado la tarea")
            return True
        if action == _tm.TR_RELANZAR:
            return self._relanzar(h, tid, say)
        return False

    def _relanzar(self, h, tid: str, say) -> bool:
        try:
            status = (h.show(tid).get("task") or {}).get("status")
        except Exception:
            status = None
        if status == "blocked":
            ok = h.unblock(tid)
        elif status == "triage":
            ok = bool(h.requeue_triage(tid))
        elif status in ("review", "done"):
            ok = h.reopen_review(tid)
        elif status in ("ready", "running", "todo"):
            say(f"📝 Anotado. {tid} ya está {'en marcha' if status == 'running' else 'en cola'}: el worker verá la nota")
            return True
        else:
            say(f"📝 Anotado en {tid}; en estado «{status or '?'}» no se puede relanzar desde aquí")
            return True
        if not ok:
            say(f"⚠️ Anotado, pero no pude relanzar {tid}; detalle en el log. Puedes volver a pulsar")
            return False
        say(f"🔁 {tid} relanzada con tu nota")
        return True

    def _consola_ficha(self, board: str, show: dict) -> tuple[str, list]:
        """Zona, espera y botones de consola de la ficha (consola.ficha); nunca rompe la ficha."""
        try:
            from . import choques, consola
            lane = self.lanes.get((show.get("task") or {}).get("assignee"))
            return consola.ficha(self.desk, board, show, lane, choques.Git() if lane is not None else None)
        except Exception as exc:
            log.info("ficha: sin datos de consola (%s)", exc)
            return "", []

    def _liberar_row(self, board: str, show: dict) -> list[dict] | None:
        """Fila «🔓 Liberar» (botonera.yaml) solo si la tarjeta espera a otra por «Esperar a…» (F1-14, fallo 7)."""
        task = show.get("task") or {}
        if task.get("status") in ("done", "archived") or not deps.declared_waits(show):
            return None
        rec = {"task_id": task["id"], "board": board, "lane": task.get("assignee") or "",
               "title": task.get("title") or ""}
        return self.desk.store.issue(rec, [[_bt.boton("liberar")]])[1]["inline_keyboard"][0]

    def _fusion_row(self, board: str, show: dict) -> list[dict] | None:
        """Fila «🔗 Fusionar con…» de la ficha (fusion.py); nunca rompe la ficha."""
        fusion = getattr(self.desk, "fusion", None)
        if not fusion:
            return None
        try:
            return fusion.boton_ficha(show["task"], board, self.desk.store.issue)
        except Exception as exc:
            log.info("ficha: botón Fusionar con… no añadido: %s", exc)
            return None

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
                reason = next(((e.get("payload") or {}).get("reason") or "" for e in reversed(show.get("events") or ())
                               if e.get("kind") == "blocked"), "")
                recomendada = _bt.diagnostico_reintentable(
                    kind=p.block_kind, reason=reason, commits=None if p.changed_files else 0)[1] if status == "blocked" else None
                markup = self.desk.markup(p.state, task=task, lane=lane, block_kind=p.block_kind,
                                          questions=p.questions, summary=p.summary, changed_files=p.changed_files,
                                          for_oscar=p.for_oscar, yes_no=p.yes_no, q_index=p.q_index,
                                          ficha=True, recomendada=recomendada)
                return text, markup
        state, label = STATUS_TEXT.get(status, (status or "?", status or "?"))
        lane_name = lane.name if lane else (task.get("assignee") or board)
        links = self._links_for(lane, tid) if lane else (
            [("🗂 Tarjeta", url)] if (url := card_url(self.base_url, board, tid)) else [])
        return (render(state, tid, task.get("title"), lane_name, label, links, body=task.get("body")),
                self.ficha_markup(board, task))
