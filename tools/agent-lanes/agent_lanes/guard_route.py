"""Bloqueos por LIMITACION-GUARD: van a la cola del integrador, no a Oscar (t_f8f58acb).

Un worker que choca con el guard («requiere consola», «el guard no deja») no se resuelve desde Telegram: lo ejecuta el
integrador en una consola. Antes el aviso caía en Oscar, que no podía actuar, o no llegaba a nadie. Ahora:

1. `GuardRouter.tick()` (cada ~2 min, enganchado a `CommandCenter.veto_tick`) mete cada bloqueo de guard en la cola
   (`.state/integrador_cola.json`) y lo anuncia en el tema Atención con @mención del integrador: tarjeta, qué hace
   falta, comando exacto y quién lo ejecuta. `/integrador` lista la cola.
2. Si a las 2 h nadie lo ha resuelto (la tarjeta sigue bloqueada), llega a Oscar con @mención y dos botones:
   «Que lo resuelva el integrador» (la reasigna a oscar-e2) y «Aplazar» (24 h más en la cola).
3. Un bloqueo que Hermes pasó a triage por repetirse (block_loop_detected) también entra aquí: no vuelve a caer en
   triage en silencio. La cola cuenta cuántas veces ha vuelto la misma tarjeta («repetido»).

Nunca ejecuta nada ni desbloquea tarjetas: solo avisa. Quien pulsa los botones lo decide el DecisionDesk (operadores).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable

from .ajustes_hub import valor
from .aviso import mention
from .notices import INTEGRADOR, hours_ago, truncate

log = logging.getLogger("agent_lanes")

# Valores por defecto; los vigentes se editan con /ajustes (ajustes_hub: atascos.escalado_guard_horas / aplazar_horas).
WINDOW_HOURS = 2.0   # en la cola del integrador antes de pasar a Oscar
SNOOZE_HOURS = 24.0  # lo que dura «Aplazar»
DEFAULT_NEED = "el guard del carril no deja al worker hacerlo solo"


class GuardQueue:
    """Cola en un JSON: {"entries": {tid: {...}}, "veces": {tid: n}}. Un reinicio no la pierde."""

    def __init__(self, path: Path | str, now: Callable[[], float] = time.time):
        self.path = Path(path)
        self._now = now

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        if not isinstance(data.get("entries"), dict):
            data["entries"] = {}
        if not isinstance(data.get("veces"), dict):
            data["veces"] = {}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def entries(self) -> dict[str, dict]:
        return self._load()["entries"]

    def get(self, tid: str) -> dict | None:
        return self.entries().get(tid)

    def add(self, p) -> dict:
        """Entrada nueva para una tarjeta bloqueada por el guard; `veces` cuenta las veces que ha vuelto a la cola."""
        data = self._load()
        veces = int(data["veces"].get(p.tid) or 0) + 1
        data["veces"][p.tid] = veces
        entry = {"tid": p.tid, "lane": p.lane.name, "title": (p.task or {}).get("title") or "",
                 "need": getattr(p, "need", None) or DEFAULT_NEED, "queued_at": self._now(), "veces": veces,
                 "escalated_at": None, "snooze_until": 0}
        data["entries"][p.tid] = entry
        self._save(data)
        return entry

    def update(self, tid: str, **fields) -> None:
        data = self._load()
        if tid in data["entries"]:
            data["entries"][tid].update(fields)
            self._save(data)

    def remove(self, tid: str) -> None:
        data = self._load()
        if data["entries"].pop(tid, None) is not None:
            self._save(data)

    def snooze(self, tid: str, hours: float | None = None) -> bool:
        """«Aplazar»: vuelve a la cola del integrador y no se escala a Oscar hasta dentro de `hours` (por defecto el
        ajuste vigente). False = ya no está."""
        if tid not in self.entries():
            return False
        hours = valor("atascos.aplazar_horas") if hours is None else hours
        self.update(tid, snooze_until=self._now() + hours * 3600, escalated_at=None)
        return True


def command_for(tid: str) -> str:
    return f"/tomar-tarea {tid}"


def integrator_text(entry: dict, who: str = "", hours: float | None = None) -> str:
    """Mensaje a la cola del integrador: tarjeta, qué hace falta, comando exacto y quién lo ejecuta."""
    tid = entry["tid"]
    hours = valor("atascos.escalado_guard_horas") if hours is None else hours
    rep = f"\n⚠️ Repetido: es la {entry['veces']}ª vez que esta tarjeta vuelve a bloquearse por lo mismo." \
        if int(entry.get("veces") or 1) > 1 else ""
    return (f"🧑‍💻 Cola del integrador · {tid} · {truncate(entry.get('title'), 80)}\n"
            f"Carril: {entry.get('lane')}\n"
            f"Qué hace falta: {truncate(entry.get('need'), 300)}\n"
            f"Comando exacto: {command_for(tid)} (en una consola interactiva de Claude Code) y al acabar /cerrar-tarea {tid}\n"
            f"Quién lo ejecuta: {INTEGRADOR}{rep}\n"
            f"➡️ Próximo paso: ejecutar el comando · Lo da: {INTEGRADOR}. "
            f"Si en {int(hours)} h nadie la ha resuelto, se la paso a Oscar con botones." + (f"\n{who}" if who else ""))


def oscar_text(entry: dict, now: float, who: str = "") -> str:
    """Mensaje a Oscar cuando nadie ha resuelto el bloqueo en la ventana: qué decidir y el próximo paso."""
    tid = entry["tid"]
    return (f"⏰ {who + ' ' if who else ''}{tid} lleva {hours_ago(entry.get('queued_at'), now)} h en la cola del "
            f"integrador sin resolverse · {truncate(entry.get('title'), 80)}\n"
            f"Qué hace falta: {truncate(entry.get('need'), 300)}\n"
            f"Comando exacto: {command_for(tid)} (consola; no se puede desde Telegram)\n"
            f"➡️ Próximo paso: pulsa «Que lo resuelva el integrador» (se la paso a {INTEGRADOR}) o «Aplazar» "
            f"({int(valor("atascos.aplazar_horas"))} h más en su cola) · Lo da: Oscar, con un toque")


class GuardRouter:
    """Una pasada: cola ↔ tarjetas bloqueadas por el guard. `collect()` da los Pending con `.guard`;
    `to_integrator(p, entry)` / `to_oscar(p, entry)` devuelven True si el aviso salió (si no, se reintenta)."""

    def __init__(self, collect: Callable[[], list], to_integrator: Callable, to_oscar: Callable, queue: GuardQueue, *,
                 hours: float | None = None, now: Callable[[], float] = time.time, remind_hours: float | None = None,
                 remind: Callable | None = None):
        self.collect, self.to_integrator, self.to_oscar = collect, to_integrator, to_oscar
        self.queue, self.hours, self._now = queue, hours, now
        # Recordatorio a las 24 h (F1-03, FL-04): `recordar_horas` de workflows.yaml; `remind` = cómo (por defecto el
        # mismo aviso con botones a Oscar). Una vez al día por tarjeta, respetando el ⏳ Aplazar (snooze_until).
        self.remind_hours_cfg, self.remind = remind_hours, remind or to_oscar

    def remind_hours(self) -> float:
        if self.remind_hours_cfg is not None:
            return float(self.remind_hours_cfg)
        from .workflows import load_ajustes
        return float(load_ajustes().recordar_horas)

    def _toca_recordar(self, entry: dict, now: float) -> bool:
        from .cola_integrador import escalado
        if not entry.get("escalated_at") or now < float(entry.get("snooze_until") or 0):
            return False
        horas = (now - float(entry.get("queued_at") or now)) / 3600
        recordar = self.remind_hours()
        return (escalado(horas, self.window_hours(), recordar) == "recordar_24h"
                and now - float(entry.get("reminded_at") or 0) >= recordar * 3600)

    def window_hours(self) -> float:
        """Horas hasta escalar a Oscar: la fijada al construir (tests) o el ajuste vigente (recarga en caliente)."""
        return float(valor("atascos.escalado_guard_horas") if self.hours is None else self.hours)

    def tick(self) -> list[str]:
        """Devuelve lo hecho (`"cola t_x"` / `"oscar t_x"` / `"resuelta t_x"`), para tests y log."""
        done, live = [], {p.tid: p for p in self.collect()}
        for tid in list(self.queue.entries()):
            if tid not in live:  # ya no está bloqueada por el guard (resuelta, reasignada o aparcada)
                self.queue.remove(tid)
                done.append(f"resuelta {tid}")
        now = self._now()
        for tid, p in live.items():
            entry = self.queue.get(tid)
            try:
                if entry is None:
                    entry = self.queue.add(p)
                    if self.to_integrator(p, entry):
                        done.append(f"cola {tid}")
                    else:  # el aviso no salió: se vuelve a intentar en la próxima pasada (la cola conserva la entrada)
                        self.queue.update(tid, announced=False)
                elif entry.get("announced") is False:
                    if self.to_integrator(p, entry):
                        self.queue.update(tid, announced=True)
                        done.append(f"cola {tid}")
                elif (not entry.get("escalated_at") and now >= float(entry.get("snooze_until") or 0)
                      and now - float(entry["queued_at"]) >= self.window_hours() * 3600):
                    if self.to_oscar(p, entry):
                        self.queue.update(tid, escalated_at=now)
                        done.append(f"oscar {tid}")
                elif self._toca_recordar(entry, now):
                    if self.remind(p, entry):
                        self.queue.update(tid, reminded_at=now)
                        done.append(f"recordar {tid}")
            except Exception as exc:
                log.warning("%s: aviso de la cola del integrador falló: %s", tid, exc)
        return done


class GuardNotifier:
    """Envíos del router: cola del integrador = tema Atención con @mención; Oscar = DM + tema Atención con botones."""

    def __init__(self, notifier, desk, attention: dict | None, owner_id: str | None,
                 now: Callable[[], float] = time.time):
        self.notifier, self.desk, self.owner_id, self._now = notifier, desk, owner_id, now
        self.attention = attention if attention and attention.get("chat") else None
        a = self.attention or {}
        self.who_integrator = mention(a.get("integrator_mention") or a.get("mention"))
        self.who_oscar = mention(a.get("oscar_mention") or a.get("mention"))

    def to_integrator(self, p, entry: dict) -> bool:
        if not self.attention:  # sin tema Atención configurado: queda en la cola (/integrador) sin mensaje
            return True
        text = integrator_text(entry, self.who_integrator)
        return bool(self.notifier.send_to(str(self.attention["chat"]), str(self.attention.get("thread") or "0"),
                                          text, html=False))

    def to_oscar(self, p, entry: dict) -> bool:
        text = oscar_text(entry, self._now(), self.who_oscar)
        sent = False
        if self.owner_id:
            markup = self.desk.markup("blocked", task=p.task, lane=p.lane, block_kind="console_escalated")
            sent = bool(self.notifier.send_to(self.owner_id, None, text, html=False, reply_markup=markup))
        if self.attention:
            markup = self.desk.markup("blocked", task=p.task, lane=p.lane, block_kind="console_escalated")
            sent = bool(self.notifier.send_to(str(self.attention["chat"]), str(self.attention.get("thread") or "0"),
                                              text, html=False, reply_markup=markup)) or sent
        return sent


def queue_view(entries: dict[str, dict], now: float) -> str:
    """Texto de /integrador: lo pendiente de la cola, con el comando exacto y su próximo paso."""
    if not entries:
        return "🧑‍💻 La cola del integrador está vacía: no hay bloqueos de guard pendientes."
    lines = [f"🧑‍💻 Cola del integrador ({len(entries)})"]
    for e in sorted(entries.values(), key=lambda x: (x.get("queued_at") or 0, x["tid"])):
        state = "ya escalada a Oscar" if e.get("escalated_at") else f"pasa a Oscar a las {int(valor('atascos.escalado_guard_horas'))} h"
        rep = f" · repetido ({e['veces']}ª)" if int(e.get("veces") or 1) > 1 else ""
        lines.append(f"• {e['tid']} · {e.get('lane')} · {truncate(e.get('title'), 50)} · hace "
                     f"{hours_ago(e.get('queued_at'), now)} h · {state}{rep}\n"
                     f"   Falta: {truncate(e.get('need'), 160)}\n   Comando: {command_for(e['tid'])}")
    lines.append(f"➡️ Próximo paso: ejecutar el comando de cada una en una consola · Lo da: {INTEGRADOR}")
    return "\n".join(lines)
