"""Workflows, ajustes, /estado, /falta y texto libre de CommandCenter.

Métodos movidos tal cual desde commands.py (sin cambios de comportamiento).
"""
from __future__ import annotations

import hashlib
import html
import re

from . import ayuda as _ayuda
from . import triaje, workflows
from .avance_workflows import make_create_card
from .decisions import (DEPLOY_ASK, FALTA, WORKFLOW)
from .notices import (BRANDS, TEXT_MAX, truncate)
from .comandos_comun import (BRAND_ARGS, COMMANDS,
                              HAZLO_TITLE_MAX,
                              SOLO_TODAS_LAS_MARCAS,
                              log,
                              scope_brand)

class EstadoMixin:
    @property
    def workflow_store(self) -> workflows.Store:
        if self._workflow_store is None:
            self._workflow_store = workflows.Store()
        return self._workflow_store

    def cmd_workflow(self, where, args: str, brand: str | None) -> None:
        """/workflow <texto>: plan (tarjetas enlazadas), coste estimado y tope; NO crea nada hasta [Aprobar plan y gasto]."""
        text = args.strip()
        if not text:
            self._send(where, "Uso: /workflow <trabajo largo>. Te enseño el plan y el coste; no se crea nada hasta que "
                       "apruebes.", html=False)
            return
        from . import flujo
        cfg = self.triaje_config()
        lanes = {n: f"{BRANDS.get(n, 'Sistema')} (tablero {self.lanes[n].board or 'default'})"
                 for n in self._implement_lanes()}
        self._send(where, "Claude está preparando el plan…", html=False)
        cards = (self._flow_planner or flujo.propose)(text, lanes)
        if not cards:
            self._send(where, "No pude proponer un plan válido. Reformula la petición o usa /hazlo", html=False)
            return
        wid = "wf_" + hashlib.sha1(f"{where[0]}|{where[1]}|{int(self._now() // 600)}|{text}".encode()).hexdigest()[:10]
        titulo = truncate(text.splitlines()[0], HAZLO_TITLE_MAX)
        req = (self.peticiones.crear(None, titulo, "workflow") if self.peticiones else "req_" + wid[3:])
        by_n = {c["n"]: c["titulo"] for c in cards}
        plan = [{"titulo": c["titulo"], "carril": c["carril"], "depende": [by_n[p] for p in c["padres"]],
                 "codigo": True, "cuerpo": c.get("cuerpo", ""), "limites": c.get("limites", "")} for c in cards]
        riesgos = [f"«{c['titulo']}» pide tu confirmación (merge/despliegue/envío)" for c in cards if c["confirmar"]]
        wf = workflows.propose(self.workflow_store, wid=wid, titulo=titulo, req=req, preguntas=[], tarjetas=plan,
                               riesgos=riesgos, prueba="Cada tarjeta pasa los tests de su carril y se revisa antes de "
                               "integrar", coste_estimado_eur=round(len(plan) * cfg["coste_por_tarjeta_eur"], 2))
        rec = {"task_id": "workflow", "board": "", "lane": "", "kind": "workflow", "wid": wid}
        spec = [[{"text": "✅ Aprobar plan y gasto", "action": WORKFLOW, "index": 1},
                 {"text": "🚫 Cancelar", "action": WORKFLOW, "index": 0}]]
        self._send(where, workflows.render_plan(wf)[0], html=False, markup=self.desk.store.issue(rec, spec)[1])

    def workflow_choice(self, rec: dict, button: dict, where: dict) -> bool:
        """Botón del plan. Solo [Aprobar] crea tarjetas (la primera tanda: las que no dependen de otra)."""
        wid, store = rec.get("wid") or "", self.workflow_store
        if button.get("index") not in (1, 2, 3):
            if store.get(wid):
                workflows.parar(store, wid)
            self.notifier.edit(where["chat_id"], where["message_id"], "🚫 Plan descartado, no he creado nada", html=False)
            return True
        wf = store.get(wid)
        if not wf:
            return False
        if button.get("index") in (2, 3):  # botones del aviso de gasto: [➕ Ampliar] / [⛔ Parar]
            wf = workflows.ampliar(store, wid) if button["index"] == 2 else workflows.parar(store, wid)
            self.notifier.edit(where["chat_id"], where["message_id"],
                               f"➕ Tope ampliado a {wf['tope_eur']:g} €: el workflow sigue." if button["index"] == 2
                               else "⛔ Workflow parado: no se crean más tarjetas.", html=False)
            return True
        wf["origen"] = {"chat": where["chat_id"], "thread": where["thread_id"]}  # los avisos de avance vuelven aquí
        store.put(wf)
        create_card = make_create_card(wf, self.lanes, self.hermes_for, lambda t, n: truncate(t, n))

        try:
            workflows.approve(store, wid)
            new = workflows.launch(store, wid, create_card)
        except Exception as exc:
            log.warning("/workflow %s: no se pudo lanzar: %s", wid, type(exc).__name__)
            return False
        self.notifier.edit(where["chat_id"], where["message_id"],
                           f"✅ Plan aprobado ({wf['req']}) · creadas {len(new)} tarjeta(s): {', '.join(new)}. Las que "
                           "dependen de otras quedan en el plan hasta que las anteriores terminen.", html=False)
        return True

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
                                 "[🔀 Integrar] en su ficha" + (" · PR del hub" if name == "claude-hub" else ""))
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
        return self._asker is not None or self._classifier is not None

    def triaje_config(self) -> dict:
        return self._triaje_cfg if self._triaje_cfg is not None else triaje.load_config()

    def _sugerir(self, where, msg: dict, text: str) -> None:
        """Si el texto pide algo que un comando ya hace, una línea «💡 Eso ya lo hace /x» (reglas de sugerencias.yaml,
        sin LLM). Un aviso por comando y chat cada `enfriamiento_s`; un operador acotado por marca no recibe sugerencias
        de comandos que no podría usar. Nunca rompe el texto libre."""
        try:
            cfg = _ayuda.load_config()
            actor = (msg.get("from") or {}).get("id")
            restringido = getattr(self.desk, "restringido", None)
            acotado = bool(restringido and restringido(actor))
            sug = _ayuda.sugerir(text, cfg, permitido=lambda c: c in dict(COMMANDS)
                                 and not (acotado and c in SOLO_TODAS_LAS_MARCAS))
            if not sug:
                return
            ahora, clave = self._now(), (str(where[0]), sug["comando"])
            vistos = getattr(self, "_sugeridos", None)
            if vistos is None:
                vistos = self._sugeridos = {}
            if ahora - vistos.get(clave, -1e12) < cfg["enfriamiento_s"]:
                return
            vistos[clave] = ahora
            self._send(where, _ayuda.texto_sugerencia(sug, dict(COMMANDS).get(sug["comando"])), html=False)
        except Exception as exc:
            log.warning("sugerencia de comando falló: %s", type(exc).__name__)

    def free_text(self, msg: dict) -> bool:
        """Texto libre del dueño en su DM (ni comando, ni /sesion abierta). Con el triaje activo se clasifica antes de
        nada: CONSULTA responde con el estado real (sin tarjeta); lo demás pregunta/propone y solo crea tras tus botones.
        Sin triaje solo informa. False = no actúa (sin asker ni triaje: queda la ayuda de siempre)."""
        text = (msg.get("text") or "").strip()
        cfg = self.triaje_config()
        triaging = self._classifier is not None and cfg["activo"]
        if not text or not (self._asker or triaging):
            return False
        where = self._where(msg)
        self._sugerir(where, msg, text)
        if not self._ask_lock.acquire(blocking=False):
            self._send(where, "Sigo con tu pregunta anterior; dame un momento.", html=False)
            return True
        try:
            if triaging:
                try:
                    cat = self._classifier(text, cfg)
                except Exception as exc:  # nunca se pierde una petición: ante la duda, trabajo pequeño
                    log.warning("triaje: clasificador falló: %s", type(exc).__name__)
                    cat = triaje.PEQUENA
                if cat != triaje.CONSULTA:
                    self.triaje_route(where, text, cat if cat in triaje.CATEGORIAS else triaje.PEQUENA, cfg)
                    return True
                if not self._asker:
                    self._send(where, cfg["textos"]["consulta_sin_dato"], html=False)
                    return True
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
