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
    /pendiente           «Lo tuyo» en UN mensaje agrupado por prioridad: preguntas, PR por aprobar, cola del integrador,
                         PR de sesiones, tarjetas tuyas y pasos del integrador, con un botón por línea que abre su
                         ficha (lo_tuyo.py; `lanes.py pendiente` es la misma lista para Hermes)
    /personal            propone mover tus tarjetas personales (no son decisiones de carril) al board `personal`;
                         no mueve nada hasta pulsar [✅ Mover] (personal.py, personal.yaml)
    /agrupar [proyecto|todo]  propone grupos de tarjetas abiertas (duplicadas, mismo fichero, misma decisión, lote) con
                         [🔗 Fusionar] [⛓ Encadenar] [📦 Mismo lote] [✋ Dejar]; nada se aplica sin botón (agrupar.py)
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
    /silenciar <clave> <horas>  calla una alerta repetida del monitor (0 horas = quitar el silencio); sin argumentos
                         lista los silencios y las claves recientes
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
import threading
import time
from typing import Callable

from . import ayuda as _ayuda
from . import bandeja as _bandeja
from . import titulos, workflows
from .card_options import PREPARED_AUTHOR, PREPARED_PREFIX
from .decisions import (OWNER_TELEGRAM_ID)
from .deps import (ShowCache)
from .notices import (BOARD_BRANDS, BRANDS)
from .comandos_aprobaciones import AprobacionesMixin
from .comandos_estado import EstadoMixin
from .comandos_pendientes import PendientesMixin
from .comandos_peticiones import PeticionesMixin
from .comandos_tableros import TablerosMixin
from .comandos_tarjetas import TarjetasMixin
# Los nombres públicos de siempre se reexportan desde comandos_comun (los usan decisions, runner y los tests).
from .comandos_comun import (BRAND_ARGS, BUSCA_ESTADOS, CARD_TITLE_MAX, COLA_PER_LANE, COMMAND_SCOPES, COMMANDS,  # noqa: F401
                             EXTRA_BOARDS, FICHAS_PAUSA, HAZLO_MINUTES, HAZLO_PRIORITY, HAZLO_TITLE_MAX,
                             HELP_ALIASES, HERMES_BOT, HERMES_COMMANDS, LANE_HINTS, MAX_CARDS, MOMENTO, NOT_OWNER,
                             PREPARE_MAX, PRIORITY_MAX, PRIORITY_STEP, SLOW_COMMANDS, SOLO_TODAS_LAS_MARCAS,
                             STATUS_TEXT, TASK_ID_RE, TOP_DECISIONS, URGENT_EVERY, VIEW_STATUSES, _CMD_RE, _elapsed,
                             _plural, _prio, _progress_label, command_target, fully_recommended, group_pending, log,
                             panel_url, parse_command, pending_of, question_key, register_commands, scope_brand)


class CommandCenter(AprobacionesMixin, TablerosMixin, PendientesMixin, PeticionesMixin, EstadoMixin, TarjetasMixin):
    def __init__(self, notifier, desk, *, lanes: dict, hermes_for: Callable[[str], object], messages, links=None,
                 generic_origins=(), bot_username: str | None = None, base_url: str | None = None,
                 owner_id: str | None = None, brief: Callable[[str | None], str] | None = None,
                 renotifier=None, now: Callable[[], float] = time.time,
                 health: Callable[[], str] | None = None, preparer: Callable[[dict], str | None] | None = None,
                 sesiones=None, suggester: Callable[[dict], dict | None] | None = None,
                 flow_planner: Callable[[str, dict], list[dict]] | None = None,
                 bandeja: _bandeja.Bandeja | None = None, asker: Callable[[str, str], str | None] | None = None,
                 classifier: Callable[[str, dict], str] | None = None, triaje_cfg: dict | None = None,
                 workflow_store: workflows.Store | None = None):
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
        self._classifier = classifier  # entrada inteligente: (texto, cfg) -> categoría de triaje.py (None = triaje apagado)
        self._triaje_cfg = triaje_cfg  # triaje.yaml ya cargado (None = se lee en cada texto: editable en caliente)
        self._workflow_store = workflow_store  # /workflow: workflows.Store (None = .state/workflows.json)
        self._health = health  # /salud: texto HTML ya montado (tests); None = health.build con lo real
        self._ctx = threading.local()  # actor del comando en curso (por hilo)
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
        self._ctx.actor = actor  # quién pidió el comando (las ofertas de /hoy se filtran por sus marcas)
        self._ctx.momento = self._momento(where) if cmd in SLOW_COMMANDS else None
        try:
            with actuando(actor) if actuando else contextlib.nullcontext():
                if cmd in HELP_ALIASES:
                    self._send(where, self.help_text(), html=False)
                else:
                    getattr(self, f"cmd_{cmd}")(where, args, brand)
        except Exception as exc:
            log.warning("/%s falló: %s", cmd, exc)
            self._send(where, f"No pude completar /{cmd}; detalle en el log", html=False)
        finally:
            sobra, self._ctx.momento = getattr(self._ctx, "momento", None), None
            if sobra:  # el comando no mandó nada: que el «Un momento…» no se quede colgado
                self.notifier.edit(sobra["chat_id"], sobra["message_id"], "Listo, sin nada que mostrar.", html=False)
        return True

    def _momento(self, where) -> dict | None:
        """Manda «Un momento…» al instante (el comando tarda y Oscar lo repetía); `_send` lo edita con el resultado."""
        try:
            sent = self.notifier.send_to(where[0], where[1], MOMENTO, html=False)
        except Exception as exc:
            log.warning("no pude enviar el «un momento»: %s", exc)
            return None
        return sent if sent and sent.get("message_id") else None

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
        if cmd in ("al_dia", "aldia"):  # un proyecto = su marca; "todos" abarca todas las marcas
            if "prod" in args.lower().split():  # producción: solo Oscar o quien tenga "*"
                return desk.aviso_marca(actor, None)
            al_dia = self._al_dia()
            clave = next((t for t in args.lower().split() if t in al_dia.proyectos()), None)
            return check(BRANDS.get(f"claude-{clave}")) if clave else desk.aviso_marca(actor, None)
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
        momento = getattr(self._ctx, "momento", None)
        if momento and momento["chat_id"] == str(where[0]):  # primer mensaje del comando: reemplaza el «Un momento…»
            self._ctx.momento = None
            if self.notifier.edit(momento["chat_id"], momento["message_id"], text, html=html,
                                  **({"reply_markup": markup} if markup else {})):
                return momento
        return self.notifier.send_to(where[0], where[1], text, html=html,
                                     **({"reply_markup": markup} if markup else {}))

    @staticmethod
    def help_text() -> str:
        return (_ayuda.texto_ayuda(COMMANDS, SOLO_TODAS_LAS_MARCAS, _ayuda.load_config()["docs_url"])
                + "\n\nDónde contesta cada bot:"
                + "\n· Trabajos (yo): a los /comandos y a las respuestas (reply) a mis mensajes. En grupo no leo el "
                  "texto suelto."
                + "\n· Hermes (@oscarsaoBot): conversa sin mención en Gestión (temas 5, 230, 231 y 1903), Marketing "
                  "(tema 406) y Desarrollo (tema 71); en el resto de temas, menciónalo."
                + "\n· Un /comando de Hermes (/new, /status…) escríbelo como /comando@oscarsaoBot; los míos, como "
                  "/comando@pildora_carriles_bot."
                + "\n\n/hazlo crea la tarea ya (prioridad alta, tope 15 min). /desplegar pide dos toques (🚀 y ✅ Sí); "
                  "/promover, doble confirmación."
                + "\nSi me escribes en texto libre (solo en privado): una pregunta se responde con datos reales; un "
                  "trabajo pequeño lleva preguntas con botones y se crea; uno largo propone /workflow; producción, "
                  "SQL u otro repo va al integrador.")

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

