"""Carriles libres (t_ebd2b253, PR 1): detecta qué carril de código lleva un rato sin trabajo.

«Libre» = ninguna tarjeta running ni ready asignada al carril. Se anota desde cuándo en `.state/ociosos.json` para que
el dato sobreviva a un reinicio del runner. Solo lee el kanban: no asigna ni avisa (eso llega en los PR siguientes).
Reloj y tablero inyectables: sin red, sin Hermes real en los tests.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .ajustes_hub import valor
from .config import ROOT

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "ociosos.json"


@dataclass(frozen=True)
class Libre:
    carril: str
    desde: float
    minutos: float      # cuánto lleva libre
    supera_umbral: bool  # ya pasó `carriles.ocioso_minutos`


def umbral_minutos() -> float:
    try:
        return float(valor("carriles.ocioso_minutos"))
    except (TypeError, ValueError):
        return 30.0


def _leer(path: Path) -> dict:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in raw.items() if isinstance(v, (int, float))} if isinstance(raw, dict) else {}


def _guardar(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


class Ociosos:
    def __init__(self, lanes: dict, hermes_for: Callable, state_path: Path | str = STATE_PATH,
                 clock: Callable[[], float] = time.time):
        self.lanes, self.hermes_for, self.state_path, self.clock = lanes, hermes_for, Path(state_path), clock

    def _libre(self, lane) -> bool | None:
        """True si no tiene running ni ready; None si el tablero no se pudo leer (no se toca su estado)."""
        try:
            h = self.hermes_for(lane.board)
            return not h.list_status(lane.name, "running") and not h.list_status(lane.name, "ready")
        except Exception as exc:  # un tablero roto no tumba la pasada ni da un «libre» falso
            log.info("ociosos: %s sin leer (%s)", lane.name, type(exc).__name__)
            return None

    def pasada(self) -> list[Libre]:
        """Actualiza `.state/ociosos.json` (desde cuándo está libre cada carril) y devuelve los libres, más antiguos primero."""
        now, umbral = self.clock(), umbral_minutos()
        previo = _leer(self.state_path)
        nuevo: dict[str, float] = {}
        for name, lane in self.lanes.items():
            if getattr(lane, "kind", None) != "implement" or not getattr(lane, "board", None):
                continue
            libre = self._libre(lane)
            if libre is None:
                if name in previo:
                    nuevo[name] = previo[name]
            elif libre:
                nuevo[name] = previo.get(name, now)
        if nuevo != previo:
            try:
                _guardar(self.state_path, nuevo)
            except OSError as exc:
                log.warning("ociosos: no se pudo guardar el estado: %s", exc)
        out = [Libre(n, d, max(0.0, (now - d) / 60), (now - d) / 60 >= umbral) for n, d in nuevo.items()]
        return sorted(out, key=lambda x: x.desde)


class OciososTicker:
    """Bucle del runner: una pasada cada `minutes` min. Un fallo no tumba el runner."""

    def __init__(self, ociosos: Ociosos, minutes: float = 1, clock: Callable[[], float] = time.time):
        self.ociosos, self.minutes, self.clock = ociosos, minutes, clock
        self._last = 0.0

    def tick(self) -> list[Libre]:
        now = self.clock()
        if now - self._last < self.minutes * 60:
            return []
        self._last = now
        try:
            return self.ociosos.pasada()
        except Exception as exc:
            log.warning("ociosos: pasada fallida: %s", exc)
            return []


def texto(libres: list[Libre], umbral: float) -> str:
    """Texto de /libres."""
    if not libres:
        return "✅ Ningún carril libre: todos tienen trabajo en curso o en cola."
    out = ["🟢 <b>Carriles libres</b>"]
    for x in libres:
        m = int(x.minutos)
        out.append(f"• <b>{x.carril}</b> · libre desde hace {m} min" + ("" if x.supera_umbral else f" (aviso a los {int(umbral)})"))
    return "\n".join(out)
