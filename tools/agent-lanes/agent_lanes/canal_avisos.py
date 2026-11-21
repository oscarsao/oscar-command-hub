"""Canal de cada aviso: DECISIÓN, INFORMATIVO o URGENTE (decisión de Oscar 25-10, «Tema 🎯 Decisiones en Gestión»).

Un solo sitio que dice a dónde va cada tipo de mensaje del bot de carriles (borrador «avisos accionables», tabla §1):

- DECISIÓN: lo que solo Oscar, María o Ángel pueden resolver. Con `telegram.destinos.decisiones` en lanes.yaml va AL
  MOMENTO, con botones, al tema «🎯 Decisiones» del grupo Gestión, y al DM de Oscar solo una línea con enlace. Al
  decidir (botón de quien sea), el mensaje del tema pasa a «✅ <acción> por <persona>» sin botones, y un mensaje fijado
  del tema dice «N decisiones pendientes» (se edita, no se crea uno nuevo).
- URGENTE: caída, producción, seguridad: al DM al momento, como hoy.
- INFORMATIVO: por ahora, como hoy (el resumen diario es el paso siguiente).

Sin `destinos.decisiones` todo sale exactamente como antes (DM de Oscar / copia espejo al DM).

Siembra (F1-03, FL-02/FL-12): al arrancar y cada día a las 9:00 (Madrid) el tema recibe el backlog que calcula
`lo_tuyo.py` (lo mismo que `lanes.py pendiente`): un mensaje con botones por cosa, 1 s entre mensajes y sin repetir lo
que ya está abierto (por tipo+tarjeta, ni lo que ya tiene un aviso vivo de su tarea). Cada `REVISAR_SEMILLAS` se
recalcula para cerrar lo que ya no está pendiente; las semillas no caducan por su teclado (abrir la ficha no decide).

El contador no suma ni resta: se deriva del estado. Una decisión del tema sigue abierta mientras el teclado vigente de
ese mensaje esté vivo en el CallbackStore (respuesta por Hermes, texto libre, aviso nuevo de la misma tarea o un fallo
que devuelve los botones quedan cubiertos solos), así que un reinicio del runner rehace el mismo número.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import proc as _proc
from .integration import PinnedSummary, age_label, topic_link
from .nivel_avisos import activo
from .resumen_carriles import TIPOS_RESUMEN
from .resumen_carriles import anotar as anotar_resumen
from .notices import markup_token, truncate
from .telegram import resolve_target

log = logging.getLogger("agent_lanes")

DECISION, INFO, URGENTE = "decision", "info", "urgente"

# Tipo de mensaje -> clase (borrador §1, columna «Clase»; las filas que el borrador deja a medias, comentadas).
CLASES: dict[str, str] = {
    "choque": DECISION,          # 1  choques.yaml ya solo compara tarjetas activas: tarjeta↔tarjeta = «¿cuál primero?»
    "solape": DECISION,          # 2  dos tarjetas activas tocan lo mismo
    "needs_input": DECISION,     # 3  pregunta del worker
    "blocked": DECISION,         # 4  solo si lleva teclado de decisión (Reintentar/Aparcar…); sin botones no es decisión
    "bloqueo_urgente": URGENTE,  # 5  UrgentBlockWatcher (prioridad ≥ 8): sin cambios, al momento
    "aprobar": DECISION,         # 6  ficha «Aprobar» (tarjeta done revisada)
    "revision": INFO,            # 7  resumen agrupado «listas para revisar»: sus botones son Ver/silenciar; las
                                 #    decisiones son las fichas «Aprobar» (6), que ya entran aquí
    "integrar": DECISION,        # 8  lote listo / listo para integrar / desplegar
    "pr_conflicto": INFO,        # 9  vive en el mensaje fijado de Integración
    "paradas": INFO,             # 10 tarjetas paradas > 24 h: al resumen diario (§2.b)
    "limpieza": INFO,            # 11 el borrador la marca ACC «baja urgencia» en §1 pero la manda al resumen en §2.e;
    "reconcile": INFO,           # 12 manda §2.e (regla de reparto). Hasta el resumen diario siguen al DM como hoy
    "board_guard": INFO,         # 13 guarda de tablero
    "al_dia": INFO,              # 14 a demanda
    "equipo": DECISION,          # 15 solicitudes del equipo (hoy en su tema «👥 Equipo»; no se mueven)
    "workflow": DECISION,        # 16 aprobar plan / tope de gasto (hoy donde se pidió; no se mueven)
    "hazlo": DECISION,           # 17
    "recordatorio": INFO,        # 18 13:00/18:00
    "resumen": INFO,             # 19 parte de las 9:00
    "progreso": INFO,            # 20 terminada / en review / en pausa (un mensaje editado por tarjeta)
    "empezando": INFO,           # 20b ▶️ empezando (nivel esencial: no se envía; ver nivel_avisos)
    "monitor": URGENTE,          # 21 runner parado, panel caído…
    "prodwatch": URGENTE,        # 22 producción en fallo
    "team_notice": INFO,         # 23 aviso al equipo en Marketing
    "fijado": INFO,              # 24 mensajes fijados (Integración, panel de carriles)
}
# Los tipos del resumen diario INFO (F1-04, resumen_carriles.TIPOS_RESUMEN: vigilancia, relanzada, limpieza,
# workflow_sin_origen, bateria) son INFORMATIVOS por la regla general (tipo fuera de la tabla = INFO).

# Etiqueta de cada tipo en el contador fijado (singular, plural).
ETIQUETAS = {"needs_input": ("pregunta de un carril", "preguntas de carriles"),
             "choque": ("choque entre tarjetas", "choques entre tarjetas"),
             "solape": ("solape de tarjetas", "solapes de tarjetas"),
             "aprobar": ("revisión lista para aprobar", "revisiones listas para aprobar"),
             "integrar": ("paso de integración", "pasos de integración"),
             "blocked": ("tarjeta bloqueada", "tarjetas bloqueadas"),
             "validar": ("validación del carril ops", "validaciones del carril ops"),
             "cola": ("bloqueo en la cola del integrador", "bloqueos en la cola del integrador"),
             "tuyo": ("tarjeta tuya", "tarjetas tuyas"),
             "externo": ("PR de sesión que espera tu OK", "PR de sesiones que esperan tu OK"),
             "integrador": ("paso del integrador", "pasos del integrador")}

# Tipos cuya decisión también se copia (con los mismos botones y el mismo teclado de un solo uso) a quien en
# people.yaml decide esa marca sin ser de todas (Ángel, D9): su tema de Desarrollo y su DM. Solo las aprobaciones.
TIPOS_COPIA_EQUIPO = frozenset({"aprobar"})

GRACIA = 15 * 60  # s: un teclado gastado tanto tiempo (y no devuelto) = decisión cerrada aunque nadie avisara
MADRID = ZoneInfo("Europe/Madrid")
SEMBRAR_HORA = 9               # siembra diaria (además de la del arranque)
REVISAR_SEMILLAS = 30 * 60     # s entre recálculos que solo cierran lo ya resuelto (sin publicar nada nuevo)
PAUSA_ENTRE_MENSAJES = 1.0     # s entre mensajes de la siembra (TG-07: varias fichas seguidas dan 429)
SEMBRAR_MAX = 40               # tope de mensajes por siembra
NO_CIERRAN = frozenset({"lt_open", "estado_mas", "explain", "detail"})  # abren/explican: no deciden
# Avisos obsoletos (decisión de Oscar 30-10): se cierran en la revisión de 30 min. Tope por pasada por el 429 de Telegram.
CERRAR_MAX = 25
TIPOS_CIERRE_TARJETA = frozenset({"blocked", "needs_input"})  # cerrar si la tarjeta está done/archived
TIPOS_CIERRE_INTEGRADO = frozenset({"aprobar", "integrar"})   # además, solo con constancia de integración
ESTADOS_TERMINADA = ("done", "archived")
# Choques y solapes (decisión de Oscar 20-11): se cierran solos cuando dejan de ser ciertos.
TIPOS_CIERRE_PAR = frozenset({"choque", "solape"})
AJUSTES_CIERRE = "choques.yaml"   # claves `cerrar_avisos_solos` (false = apagado) y `solapes_caducan_dias` (0 = nunca)
CIERRES_ARCHIVO = "decisiones_cierres.jsonl"  # auditoría y vuelta atrás de los cierres de choque/solape
_TID_RE = re.compile(r"\bt_[0-9a-f]{8}\b")
HEAD_MAX = 90
_TAG_RE = re.compile(r"<[^>]+>")


def clase_de(tipo: str) -> str:
    """Clase de un tipo de mensaje; un tipo que no está en la tabla es INFORMATIVO (nunca despierta a nadie)."""
    return CLASES.get(tipo, INFO)


def con_botones(markup: dict | None) -> bool:
    """¿El teclado tiene algún botón de decisión (callback)? Los botones de URL solos no deciden nada."""
    return markup_token(markup) is not None


def cabecera(text: str, html_text: bool = True) -> str:
    """Primera línea con contenido del aviso, en texto plano y corta (para la línea del DM y el ✅)."""
    plano = html.unescape(_TAG_RE.sub("", text or "")) if html_text else (text or "")
    for line in plano.splitlines():
        if line.strip():
            return truncate(line, HEAD_MAX)
    return ""


def accion_de(button: dict | None) -> str:
    """Texto del botón pulsado sin el emoji del principio («✅ Aprobar» -> «Aprobar»)."""
    text = str((button or {}).get("text") or "").strip()
    return re.sub(r"^[^\w¿¡(]+", "", text).strip() or "Decidido"


class CanalAvisos:
    """Enrutado DECISIÓN / INFORMATIVO / URGENTE + registro de las decisiones publicadas en el tema.

    `destino` = (chat, thread) del tema 🎯 Decisiones, o None (todo como antes). `vivo(token)`: ¿ese teclado sigue sin
    usar? (CallbackStore.get + cola de reintentos). `messages`: MessageStore de los avisos por tarea (sus copias del
    tema son espejos marcados `decision`). Estado en `state_dir`/decisiones_tema.json y decisiones_fijado.json."""

    def __init__(self, notifier, owner_dm: str, destino: tuple[str, str] | None, state_dir: Path | str, *,
                 vivo: Callable[[str], bool], messages=None, clock: Callable[[], float] = time.time,
                 sembrador: Callable[[], list[dict]] | None = None, sleep: Callable[[float], None] = time.sleep,
                 run_async: bool = False, tarjeta: Callable[[str], dict | None] | None = None,
                 ajustes_cierre: Callable[[], tuple[bool, float]] | None = None):
        self.notifier = notifier
        self.owner_dm = str(owner_dm)
        self.destino = (str(destino[0]), str(destino[1])) if destino else None
        self.vivo = vivo
        self.messages = messages
        self.clock = clock
        self.path = Path(state_dir) / "decisiones_tema.json"
        self.fijado = (PinnedSummary(notifier, self.destino, Path(state_dir) / "decisiones_fijado.json")
                       if self.destino else None)
        self._lock = threading.RLock()
        # Siembra (F1-03): `sembrador()` -> [{clave, tid, tipo, texto, markup (dict o callable)}] (ver semilla()).
        self.sembrador, self.sleep, self.run_async = sembrador, sleep, run_async
        # `tarjeta(tid)` -> {status, comentarios [str], rama_integrada (True/False/None)} o None: para cerrar obsoletos.
        self.tarjeta = tarjeta
        # `vigentes(tipo)` -> pares de tarjetas {frozenset({a, b})} que hoy siguen en conflicto, o None si no se sabe.
        self.vigentes: Callable[[str], set | None] | None = None
        # `ajustes_cierre()` -> (cierre automático encendido, días tras los que caduca un SOLAPE; 0 = nunca). Se relee
        # en cada pasada; sin YAML o con YAML inválido: (False, 0) = apagado.
        self.ajustes_cierre = ajustes_cierre or cargar_ajustes_cierre
        self.cierres_path = Path(state_dir) / CIERRES_ARCHIVO
        self._inicio_pendiente = True
        self._sembrando = threading.Lock()
        # `copias(markup)` -> ids de usuario de quienes reciben copia de una aprobación (ver para_desk); None = nadie.
        self.copias: Callable[[dict | None], list[str]] | None = None

    # --- clasificación ---------------------------------------------------------------------------------------------

    def es_decision(self, tipo: str, markup: dict | None) -> bool:
        """Va al tema 🎯 Decisiones: clase DECISIÓN, con botones y con el tema configurado."""
        return bool(self.destino) and clase_de(tipo) == DECISION and con_botones(markup)

    def es_dm(self, target) -> bool:
        return bool(target) and str(target[0]) == self.owner_dm

    # --- avisos sueltos (choques, solapes, limpieza, reconcile, guarda de tablero, paradas) -------------------------

    def enviar(self, tipo: str, text: str, markup: dict | None = None, *, defecto: tuple | None = None,
               html_text: bool = False) -> dict | None:
        """Aviso sin mensaje por tarea. DECISIÓN -> tema (+ línea al DM); lo demás -> `defecto` (DM de Oscar si no se
        dice otra cosa), exactamente como antes."""
        extra = {"reply_markup": markup} if markup is not None else {}
        if not activo(tipo):  # nivel «esencial»: sin mensaje, pero lo que pide decisión sigue contando en el contador
            self._contar_silenciada(tipo, text, markup, html_text)
            return None
        if not con_botones(markup) and tipo in TIPOS_RESUMEN and anotar_resumen(tipo, text):
            return None  # F1-04: INFO sin botones del resumen diario; sale una vez al día (URGENTE nunca entra aquí)
        if not self.es_decision(tipo, markup):
            chat, thread = defecto or (self.owner_dm, None)
            return self.notifier.send_to(chat, thread, text, html=html_text, **extra)
        sent = self.notifier.send_to(self.destino[0], self.destino[1], text, html=html_text, **extra)
        if sent and sent.get("message_id"):
            self._registrar({"tipo": tipo, "rol": "solo", "token": markup_token(markup),
                             "head": cabecera(text, html_text), **_tids_de(tipo, text), **_donde(sent)})
            self._linea_dm(cabecera(text, html_text), sent)
            self.refrescar()
        return sent

    def _contar_silenciada(self, tipo: str, text: str, markup: dict | None, html_text: bool) -> None:
        """Una decisión que el nivel apaga (choque, solape) no manda mensaje, pero se anota sin mensaje: mientras su
        teclado viva cuenta en el contador fijado (se cierra sola pasada la GRACIA, ver refrescar)."""
        token = markup_token(markup)
        if not (self.destino and token and clase_de(tipo) == DECISION):
            return
        with self._lock:
            data = self._load()
            data["abiertas"].setdefault(f"silenciada:{token}", {
                "tipo": tipo, "rol": "silenciada", "token": token, "head": cabecera(text, html_text),
                **_tids_de(tipo, text), "at": self.clock()})
            self._save(data)
        self.refrescar()

    # --- avisos por tarea (TaskNotices) ------------------------------------------------------------------------------

    def publicar(self, notices, tipo: str, tid: str, text: str, target, lane_target, *, alert: bool,
                 reply_markup: dict | None = None, mirror: str | None = None, **kw) -> None:
        """TaskNotices.publish con el canal aplicado. Sin decisión (o sin tema): igual que antes, con la copia espejo
        al DM (`mirror`). Con decisión: el aviso principal se queda donde iba (tema del carril, Integración) salvo
        que fuese el DM de Oscar, que pasa al tema; la copia completa con botones va al tema (en vez de al DM) y al
        DM solo una línea con enlace."""
        if not (alert and self.es_decision(tipo, reply_markup)):
            notices.publish(tid, text, target, lane_target, alert=alert, reply_markup=reply_markup,
                            **({"mirror_to": mirror} if mirror else {}), **kw)
            return
        if self.es_dm(self._resuelto(target, lane_target)):  # el DM de Oscar solo recibe la línea con enlace
            target, lane_target = None, self.destino
        out = notices.publish(tid, text, target, lane_target, alert=True, reply_markup=reply_markup,
                              decision_to=self.destino, **kw)
        if not out:
            return  # aviso idéntico ya enviado (o Telegram apagado): nada nuevo que registrar
        main, copia = out.get("main"), out.get("decision")
        self._copias_equipo(notices, tipo, tid, text, reply_markup, main, lane_target)
        if copia and copia.get("message_id"):
            rol, donde = "mirror", copia   # copia del tema (espejo `decision` de la tarea)
        elif main and main.get("message_id") and _en(main, self.destino):
            rol, donde = "main", main      # el principal cayó en el tema (iba al DM)
        else:
            return
        head = cabecera(text)
        self._registrar({"tipo": tipo, "rol": rol, "tid": tid, "token": markup_token(reply_markup), "head": head,
                         **_donde(donde)})
        if not self.es_dm((main.get("chat_id"), main.get("thread_id"))):
            self._linea_dm(head, donde)
        self.refrescar()

    def _copias_equipo(self, notices, tipo: str, tid: str, text: str, markup: dict | None, main: dict | None,
                       lane_target) -> None:
        """D9: la aprobación de una marca de Ángel llega además a su tema de Desarrollo (el del carril, salvo que el
        aviso principal ya cayera ahí) y a su DM. Mismo teclado y mismo token que el del tema 🎯 Decisiones: el primer
        voto lo gasta y el resto de copias se editan a «Decidido por…» (DecisionDesk._edit). Nunca lanza."""
        if not self.copias or tipo not in TIPOS_COPIA_EQUIPO or not markup_token(markup):
            return
        try:
            ids = [str(i) for i in self.copias(markup) or ()]
        except Exception as exc:  # falla cerrado: sin marca clara no hay copia
            log.info("%s: no se pudo decidir las copias al equipo: %s", tid, exc)
            return
        if not ids:
            return
        destinos: list[tuple[str, str | None]] = []
        if lane_target and len(lane_target) > 1 and str(lane_target[1]) not in ("", "0", "None"):
            tema = (str(lane_target[0]), str(lane_target[1]))
            if not _en(main or {}, tema) and tema != self.destino and tema[0] != self.owner_dm:
                destinos.append(tema)
        destinos += [(i, None) for i in ids if i != self.owner_dm]
        token, bot = markup_token(markup), getattr(self.notifier, "bot_id", None)
        for chat, thread in destinos:
            try:  # trusted: el DM de Ángel está filtrado para colaboradores, estas copias llevan botones a propósito
                sent = self.notifier.send_to(chat, thread, text, reply_markup=markup, trusted=True)
                notices.store.add_mirror(tid, sent, token=token, bot=bot, decision=True)
            except Exception as exc:  # DM sin /start o tema borrado: no rompe el aviso principal
                log.info("%s: no se pudo enviar la copia al equipo: %s", tid, exc)

    # --- al decidir ----------------------------------------------------------------------------------------------

    def resuelta(self, token: str, button: dict | None, persona: str) -> bool:
        """Botón pulsado (por quien sea) y aplicado: el mensaje del tema pasa a «✅ <acción> por <persona>» sin botones
        y deja de seguir a la tarea. Varias preguntas en secuencia: si el mensaje ya lleva el teclado de la siguiente
        (MessageStore.set_token), sigue abierto. True si el teclado era de una decisión del tema."""
        if not self.destino or not token:
            return False
        accion = accion_de(button)
        with self._lock:
            data = self._load()
            hit = False
            for key, e in list(data["abiertas"].items()):
                actual = self._token_actual(e)
                if token not in self._tokens(e, actual):
                    continue
                hit = True
                if e.get("rol") == "semilla" and (button or {}).get("action") in NO_CIERRAN:
                    continue  # 👁 Ver / 📖 Ver más abren la ficha: la decisión sigue pendiente
                # Sigue habiendo un teclado vivo en el mensaje: pregunta siguiente (set_token), ✖ No o confirmación
                # caducada del doble toque (vuelve el teclado original). No se cierra.
                if any(t != token and self.vivo(t) for t in self._tokens(e, actual)):
                    if actual and self.vivo(actual):
                        e["token"] = actual
                    e.pop("gone_at", None)
                    continue
                hora = time.strftime("%H:%M", time.localtime(self.clock()))
                self._cerrar(data, key, f"✅ {accion} por {persona} · {hora}\n{e.get('head') or ''}".strip())
            if hit:
                self._save(data)
        if hit:
            self.refrescar()
        return hit

    # --- contador fijado -------------------------------------------------------------------------------------------

    def pendientes(self) -> list[dict]:
        with self._lock:
            return list(self._load()["abiertas"].values())

    def refrescar(self) -> None:
        """Cierra lo que ya no está pendiente y edita el mensaje fijado con el contador (solo si cambia). Un teclado
        gastado cuenta como cerrado tras GRACIA: mientras se aplica la acción (o si falla y vuelven los botones) sigue
        abierto. Nunca lanza."""
        if not self.destino:
            return
        try:
            self._sembrar_si_toca()
        except Exception as exc:  # estado de la siembra ilegible: el contador y los avisos siguen
            log.warning("tema Decisiones: no se pudo decidir la siembra: %s", exc)
        try:
            with self._lock:
                data = self._load()
                now, cambio = self.clock(), False
                for key, e in list(data["abiertas"].items()):
                    if e.get("rol") == "semilla":
                        continue  # las cierra el recálculo de la siembra o su botón (resuelta)
                    if e.get("rol") == "silenciada":  # sin mensaje que editar: se suelta al gastarse su teclado
                        if self.vivo(e.get("token")):
                            cambio = e.pop("gone_at", None) is not None or cambio
                        elif not e.get("gone_at"):
                            e["gone_at"] = now
                            cambio = True
                        elif now - float(e["gone_at"]) >= GRACIA:
                            data["abiertas"].pop(key, None)
                            cambio = True
                        continue
                    if not self._sigue_en_la_tarea(e):
                        self._cerrar(data, key, f"↪️ Ya no está pendiente (hay un aviso más reciente)\n"
                                                f"{e.get('head') or ''}".strip())
                        cambio = True
                        continue
                    actual = self._token_actual(e)
                    if any(self.vivo(t) for t in self._tokens(e, actual, registro=False)):
                        if e.pop("gone_at", None) is not None or (actual and actual != e.get("token")):
                            e["token"] = actual or e.get("token")
                            cambio = True
                        continue
                    if not e.get("gone_at"):
                        e["gone_at"] = now
                        cambio = True
                    elif now - float(e["gone_at"]) >= GRACIA:
                        self._cerrar(data, key, f"✅ Ya no está pendiente\n{e.get('head') or ''}".strip())
                        cambio = True
                if cambio:
                    self._save(data)
                texto = contador_texto(list(data["abiertas"].values()), now)
            self.fijado.update(texto)
        except Exception as exc:  # el contador es una ayuda: nunca tumba un aviso ni la pasada
            log.warning("tema Decisiones: no se pudo refrescar el contador: %s", exc)

    # --- siembra del backlog (F1-03) ------------------------------------------------------------------------------

    def _toca(self) -> str | None:
        """«inicio» (primera pasada tras arrancar), «diario» (desde las 9:00, una vez al día), «revisar» (solo cerrar,
        cada REVISAR_SEMILLAS) o None."""
        if not self.sembrador or not self.destino:
            return None
        if self._inicio_pendiente:
            return "inicio"
        now = self.clock()
        dt = datetime.fromtimestamp(now, MADRID)
        estado = self._load().get("sembrado") or {}
        if dt.hour >= SEMBRAR_HORA and estado.get("dia") != f"{dt:%Y-%m-%d}":
            return "diario"
        if now - float(estado.get("revisado_at") or 0) >= REVISAR_SEMILLAS:
            return "revisar"
        return None

    def _sembrar_si_toca(self) -> None:
        modo = self._toca()
        if not modo or not self._sembrando.acquire(blocking=False):
            return
        self._inicio_pendiente = False

        def run() -> None:
            try:
                n = self.sembrar(publicar=modo != "revisar")
                if n:
                    log.info("tema Decisiones: sembradas %d decisiones (%s)", n, modo)
                self.cerrar_obsoletas()
            except Exception as exc:  # la siembra es una ayuda: nunca tumba la pasada
                log.warning("tema Decisiones: la siembra (%s) falló: %s", modo, exc)
            finally:
                self._sembrando.release()
            if self.run_async:
                self.refrescar()

        if self.run_async:
            threading.Thread(target=run, name="siembra-decisiones", daemon=True).start()
        else:
            run()

    def sembrar(self, publicar: bool = True) -> int:
        """Recalcula el backlog: cierra las semillas que ya no están y, con `publicar`, envía las nuevas (1 s entre
        mensajes, tope SEMBRAR_MAX). Devuelve cuántas publicó."""
        items = list(self.sembrador() or ()) if self.sembrador else []
        claves = {it["clave"] for it in items}
        now = self.clock()
        dt = datetime.fromtimestamp(now, MADRID)
        with self._lock:
            data = self._load()
            for key, e in list(data["abiertas"].items()):
                if e.get("rol") == "semilla" and e.get("clave") not in claves:
                    self._cerrar(data, key, f"✅ Ya no está pendiente\n{e.get('head') or ''}".strip())
            abiertas = list(data["abiertas"].values())
            ya = {e.get("clave") for e in abiertas if e.get("rol") == "semilla"}
            con_aviso = {e.get("tid") for e in abiertas if e.get("rol") != "semilla" and e.get("tid")}
            nuevos = [it for it in items if it["clave"] not in ya and not (it.get("tid") and it["tid"] in con_aviso)]
            estado = dict(data.get("sembrado") or {}, revisado_at=now)
            if publicar and dt.hour >= SEMBRAR_HORA:
                estado["dia"] = f"{dt:%Y-%m-%d}"
            data["sembrado"] = estado
            self._save(data)
        if not publicar:
            return 0
        n = 0
        for it in nuevos[:SEMBRAR_MAX]:
            if n:
                self.sleep(PAUSA_ENTRE_MENSAJES)
            markup = it["markup"]() if callable(it.get("markup")) else it.get("markup")
            extra = {"reply_markup": markup} if markup else {}
            try:
                sent = self.notifier.send_to(self.destino[0], self.destino[1], it["texto"], html=False, **extra)
            except Exception as exc:
                log.warning("tema Decisiones: no se pudo sembrar %s: %s", it["clave"], exc)
                continue
            if sent and sent.get("message_id"):
                self._registrar({"tipo": it.get("tipo") or "decision", "rol": "semilla", "clave": it["clave"],
                                 "tid": it.get("tid"), "token": markup_token(markup),
                                 "head": cabecera(it["texto"], False), **_donde(sent)})
                log.info("decisión publicada en el tema (semilla) %s", it["clave"])
                n += 1
        return n

    def cerrar_obsoletas(self) -> int:
        """Cierra avisos de decisiones ya hechas: `blocked`/`needs_input` cuya tarjeta está done/archived, y
        `aprobar`/`integrar` de tarjetas done/archived con constancia de integración. Otros tipos no se tocan. Lo que no
        se puede comprobar queda abierto y va al log con el motivo. Tope CERRAR_MAX por pasada; sin `tarjeta` no hace
        nada. Devuelve cuántos cerró."""
        if not self.tarjeta:
            return 0
        with self._lock:
            candidatos = [(k, e) for k, e in self._load()["abiertas"].items()
                          if e.get("tid") and e.get("tipo") in TIPOS_CIERRE_TARJETA | TIPOS_CIERRE_INTEGRADO]
        info: dict[str, dict | None] = {}
        n = 0
        for key, e in candidatos:
            if n >= CERRAR_MAX:
                break
            tid = e["tid"]
            if tid not in info:
                try:
                    info[tid] = self.tarjeta(tid)
                except Exception as exc:  # una tarjeta que no se lee no para la pasada
                    log.info("tema Decisiones: no se pudo leer %s: %s", tid, exc)
                    info[tid] = None
            texto, motivo = self._cierre(e["tipo"], info[tid])
            if not texto:
                if motivo:
                    log.info("tema Decisiones: %s (%s) sigue abierto: %s", tid, e["tipo"], motivo)
                continue
            with self._lock:
                data = self._load()
                if key not in data["abiertas"]:
                    continue
                self._cerrar(data, key, texto)
                self._save(data)
            n += 1
            self.sleep(PAUSA_ENTRE_MENSAJES)
        n += self._cerrar_pares(CERRAR_MAX - n)
        if n:
            log.info("tema Decisiones: %d avisos obsoletos cerrados (tope %d por pasada)", n, CERRAR_MAX)
        return n

    # --- choques y solapes que ya no aplican -------------------------------------------------------------------------

    def _juzgar_par(self, e: dict, now: float, info: dict, pares: dict, caducan_dias: float = 0) -> str | None:
        """Motivo por el que un choque/solape ya no aplica, o None si sigue siendo cierto (o no se puede comprobar).
        Una tarjeta `done` solo lo cierra con constancia de integración (done no es integrada: el conflicto sigue
        vivo hasta que se fusiona); `archived` (descartada) lo cierra. El CHOQUE es una decisión pendiente de Oscar:
        nunca caduca por antigüedad ni por heurística, solo se cierra si deja de existir de forma verificable. El
        SOLAPE puede caducar (`caducan_dias` > 0). Sin la clave `tids` (avisos anteriores a este cierre) no se sabe qué
        tarjetas eran: un choque se conserva siempre y un solape solo caduca."""
        tids = [t for t in e.get("tids") or () if t]
        if "tids" in e:
            for tid in tids:
                if tid not in info:
                    try:
                        info[tid] = self.tarjeta(tid) if self.tarjeta else None
                    except Exception as exc:  # una tarjeta que no se lee no para la pasada
                        log.info("tema Decisiones: no se pudo leer %s: %s", tid, exc)
                        info[tid] = None
                status = (info[tid] or {}).get("status") or ""
                if status == "archived":
                    return f"la tarjeta {tid} está archived"
                if status in ESTADOS_TERMINADA and self._cierre("integrar", info[tid])[0]:
                    return f"la tarjeta {tid} está {status} e integrada"
            tipo = e.get("tipo")
            if len(tids) >= 2 and self.vigentes:
                if tipo not in pares:
                    try:
                        pares[tipo] = self.vigentes(tipo)
                    except Exception as exc:
                        log.info("tema Decisiones: no se pudo recalcular los %ss: %s", tipo, exc)
                        pares[tipo] = None
                vivos = pares[tipo]
                if vivos is not None and not any(frozenset((a, b)) in vivos
                                                 for i, a in enumerate(tids) for b in tids[i + 1:]):
                    return "ya no comparten ficheros"
        dias = (now - float(e.get("at") or now)) / 86400
        if caducan_dias and dias >= caducan_dias and e.get("tipo") == "solape":
            return f"lleva {int(dias)} días abierto"
        return None

    def _cerrar_pares(self, cupo: int) -> int:
        """Pasada automática: cierra los choques/solape con `tids` que ya no aplican (edita «↪️ Ya no aplica», sin
        botones) y anota cada cierre en CIERRES_ARCHIVO. Los anteriores a este cambio (sin `tids`) solo los trata
        `limpiar_antiguos`, con dry-run y confirmación. Máximo `cupo`. Devuelve cuántos cerró."""
        if cupo <= 0:
            return 0
        try:
            encendido, caducan = self.ajustes_cierre()
        except Exception as exc:  # ante la duda no cierra
            log.info("tema Decisiones: ajustes de cierre ilegibles: %s", exc)
            return 0
        if not encendido:
            return 0
        with self._lock:
            candidatos = [(k, e) for k, e in self._load()["abiertas"].items()
                          if e.get("tipo") in TIPOS_CIERRE_PAR and "tids" in e]
        info: dict = {}
        pares: dict = {}
        n, now = 0, self.clock()
        for key, e in candidatos:
            if n >= cupo:
                break
            motivo = self._juzgar_par(e, now, info, pares, caducan)
            if motivo and self._cerrar_con_motivo(key, motivo, None):
                n += 1
                if e.get("rol") != "silenciada":
                    self.sleep(PAUSA_ENTRE_MENSAJES)
        return n

    def _cerrar_con_motivo(self, key: str, motivo: str, lote: str | None) -> bool:
        with self._lock:
            data = self._load()
            e = data["abiertas"].get(key)
            if not e:
                return False
            self._anotar_cierre({"lote": lote, "cuando": self.clock(), "key": key, "motivo": motivo, "entrada": e})
            self._cerrar(data, key, f"↪️ Ya no aplica: {motivo}\n{e.get('head') or ''}".strip())
            self._save(data)
        return True

    def _anotar_cierre(self, rec: dict) -> None:
        """Una línea por cierre (auditoría y vuelta atrás). Si no se puede anotar, no se cierra: lanza."""
        self.cierres_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cierres_path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def limpiar_antiguos(self, *, dry_run: bool = True, max_cierres: int = CERRAR_MAX) -> dict:
        """Limpieza única de los avisos de choque/solape anteriores al cierre automático (sin `tids`).

        `dry_run` (por defecto): no edita ni escribe nada; devuelve cuántos se cerrarían y cuáles se conservan.
        Con `dry_run=False` cierra como mucho `max_cierres` por llamada (Telegram da 429 si se satura) y anota cada
        cierre en CIERRES_ARCHIVO con un id de lote (ver `revertir`); se repite hasta que `quedan` sea 0.
        Solo edita mensajes existentes: nunca envía mensajes nuevos. Los choques antiguos (sin `tids`) no se pueden
        verificar y se CONSERVAN siempre: son decisiones de Oscar. Solo se cierran solapes caducados
        (`solapes_caducan_dias` de choques.yaml; con 0 no se cierra ninguno)."""
        try:
            caducan = self.ajustes_cierre()[1]
        except Exception as exc:
            log.info("tema Decisiones: ajustes de cierre ilegibles: %s", exc)
            caducan = 0
        now = self.clock()
        with self._lock:
            candidatos = [(k, e) for k, e in self._load()["abiertas"].items()
                          if e.get("tipo") in TIPOS_CIERRE_PAR and "tids" not in e]
        info: dict = {}
        pares: dict = {}
        cerrar, conservar = [], []
        for key, e in candidatos:
            motivo = self._juzgar_par(e, now, info, pares, caducan)
            ficha = {"key": key, "tipo": e.get("tipo"), "head": e.get("head"), "motivo": motivo,
                     "mensaje": e.get("rol") != "silenciada"}
            (cerrar if motivo else conservar).append(ficha)
        informe = {"dry_run": dry_run, "candidatos": len(candidatos), "se_cerrarian": len(cerrar),
                   "se_conservan": len(conservar), "conservados": conservar,
                   "este_lote": min(len(cerrar), max_cierres), "tope": max_cierres, "lote": None, "cerrados": 0}
        if dry_run:
            informe["a_cerrar"] = cerrar
            return informe
        informe["lote"] = lote = f"limpieza-{int(now)}"
        hechos = 0
        for ficha in cerrar[:max_cierres]:
            if self._cerrar_con_motivo(ficha["key"], ficha["motivo"], lote):
                hechos += 1
                if ficha["mensaje"]:
                    self.sleep(PAUSA_ENTRE_MENSAJES)
        informe["cerrados"], informe["quedan"] = hechos, len(cerrar) - hechos
        self._actualizar_contador()
        return informe

    def revertir(self, lote: str, *, max_reabiertos: int = CERRAR_MAX) -> int:
        """Vuelta atrás de un lote de CIERRES_ARCHIVO: devuelve cada aviso al contador y restaura en su mensaje la
        primera línea guardada («↩️ Reabierto»; el texto entero y los botones no se guardan, y los botones ya habían
        caducado). Máximo `max_reabiertos` por llamada. Devuelve cuántos reabrió."""
        try:
            lineas = [json.loads(x) for x in self.cierres_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        except (OSError, json.JSONDecodeError):
            return 0
        hechos = {r["key"] for r in lineas if r.get("lote") == lote and r.get("revertido")}
        n = 0
        for rec in lineas:
            if rec.get("lote") != lote or rec.get("revertido") or rec["key"] in hechos or n >= max_reabiertos:
                continue
            e = rec["entrada"]
            with self._lock:
                data = self._load()
                data["abiertas"].setdefault(rec["key"], e)
                self._save(data)
            if e.get("rol") != "silenciada":
                try:
                    self.notifier.edit(e["chat_id"], e["message_id"], f"↩️ Reabierto\n{e.get('head') or ''}".strip(),
                                       html=False)
                except Exception as exc:
                    log.info("tema Decisiones: no se pudo reabrir %s: %s", e.get("message_id"), exc)
                self.sleep(PAUSA_ENTRE_MENSAJES)
            self._anotar_cierre({"lote": lote, "cuando": self.clock(), "key": rec["key"], "revertido": True,
                                 "entrada": e})
            n += 1
        if n:
            self._actualizar_contador()
        return n

    def _actualizar_contador(self) -> None:
        """Edita el mensaje fijado con las decisiones que quedan. Solo edita: sin fijado previo no crea ninguno, y no
        siembra ni envía decisiones nuevas."""
        if not self.fijado or not self.fijado._load().get("message_id"):
            return
        try:
            self.fijado.update(contador_texto(self.pendientes(), self.clock()))
        except Exception as exc:
            log.warning("tema Decisiones: no se pudo refrescar el contador: %s", exc)

    @staticmethod
    def _cierre(tipo: str, info: dict | None) -> tuple[str | None, str]:
        """(texto del ✅ si el aviso está obsoleto, motivo por el que queda abierto o "" si la tarjeta sigue viva)."""
        if not info:
            return None, "no se pudo leer la tarjeta"
        status = info.get("status") or ""
        if status not in ESTADOS_TERMINADA:
            return None, ""
        texto = f"✅ Resuelta: tarjeta {status}"
        if tipo in TIPOS_CIERRE_TARJETA:
            return texto, ""
        if any(_es_integracion(c) for c in info.get("comentarios") or ()):
            return texto, ""
        if info.get("rama_integrada") is True:
            return texto, ""
        if info.get("rama_integrada") is None:
            return None, "sin comentario de integración y no se pudo comprobar la rama"
        return None, "sin comentario de integración ni rama integrada"

    def confirmacion(self, original: str, nuevo: str) -> None:
        """1.er toque del doble toque (consola.pedir): el mensaje lleva ahora [✅ Sí] [✖ No] con otro teclado. La
        decisión sigue abierta mientras ese teclado viva y el ✅ Sí la cierra con la acción real."""
        if not self.destino or not original or not nuevo:
            return
        with self._lock:
            data = self._load()
            hit = False
            for e in data["abiertas"].values():
                if original in self._tokens(e, self._token_actual(e)):
                    e["extra"] = [*(e.get("extra") or [])[-4:], nuevo]
                    e.pop("gone_at", None)
                    hit = True
            if hit:
                self._save(data)

    # --- registro --------------------------------------------------------------------------------------------------

    @staticmethod
    def _tokens(e: dict, actual: str | None, registro: bool = True) -> list[str]:
        """Teclados del mensaje: el vigente, el registrado al enviar (`registro`) y los de confirmación del doble
        toque. En los avisos por tarea el registrado puede ser ya de una pregunta anterior: para saber si sigue
        abierto cuenta el vigente (`registro=False`)."""
        base = [actual] if e.get("rol") in ("mirror", "main") else [e.get("token")]
        if registro:
            base.append(e.get("token"))
        return [t for t in (*base, *(e.get("extra") or ())) if t]

    def _token_actual(self, e: dict) -> str | None:
        """Teclado vigente del mensaje del tema: el de su copia en MessageStore (set_token lo mantiene al día en las
        preguntas en secuencia) o, en los avisos sueltos, el del registro."""
        if e.get("rol") in ("mirror", "main") and self.messages and e.get("tid"):
            for m in self.messages.all_messages(e["tid"]):
                if _misma(m, e):
                    return m.get("token") or e.get("token")
            return None
        return e.get("token")

    def _sigue_en_la_tarea(self, e: dict) -> bool:
        """Una copia que un aviso nuevo de la misma tarea ya retiró (TaskNotices le quita los botones) no cuenta."""
        if e.get("rol") not in ("mirror", "main") or not self.messages or not e.get("tid"):
            return True
        return any(_misma(m, e) for m in self.messages.all_messages(e["tid"]))

    def _registrar(self, entry: dict) -> None:
        with self._lock:
            data = self._load()
            if entry.get("tid") and entry.get("rol") != "semilla":  # un aviso nuevo de la tarea sustituye al anterior
                for key, e in list(data["abiertas"].items()):
                    if e.get("tid") == entry["tid"] and not _misma(e, entry):
                        self._cerrar(data, key, f"↪️ Sustituida por un aviso más reciente\n{e.get('head') or ''}"
                                     .strip())
            entry["at"] = self.clock()
            data["abiertas"][f"{entry['chat_id']}:{entry['message_id']}"] = entry
            self._save(data)

    def _cerrar(self, data: dict, key: str, texto: str) -> None:
        """Edita el mensaje del tema (texto plano, sin botones) y lo suelta de la tarea: los avisos posteriores de la
        tarea (▶️, terminada) ya no lo pisan."""
        e = data["abiertas"].pop(key, None)
        if not e or e.get("rol") == "silenciada":  # las silenciadas no tienen mensaje
            return
        try:
            self.notifier.edit(e["chat_id"], e["message_id"], texto, html=False)
        except Exception as exc:
            log.info("tema Decisiones: no se pudo editar %s: %s", e.get("message_id"), exc)
        if self.messages and e.get("tid"):
            try:
                if e.get("rol") == "mirror":
                    self.messages.drop_mirror(e["tid"], e["chat_id"], e["message_id"])
                elif e.get("rol") == "main" and _misma(self.messages.get(e["tid"]) or {}, e):
                    self.messages.drop(e["tid"])  # el siguiente aviso de la tarea sale como mensaje nuevo
            except Exception as exc:
                log.info("tema Decisiones: no se pudo soltar la copia de %s: %s", e.get("tid"), exc)

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if not isinstance(data.get("abiertas"), dict):
            data["abiertas"] = {}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def _resuelto(self, target, lane_target) -> tuple | None:
        """Chat donde caería el aviso principal (la misma regla que TelegramNotifier.send)."""
        n = self.notifier
        got = resolve_target(target, lane_target, allowed_chats=set(getattr(n, "allowed_chats", ()) or ()),
                             generic_origins=set(getattr(n, "generic_origins", ()) or ()))
        return got or ((getattr(n, "chat_id", None), getattr(n, "thread_id", None)) if getattr(n, "chat_id", None)
                       else None)

    def _linea_dm(self, head: str, donde: dict) -> None:
        """Una línea al DM de Oscar con enlace al mensaje del tema (sin botones y sin registrarla como copia: la
        sincronización de copias la convertiría en el aviso entero)."""
        url = topic_link(donde.get("chat_id"), donde.get("thread_id"), donde.get("message_id"))
        text = f"🎯 Decisión nueva: {head}" + (f" → {url}" if url else " → tema 🎯 Decisiones")
        try:
            self.notifier.send_to(self.owner_dm, None, text, html=False, silent=False)
        except Exception as exc:  # DM sin /start: la decisión ya está en el tema
            log.info("tema Decisiones: no se pudo avisar al DM: %s", exc)


def contador_texto(abiertas: list[dict], now: float) -> str:
    """Texto (HTML) del mensaje fijado: «🎯 N decisiones pendientes», desglose por tipo y la más antigua."""
    n = len(abiertas)
    if not n:
        return "🎯 <b>Sin decisiones pendientes</b>"
    lines = [f"🎯 <b>{n} decisi{'ón' if n == 1 else 'ones'} pendiente{'' if n == 1 else 's'}</b>"]
    por_tipo: dict[str, int] = {}
    for e in abiertas:
        por_tipo[e.get("tipo") or "?"] = por_tipo.get(e.get("tipo") or "?", 0) + 1
    for tipo, k in sorted(por_tipo.items(), key=lambda x: (-x[1], x[0])):
        one, many = ETIQUETAS.get(tipo, ("decisión", "decisiones"))
        lines.append(f"• {k} {html.escape(one if k == 1 else many)}")
    oldest = min(float(e.get("at") or now) for e in abiertas)
    lines.append(f"La más antigua lleva {html.escape(age_label(oldest, now))}.")
    return "\n".join(lines)


# Tipo del contador de cada kind de lo_tuyo («pregunta» cuenta igual que el aviso en vivo de un needs_input).
TIPO_SEMILLA = {"pregunta": "needs_input", "atascada": "blocked"}


def semilla(it: dict, markup, verbo: str, now: float) -> dict:
    """Ítem de lo_tuyo -> semilla del tema: {clave, tid, tipo, texto, markup}. `markup`: teclado o función que lo
    emite (solo se llama si de verdad se publica: así el recálculo de cada 30 min no gasta teclados)."""
    from .notices import hours_ago
    cuando = f" · hace {hours_ago(it['since'], now)} h" if it.get("since") else ""
    extra = f"\n{truncate(it['extra'], 200)}" if it.get("extra") else ""
    texto = f"🎯 {verbo} · {it['tid']} · {truncate(it.get('title') or '', 90)}{cuando}{extra}"
    return {"clave": f"{it['kind']}:{it['tid']}", "tid": it["tid"] if str(it["tid"]).startswith("t_") else None,
            "tipo": TIPO_SEMILLA.get(it["kind"], it["kind"]), "texto": texto, "markup": markup}


def cargar_ajustes_cierre(path: Path | str | None = None) -> tuple[bool, float]:
    """(encendido, días de caducidad de un solape) desde choques.yaml. NACE APAGADO: solo `cerrar_avisos_solos: true`
    (booleano de verdad) lo enciende; sin fichero, con YAML inválido o con otro valor, (False, 0)."""
    import yaml
    from .config import ROOT
    try:
        raw = yaml.safe_load(Path(path or ROOT / AJUSTES_CIERRE).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False, 0.0
    if not isinstance(raw, dict) or raw.get("cerrar_avisos_solos") is not True:
        return False, 0.0
    d = raw.get("solapes_caducan_dias")
    dias = float(d) if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0 else 0.0
    return True, dias


def _tids_de(tipo: str, text: str) -> dict:
    """{"tids": [...]} con las tarjetas que cita un choque/solape (para cerrarlo cuando ya no aplique); {} en otros tipos."""
    if tipo not in TIPOS_CIERRE_PAR:
        return {}
    return {"tids": list(dict.fromkeys(_TID_RE.findall(text or "")))}


def _misma(a: dict, b: dict) -> bool:
    return (str(a.get("chat_id")), str(a.get("message_id"))) == (str(b.get("chat_id")), str(b.get("message_id")))


def _donde(sent: dict) -> dict:
    return {"chat_id": str(sent["chat_id"]), "thread_id": str(sent.get("thread_id") or "0"),
            "message_id": sent["message_id"]}


def _es_integracion(body: str) -> bool:
    """Comentario de integración: «INTEGRADO <sha> …» del integrador o «Integrada en …». Con el espacio final, «INTEGRADOR:»
    (sus otros comentarios) no cuenta."""
    b = str(body or "").lstrip()
    return b.startswith("INTEGRADO ") or b.startswith("Integrada en")


def _git_rc(repo: str, *args: str) -> int | None:
    """Código de salida de `git -C repo args`, o None si git no se pudo ejecutar."""
    try:
        return _proc.run(["git", "-C", repo, *args], capture_output=True, timeout=30).returncode  # sin ventana de consola
    except (OSError, subprocess.SubprocessError):
        return None


def _rama_integrada(lanes, tid: str, git: Callable[..., int | None] = _git_rc,
                    contenido: Callable[..., bool | None] | None = None) -> bool | None:
    """¿La rama `lane/<tid>` está integrada en la base de su carril? Mismo criterio que `sin_integrar`: ancestro de la
    base o, si no lo es y se pasa `contenido` (sin_integrar.Lector.integrada_por_contenido), que fusionarla no cambie
    nada (rebase/squash). Sin fetch: solo las refs que ya hay en el repo. True si lo está en algún carril; False si
    se comprobó y no; None si no se pudo comprobar (ante la duda no se da por integrada)."""
    resultado, vistos = None, set()
    for lane in lanes:
        if not lane.repo or not lane.base or (lane.repo, lane.remote, lane.base) in vistos:
            continue
        vistos.add((lane.repo, lane.remote, lane.base))
        base = f"{lane.remote}/{lane.base}"
        for ref in (f"lane/{tid}", f"{lane.remote}/lane/{tid}"):
            rc = git(lane.repo, "merge-base", "--is-ancestor", ref, base)
            if rc == 0:
                return True
            if rc == 1:
                resultado = False
        if resultado is False and contenido is not None:
            try:
                val = contenido(lane.repo, lane.remote, lane.base, f"lane/{tid}")
            except Exception:
                val = None
            if val is True:
                return True
            if val is None:
                resultado = None
    return resultado


def estado_tarjeta_de(desk, git: Callable[..., int | None] = _git_rc,
                      contenido: Callable[..., bool | None] | None = None) -> Callable[[str], dict | None]:
    """`tarjeta(tid)` del canal: estado y comentarios de la tarjeta (busca en los tableros de los carriles) y, si está
    terminada, si su rama ya es ancestro de la base. None si no se puede leer."""
    lanes = list((getattr(desk, "lanes", None) or {}).values())
    boards = sorted({ln.board for ln in lanes if ln.board}) or ["default"]

    def tarjeta(tid: str) -> dict | None:
        for board in boards:
            try:
                show = desk.hermes_for(board).show(tid) or {}
            except Exception:  # el tablero no tiene la tarjeta (o no responde): se prueba el siguiente
                continue
            task = show.get("task") or {}
            if not task:
                continue
            status = str(task.get("status") or "")
            comentarios = [str(c.get("body") or "") for c in show.get("comments") or () if isinstance(c, dict)]
            rama = _rama_integrada(lanes, tid, git, contenido) if status in ESTADOS_TERMINADA else None
            return {"status": status, "comentarios": comentarios, "rama_integrada": rama}
        return None

    return tarjeta


def _en(sent: dict, destino: tuple[str, str] | None) -> bool:
    return bool(destino) and (str(sent.get("chat_id")), str(sent.get("thread_id") or "0")) == destino


def de(desk) -> CanalAvisos | None:
    """Canal del despachador de decisiones (None sin bot de carriles: todo como antes)."""
    return getattr(desk, "canal", None) if desk is not None else None


def tipo_de_estado(state: str | None, alert: bool = False) -> str:
    """Tipo de un aviso de tarea según su estado: needs_input y blocked son suyos; el «▶️ empezando» (running sin
    alerta) es `empezando`, que el nivel esencial puede apagar sin perder la terminada; el resto es progreso."""
    if state in ("needs_input", "blocked"):
        return state
    return "empezando" if state == "running" and not alert else "progreso"


def publicar(canal: CanalAvisos | None, notices, tipo: str, tid: str, text: str, target, lane_target, *,
             alert: bool, reply_markup: dict | None = None, mirror: str | None = None, **kw) -> None:
    """Punto único de los avisos por tarea (runner, review, integrador). Sin canal (sin bot de carriles): como antes."""
    if not activo(tipo):  # nivel «esencial»: este tipo no manda mensaje (sigue estando a demanda por comando)
        return
    if canal is None:
        notices.publish(tid, text, target, lane_target, alert=alert, reply_markup=reply_markup,
                        **({"mirror_to": mirror} if mirror else {}), **kw)
        return
    canal.publicar(notices, tipo, tid, text, target, lane_target, alert=alert, reply_markup=reply_markup,
                   mirror=mirror, **kw)


def emisor(canal: CanalAvisos | None, notifier, tipo: str, owner_dm, defecto: tuple | None = None) -> Callable:
    """`send(text, markup=None)` de un aviso suelto (choques, solapes, limpieza, reconcile, guarda de tablero) con su
    tipo ya clasificado. Sin canal: el envío de siempre a `defecto` (DM de Oscar)."""
    chat, thread = defecto or (str(owner_dm), None)
    if canal is None:
        return lambda text, markup=None: notifier.send_to(chat, thread, text, html=False, reply_markup=markup)
    return lambda text, markup=None: canal.enviar(tipo, text, markup, defecto=(chat, thread))


def para_desk(desk, destino: tuple[str, str] | None) -> CanalAvisos:
    """Canal del runner: el del DecisionDesk (sus teclados, su cola de reintentos y su MessageStore). El estado va junto
    a los teclados (.state/), así los tests con tmp_path no escriben en el .state real."""
    store = desk.store
    pending = getattr(desk, "pending", None)

    def vivo(token: str) -> bool:
        return store.get(token) is not None or bool(pending and token in pending.all())

    canal = CanalAvisos(desk.notifier, desk.owner_id, destino, Path(store.root).parent, vivo=vivo,
                        messages=desk.messages)
    from .sin_integrar import Lector
    canal.tarjeta = estado_tarjeta_de(desk, contenido=Lector().integrada_por_contenido) if getattr(desk, "hermes_for", None) else None

    def copias(markup: dict | None) -> list[str]:
        """Operadores acotados por marca (no Oscar ni los de «*») que pueden decidir TODAS las marcas de la tarjeta
        (people.yaml). Sin marca conocida (None) o de otra marca (MigraTeam): nadie."""
        marcas = desk.rec_marcas(store.get(markup_token(markup)) or {})
        if not marcas or None in marcas:
            return []
        gente = desk._people()
        return [p.user_id for p in gente._by_id.values()
                if gente.restringido(p.user_id) and all(gente.puede_en(p.user_id, m) for m in marcas)]

    canal.copias = copias
    if getattr(desk, "hermes_for", None) and getattr(desk, "lanes", None):
        canal.vigentes = pares_vigentes_de(desk)
    return canal


def limpiar_main(argv: list[str]) -> int:
    """`lanes.py limpiar-avisos`: limpieza única de choques/solapes antiguos del tema 🎯 Decisiones.
    Sin opciones: DRY-RUN (no edita ni escribe nada). `--aplicar [--max N]`: cierra como mucho N (por defecto
    CERRAR_MAX) y anota el lote en .state/decisiones_cierres.jsonl; repetir hasta que `quedan` sea 0.
    `--revertir LOTE`: reabre un lote. Solo edita mensajes existentes."""
    import argparse
    from .config import load_env, load_lanes, load_telegram_settings
    from .decisions import CALLBACKS_DIR, OWNER_TELEGRAM_ID, CallbackStore, DecisionDesk
    from .hermes import HermesCLI
    from .kanban_api import backend_for
    from .notices import LinkBuilder
    from .telegram import notifier_from_env

    ap = argparse.ArgumentParser(prog="lanes.py limpiar-avisos", description=limpiar_main.__doc__.split("\n\n")[0])
    ap.add_argument("--aplicar", action="store_true", help="ejecución real (sin esto es dry-run)")
    ap.add_argument("--max", type=int, default=CERRAR_MAX, help=f"tope de cierres por llamada (def. {CERRAR_MAX})")
    ap.add_argument("--revertir", metavar="LOTE", help="reabre los cierres de ese lote")
    args = ap.parse_args(argv)
    env, tg_settings, lanes = load_env(), load_telegram_settings(), load_lanes()
    notifier, lanes_bot = notifier_from_env(env, tg_settings)
    if not (lanes_bot and notifier.enabled and tg_settings.get("decisiones")):
        raise SystemExit("limpiar-avisos necesita CARRILES_BOT_TOKEN, TELEGRAM_CHAT_ID y el tema de decisiones")
    cache: dict[str, HermesCLI] = {}
    hermes_for = lambda board: cache.setdefault(board, backend_for(board))
    desk = DecisionDesk(notifier, CallbackStore(CALLBACKS_DIR), lanes=lanes, hermes_for=hermes_for,
                        links=LinkBuilder(env.get("KANBAN_BASE_URL") or tg_settings["kanban_base_url"]),
                        owner_id=env.get("OWNER_TELEGRAM_ID") or OWNER_TELEGRAM_ID)
    canal = para_desk(desk, tg_settings["decisiones"])
    if args.revertir:
        print(f"reabiertos: {canal.revertir(args.revertir, max_reabiertos=args.max)}")
        return 0
    inf = canal.limpiar_antiguos(dry_run=not args.aplicar, max_cierres=args.max)
    print(json.dumps({k: v for k, v in inf.items() if k not in ("a_cerrar", "conservados")},
                     ensure_ascii=False, indent=2))
    print(f"conservados: {len(inf['conservados'])}" + ("" if args.aplicar else
          f" · se cerrarían {inf['se_cerrarian']} (para ejecutar: añade --aplicar, con OK de Oscar)"))
    for f in inf["conservados"][:50]:
        print(f"  conserva {f['tipo']} {f['key']}: {f['head']}")
    return 0


def pares_vigentes_de(desk) -> Callable[[str], set | None]:
    """`vigentes(tipo)` del canal. Solo los solapes se recalculan (solo lee Hermes); los choques necesitan git y solo
    se cierran por tarjeta terminada o por caducidad. Con solapes.yaml apagado devuelve None (no se sabe)."""
    def vigentes(tipo: str) -> set | None:
        if tipo != "solape":
            return None
        from . import solapes
        ajustes = solapes.load_ajustes()
        if not ajustes.activo:
            return None
        pares: set = set()
        for g in solapes.detectar(desk.lanes, desk.hermes_for, ajustes):
            tids = [c.tid for c in g.cards]
            pares.update(frozenset((a, b)) for i, a in enumerate(tids) for b in tids[i + 1:])
        return pares

    return vigentes
