"""Aviso diario de las 9:00: tarjetas de carril paradas más de 24 h (decisión de Oscar 23-10, auditoría de bots nº 8).

Una vez al día el runner publica en el tema de Integración (o en el DM de Oscar si no hay) una ficha por tarjeta de carril
que lleva más de `atascos.paradas_horas` sin moverse en estas colas: ready sin worker, blocked, triage y review sin revisar.
Quedan fuera las aparcadas (`scheduled`, o asignadas a `oscar`) y las de personas: solo se leen las tarjetas asignadas a un
carril de lanes.yaml, y nunca las `running`. Sin ninguna parada no se manda mensaje. Cada ficha lleva
[🔁 Reintentar] [🛠 Integrador] [🗄 Archivar]; nada se toca sin el botón de Oscar.

- `Paradas.encontrar`: solo lectura (lo usa también /salud con `resumen`).
- `ParadasRunner`: publica las fichas y atiende los botones.
- `ParadasTicker`: bucle del runner; una vez al día, pasada la hora (`atascos.paradas_hora`, editable con /ajustes).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .ajustes_hub import valor
from .config import ROOT
from .hermes import resume
from .notices import decision_since, truncate

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "paradas.json"
PA_RETRY, PA_INTEGRADOR, PA_ARCHIVE = "pa_retry", "pa_integrador", "pa_archive"
PA_ACTIONS = (PA_RETRY, PA_INTEGRADOR, PA_ARCHIVE)
AUTHOR = "paradas"
INTEGRATOR_PROFILE = "oscar-e2"  # = reconcile.INTEGRATOR_PROFILE
PARK_PROFILE = "oscar"           # = decisions.PARK_PROFILE
STATUSES = ("ready", "blocked", "triage", "review")
LABELS = {"ready": "en cola sin worker", "blocked": "bloqueada", "triage": "en triage", "review": "en review sin revisar"}
QUIET_EVENTS = ("commented", "comment")  # un comentario no mueve la tarjeta
TITLE_MAX = 60


@dataclass(frozen=True)
class Parada:
    tid: str
    board: str
    lane: str
    title: str
    status: str
    since: float

    def desde(self, now: float) -> str:
        return duracion(now - self.since)

    def linea(self, now: float) -> str:
        return f"{self.tid} · {truncate(self.title, TITLE_MAX)} · {LABELS.get(self.status, self.status)} · hace {self.desde(now)}"


def duracion(seconds: float) -> str:
    h = max(0, int(seconds // 3600))
    return f"{h // 24} d {h % 24} h" if h >= 24 else f"{h} h"


def _stamp(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value else None


def desde_cuando(task: dict, show: dict | None) -> float | None:
    """Último movimiento de la tarjeta: el último evento que no sea un comentario; sin él, lo que dicen la propia tarea
    (review: cuando pidió la revisión) o su creación."""
    stamps = [_stamp(e.get("created_at")) for e in (show or {}).get("events") or () if e.get("kind") not in QUIET_EVENTS]
    last = max((s for s in stamps if s), default=None)
    if last:
        return last
    t = (show or {}).get("task") or task
    return _stamp(t.get("completed_at")) if t.get("status") == "review" and _stamp(t.get("completed_at")) \
        else decision_since(t, show)


class Paradas:
    """Lee los tableros de los carriles. `hermes_for(board)` -> cliente; `lanes`: lanes.yaml (nombre -> Lane)."""

    def __init__(self, lanes: dict, hermes_for: Callable, clock: Callable[[], float] = time.time):
        self.lanes, self.hermes_for, self.clock = lanes, hermes_for, clock

    def encontrar(self, horas: float | None = None) -> list[Parada]:
        """Paradas más de `horas` (por defecto `atascos.paradas_horas`), la más antigua primero. Una tarjeta o tablero
        ilegible se salta con un aviso en el log: nunca tumba el aviso."""
        horas = valor("atascos.paradas_horas") if horas is None else horas
        now, seen, found = self.clock(), set(), []
        for name, lane in self.lanes.items():
            if not getattr(lane, "board", None):
                continue
            try:
                h = self.hermes_for(lane.board)
            except Exception as exc:
                log.warning("paradas: tablero %s no disponible: %s", lane.board, exc)
                continue
            for status in STATUSES:
                try:
                    listed = h.list_status(name, status)
                except Exception as exc:
                    log.warning("paradas: no se pudo listar %s de %s: %s", status, name, exc)
                    continue
                for t in listed:
                    tid = t.get("id")
                    if not tid or tid in seen or t.get("status", status) != status:
                        continue
                    created = _stamp(t.get("created_at"))
                    if created and now - created < horas * 3600:
                        continue  # más joven que el umbral: no hace falta ni leerla
                    try:
                        show = h.show(tid)
                    except Exception as exc:
                        log.info("paradas: %s no leída: %s", tid, exc)
                        show = None
                    task = (show or {}).get("task") or t
                    if task.get("status", status) != status:
                        continue
                    since = desde_cuando(t, show)
                    if since and now - since >= horas * 3600:
                        seen.add(tid)
                        found.append(Parada(tid, lane.board, name, task.get("title") or t.get("title") or tid, status,
                                            since))
        return sorted(found, key=lambda p: (p.since, p.tid))

    def resumen(self) -> str:
        """Línea para /salud."""
        found = self.encontrar()
        if not found:
            return "ninguna tarjeta parada"
        horas = valor("atascos.paradas_horas")
        por: dict[str, int] = {}
        for p in found:
            por[p.status] = por.get(p.status, 0) + 1
        return (f"{len(found)} parada{'s' if len(found) != 1 else ''} más de {horas:g} h ("
                + ", ".join(f"{n} {LABELS[s]}" for s, n in por.items()) + ") · la más antigua hace "
                + found[0].desde(self.clock()))


class _State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(tmp, self.path)


class ParadasRunner:
    """`send(text, markup)` publica en el canal de Oscar; `issue(rec, spec)` -> (token, reply_markup) (CallbackStore)."""

    def __init__(self, lanes: dict, hermes_for: Callable, send: Callable[[str, dict | None], object],
                 issue: Callable | None = None, state_path: Path | str = STATE_PATH,
                 clock: Callable[[], float] = time.time):
        self.lanes, self.hermes_for, self.send, self.issue, self.clock = lanes, hermes_for, send, issue, clock
        self.state_path = Path(state_path)
        self.paradas = Paradas(lanes, hermes_for, clock)

    def publicar(self) -> list[str]:
        """Cabecera + una ficha con botones por tarjeta parada. Sin paradas no manda nada."""
        found = self.paradas.encontrar()
        if not found:
            return []
        now, tope = self.clock(), int(valor("atascos.paradas_max"))
        horas = valor("atascos.paradas_horas")
        n = len(found)
        self.send(f"⏳ {n} tarjeta{'s' if n != 1 else ''} de carril parada{'s' if n != 1 else ''} más de {horas:g} h"
                  + (f" · muestro las {tope} más antiguas" if n > tope else "")
                  + "\n➡️ Próximo paso: reintentarla, pasarla al integrador o archivarla · Lo da: Oscar", None)
        for p in found[:tope]:
            markup = None
            if self.issue:
                rec = {"task_id": p.tid, "board": p.board, "lane": p.lane, "title": p.title, "paradas": True}
                markup = self.issue(rec, [[{"text": "🔁 Reintentar", "action": PA_RETRY},
                                           {"text": "🛠 Integrador", "action": PA_INTEGRADOR},
                                           {"text": "🗄 Archivar", "action": PA_ARCHIVE}]])[1]
            self.send(p.linea(now) + f" · carril {p.lane}", markup)
        return [p.tid for p in found[:tope]]

    # --- botones -------------------------------------------------------------------------------------

    @staticmethod
    def _fin(desk, where: dict | None, text: str) -> None:
        """Resultado: edita la ficha pulsada (sin botones); si no se puede, responde aparte."""
        if not where:
            return
        try:
            if where.get("message_id") and hasattr(desk, "_edit_text"):
                desk._edit_text(where, text)
            elif getattr(desk, "notifier", None):
                desk.notifier.send_to(where["chat_id"], where.get("thread_id"), text, html=False)
        except Exception as exc:
            log.warning("paradas: no se pudo responder: %s", exc)

    def on_button(self, action: str, rec: dict, where: dict | None, desk) -> bool:
        tid, h = rec["task_id"], self.hermes_for(rec["board"])
        who = desk._who() if hasattr(desk, "_who") else "Oscar"
        try:
            status = (h.show(tid).get("task") or {}).get("status")
        except Exception as exc:
            log.info("paradas: %s no leída: %s", tid, exc)
            self._fin(desk, where, f"{tid}: no pude leer la tarjeta ahora")
            return False
        if status in ("done", "archived"):
            self._fin(desk, where, f"{tid}: ya estaba cerrada")
            return True
        if status == "running":
            self._fin(desk, where, f"{tid}: está en curso; no la toco")
            return False
        if action == PA_RETRY:
            if status in ("blocked", "triage"):
                new = resume(h, tid)
                if not new:
                    self._fin(desk, where, f"{tid}: no se pudo reencolar (mira la tarjeta)")
                    return False
                h.comment(tid, f"Reencolada por {who} desde el aviso de tarjetas paradas", author=AUTHOR)
                self._fin(desk, where, f"🔁 {tid} reencolada" + ("" if new == "ready" else f" · queda en {new}"))
            else:
                self._fin(desk, where, f"{tid}: ya está {LABELS.get(status, status)}; no hay nada que reintentar. "
                          "Si no avanza, pásala al integrador")
            return True
        if action == PA_INTEGRADOR:
            if not h.assign(tid, INTEGRATOR_PROFILE):
                self._fin(desk, where, f"{tid}: no se pudo pasar al integrador")
                return False
            h.comment(tid, f"Pasada al integrador ({INTEGRATOR_PROFILE}) por {who} desde el aviso de tarjetas paradas",
                      author=AUTHOR)
            self._fin(desk, where, f"🛠 {tid} la tiene el integrador ({INTEGRATOR_PROFILE})")
            return True
        if action == PA_ARCHIVE:
            if not h.archive(tid):
                self._fin(desk, where, f"{tid}: no se pudo archivar")
                return False
            h.comment(tid, f"Archivada por {who} desde el aviso de tarjetas paradas", author=AUTHOR)
            self._fin(desk, where, f"🗄 {tid} archivada")
            return True
        return False


class ParadasTicker:
    """Bucle del runner: una vez al día, pasada la hora (Madrid), publica. Un fallo no tumba el runner."""

    def __init__(self, runner: ParadasRunner, minutes: float = 10, clock: Callable[[], float] = time.time):
        self.runner, self.minutes, self.clock = runner, minutes, clock
        self._last = 0.0

    def tick(self) -> list[str]:
        from .resumen_carriles import MADRID
        now = self.clock()
        if now - self._last < self.minutes * 60:
            return []
        self._last = now
        try:
            local = datetime.fromtimestamp(now, MADRID)
            state = _State(self.runner.state_path)
            day = local.strftime("%Y-%m-%d")
            if local.hour < valor("atascos.paradas_hora") or state.data.get("last_day") == day:
                return []
            state.data["last_day"] = day  # antes de publicar: sin bucles si algo falla
            state.save()
            return self.runner.publicar()
        except Exception as exc:
            log.warning("aviso de tarjetas paradas falló: %s", exc)
            return []
