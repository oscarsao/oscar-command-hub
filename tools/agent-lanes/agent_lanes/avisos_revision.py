"""Avisos «listo para revisar» agrupados en un resumen, con silencio y tarjetas mudas (t_c1988acc, petición de Oscar 23-10).

Antes cada tarjeta que acababa mandaba su propio aviso con sonido (✅ lista para merge del carril review, «pendiente de tu
validación» del carril ops). Ahora esos dos avisos siguen creando/editando su ficha (con ✅ Aprobar), pero SIN notificación:
solo suena el resumen, que reúne varias tarjetas en un mensaje.

- `interceptar`: punto de corte único. Lo llaman `ReviewRunner.notify` (también cuando lo reemite `renotify`) y
  `LaneRunner.notify`. Apunta la tarjeta como pendiente y devuelve el `alert` efectivo (siempre False si la ha recogido).
- `AvisosRevision.tick`: emite UN mensaje cuando hay `avisos.revision_lote` tarjetas o la más antigua cumple
  `avisos.revision_ventana_horas`, lo que pase antes. Durante un silencio no sale nada; al terminar sale un único resumen
  con todo lo acumulado. Nada se pierde: `/tareas` y `/aprobar` lo siguen mostrando.
- Botones del resumen: [🔕 1 h] [🔕 3 h] [🔕 Hasta mañana] y, por tarjeta, [🙈 No avisar de t_xxx] (esa tarjeta no vuelve
  a entrar en ningún resumen, ni por renotify).
- `/avisos off [1h|3h|mañana|siempre]`, `/avisos on` y `/avisos`: mismo estado que los botones. Distinto de `/silenciar`
  (alertas del monitor).

Estado en `.state/avisos_revision.json`: {silencio: {chat: epoch | "siempre"}, mudas: [tid], pendientes: [...]}.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from .ajustes_hub import valor
from .config import ROOT
from .notices import truncate

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "avisos_revision.json"
AV_1H, AV_3H, AV_MANANA, AV_TAREA, AV_ON = "av_1h", "av_3h", "av_manana", "av_tarea", "av_on"
AV_ACTIONS = (AV_1H, AV_3H, AV_MANANA, AV_TAREA, AV_ON)
ESTADOS = ("done", "review")  # done = lista para merge (carril review); review = pendiente de validar (carril ops)
LINEA_MAX = 220
TEXTO_MAX = 3800
TARJETAS_CON_BOTON = 8

ACTIVO: "AvisosRevision | None" = None  # lo instala el runner; sin él nada cambia (tests, consolas, --once)


def instalar(instancia: "AvisosRevision | None") -> None:
    global ACTIVO
    ACTIVO = instancia


def interceptar(state: str, task: dict, status: str, alert: bool, *, summary=None, for_oscar=None) -> bool:
    """Devuelve el `alert` efectivo: False si el aviso lo recoge el resumen agrupado; el original si no aplica."""
    if not alert or ACTIVO is None or state not in ESTADOS:
        return alert
    try:
        ACTIVO.registrar(task, for_oscar or summary or status)
        return False
    except Exception as exc:  # un fallo del resumen nunca debe tragarse el aviso
        log.warning("avisos_revision: no se pudo recoger %s: %s", (task or {}).get("id"), exc)
        return alert


def _limpio(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def hasta_manana(now: float) -> float:
    """Las `avisos.hora_parte` (9:00 Madrid) del día siguiente."""
    from .resumen_carriles import MADRID
    hora = valor("avisos.hora_parte")
    hora = 9 if hora is None else int(hora)
    local = datetime.fromtimestamp(now, MADRID)
    return (local + timedelta(days=1)).replace(hour=hora, minute=0, second=0, microsecond=0).timestamp()


def parse_duracion(arg: str, now: float) -> float | str | None:
    """'1h' / '3h' / '2,5h' / 'mañana' / 'siempre' -> epoch de fin (o 'siempre'); None si no se entiende."""
    a = _limpio(arg).lower()
    if a in ("mañana", "manana"):
        return hasta_manana(now)
    if a == "siempre":
        return "siempre"
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*h?", a)
    if m and float(m.group(1).replace(",", ".")) > 0:
        return now + float(m.group(1).replace(",", ".")) * 3600
    return None


class AvisosRevision:
    """`send(text, markup)` publica en el canal de Oscar; `issue(rec, spec)` -> (token, reply_markup) (CallbackStore)."""

    def __init__(self, send: Callable[[str, dict | None], object], chat: str, issue: Callable | None = None,
                 state_path: Path | str = STATE_PATH, clock: Callable[[], float] = time.time):
        self.send, self.chat, self.issue, self.clock = send, str(chat), issue, clock
        self.state_path = Path(state_path)
        # Leer-modificar-guardar desde varios hilos (jobs de carril y review, bucle principal, lane-decision):
        # sin lock, un registrar concurrente con tick duplicaba o perdía avisos. Nunca se retiene durante send.
        self._lock = threading.RLock()

    # --- estado --------------------------------------------------------------------------------------

    def _load(self) -> dict:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        return {"silencio": raw.get("silencio") if isinstance(raw.get("silencio"), dict) else {},
                "mudas": [t for t in raw.get("mudas") or () if isinstance(t, str)],
                "pendientes": [p for p in raw.get("pendientes") or () if isinstance(p, dict) and p.get("tid")]}

    def _save(self, data: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def silenciado_hasta(self, data: dict | None = None) -> float | str | None:
        """Fin del silencio vigente de este chat ('siempre' si no caduca); None si no hay."""
        v = (data or self._load())["silencio"].get(self.chat)
        if v == "siempre" or (isinstance(v, (int, float)) and v > self.clock()):
            return v
        return None

    def silenciar(self, hasta: float | str) -> None:
        with self._lock:
            data = self._load()
            data["silencio"][self.chat] = hasta
            self._save(data)

    def reactivar(self) -> None:
        with self._lock:
            data = self._load()
            data["silencio"].pop(self.chat, None)
            self._save(data)

    def mutear(self, tid: str) -> None:
        with self._lock:
            data = self._load()
            if tid not in data["mudas"]:
                data["mudas"].append(tid)
            data["pendientes"] = [p for p in data["pendientes"] if p["tid"] != tid]
            self._save(data)

    def estado_texto(self) -> str:
        data = self._load()
        hasta = self.silenciado_hasta(data)
        if hasta == "siempre":
            cab = "🔕 Avisos de «listo para revisar» apagados hasta que hagas /avisos on"
        elif hasta:
            cab = f"🔕 Avisos de «listo para revisar» silenciados hasta {_fecha(hasta)}"
        else:
            cab = "🔔 Avisos de «listo para revisar» activos (un resumen por lote)"
        extra = [f"{len(data['pendientes'])} tarjeta(s) esperando en el resumen"] if data["pendientes"] else []
        if data["mudas"]:
            extra.append("sin avisar de: " + ", ".join(data["mudas"]))
        return "\n".join([cab, *extra, "Uso: /avisos off [1h|3h|mañana|siempre] · /avisos on"])

    # --- recogida y emisión --------------------------------------------------------------------------

    def registrar(self, task: dict, resumen: str) -> None:
        tid = task.get("id")
        with self._lock:
            data = self._load()
            if not tid or tid in data["mudas"]:
                return
            ret = bool(self.silenciado_hasta(data))
            rec = {"tid": tid, "titulo": _limpio(task.get("title")), "resumen": _limpio(resumen),
                   "desde": self.clock(), "ret": ret}
            for p in data["pendientes"]:
                if p["tid"] == tid:  # misma tarjeta otra vez (renotify): se actualiza, conserva su antigüedad
                    p.update(titulo=rec["titulo"], resumen=rec["resumen"], ret=p.get("ret") or ret)
                    break
            else:
                data["pendientes"].append(rec)
            self._save(data)

    def tick(self) -> list[str]:
        with self._lock:
            data = self._load()
            pend = data["pendientes"]
            if not pend or self.silenciado_hasta(data):
                return []
            now = self.clock()
            lote, ventana = int(valor("avisos.revision_lote")), float(valor("avisos.revision_ventana_horas"))
            vencido = min(float(p.get("desde") or now) for p in pend) + ventana * 3600 <= now
            if not (len(pend) >= max(1, lote) or vencido or any(p.get("ret") for p in pend)):
                return []
            data["pendientes"] = []  # antes de enviar: sin bucles si Telegram falla
            self._save(data)
        tids = [p["tid"] for p in pend]
        texto = self._texto(pend)
        try:
            # send_to devuelve None sin lanzar si falta token o chat: también es «no enviado» (no perder el aviso)
            if self.send(texto, self._teclado({"tids": tids, "texto": texto})) is None:
                raise RuntimeError("send devolvió None")
        except Exception as exc:
            log.warning("avisos_revision: resumen no enviado, vuelve a pendientes: %s", exc)
            with self._lock:
                actual = self._load()
                vistos = {p["tid"] for p in pend}
                actual["pendientes"] = pend + [p for p in actual["pendientes"] if p["tid"] not in vistos]
                self._save(actual)
            return []
        return tids

    @staticmethod
    def _texto(pend: list[dict]) -> str:
        n = len(pend)
        lineas = [f"📋 {n} tarjeta{'s' if n != 1 else ''} lista{'s' if n != 1 else ''} para revisar:"]
        for p in pend:
            resumen = f" · {p['resumen']}" if p.get("resumen") else ""
            lineas.append("• " + truncate(f"{p['tid']} · {p.get('titulo') or ''}{resumen}", LINEA_MAX))
        lineas.append("Cada una tiene su ficha con ✅ Aprobar (/aprobar) · detalle en /tareas")
        out = "\n".join(lineas)
        return out if len(out) <= TEXTO_MAX else truncate(out, TEXTO_MAX)

    def _teclado(self, rec: dict, *, mudas: tuple = ()) -> dict | None:
        if not self.issue:
            return None
        spec = [[{"text": "🔕 1 h", "action": AV_1H}, {"text": "🔕 3 h", "action": AV_3H},
                 {"text": "🔕 Hasta mañana", "action": AV_MANANA}]]
        for i, tid in enumerate(rec["tids"][:TARJETAS_CON_BOTON]):
            if tid not in mudas:
                spec.append([{"text": f"🙈 No avisar de {tid}", "action": AV_TAREA, "index": i}])
        return self.issue({"avisos_revision": True, "chat": self.chat, **rec}, spec)[1]

    # --- botones -------------------------------------------------------------------------------------

    def on_button(self, action: str, rec: dict, where: dict | None, desk, index=None) -> bool:
        now, tids, texto = self.clock(), list(rec.get("tids") or ()), rec.get("texto") or ""
        if action in (AV_1H, AV_3H, AV_MANANA):
            hasta = {AV_1H: now + 3600, AV_3H: now + 3 * 3600, AV_MANANA: hasta_manana(now)}[action]
            self.silenciar(hasta)
            nota = f"🔕 Silenciado hasta {_fecha(hasta)}. Lo que llegue saldrá en un resumen al terminar."
            self._editar(desk, where, f"{texto}\n\n{nota}", self._reactivar(rec))
            return True
        if action == AV_ON:
            self.reactivar()
            self._editar(desk, where, f"{texto}\n\n🔔 Avisos reactivados.", None)
            return True
        if action == AV_TAREA:
            if not isinstance(index, int) or not 0 <= index < len(tids):
                return False
            self.mutear(tids[index])
            mudas = tuple(self._load()["mudas"])
            nota = "\n".join(f"🙈 {t}: no vuelvo a avisar de ella" for t in tids if t in mudas)
            self._editar(desk, where, f"{texto}\n\n{nota}", self._teclado({"tids": tids, "texto": texto}, mudas=mudas))
            return True
        return False

    def _reactivar(self, rec: dict) -> dict | None:
        if not self.issue:
            return None
        keep = {"avisos_revision": True, "chat": self.chat, "tids": rec.get("tids") or [], "texto": rec.get("texto") or ""}
        return self.issue(keep, [[{"text": "🔔 Reactivar", "action": AV_ON}]])[1]

    @staticmethod
    def _editar(desk, where: dict | None, text: str, markup: dict | None) -> None:
        if not where or not where.get("message_id"):
            return
        try:
            kw = {"reply_markup": markup} if markup else {}
            desk.notifier.edit(where["chat_id"], where["message_id"], text[:TEXTO_MAX + 400], html=False, **kw)
        except Exception as exc:
            log.warning("avisos_revision: no se pudo editar el resumen: %s", exc)


def _fecha(ts) -> str:
    from .resumen_carriles import MADRID
    return datetime.fromtimestamp(float(ts), MADRID).strftime("%d-%m %H:%M")


class AvisosRevisionTicker:
    """Bucle del runner. Un fallo no tumba el runner."""

    def __init__(self, avisos: AvisosRevision, minutes: float = 5, clock: Callable[[], float] = time.time):
        self.avisos, self.minutes, self.clock = avisos, minutes, clock
        self._last = 0.0

    def tick(self) -> list[str]:
        now = self.clock()
        if now - self._last < self.minutes * 60:
            return []
        self._last = now
        try:
            return self.avisos.tick()
        except Exception as exc:
            log.warning("resumen de tarjetas listas para revisar falló: %s", exc)
            return []


def comando(texto: str) -> str:
    """Cuerpo de `/avisos [off [dur]|on]`."""
    av = ACTIVO
    if av is None:
        return "Los avisos agrupados no están activos en este runner."
    partes = _limpio(texto).split(" ", 1)
    sub, resto = partes[0].lower(), (partes[1] if len(partes) > 1 else "")
    if sub in ("", "estado"):
        return av.estado_texto()
    if sub == "on":
        av.reactivar()
        return "🔔 Avisos de «listo para revisar» reactivados"
    if sub == "off":
        hasta = parse_duracion(resto or "siempre", av.clock())
        if hasta is None:
            return "No entiendo la duración. Uso: /avisos off [1h|3h|mañana|siempre]"
        av.silenciar(hasta)
        return ("🔕 Avisos apagados hasta que hagas /avisos on" if hasta == "siempre"
                else f"🔕 Avisos silenciados hasta {_fecha(hasta)}") + ". Las tarjetas siguen su curso (/tareas)."
    return "Uso: /avisos [off [1h|3h|mañana|siempre]|on]"
