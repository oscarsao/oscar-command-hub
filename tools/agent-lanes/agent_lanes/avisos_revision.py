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

from . import nivel_avisos
from .ajustes_hub import valor
from .config import ROOT
from .notices import truncate

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "avisos_revision.json"
AV_1H, AV_3H, AV_MANANA, AV_TAREA, AV_ON = "av_1h", "av_3h", "av_manana", "av_tarea", "av_on"
AV_LOTE = "av_lote"
AV_ACTIONS = (AV_1H, AV_3H, AV_MANANA, AV_TAREA, AV_ON, AV_LOTE)
ENVIADAS_MAX = 400
ENVIADAS_DIAS = 14
SEVERIDADES_GRAVES = ("blocker", "major", "critical")
ESTADOS = ("done", "review")  # done = lista para merge (carril review); review = pendiente de validar (carril ops)
LINEA_MAX = 220
TEXTO_MAX = 3800
TARJETAS_CON_BOTON = 8

ACTIVO: "AvisosRevision | None" = None  # lo instala el runner; sin él nada cambia (tests, consolas, --once)


def instalar(instancia: "AvisosRevision | None") -> None:
    global ACTIVO
    ACTIVO = instancia


def bajo_riesgo(files, findings, titulo: str = "", policy=None) -> bool:
    """¿Se puede aprobar en lote? Solo si hay datos (archivos y hallazgos conocidos), ningún hallazgo blocker/major/
    critical, el carril no despliega a producción ni es el del propio sistema, y ni los archivos ni el título tocan
    lo que lista `avisos.lote_rutas_riesgo` (entorno, pagos, datos de cliente, migraciones, deploy…)."""
    if files is None or findings is None:
        return False
    if any(str(f.get("severity") if isinstance(f, dict) else f).lower() in SEVERIDADES_GRAVES for f in findings):
        return False
    from .integration import RISKS, risk_of, risks_from_files
    if risk_of(policy).emoji in (RISKS["production"].emoji, RISKS["system"].emoji) or risks_from_files(policy, files):
        return False
    pajar = [str(f).lower() for f in files] + [str(titulo or "").lower()]
    return not any(str(c).lower() in t for c in valor("avisos.lote_rutas_riesgo") for t in pajar)


def interceptar(state: str, task: dict, status: str, alert: bool, *, summary=None, for_oscar=None, lane=None,
                changed_files=None, findings=None, policy=None) -> bool:
    """Devuelve el `alert` efectivo: False si el aviso lo recoge el resumen agrupado; el original si no aplica.
    `lane`/`changed_files`/`findings`/`policy`: solo de la tarjeta done del carril review; deciden si entra en el lote."""
    if not alert or ACTIVO is None or state not in ESTADOS:
        return alert
    try:
        bajo = (state == "done" and lane is not None and getattr(lane, "kind", "") != "ops"
                and bajo_riesgo(changed_files, findings, (task or {}).get("title"), policy))
        ACTIVO.registrar(task, for_oscar or summary or status, lane=getattr(lane, "name", None), bajo=bool(bajo))
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
                "pendientes": [p for p in raw.get("pendientes") or () if isinstance(p, dict) and p.get("tid")],
                "enviadas": [p for p in raw.get("enviadas") or () if isinstance(p, dict) and p.get("tid")]}

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

    def pendientes_texto(self) -> str:
        """Cuerpo de `/revision`: las tarjetas listas para revisar que esperan en el resumen (lo mismo que enviaría)."""
        pend = self._load()["pendientes"]
        if not pend:
            return "📋 Ninguna tarjeta lista para revisar esperando en el resumen."
        return self._texto(pend)

    # --- recogida y emisión --------------------------------------------------------------------------

    def registrar(self, task: dict, resumen: str, lane: str | None = None, bajo: bool = False) -> None:
        tid = task.get("id")
        with self._lock:
            data = self._load()
            if not tid or tid in data["mudas"]:
                return
            ret = bool(self.silenciado_hasta(data))
            rec = {"tid": tid, "titulo": _limpio(task.get("title")), "resumen": _limpio(resumen),
                   "desde": self.clock(), "ret": ret, "lane": lane, "bajo": bajo}
            for p in data["pendientes"]:
                if p["tid"] == tid:  # misma tarjeta otra vez (renotify): se actualiza, conserva su antigüedad
                    p.update(titulo=rec["titulo"], resumen=rec["resumen"], ret=p.get("ret") or ret, lane=lane, bajo=bajo)
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
            if not nivel_avisos.activo("revision"):  # nivel «esencial»: sin resumen; queda a demanda en /revision
                vivas = [p for p in pend if float(p.get("desde") or now) >= now - ENVIADAS_DIAS * 86400]
                if len(vivas) != len(pend):  # lo que lleva semanas sin resumirse no se acumula para siempre
                    data["pendientes"] = vivas
                    self._save(data)
                return []
            lote, ventana = int(valor("avisos.revision_lote")), float(valor("avisos.revision_ventana_horas"))
            vencido = min(float(p.get("desde") or now) for p in pend) + ventana * 3600 <= now
            if not (len(pend) >= max(1, lote) or vencido or any(p.get("ret") for p in pend)):
                return []
            data["pendientes"] = []  # antes de enviar: sin bucles si Telegram falla
            self._save(data)
            lotes = self._lotes(pend, data)
        tids = [p["tid"] for p in pend]
        texto = self._texto(pend, lotes)
        try:
            # send_to devuelve None sin lanzar si falta token o chat: también es «no enviado» (no perder el aviso)
            if self.send(texto, self._teclado({"tids": tids, "texto": texto, "lotes": lotes})) is None:
                raise RuntimeError("send devolvió None")
        except Exception as exc:
            log.warning("avisos_revision: resumen no enviado, vuelve a pendientes: %s", exc)
            with self._lock:
                actual = self._load()
                vistos = {p["tid"] for p in pend}
                actual["pendientes"] = pend + [p for p in actual["pendientes"] if p["tid"] not in vistos]
                self._save(actual)
            return []
        with self._lock:  # lo enviado de bajo riesgo sigue siendo candidato al lote de los próximos resúmenes
            actual = self._load()
            vistos = {p["tid"] for p in pend}
            corte = self.clock() - ENVIADAS_DIAS * 86400
            previas = [p for p in actual["enviadas"] if p["tid"] not in vistos and float(p.get("desde") or 0) >= corte]
            actual["enviadas"] = (previas + [p for p in pend if p.get("bajo") and p.get("lane")])[-ENVIADAS_MAX:]
            self._save(actual)
        return tids

    def _lotes(self, pend: list[dict], data: dict) -> list[dict]:
        """Por carril con al menos `avisos.lote_aprobar_min` tarjetas de bajo riesgo sin decidir (las de este resumen y
        las de resúmenes anteriores) y alguna en este resumen: [{lane, tids}]."""
        minimo = max(1, int(valor("avisos.lote_aprobar_min")))
        nuevas = {p["tid"] for p in pend}
        por_carril: dict[str, dict[str, None]] = {}
        for p in [*data["enviadas"], *pend]:
            if p.get("bajo") and p.get("lane") and p["tid"] not in data["mudas"]:
                por_carril.setdefault(p["lane"], {})[p["tid"]] = None
        return [{"lane": lane, "tids": list(tids)} for lane, tids in por_carril.items()
                if len(tids) >= minimo and nuevas & set(tids)]

    @staticmethod
    def _texto(pend: list[dict], lotes: list[dict] | None = None) -> str:
        n = len(pend)
        lineas = [f"📋 {n} tarjeta{'s' if n != 1 else ''} lista{'s' if n != 1 else ''} para revisar:"]
        for p in pend:
            resumen = f" · {p['resumen']}" if p.get("resumen") else ""
            lineas.append("• " + truncate(f"{p['tid']} · {p.get('titulo') or ''}{resumen}", LINEA_MAX))
        lineas.append("Cada una tiene su ficha con ✅ Aprobar (/aprobar) · detalle en /tareas")
        for lote in lotes or ():
            lineas.append(f"✅ {len(lote['tids'])} de {lote['lane']} son de bajo riesgo (review aprobada, sin hallazgos "
                          "graves, sin producción, pagos, entorno ni datos de cliente): puedes aprobarlas de una vez.")
        out = "\n".join(lineas)
        return out if len(out) <= TEXTO_MAX else truncate(out, TEXTO_MAX)

    def _teclado(self, rec: dict, *, mudas: tuple = ()) -> dict | None:
        if not self.issue:
            return None
        spec = [[{"text": "🔕 1 h", "action": AV_1H}, {"text": "🔕 3 h", "action": AV_3H},
                 {"text": "🔕 Hasta mañana", "action": AV_MANANA}]]
        lotes = rec.get("lotes") or []
        for i, lote in enumerate(lotes):
            n = len([t for t in lote["tids"] if t not in mudas])
            if n:
                carril = f" · {lote['lane']}" if len(lotes) > 1 else ""
                spec.append([{"text": f"✅ Aprobar todas las de bajo riesgo ({n}){carril}", "action": AV_LOTE, "index": i}])
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
            self._editar(desk, where, f"{texto}\n\n{nota}",
                         self._teclado({"tids": tids, "texto": texto, "lotes": rec.get("lotes") or []}, mudas=mudas))
            return True
        if action == AV_LOTE:
            lotes = rec.get("lotes") or []
            if not isinstance(index, int) or not 0 <= index < len(lotes):
                return False
            return self._aprobar_lote(lotes, index, rec, where, desk)
        return False

    def _elegible(self, show: dict, lane, policy) -> str | None:
        """Motivo por el que una tarjeta ya NO puede entrar en el lote (se vuelve a comprobar al pulsar); None = entra."""
        from .review import aprobada_por_oscar, implementation_metadata
        task = (show or {}).get("task") or {}
        if task.get("status") != "done":
            return "ya no está lista"
        if aprobada_por_oscar(show):
            return "ya aprobada"
        meta = implementation_metadata(show)
        review = meta.get("review") or {}
        if review.get("status") != "approve":
            return "sin review aprobada"
        if not bajo_riesgo(meta.get("changed_files"), review.get("findings"), task.get("title"), policy):
            return "no es de bajo riesgo"
        return None

    def _aprobar_lote(self, lotes: list[dict], index: int, rec: dict, where: dict | None, desk) -> bool:
        """Aprueba una a una (misma ruta que ✅ Aprobar) las tarjetas del lote que SIGUEN siendo de bajo riesgo:
        la elegibilidad se recalcula con el kanban, no se fía del resumen. Nunca fusiona (eso es del integrador)."""
        from .review import implementation_metadata
        lote = lotes[index]
        lane = desk.lanes.get(lote["lane"])
        if lane is None or getattr(lane, "kind", "") == "ops":
            return False
        policies = getattr(getattr(desk.integrator, "settings", None), "policies", None) or {}
        policy, h = policies.get(lane.name), desk.hermes_for(lane.board)
        mudas = set(self._load()["mudas"])
        hechas, saltadas, fallidas = [], [], []
        for tid in lote["tids"]:
            if tid in mudas:
                continue
            try:
                show = h.show(tid)
                if self._elegible(show, lane, policy):
                    saltadas.append(tid)
                    continue
                meta = implementation_metadata(show)
                m = {"task_id": tid, "board": lane.board, "lane": lane.name, "title": (show["task"].get("title") or ""),
                     "body": "", "changed_files": list(meta.get("changed_files") or [])[:50], "lote": True,
                     "lote_actor": (where or {}).get("user_id")}
                ok = desk._approve(m, None)
            except Exception as exc:
                log.warning("avisos_revision: lote %s: no se pudo aprobar %s: %s", lote["lane"], tid, exc)
                ok = False
            if ok:
                desk._retire(m)
                hechas.append(tid)
            elif tid not in saltadas:
                fallidas.append(tid)
        with self._lock:  # decididas o ya no elegibles: no cuentan para futuros lotes
            data = self._load()
            fuera = set(hechas) | set(saltadas)
            data["enviadas"] = [p for p in data["enviadas"] if p["tid"] not in fuera]
            self._save(data)
        partes = [f"✅ Lote de {lane.name}: {len(hechas)} aprobada{'s' if len(hechas) != 1 else ''}"
                  + (f" ({', '.join(hechas)})" if hechas else "")]
        if saltadas:
            partes.append(f"fuera del lote (cambió su estado o su riesgo): {', '.join(saltadas)}")
        if fallidas:
            partes.append(f"fallaron, apruébalas con su ficha: {', '.join(fallidas)}")
        restantes = [x for i, x in enumerate(lotes) if i != index]
        texto = rec.get("texto") or ""
        self._editar(desk, where, f"{texto}\n\n" + " · ".join(partes),
                     self._teclado({"tids": list(rec.get("tids") or ()), "texto": texto, "lotes": restantes},
                                   mudas=tuple(mudas)))
        return bool(hechas) or not fallidas

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
