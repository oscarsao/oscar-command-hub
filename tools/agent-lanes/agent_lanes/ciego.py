"""Vigilante de «runner ciego»: el mismo fallo de infraestructura repetido (WinError 4551 de App Control y similares).

Incidente 29-30/09: 24 h 30 min con 13.758 líneas WARNING/ERROR (no se podían listar tareas ni leer a Hermes) sin que
nadie lo supiera; se arregló con un `restart --drain` a mano. Ahora el bucle principal del runner le cuenta a este
vigilante, tras cada pasada, qué listados de tareas fallaron (Service.list_errors):

- Fallos seguidos durante `ciego_minutes` (lanes.yaml `runner:`, 5 por defecto) = episodio «runner ciego».
- Al abrirse: UN aviso ALTA al DM de Oscar (inicio, error, acción) y, si no hubo otro reinicio en la última hora, un
  `lanes.py restart --drain` lanzado aparte. Si tras el reinicio el error sigue `ciego_minutes` más: un segundo aviso
  «requiere a Oscar» con el error y nada más (no se reinicia en bucle).
- Al recuperarse: aviso «recuperado tras X min».
- Todo queda en .state/ciego.json (inicio, fin, error, acción) y /salud lo resume (`resumen`).

El estado vive en disco porque el reinicio sustituye al proceso del runner: el nuevo debe saber que el episodio sigue
abierto y que ya se reinició. Los avisos y el reinicio nunca tumban al runner.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import ROOT

log = logging.getLogger("agent_lanes")

FILE = ROOT / ".state" / "ciego.json"
RESTART_LOG = ROOT / ".state" / "ciego-restart.log"
DEFAULT_MINUTES = 5.0
RESTART_COOLDOWN = 3600.0  # máximo un reinicio automático por hora
KEEP_EPISODES = 20
ERROR_MAX = 300
_WINERROR = re.compile(r"\[WinError \d+\]")


def error_label(errors: list[str]) -> str:
    """Texto corto y estable del primer error: «[WinError 4551] …» o el principio del mensaje."""
    text = " ".join(str(errors[0]).split()) if errors else ""
    return text[:ERROR_MAX]


def error_key(text: str) -> str:
    """Clave para saber si es «el mismo error» entre pasadas: el código WinError si lo hay, si no el texto."""
    m = _WINERROR.search(text or "")
    return m.group(0) if m else (text or "")[:80]


def launch_restart(lanes_py: Path | None = None, log_path: Path = RESTART_LOG) -> None:
    """`lanes.py restart --drain` en un proceso APARTE: el reinicio drena a ESTE runner (necesita su acuse) y luego lo
    mata con `schtasks /End`; si colgara de él moriría antes de relanzarlo. Se intenta salir del job de la tarea
    programada (CREATE_BREAKAWAY_FROM_JOB) y, si no se permite, se lanza igualmente."""
    cmd = [sys.executable, str(lanes_py or ROOT / "lanes.py"), "restart", "--drain"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    with open(log_path, "ab") as out:
        kw = dict(cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        try:
            subprocess.Popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0), **kw)
        except OSError:
            subprocess.Popen(cmd, creationflags=flags, **kw)


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d-%m %H:%M")


def _dur(seconds: float) -> str:
    minutes = max(0, int(seconds // 60))
    return f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"


class Vigilante:
    def __init__(self, *, path: Path = FILE, minutes: float = DEFAULT_MINUTES,
                 alert: Callable[[str], None] | None = None, restart: Callable[[], None] = launch_restart,
                 now: Callable[[], float] = time.time, cooldown: float = RESTART_COOLDOWN):
        self.path = Path(path)
        self.threshold = float(minutes) * 60
        self._alert = alert
        self._restart = restart
        self._now = now
        self.cooldown = cooldown
        self._streak: tuple[float, str] | None = None  # (inicio de la racha, clave del error) aún sin episodio

    # --- estado en disco ---

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"ultimo_reinicio": None, "episodios": []}
        if not isinstance(data, dict) or not isinstance(data.get("episodios"), list):
            return {"ultimo_reinicio": None, "episodios": []}
        return data

    def _save(self, data: dict) -> None:
        data["episodios"] = data["episodios"][-KEEP_EPISODES:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("ciego.json no escrito: %s", exc)

    def _send(self, text: str) -> None:
        log.error("%s", text)
        if not self._alert:
            return
        try:
            self._alert(text)
        except Exception as exc:  # sin red o sin token: queda en el log y en ciego.json
            log.warning("aviso de runner ciego no enviado: %s", exc)

    # --- observación ---

    def observe(self, errors: list[str]) -> None:
        """Una vez por pasada del bucle. `errors`: fallos al listar tareas en esa pasada (vacío = todo bien)."""
        try:
            self._observe(list(errors or ()))
        except Exception as exc:  # el vigilante nunca tumba al runner
            log.warning("vigilante de runner ciego falló: %s", exc)

    def _observe(self, errors: list[str]) -> None:
        now = self._now()
        data = self._load()
        episodes = data["episodios"]
        ep = episodes[-1] if episodes and episodes[-1].get("fin") is None else None
        if not errors:
            self._streak = None
            if ep:
                ep["fin"] = now
                self._save(data)
                self._send(f"✅ Runner recuperado tras {_dur(now - ep['inicio'])} (error: {ep['error']}). "
                           f"Inicio {_hm(ep['inicio'])}.")
            return
        label = error_label(errors)
        if ep:
            reinicio = ep.get("reinicio_en")
            if reinicio and not ep.get("requiere_oscar") and now - reinicio >= self.threshold:
                ep["requiere_oscar"] = True
                ep["accion"] += "; el reinicio no lo arregló, requiere a Oscar"
                self._save(data)
                self._send(f"🚨 ALTA · El reinicio automático no arregló el runner ciego (desde {_hm(ep['inicio'])}). "
                           f"No lo reinicio más: requiere a Oscar. Error: {label}")
            return
        if self._streak is None or self._streak[1] != error_key(label):
            self._streak = (now, error_key(label))
        since = self._streak[0]
        if now - since < self.threshold:
            return
        last = data.get("ultimo_reinicio")
        if last is None or now - float(last) >= self.cooldown:
            action = "reinicio automático (restart --drain) lanzado"
            restart = True
        else:
            action = f"sin reinicio automático (ya hubo uno hace {_dur(now - float(last))}): requiere a Oscar"
            restart = False
        ep = {"inicio": since, "fin": None, "error": label, "accion": action}
        if restart:
            ep["reinicio_en"] = now
            data["ultimo_reinicio"] = now
        episodes.append(ep)
        self._save(data)
        self._send(f"🚨 ALTA · Runner ciego desde {_hm(since)}: no puede listar tareas ni hablar con Hermes. "
                   f"Error: {label}. Acción: {action}.")
        if restart:
            try:
                self._restart()
            except Exception as exc:
                ep["accion"] += f" (falló al lanzarlo: {exc}); requiere a Oscar"
                ep["requiere_oscar"] = True
                self._save(data)
                self._send(f"🚨 ALTA · No se pudo lanzar el reinicio automático del runner ciego: {exc}. Requiere a Oscar.")


def resumen(path: Path = FILE, now: float | None = None) -> str:
    """Línea para /salud: episodio activo o el último; vacío si nunca hubo ninguno."""
    try:
        episodes = json.loads(Path(path).read_text(encoding="utf-8")).get("episodios") or []
        ep = episodes[-1]
    except (OSError, ValueError, IndexError, AttributeError):
        return ""
    now = time.time() if now is None else now
    if ep.get("fin") is None:
        return (f"🙈 runner ciego desde {_hm(ep['inicio'])} ({_dur(now - ep['inicio'])}) · {ep['error']} · "
                f"{ep['accion']}")
    return (f"último episodio ciego: {_hm(ep['inicio'])} → {_hm(ep['fin'])} ({_dur(ep['fin'] - ep['inicio'])}) · "
            f"{ep['error']} · {ep['accion']}")
