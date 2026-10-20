"""Contrato único de aviso accionable (autonomía F0, pitch «Autonomía de carriles» §4.1).

Un aviso que pide algo a Oscar lleva siempre una acción: `AvisoAccionable.lint()` falla si no hay ninguna, y los
tests lo ejecutan sobre los avisos que construye el hub. El recordatorio de 24 h (`StuckWatcher`) solo vuelve a
avisar: el silencio nunca equivale a aprobación y ningún recordatorio ejecuta nada.

Estado del vigilante en `.state/stuck_avisos.json` (clave `tarjeta:estado:ronda`), así un reinicio no repite el
aviso (evita el triple mensaje) y un callback repetido no duplica nada.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

log = logging.getLogger("agent_lanes")

REMIND_HOURS = 24
_USER_RE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{4,31})$")


class AvisoSinAccion(ValueError):
    """Un aviso sin acciones deja a Oscar de cable entre el mensaje y la acción: no se envía."""


@dataclass(frozen=True)
class AvisoAccionable:
    motivo: str                              # una frase: por qué se avisa
    contexto_corto: tuple[str, ...]          # viñetas con el historial (lo preguntado, lo contestado, lo que falta)
    acciones: tuple[dict, ...]               # botones {text, action}: al menos uno
    dueño: str = "Oscar"                     # quién tiene que moverlo
    plazo: str = f"{REMIND_HOURS} h"         # a partir de cuándo se recuerda
    escalado: str = "recordatorio al DM de Oscar (no aprueba nada)"
    extra: dict = field(default_factory=dict)

    def lint(self) -> "AvisoAccionable":
        if not self.motivo.strip() or not self.dueño.strip():
            raise AvisoSinAccion("aviso sin motivo o sin dueño")
        if not self.acciones or any(not a.get("text") or not a.get("action") for a in self.acciones):
            raise AvisoSinAccion(f"aviso sin acciones: {self.motivo!r}")
        return self


def actions_of(spec) -> tuple[dict, ...]:
    """Botones de un `keyboard_spec` (filas de {text, action}) en una sola lista."""
    return tuple({"text": b["text"], "action": b["action"]} for row in spec or () for b in row)


def mention(user: str | None) -> str:
    """`@usuario` de Telegram desde la configuración (`attention_telegram.mention`); "" si falta o no es válido."""
    m = _USER_RE.match((user or "").strip())
    return f"@{m.group(1)}" if m else ""


def stuck_history(question: str | None, times: int, last_ask: float | None, answers: list[str],
                  answered_after: bool, kind: str | None) -> tuple[str, ...]:
    """Viñetas del aviso 🧊: qué se preguntó, cuántas veces y cuándo fue la última, qué se contestó y qué falta."""
    lines = []
    if question:
        when = datetime.fromtimestamp(last_ask).strftime("%d/%m %H:%M") if last_ask else "?"
        lines.append(f"Lo que se preguntó: «{question}» ({times} {'vez' if times == 1 else 'veces'}, última {when})")
    elif times:
        lines.append(f"Bloqueada {times} {'vez' if times == 1 else 'veces'} por lo mismo")
    if answers:
        lines.append("Lo que contestaste: " + " · ".join(f"«{a}»" for a in answers[-2:]))
    if kind == "needs_input" and not answered_after:
        lines.append("Falta: tu respuesta (✍️ Responder) o sacarla de triage si ya no hace falta")
    elif kind == "needs_input":
        lines.append("Falta: solo sacarla de triage; ya contestaste y Hermes la dejó parada")
    else:
        lines.append("Falta: sacarla de triage (se reintenta tal cual) o archivarla")
    return tuple(lines)


URGENT_PRIORITY = 8  # prioridad mínima de una tarjeta para que su bloqueo se avise al momento (t_bb92566e, prio 10, ~17 h callada)


class UrgentBlockWatcher:
    """Avisa AL MOMENTO (una vez por bloqueo) de las tarjetas bloqueadas con prioridad >= `min_priority`: no espera a
    los recordatorios de las 13:00/18:00. `blocked()` da los Pending bloqueados (needs_input/transient/console) y
    `alert(p)` devuelve True si el aviso (DM de Oscar + tema Atención) salió; si falla se reintenta en la siguiente
    vuelta. Estado en un JSON (clave `tarjeta:estado:desde`): un reinicio no repite el aviso. Nunca ejecuta nada."""

    def __init__(self, blocked: Callable[[], list], alert: Callable, state_path: Path | str, *,
                 min_priority: int = URGENT_PRIORITY):
        self.blocked, self.alert = blocked, alert
        self.state_path = Path(state_path)
        self.min_priority = min_priority

    def _load(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def tick(self) -> list[str]:
        """Una pasada. Devuelve los ids avisados, para tests y log."""
        done, seen = [], self._load()
        for p in self.blocked():
            prio = (getattr(p, "task", None) or {}).get("priority")
            if isinstance(prio, bool) or not isinstance(prio, int) or prio < self.min_priority:
                continue
            key = f"{p.tid}:{p.state}:{int(getattr(p, 'since', None) or 0)}"
            if key in seen:
                continue
            try:
                if self.alert(p):
                    seen[key] = {"at": time.time(), "priority": prio}
                    done.append(p.tid)
            except Exception as exc:
                log.warning("%s: aviso urgente falló: %s", p.tid, exc)
        if done:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
        return done


class StuckWatcher:
    """Avisa de cada tarjeta atascada una vez por ronda (tema Atención) y, pasadas `hours` h sin respuesta, manda un
    recordatorio al DM de Oscar. `publish(p)` y `remind(p, hours)` devuelven True si se enviaron; `stuck()` da los
    Pending 🧊. Cualquier fallo se reintenta en la siguiente vuelta; nunca tumba el runner."""

    def __init__(self, stuck: Callable[[], list], publish: Callable, remind: Callable, state_path: Path | str, *,
                 hours: float = REMIND_HOURS, now: Callable[[], float] = time.time):
        self.stuck, self.publish, self.remind = stuck, publish, remind
        self.state_path = Path(state_path)
        self.hours = hours
        self._now = now

    def _load(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def tick(self) -> list[str]:
        """Una pasada. Devuelve lo hecho (`"aviso t_x"` / `"recordatorio t_x"`), para tests y log."""
        done, seen = [], self._load()
        for p in self.stuck():
            key = f"{p.tid}:{p.state}:{getattr(p, 'ronda', '')}"
            rec = seen.get(key)
            try:
                if rec is None:
                    if self.publish(p):
                        seen[key] = {"at": self._now()}
                        done.append(f"aviso {p.tid}")
                elif not rec.get("reminded") and self._now() - rec["at"] >= self.hours * 3600:
                    if self.remind(p, self.hours):
                        rec["reminded"] = self._now()
                        done.append(f"recordatorio {p.tid}")
            except Exception as exc:
                log.warning("%s: aviso de atascada falló: %s", p.tid, exc)
            self._save(seen)
        return done
