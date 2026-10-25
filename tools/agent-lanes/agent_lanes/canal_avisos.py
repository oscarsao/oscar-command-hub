"""Canal de cada aviso: DECISIÓN, INFORMATIVO o URGENTE (decisión de Oscar 25-10, «Tema 🎯 Decisiones en Gestión»).

Un solo sitio que dice a dónde va cada tipo de mensaje del bot de carriles (borrador «avisos accionables», tabla §1):

- DECISIÓN: lo que solo Oscar, María o Ángel pueden resolver. Con `telegram.destinos.decisiones` en lanes.yaml va AL
  MOMENTO, con botones, al tema «🎯 Decisiones» del grupo Gestión, y al DM de Oscar solo una línea con enlace. Al
  decidir (botón de quien sea), el mensaje del tema pasa a «✅ <acción> por <persona>» sin botones, y un mensaje fijado
  del tema dice «N decisiones pendientes» (se edita, no se crea uno nuevo).
- URGENTE: caída, producción, seguridad: al DM al momento, como hoy.
- INFORMATIVO: por ahora, como hoy (el resumen diario es el paso siguiente).

Sin `destinos.decisiones` todo sale exactamente como antes (DM de Oscar / copia espejo al DM).

El contador no suma ni resta: se deriva del estado. Una decisión del tema sigue abierta mientras el teclado vigente de
ese mensaje esté vivo en el CallbackStore (respuesta por Hermes, texto libre, aviso nuevo de la misma tarea o un fallo
que devuelve los botones quedan cubiertos solos), así que un reinicio del runner rehace el mismo número.
"""
from __future__ import annotations

import html
import logging
import re
import time
from pathlib import Path
from typing import Callable

from .integration import topic_link
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
    "progreso": INFO,            # 20 ▶️ / terminada (un mensaje editado por tarjeta)
    "monitor": URGENTE,          # 21 runner parado, panel caído…
    "prodwatch": URGENTE,        # 22 producción en fallo
    "team_notice": INFO,         # 23 aviso al equipo en Marketing
    "fijado": INFO,              # 24 mensajes fijados (Integración, panel de carriles)
}

# Etiqueta de cada tipo en el contador fijado (singular, plural).
ETIQUETAS = {"needs_input": ("pregunta de un carril", "preguntas de carriles"),
             "choque": ("choque entre tarjetas", "choques entre tarjetas"),
             "solape": ("solape de tarjetas", "solapes de tarjetas"),
             "aprobar": ("revisión lista para aprobar", "revisiones listas para aprobar"),
             "integrar": ("paso de integración", "pasos de integración"),
             "blocked": ("tarjeta bloqueada", "tarjetas bloqueadas")}

GRACIA = 15 * 60  # s: un teclado gastado tanto tiempo (y no devuelto) = decisión cerrada aunque nadie avisara
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
                 vivo: Callable[[str], bool], messages=None, clock: Callable[[], float] = time.time):
        self.notifier = notifier
        self.owner_dm = str(owner_dm)
        self.destino = (str(destino[0]), str(destino[1])) if destino else None
        self.vivo = vivo
        self.messages = messages
        self.clock = clock

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
        if not self.es_decision(tipo, markup):
            chat, thread = defecto or (self.owner_dm, None)
            return self.notifier.send_to(chat, thread, text, html=html_text, **extra)
        sent = self.notifier.send_to(self.destino[0], self.destino[1], text, html=html_text, **extra)
        if sent and sent.get("message_id"):
            self._linea_dm(cabecera(text, html_text), sent)
        return sent

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
        donde = copia if copia and copia.get("message_id") else (main if main and _en(main, self.destino) else None)
        if donde and not (main and self.es_dm((main.get("chat_id"), main.get("thread_id")))):
            self._linea_dm(cabecera(text), donde)

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


def _donde(sent: dict) -> dict:
    return {"chat_id": str(sent["chat_id"]), "thread_id": str(sent.get("thread_id") or "0"),
            "message_id": sent["message_id"]}


def _en(sent: dict, destino: tuple[str, str] | None) -> bool:
    return bool(destino) and (str(sent.get("chat_id")), str(sent.get("thread_id") or "0")) == destino


def de(desk) -> CanalAvisos | None:
    """Canal del despachador de decisiones (None sin bot de carriles: todo como antes)."""
    return getattr(desk, "canal", None) if desk is not None else None


def tipo_de_estado(state: str | None) -> str:
    """Tipo de un aviso de tarea según su estado (needs_input, blocked); el resto es progreso (INFORMATIVO)."""
    return state if state in ("needs_input", "blocked") else "progreso"


def publicar(canal: CanalAvisos | None, notices, tipo: str, tid: str, text: str, target, lane_target, *,
             alert: bool, reply_markup: dict | None = None, mirror: str | None = None, **kw) -> None:
    """Punto único de los avisos por tarea (runner, review, integrador). Sin canal (sin bot de carriles): como antes."""
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

    return CanalAvisos(desk.notifier, desk.owner_id, destino, Path(store.root).parent, vivo=vivo,
                       messages=desk.messages)
