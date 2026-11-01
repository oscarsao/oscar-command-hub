"""Tableros y avisos de CommandCenter: ticks, vigilancia, integrador, salud y silencios.

Métodos movidos tal cual desde commands.py (sin cambios de comportamiento).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from . import solapes
from .aviso import mention
from .notices import (truncate)
from .comandos_comun import (EXTRA_BOARDS, MAX_CARDS, URGENT_EVERY, log)

class TablerosMixin:
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
        from .resumen_peticiones import DailyRequestSummary
        path = self._state_file("peticiones_daily_state", "resumen_peticiones.json")
        if path is None or rc.due_date(datetime.fromtimestamp(self._now(), rc.MADRID), set()) is None:
            return None
        att = self.renotifier().attention
        chat, thread = (str(att["chat"]), str(att.get("thread") or "0")) if att else (self.owner_id, None)
        return DailyRequestSummary(
            self.peticion_items, lambda t: bool(self.notifier.send_to(chat, thread, t, html=False)), path,
            now=lambda: datetime.fromtimestamp(self._now(), rc.MADRID)).tick()

    # --- vigilante del equipo (t_04fe996e): tema «👥 Equipo» de Gestión ----------------------------------------------

    def vigilancia(self):
        """Vigilancia del tema 👥 Equipo (None sin `.state`, p. ej. en tests que no la inyectan)."""
        v = getattr(self, "_vigilancia", None)
        if v is not None:
            return v
        path, store = self._state_file("vigilancia_state", "vigilancia.json"), self.registro_peticiones()
        if path is None or store is None:
            return None
        import os

        from . import vigilancia as vg
        from .config import load_env, ohq_api_url
        from .sync_usuarios import ApiOhq
        cfg = vg.load_config()
        env = {**load_env(), **os.environ}
        base = ohq_api_url(env)  # .env manda; sin él, ohq.api_url de lanes.yaml (F1-02)
        api = ApiOhq(base, env["OHQ_API_KEY"]) if base and env.get("OHQ_API_KEY") else None
        if api is None:
            log.info("vigilancia: sin OHQ_API_URL/OHQ_API_KEY; solo vigilo el kanban de Hermes")

        def hermes_tasks() -> list:
            out = []
            for board in dict.fromkeys([l.board for l in self.lanes.values() if l.board] + list(EXTRA_BOARDS)):
                h = self.hermes_for(board)
                for st in vg.ESTADOS_ABIERTOS_HERMES:
                    try:
                        out += [dict(t, board=board) for t in h.list_status_all(st)]
                    except Exception as exc:
                        log.info("vigilancia: %s/%s no leído: %s", board, st, exc)
            return out

        botones = lambda: self._equipo_botones()  # noqa: E731 (se crea con la vigilancia ya montada)

        def send(text, spec, thread):
            markup = botones().markup(spec)
            return self.notifier.send_to(cfg.chat, thread, text, html=False,
                                         **({"reply_markup": markup} if markup else {}))

        def edit(chat, message_id, text, spec):
            return botones()._edit(chat, message_id, text, spec)

        self._vigilancia = vg.Vigilancia(
            estado=vg.Estado(path), registro=store, people=self.desk._people(),
            fetch_ohq=(lambda: api.vigilancia()) if api else (lambda: None), hermes_tasks=hermes_tasks,
            show_card=self._show_card, send=send, edit=edit, create_topic=getattr(self.notifier, "create_topic", None),
            cfg=cfg, now=self._now)
        return self._vigilancia

    def _equipo_botones(self):
        from .vigilancia_botones import EquipoBotones
        return EquipoBotones(self, self.vigilancia())

    def vigilancia_tick(self):
        """Una pasada del vigilante cada INTERVALO_S como mucho (lee OHQ y el kanban)."""
        from .vigilancia import INTERVALO_S
        now = self._now()
        if now - getattr(self, "_vigilancia_at", 0) < INTERVALO_S:
            return None
        self._vigilancia_at = now
        v = self.vigilancia()
        return v.tick() if v else None

    def equipo_choice(self, action: str, rec: dict, button: dict, where: dict) -> bool:
        return self.vigilancia() is not None and self._equipo_botones().on_button(action, rec, button, where)

    def equipo_reply(self, rec: dict, button: dict, where: dict, text: str) -> bool:
        return self.vigilancia() is not None and self._equipo_botones().on_reply(rec, button, where, text)

    def equipo_prompt(self, rec: dict, button: dict) -> str:
        return self._equipo_botones().prompt(rec, button) if self.vigilancia() else "Escribe tu respuesta:"

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

    def cmd_salud(self, where, args: str, brand: str | None) -> None:
        self._send(where, self._health() if self._health is not None else self.health_text())

    def cmd_ram(self, where, args: str, brand: str | None) -> None:
        """RAM bajo demanda; el monitor manda el mismo informe al DM cada 4 h (ya no hay aviso continuo)."""
        from . import health
        self._send(where, health.ram_report(), html=False)

    def cmd_avisos(self, where, args: str, brand: str | None) -> None:
        """/avisos [off [1h|3h|mañana|siempre]|on]: mismo estado que los botones del resumen de tarjetas listas."""
        from . import avisos_revision
        self._send(where, avisos_revision.comando(args), html=False)

    # --- avisos que el nivel «esencial» no envía, a demanda (nivel_avisos.COMANDO_DE) ---------------------------------

    def _lista(self, where, titulo: str, vacio: str, lineas: list[str]) -> None:
        cuerpo = "\n".join(f"• {truncate(ln, 220)}" for ln in lineas[:MAX_CARDS])
        extra = f"\n… y {len(lineas) - MAX_CARDS} más" if len(lineas) > MAX_CARDS else ""
        self._send(where, f"{titulo} ({len(lineas)})\n{cuerpo}{extra}" if lineas else vacio, html=False)

    def cmd_choques(self, where, args: str, brand: str | None) -> None:
        """Choques (git) y solapes (alcance) vivos ahora: lo que el nivel esencial no manda como aviso."""
        from . import choques
        lineas: list[str] = []
        try:
            t = getattr(self.desk, "choques", None)
            aj = choques.load_ajustes(t.settings_path if t else None)
            sil = frozenset(choques.load_silenciadas(t.silenciadas_path if t else None))
            ign = choques.load_ignorados(t.ignorados_path if t else None, dias=aj.recordar_dias)
            for c in choques.detectar(self.lanes, self.hermes_for, t.git if t else choques.Git(), aj, silenciadas=sil):
                if choques.par_uid(c) not in ign and not (c.clase == "compatible" and not aj.avisar_compatibles):
                    lineas.append("⛔ " + " ".join(choques.aviso(c, aj).split()))
            t = getattr(self.desk, "solapes", None)
            sa = solapes.load_ajustes(t.settings_path if t else None)
            for g in solapes.detectar(self.lanes, self.hermes_for, sa,
                                      solapes.load_descartes(t.descartes_path if t else None)):
                lineas.append("🔀 " + " ".join(solapes.aviso(g, sa).split()))
        except Exception as exc:
            log.warning("/choques falló: %s", exc)
            self._send(where, "No he podido calcular los choques ahora; prueba en un minuto.", html=False)
            return
        self._lista(where, "⛔ Choques y solapes vivos", "✅ Sin choques ni solapes vivos.", lineas)

    def cmd_paradas(self, where, args: str, brand: str | None) -> None:
        from .paradas import Paradas
        now = self._now()
        found = Paradas(self.lanes, self.hermes_for, self._now).encontrar()
        self._lista(where, "⏳ Tarjetas paradas", "✅ Ninguna tarjeta de carril parada.",
                    [f"{p.linea(now)} · carril {p.lane}" for p in found])

    def cmd_revision(self, where, args: str, brand: str | None) -> None:
        from . import avisos_revision
        av = avisos_revision.ACTIVO
        self._send(where, av.pendientes_texto() if av else "Los avisos agrupados no están activos en este runner.",
                   html=False)

    def cmd_limpieza(self, where, args: str, brand: str | None) -> None:
        lim = getattr(self.desk, "limpieza", None)
        if lim is None:
            self._send(where, "La limpieza de tarjetas no está activa en este runner.", html=False)
            return
        try:
            found = lim.candidatas()
        except Exception as exc:
            log.warning("/limpieza falló: %s", exc)
            self._send(where, "No he podido calcular la limpieza ahora; prueba en un minuto.", html=False)
            return
        self._lista(where, "🧹 Tarjetas que propongo cerrar o archivar", "✅ Nada que limpiar ahora.",
                    [c.linea() for c in found])

    def cmd_silenciar(self, where, args: str, brand: str | None) -> None:
        """/silenciar <clave> <horas>: el monitor deja de avisar de esa clave (y las que empiezan por ella)."""
        from . import silencios
        parts = args.split()
        if len(parts) != 2:
            vivos = silencios.activos()
            lines = ["Uso: /silenciar <clave> <horas>   (0 horas quita el silencio)"]
            lines += [f"🔕 {k} hasta {datetime.fromtimestamp(v):%d-%m %H:%M}" for k, v in sorted(vivos.items())]
            recientes = silencios.claves_recientes()
            if recientes:
                lines.append("Claves avisadas últimamente: " + ", ".join(recientes))
            self._send(where, "\n".join(lines), html=False)
            return
        try:
            horas = float(parts[1].replace(",", "."))
            hasta = silencios.silenciar(parts[0], horas)
        except ValueError as exc:
            self._send(where, str(exc) if "clave" in str(exc) or "horas" in str(exc) else
                       "Las horas tienen que ser un número, por ejemplo: /silenciar ram 12", html=False)
            return
        self._send(where, f"🔕 «{parts[0].lower()}» silenciada hasta {datetime.fromtimestamp(hasta):%d-%m %H:%M}"
                   if horas else f"🔔 «{parts[0].lower()}» vuelve a avisar", html=False)

    def health_text(self) -> str:
        """Todo local (health.py): .state, `hermes kanban list`, ficheros del monitor y git sin fetch."""
        from . import ciego, drain, health
        from .paradas import Paradas
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
            now=now, blind=lambda: ciego.resumen(ciego.FILE, now),
            paradas=lambda: Paradas(self.lanes, self.hermes_for, self._now).resumen())

