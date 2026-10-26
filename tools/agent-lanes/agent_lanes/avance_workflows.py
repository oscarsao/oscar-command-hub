"""Avance de los workflows de Telegram: cuando una tarjeta termina se crean los pasos que desbloquea.

Antes `workflows.card_done` y `record_spend` no los llamaba nadie y solo se creaba la primera tanda (t_ff4de3a1).
`AvanceWorkflows.tick()` mira el kanban de cada workflow vivo y, por cada tarjeta activa:
  - `done`      -> `card_done`, suma su coste real (`cost_usd` del worker y de la review, en € con `eur_por_usd`) y,
                   con todo hecho, entrega (un único mensaje); si no, `launch` crea los pasos desbloqueados y avisa
                   «Paso k/N en marcha: …»;
  - `archived`  -> el workflow queda «bloqueado» y se avisa a quien lo pidió.
Los avisos van al chat/hilo guardado en `wf["origen"]` (el de la aprobación); sin él, al destino de reserva (DM de Oscar).
Todo el estado vive en el Store de workflows: cada transición se persiste antes de avisar, así un aviso no se repite.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Callable

from . import resumen_carriles, workflows
from .review import implementation_metadata

log = logging.getLogger("agent_lanes")
TITLE_MAX = 90


def coste_usd(show: dict) -> float:
    """USD gastados en la tarjeta: implementación + review (la misma suma que el aviso de «lista para merge»)."""
    meta = implementation_metadata(show)
    review = meta.get("review") or {}
    return sum(float(c or 0) for c in (meta.get("cost_usd"), review.get("cost_usd")))


def make_create_card(wf: dict, lanes: dict, hermes_for: Callable, truncate: Callable[[str, int], str]):
    """`create_card(item, req) -> id` del workflow `wf` sobre el kanban real (lo usan la aprobación y el avance)."""
    wid, origin = wf["id"], wf.get("origen") or {}

    def create_card(item: dict, req: str) -> str:
        lane = lanes[item["carril"]]
        body = (f"## Objetivo\n{item.get('cuerpo') or item['titulo']}\n\n## Límites\n"
                f"{item.get('limites') or '- Solo lo que pide el objetivo; nada más.'}\n\n"
                f"req: {req}\nOrigen: /workflow {wid}\n"
                f"Origen-Telegram: chat={origin.get('chat')} thread={origin.get('thread')}\n")
        key = "wf-" + hashlib.sha1(f"{wid}|{item['titulo']}".encode()).hexdigest()[:16]
        return hermes_for(lane.board).create(truncate(item["titulo"], TITLE_MAX), body, item["carril"],
                                             key=key, created_by="oscar")

    return create_card


class AvanceWorkflows:
    def __init__(self, store: workflows.Store, lanes: dict, hermes_for: Callable, send: Callable,
                 truncate: Callable[[str, int], str], issue: Callable | None = None,
                 fallback: tuple | None = None, ajustes: Callable[[], workflows.Ajustes] = workflows.load_ajustes):
        """`send(chat, thread, text, markup=None)`; `issue(rec, spec) -> (_, markup)` crea los botones de gasto."""
        self.store, self.lanes, self.hermes_for, self.send = store, lanes, hermes_for, send
        self.truncate, self.issue, self.fallback, self.ajustes = truncate, issue, fallback or (None, None), ajustes

    def _avisar(self, wf: dict, text: str, markup: dict | None = None) -> None:
        o = wf.get("origen") or {}
        chat, thread = (o.get("chat"), o.get("thread")) if o.get("chat") else self.fallback
        try:
            self.send(chat, thread, text, markup)
        except Exception as exc:  # un aviso que falla no debe frenar el avance
            log.warning("workflow %s: aviso falló: %s", wf["id"], type(exc).__name__)

    def _carril(self, wf: dict, card: dict) -> str | None:
        if card.get("carril"):
            return card["carril"]
        return next((i["carril"] for i in wf["plan"] if i["titulo"] == card["titulo"]), None)

    def _show(self, wf: dict, card: dict) -> dict | None:
        carril = self._carril(wf, card)
        lane = self.lanes.get(carril)
        if lane is None:
            return None
        try:
            return self.hermes_for(lane.board).show(card["id"])
        except Exception as exc:
            log.warning("workflow %s: no se pudo leer %s: %s", wf["id"], card["id"], type(exc).__name__)
            return None

    def tick(self) -> list[str]:
        """Una pasada. Devuelve las tarjetas nuevas creadas (para el log). Nunca lanza."""
        creadas: list[str] = []
        try:
            # Sin `origen` (workflows anteriores al avance automático) no se tocan: los reactiva el integrador a mano.
            todos = [w for w in self.store.load().values()
                     if w.get("tipo") == workflows.TIPO and w.get("estado") in (*workflows.ACTIVOS, "pausado")]
            vivos = [w for w in todos if w.get("origen")]
            for w in todos:  # F1-04 (FL-01): los sin `origen` no avanzan solos; que se vea en el resumen diario
                if not w.get("origen"):
                    resumen_carriles.anotar("workflow_sin_origen", f"{w.get('id')} · "
                                            f"{w.get('titulo') or w.get('nombre') or 'sin título'}: sin `origen`, "
                                            "no avanza solo (lo reactiva el integrador)", clave=str(w.get("id")))
        except Exception as exc:
            log.warning("workflows: no se pudo leer el estado: %s", type(exc).__name__)
            return creadas
        for w in vivos:
            try:
                creadas += self._avanzar(w["id"])
            except Exception as exc:
                log.warning("workflow %s: avance falló: %s", w.get("id"), type(exc).__name__)
        return creadas

    def _avanzar(self, wid: str) -> list[str]:
        wf = self.store.get(wid)
        if wf is None:
            return []
        aj = self.ajustes()
        for card in [c for c in wf["tarjetas"] if c["estado"] == "activa"]:
            show = self._show(wf, card)
            status = ((show or {}).get("task") or {}).get("status")
            if status == "done":
                workflows.card_done(self.store, wid, card["id"])
                self._gasto(wid, card["id"], show, aj)
            elif status == "archived":
                wf = workflows.card_blocked(self.store, wid, card["id"])
                self._avisar(wf, f"⚠️ {wf['titulo']} ({wf['req']}) queda bloqueado: la tarjeta {card['id']} "
                             f"«{self.truncate(card['titulo'], 60)}» se archivó sin terminar. No creo más pasos hasta "
                             "que decidas qué hacer.")
                return []
        wf = self.store.get(wid)
        if wf["estado"] in (*workflows.ACTIVOS, "pausado") and all(
                any(c["titulo"] == i["titulo"] and c["estado"] == "hecha" for c in wf["tarjetas"]) for i in wf["plan"]):
            return self._entregar(wid)  # también si el gasto de la última tarjeta cruzó el tope
        if wf["estado"] not in workflows.ACTIVOS:  # pausado por gasto o bloqueado: no se crea nada
            return []
        nuevos = workflows.launch(self.store, wid,
                                  make_create_card(wf, self.lanes, self.hermes_for, self.truncate), aj)
        if nuevos:
            wf = self.store.get(wid)
            nombres = [c["titulo"] for c in wf["tarjetas"] if c["id"] in nuevos]
            hechas = sum(1 for c in wf["tarjetas"] if c["estado"] == "hecha")
            self._avisar(wf, f"▶️ Paso {hechas + 1}/{len(wf['plan'])} en marcha ({wf['req']}): "
                         + "; ".join(self.truncate(n, 70) for n in nombres))
        return nuevos

    def _gasto(self, wid: str, card_id: str, show: dict, aj: workflows.Ajustes) -> None:
        wf = self.store.get(wid)
        card = next(c for c in wf["tarjetas"] if c["id"] == card_id)
        if card.get("gasto_registrado"):
            return
        card["gasto_registrado"] = True
        self.store.put(wf)
        eur = round(coste_usd(show) * aj.eur_por_usd, 4)
        wf, evento = workflows.record_spend(self.store, wid, eur)
        if evento:
            markup = None
            if self.issue and evento == "pausa":
                rec = {"task_id": "workflow", "board": "", "lane": "", "kind": "workflow", "wid": wid}
                spec = [[{"text": "➕ Ampliar", "action": "workflow", "index": 2},
                         {"text": "⛔ Parar", "action": "workflow", "index": 3}]]
                markup = self.issue(rec, spec)[1]
            self._avisar(wf, workflows.render_gasto(wf, evento)[0], markup)

    def _entregar(self, wid: str) -> list[str]:
        wf = workflows.deliver(self.store, wid)
        pasos = "; ".join(self.truncate(i["titulo"], 60) for i in wf["plan"])
        self._avisar(wf, workflows.render_entrega(wf, pasos, [c["id"] for c in wf["tarjetas"]], wf["prueba"]))
        return []


class AvanceTicker:
    """Para el bucle del runner: una pasada cada `minutes`."""

    def __init__(self, avance: AvanceWorkflows, minutes: float = 10, clock: Callable[[], float] = time.time):
        self.avance, self.minutes, self.clock, self._last = avance, minutes, clock, 0.0

    def tick(self) -> list[str]:
        now = self.clock()
        if now - self._last < self.minutes * 60:
            return []
        self._last = now
        return self.avance.tick()
