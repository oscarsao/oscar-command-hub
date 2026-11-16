"""Caducidad de lo que espera a Oscar: 3 recordatorios de 24 h y después «¿Sigue vigente?».

Decisión de Oscar (16-11-2026): «pregunta cada 24h los primeros 3 días y luego proponerme borrarlo o algo así».
Lógica pura con estado en `.state/caducidad.json` y reloj inyectado; quien envía los mensajes es CommandCenter.

Escalera de una tarjeta que espera a Oscar (preguntas y «tuyo»/«decidir»), contada desde que EMPEZÓ a esperarle:
  recordando -> como mucho un recordatorio cada `recordatorio_cada_horas`, `recordatorio_veces` veces (cada aviso dice
                cuántos quedan);
  vigente_pend -> pasados los recordatorios (o si ya llevaba más de `vigente_legado_dias` esperando al estrenar esto):
                cola para el aviso «¿Sigue vigente?», con tope de `vigente_tanda_dia` por día (hora de Madrid);
  vigente -> el aviso salió; sin respuesta en `vigente_responde_horas` se aplaza `vigente_aplazar_dias` días (una línea);
  reposo -> aplazada o mantenida hasta `hasta`; después reinicia el contador desde cero.
Una tarjeta que deja de esperar (contestada o movida) sale de la escalera y, si vuelve, empieza de cero.

Estado: {tid: {fase, first, n, last, asked, hasta}, "_dia": {fecha, n}}. Nunca lanza por un fichero ilegible.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
KINDS = ("pregunta", "tuyo")      # tipos de ítem de lo_tuyo que esperan a Oscar y caducan
RECORDANDO, PENDIENTE, VIGENTE, REPOSO = "recordando", "vigente_pend", "vigente", "reposo"
_DIA = "_dia"


class Escalera:
    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()

    # --- estado ----------------------------------------------------------------------------------------
    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.path)

    def estado(self, tid: str) -> dict | None:
        return self._load().get(tid)

    def reposo(self, tid: str, dias: float, now: float | None = None) -> None:
        """Aplazada o mantenida `dias` días: sin avisos hasta entonces; después el contador vuelve a empezar."""
        now = self._clock() if now is None else now
        with self._lock:
            data = self._load()
            data[tid] = {"fase": REPOSO, "hasta": now + dias * 86400}
            self._save(data)

    def olvidar(self, tid: str) -> None:
        with self._lock:
            data = self._load()
            if data.pop(tid, None) is not None:
                self._save(data)

    # --- una pasada ------------------------------------------------------------------------------------
    def tick(self, items: list[dict], cfg, *, enviar_recordatorios: Callable[[list[dict]], bool],
             enviar_vigente: Callable[[dict], bool], aplazar: Callable[[dict, float], None],
             avisar_aplazadas: Callable[[list[dict]], None], ocultas=(), completo: bool = True,
             now: float | None = None) -> dict[str, list[str]]:
        """Avanza la escalera. Un cambio de estado solo se guarda si su aviso salió (`enviar_*` devuelve True); si no, se
        reintenta en la próxima pasada. `completo=False` (alguna fuente de ítems falló o fue lenta) no limpia nada: una
        tarjeta que no se ve no es una tarjeta contestada. Devuelve los ids tratados por tipo."""
        now = self._clock() if now is None else now
        vivos = {it["tid"]: it for it in items if it.get("kind") in KINDS and it.get("tid") and it["tid"] not in set(ocultas)}
        out: dict[str, list[str]] = {"recordatorios": [], "vigentes": [], "aplazadas": []}
        with self._lock:
            data = self._load()
            if completo:
                for tid in [t for t in data if t != _DIA]:
                    e = data[tid]
                    if tid not in vivos and not (e.get("fase") == REPOSO and float(e.get("hasta") or 0) > now):
                        del data[tid]
            hoy = datetime.fromtimestamp(now, MADRID).strftime("%Y-%m-%d")
            dia = data.get(_DIA) if (data.get(_DIA) or {}).get("fecha") == hoy else {"fecha": hoy, "n": 0}
            cada, veces = cfg.recordatorio_cada_horas * 3600, cfg.recordatorio_veces
            legado = cfg.vigente_legado_dias * 86400
            recs: list[tuple[str, dict]] = []
            pend: list[dict] = []
            for it in sorted(vivos.values(), key=lambda i: (i.get("since") or now, i["tid"])):
                tid, since = it["tid"], it.get("since")
                e = data.get(tid)
                if e and e.get("fase") == REPOSO:
                    if now < float(e.get("hasta") or 0):
                        continue
                    e = None  # terminó el reposo: empieza de cero
                    reiniciada = True
                else:
                    reiniciada = False
                if not e:
                    first = min(float(since), now) if since else now
                    if reiniciada:
                        first = now
                    if not reiniciada and since and now - float(since) > legado:
                        e = {"fase": PENDIENTE, "first": first, "n": veces, "last": 0}  # ya esperaba mucho: sin recordatorios
                    else:
                        e = {"fase": RECORDANDO, "first": first, "n": 0, "last": 0}
                    data[tid] = e
                if e["fase"] == RECORDANDO:
                    if now - max(float(e.get("last") or 0), float(e["first"])) < cada:
                        continue
                    if e["n"] < veces:
                        recs.append((tid, {**e, "n": e["n"] + 1, "last": now}))
                        continue
                    e = data[tid] = {**e, "fase": PENDIENTE}
                if e["fase"] == PENDIENTE:
                    pend.append(it)
                elif e["fase"] == VIGENTE and now - float(e.get("asked") or now) >= cfg.vigente_responde_horas * 3600:
                    aplazar(it, cfg.vigente_aplazar_dias)
                    data[tid] = {"fase": REPOSO, "hasta": now + cfg.vigente_aplazar_dias * 86400}
                    out["aplazadas"].append(tid)
            if recs:
                items_rec = [{**vivos[t], "quedan": veces - e["n"]} for t, e in recs]
                if enviar_recordatorios(items_rec):
                    for t, e in recs:
                        data[t] = e
                    out["recordatorios"] = [t for t, _ in recs]
            for it in pend:
                if dia["n"] >= cfg.vigente_tanda_dia:
                    break
                if enviar_vigente(it):
                    data[it["tid"]] = {**data[it["tid"]], "fase": VIGENTE, "asked": now}
                    dia["n"] += 1
                    out["vigentes"].append(it["tid"])
            data[_DIA] = dia
            self._save(data)
        if out["aplazadas"]:
            avisar_aplazadas([vivos[t] for t in out["aplazadas"]])
        return out
