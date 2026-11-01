"""Crear tarjetas desde el chat en CommandCenter: /hazlo, /pedir, sesiones de correcciones, /flujo y triaje.

Métodos movidos tal cual desde commands.py (sin cambios de comportamiento).
"""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

from . import clasificador
from . import tipo_peticion, triaje
from .decisions import (FLUJO, HAZLO, TRIAJE)
from .notices import (BRANDS, OSCAR_ASSIGNEE, truncate)
from .comandos_comun import (BRAND_ARGS, EXTRA_BOARDS, HAZLO_MINUTES, HAZLO_PRIORITY, HAZLO_TITLE_MAX,
                              LANE_HINTS, _plural, log,
                              panel_url, scope_brand)

class PeticionesMixin:
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
                f"{HAZLO_MINUTES} min. Te aviso al terminar." + self._aviso_clasificador(lane, title_body=(text, body)))

    def _aviso_clasificador(self, lane, *, title_body: tuple[str, str]) -> str:
        """Aviso (HTML) de que un carril no podrá con la tarjeta recién creada; '' si puede. Ver clasificador.py."""
        title, body = title_body
        verdict = clasificador.evaluar(lane, {"title": title, "body": body}, lanes=self.lanes)
        return html.escape(clasificador.aviso_creacion(verdict))

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
        aviso = "" if st["tipo"] == "consulta" or lane not in self.lanes else self._aviso_clasificador(
            self.lanes[lane], title_body=(title, body))
        return (f"✅ Creada <code>{html.escape(tid)}</code> en {html.escape(assignee)} · "
                f"{html.escape(st['tipo'])}" + (f" {html.escape(st['urgencia'])}" if st.get("urgencia") else "")
                + (". Te respondo en un comentario, sin rama." if st["tipo"] == "consulta" else ". Te aviso al terminar.")
                + aviso)

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

    # --- entrada inteligente (triaje.py) ---------------------------------------------------------------

    @staticmethod
    def _tbtn(text: str, index: int) -> dict:
        return {"text": text, "action": TRIAJE, "index": index}

    def triaje_route(self, where, text: str, cat: str, cfg: dict) -> None:
        """Qué hace el bot con un texto ya clasificado. Aquí no se crea ninguna tarjeta: solo se pregunta o se propone."""
        t = cfg["textos"]
        brand = scope_brand(where[0], where[1], self.lanes, self.generic_origins)
        base = {"task_id": "triaje", "board": "", "lane": "", "kind": "triaje", "text": text,
                "origin": [where[0], where[1]], "answers": [], "step": 0}
        if cat == triaje.GRANDE:
            rec = dict(base, stage="grande")
            spec = [[self._tbtn(t["boton_workflow"], -3)], [self._tbtn(t["boton_pequena"], -4)],
                    [self._tbtn(t["boton_cancelar"], -1)]]
            self._send(where, t["propone_workflow"] + "\n\n" + truncate(text, 300), html=False,
                       markup=self.desk.store.issue(rec, spec)[1])
        elif cat == triaje.NO_CARRIL:
            rec = dict(base, stage="integrador")
            spec = [[self._tbtn(t["boton_integrador"], -7), self._tbtn(t["boton_cancelar"], -1)]]
            self._send(where, t["no_carril"] + "\n\n" + truncate(text, 300), html=False,
                       markup=self.desk.store.issue(rec, spec)[1])
        else:
            self._triaje_small(where, base, brand, cfg)

    def _triaje_cards(self, boards: list[str]) -> list[dict]:
        """Tarjetas abiertas y hechas de los tableros dados (para detectar duplicados). Un fallo de lectura = sin dato."""
        out: list[dict] = []
        for board in boards:
            try:
                h = self.hermes_for(board)
                for status in ("triage", "todo", "ready", "running", "blocked", "review", "done"):
                    out += list(h.list_status_all(status))
            except Exception as exc:
                log.warning("triaje: no pude leer el tablero %s: %s", board, type(exc).__name__)
        return out

    def _triaje_small(self, where, rec: dict, brand: str | None, cfg: dict) -> None:
        text, t = rec["text"], cfg["textos"]
        lane = self.infer_lane(text, brand)
        names = [lane] if lane else ([n for n in self._implement_lanes() if not brand or BRANDS.get(n) == brand]
                                     or self._implement_lanes())
        qs = triaje.preguntas(cfg, lanes=None if lane else names)
        rec = dict(rec, lane_name=lane or (names[0] if len(names) == 1 else None), qs=qs, lanes=names)
        boards = sorted({self.lanes[n].board or "default" for n in names if n in self.lanes})
        dup = triaje.buscar_duplicado(text, self._triaje_cards(boards), cfg, ahora=self._now())
        if dup:
            rec.update(stage="dup", dup=dup)
            spec = [[self._tbtn(t["boton_anadir"], -5)], [self._tbtn(t["boton_nueva"], -6)],
                    [self._tbtn(t["boton_cancelar"], -1)]]
            self._send(where, f"Se parece a {dup['id']} ({dup['status']}): {truncate(dup['title'], 100)}. ¿La amplío "
                       "en vez de crear otra?", html=False, markup=self.desk.store.issue(rec, spec)[1])
            return
        self._triaje_ask(where, dict(rec, stage="pregunta"), cfg)

    def _triaje_ask(self, where, rec: dict, cfg: dict) -> None:
        """Envía la pregunta `step` con sus botones (la recomendada, primero y con ⭐) o crea la tarjeta si no quedan."""
        t, qs, step = cfg["textos"], rec["qs"], rec["step"]
        if step >= len(qs):
            self._send(where, self._triaje_create(rec, cfg, ya=False), html=True)
            return
        q = qs[step]
        spec = [[self._tbtn(("⭐ " if i == 0 else "") + o, i)] for i, o in enumerate(q["opciones"])]
        spec.append([self._tbtn(t["boton_ya"], -2), self._tbtn(t["boton_cancelar"], -1)])
        head = t["pregunta_cabecera"].format(n=len(qs)) + "\n\n" if step == 0 else ""
        self._send(where, f"{head}{step + 1}/{len(qs)} · {q['texto']}", html=False,
                   markup=self.desk.store.issue(rec, spec)[1])

    def _triaje_create(self, rec: dict, cfg: dict, *, ya: bool) -> str:
        """Crea la tarjeta con las respuestas en el cuerpo y devuelve el aviso (HTML). Clave idempotente por texto/hueco."""
        answers = [tuple(a) for a in rec.get("answers") or []]
        lane_name = rec.get("lane_name") or next((a for q, a in answers if q == "¿En qué carril lo pongo?"), None)
        if lane_name not in self.lanes:
            return "⚠️ No sé en qué carril ponerla; usa /hazlo o dime la marca."
        origin = tuple(rec.get("origin") or ()) or None
        body = triaje.card_body(rec["text"], [a for a in answers if a[0] != "¿En qué carril lo pongo?"], origin, ya=ya)
        prio = next((v for q, a in answers for k, v in (cfg.get("prioridades") or {}).items()
                     if q == "¿Cuánto urge?" and a.startswith(k)), None)
        chat, thread = origin or ("", "")
        key = "triaje-" + hashlib.sha1(f"{lane_name}|{chat}|{thread}|{int(self._now() // 600)}|{rec['text']}"
                                      .encode()).hexdigest()[:16]
        tid = self.hermes_for(self.lanes[lane_name].board).create(
            truncate(rec["text"].splitlines()[0], HAZLO_TITLE_MAX), body, lane_name, priority=prio, key=key,
            created_by="oscar")
        return f"✅ Creada <code>{html.escape(tid)}</code> en {html.escape(lane_name)}. Te aviso al terminar."

    def triaje_choice(self, rec: dict, button: dict, where: dict) -> bool:
        """Botones de la entrada inteligente. True = terminado (el teclado queda gastado); False = fallo (vuelven)."""
        cfg, idx, stage = self.triaje_config(), button.get("index"), rec.get("stage")
        chat, mid = where["chat_id"], where["message_id"]
        dest = (where["chat_id"], where["thread_id"])
        edit = lambda s, h=False: self.notifier.edit(chat, mid, s, html=h)
        if idx == -1:
            edit("🚫 Cancelado, no he creado nada")
            return True
        try:
            if idx == -2:  # Créala ya: sin más preguntas, con lo contestado hasta ahora
                if not rec.get("lane_name") and len(rec.get("lanes") or []) > 1:
                    edit("⚠️ Dime en qué carril va (usa /hazlo con la marca).")
                    return True
                edit(self._triaje_create(rec, cfg, ya=True), True)
                return True
            if idx == -3:  # como workflow
                edit("🧩 Preparando el plan…")
                self.cmd_workflow((dest[0], dest[1]), rec["text"], None)
                return True
            if idx == -4:  # una sola tarjeta en vez de workflow
                edit("📝 De acuerdo, una sola tarjeta.")
                self._triaje_small(dest, dict(rec, stage="pregunta", step=0, answers=[]),
                                   scope_brand(dest[0], dest[1], self.lanes, self.generic_origins), cfg)
                return True
            if idx == -5:  # añadir a la existente
                dup = rec.get("dup") or {}
                ok = self.hermes_for(self.lanes[rec["lane_name"]].board if rec.get("lane_name") in self.lanes
                                     else "default").comment(dup.get("id", ""), "Ampliación pedida desde el DM:\n"
                                                             + rec["text"], "oscar")
                edit(f"➕ Añadido como comentario a {dup.get('id')}" if ok else f"⚠️ No pude comentar {dup.get('id')}")
                return bool(ok)
            if idx == -6:  # crear nueva igualmente
                edit("🆕 Sigo con una tarjeta nueva.")
                self._triaje_ask(dest, dict(rec, stage="pregunta"), cfg)
                return True
            if idx == -7:  # integrador
                tid = self.hermes_for("default").create(
                    truncate("[INTEGRADOR] " + rec["text"].splitlines()[0], HAZLO_TITLE_MAX),
                    triaje.card_body(rec["text"], [], tuple(rec.get("origin") or ()) or None),
                    OSCAR_ASSIGNEE, key="triaje-int-" + hashlib.sha1(
                        f"{rec['text']}|{int(self._now() // 600)}".encode()).hexdigest()[:16], created_by="oscar")
                edit(f"📨 Pasado al integrador (a tu nombre, sin carril): {tid}")
                return True
            qs = rec.get("qs") or []
            step = rec.get("step", 0)
            if stage != "pregunta" or not isinstance(idx, int) or not 0 <= step < len(qs) \
                    or not 0 <= idx < len(qs[step]["opciones"]):
                return False
            q = qs[step]
            answer = q["opciones"][idx]
            rec = dict(rec, answers=list(rec.get("answers") or []) + [[q["texto"], answer]], step=step + 1)
            if q.get("id") == "carril" and answer in self.lanes:
                rec["lane_name"] = answer
            edit(f"✔ {q['texto']} → {answer}")
            self._triaje_ask(dest, rec, cfg)
            return True
        except Exception as exc:
            log.warning("triaje: botón falló: %s", type(exc).__name__)
            return False

    # --- /workflow ------------------------------------------------------------------------------------

