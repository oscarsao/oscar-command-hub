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
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
                 run_async: bool = False):
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
                             "head": cabecera(text, html_text), **_donde(sent)})
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
                "at": self.clock()})
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


def _misma(a: dict, b: dict) -> bool:
    return (str(a.get("chat_id")), str(a.get("message_id"))) == (str(b.get("chat_id")), str(b.get("message_id")))


def _donde(sent: dict) -> dict:
    return {"chat_id": str(sent["chat_id"]), "thread_id": str(sent.get("thread_id") or "0"),
            "message_id": sent["message_id"]}


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
    return canal
