"""Pausa por cuota de Claude: cuando un worker acaba con «usage limit … resets 6pm», lanzar más claude solo gasta
reintentos. Se anota la pausa en .state/cuota.json y el planificador (Service) no lanza workers hasta la hora de reset.
Funciones pequeñas y puras (el reloj se inyecta): sin red, sin Hermes."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from .bloqueos import CUOTA_REGEX
from .config import ROOT

log = logging.getLogger("agent_lanes")

PATH = ROOT / ".state" / "cuota.json"
DEFAULT_MINUTOS = 60
MOTIVO_PREFIX = "pausa por cuota de Claude"  # motivo de la devolución a ready: no cuenta como fallo de la tarjeta
_RESET_AT = re.compile(r"resets?\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.I)
_RESET_IN = re.compile(r"resets?\s+in\s+(?:(\d+)\s*h\w*)?\s*(?:(\d+)\s*m\w*)?", re.I)


def es_cuota(raw_error: str | None) -> bool:
    """¿El error del worker es de cuota/límite de uso? (mismo regex que bloqueos.py)."""
    return bool(raw_error and re.search(CUOTA_REGEX, str(raw_error), re.I))


def _hora_reset(text: str, now: datetime) -> datetime | None:
    if m := _RESET_IN.search(text):
        horas, mins = int(m.group(1) or 0), int(m.group(2) or 0)
        if horas or mins:
            return now + timedelta(hours=horas, minutes=mins)
    if m := _RESET_AT.search(text):
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return at if at > now else at + timedelta(days=1)
    return None


def pausa_hasta(raw_error: str | None, now: float, minutos: float = DEFAULT_MINUTOS) -> float:
    """Epoch hasta el que se pausa: la hora de reset del mensaje (hora local) o, si no se lee, ahora + `minutos`."""
    reset = _hora_reset(str(raw_error or ""), datetime.fromtimestamp(now))
    return reset.timestamp() if reset else now + minutos * 60


def vigente(path: Path = PATH, now: float | None = None) -> dict | None:
    """La pausa en vigor ({paused_until, motivo, desde}) o None. Una pausa vencida se borra y se deja constancia."""
    now = time.time() if now is None else now
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        until = float(data["paused_until"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if now < until:
        return data
    Path(path).unlink(missing_ok=True)
    log.info("pausa por cuota vencida: se reanudan los lanzamientos")
    return None


def escribir(path: Path, paused_until: float, motivo: str, now: float) -> bool:
    """Guarda la pausa (se queda la más larga si ya había una). True si es una pausa NUEVA (la que merece aviso)."""
    previa = vigente(path, now)
    if previa:
        paused_until = max(paused_until, float(previa["paused_until"]))
    data = {"paused_until": paused_until, "motivo": motivo[:300], "desde": previa["desde"] if previa else now}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(path).with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return previa is None


def hora(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%H:%M")


def texto_estado(path: Path = PATH, now: float | None = None) -> str:
    """«pausado por cuota hasta HH:MM» si hay pausa vigente, si no ''. Lo muestra `lanes.py status`."""
    data = vigente(path, now)
    return f"pausado por cuota hasta {hora(float(data['paused_until']))}" if data else ""
