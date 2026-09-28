"""/salud: un mensaje compacto con el estado de la máquina de carriles, sin tocar producción.

Solo lecturas locales: .state del runner, `hermes kanban list` (lo mismo que `lanes.py status`), el gateway_state.json
de Hermes, el estado que ya escribe el servicio del monitor (tools/monitor/.state) y refs de git SIN fetch. Ninguna
petición HTTP: el monitor ya sondea los servicios y dos sondeos a la vez provocaron un 429 el 28-09.
Cada sección falla por separado ("no disponible"): un fichero ausente no deja a Oscar sin el resto.
"""
from __future__ import annotations

import html
import json
import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import proc as _proc
from .config import ROOT
from .notices import BRANDS, TEXT_MAX, truncate

log = logging.getLogger("agent_lanes")

HERMES_GATEWAY_STATE = Path.home() / "AppData/Local/hermes/gateway_state.json"
MONITOR_STATE_DIR = ROOT.parent / "monitor" / ".state"  # servicio del monitor (tools/monitor), hermano de agent-lanes
MIGRATEAM_LANE = "claude-migrateam"
STALE_SECONDS = 15 * 60   # gateway_state / health.json sin actualizar desde hace más: ⚠️
ALERTS_SHOWN = 5
ALERT_LEVELS = (" ALTA ", " MEDIA ")
ALERT_MAX = 110
TAIL_BYTES = 64_000
NA = "no disponible"


def _age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{seconds // 60} min"
    if seconds < 48 * 3600:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def _iso_ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


# --- secciones (texto plano; build() escapa) ------------------------------------------------------------

def gateway_line(path: Path, pid_alive: Callable[[int], bool], now: float) -> str:
    """Gateway de Hermes: no basta `gateway_state`: el fichero se queda en "running" si el proceso muere."""
    try:
        st = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f"❔ {NA} (sin gateway_state.json)"
    pid = st.get("pid")
    try:
        alive = bool(pid) and pid_alive(int(pid))
    except (TypeError, ValueError):
        alive = False
    state = str(st.get("gateway_state") or "?")
    updated = _iso_ts(st.get("updated_at"))
    age = None if updated is None else now - updated
    ok = alive and state == "running" and age is not None and age <= STALE_SECONDS
    parts = [state, f"pid {pid} {'vivo' if alive else 'MUERTO'}", f"{st.get('active_agents', '?')} agentes activos",
             f"actualizado hace {_age(age)}"]
    down = [n for n, p in (st.get("platforms") or {}).items() if isinstance(p, dict) and p.get("state") != "connected"]
    if down:
        parts.append("sin conexión: " + ", ".join(down[:3]))
    return ("✅ " if ok and not down else "⚠️ ") + " · ".join(parts)


def monitor_lines(state_dir: Path, now: float) -> tuple[str, list[str]]:
    """(servicios según el último sondeo del monitor, últimas alertas ALTA/MEDIA). La edad de health.json importa:
    un monitor caído deja el último fichero en verde."""
    health = Path(state_dir) / "health.json"
    try:
        data = json.loads(health.read_text(encoding="utf-8"))
        age = now - health.stat().st_mtime
        services = " · ".join(f"{k} {v}" for k, v in data.items()) if isinstance(data, dict) and data else "sin datos"
        stale = "⚠️ monitor sin actualizar " if age > STALE_SECONDS else ""
        svc = f"{stale}(hace {_age(age)}) {services}"
    except (OSError, ValueError):
        svc = f"{NA} (¿monitor parado?)"
    alerts: list[str] = []
    try:
        with open(Path(state_dir) / "alerts.log", "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - TAIL_BYTES))
            lines = fh.read().decode("utf-8", "replace").splitlines()
        hits = [l.strip() for l in lines if any(level in f" {l} " for level in ALERT_LEVELS)]
        alerts = [truncate(_short_alert(l), ALERT_MAX) for l in hits[-ALERTS_SHOWN:]]
    except OSError:
        alerts = []
    return svc, alerts


def _short_alert(line: str) -> str:
    """"2026-09-28 12:33:31 ALTA texto" -> "28-09 12:33 ALTA texto" (sin segundos ni año)."""
    parts = line.split(" ", 2)
    if len(parts) == 3 and len(parts[0]) == 10 and parts[0][4] == "-":
        return f"{parts[0][8:10]}-{parts[0][5:7]} {parts[1][:5]} {parts[2]}"
    return line


def migrateam_drift(repo: str, run=_proc.run) -> str:
    """develop <-> master de MigraTeam con las refs remotas locales (sin fetch: puede ir por detrás de GitHub)."""
    if not repo:
        return NA
    try:
        cp = run(["git", "-C", repo, "rev-list", "--left-right", "--count", "origin/develop...origin/master"],
                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        dev, master = (int(x) for x in (cp.stdout or "").split()[:2]) if cp.returncode == 0 else (None, None)
    except (OSError, ValueError, subprocess.SubprocessError):
        dev = master = None
    if dev is None:
        return NA
    flag = "⚠️ " if master else ""
    return (f"{flag}master tiene {master} commit{'s' if master != 1 else ''} que develop no · develop lleva {dev} sin "
            f"promocionar (refs locales, sin fetch)")


def machine_line(psutil_mod=None) -> str:
    try:
        ps = psutil_mod
        if ps is None:
            import psutil as ps  # noqa: PLC0415 - opcional
        mem = ps.virtual_memory()
        cpu = ps.cpu_percent(interval=0.5)  # interval=None devuelve 0.0 la primera vez
        gb = 1024 ** 3
        flag = "⚠️ " if mem.percent >= 90 else ""
        return (f"{flag}RAM {mem.percent:.0f} % ({mem.used / gb:.1f}/{mem.total / gb:.1f} GB) · CPU {cpu:.0f} %"
                ).replace(".", ",")
    except ImportError:
        return f"{NA} (falta psutil)"
    except Exception as exc:  # noqa: BLE001
        log.info("salud: psutil falló: %s", exc)
        return NA


# --- mensaje -------------------------------------------------------------------------------------------

def lane_line(row: dict) -> str:
    brand = BRANDS.get(row["lane"])
    now = ",".join(row["running"]) if row["running"] else ""
    return (f"{row['lane']}" + (f" ({brand})" if brand else "") + f": {row['state']}" + (f" {now}" if now else "")
            + f" · {row['ready']} en cola · {row['review']} en review")


def build(*, runner: Callable[[], str], rows: Callable[[], list[dict]], gateway: Callable[[], str],
          monitor: Callable[[], tuple[str, list[str]]], drift: Callable[[], str], machine: Callable[[], str],
          pending: Callable[[], tuple[int, int, int]], now: float | None = None) -> str:
    """Mensaje HTML de /salud. Cada argumento es una sección perezosa; si lanza, esa sección dice "no disponible"."""
    e = html.escape

    def safe(fn, default):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - una sección rota no tumba /salud
            log.warning("salud: sección falló: %s", exc)
            return default

    stamp = datetime.fromtimestamp(time.time() if now is None else now).strftime("%d-%m %H:%M")
    out = [f"🩺 <b>Salud</b> · {e(stamp)}", "", f"<b>Carriles</b> · {e(safe(runner, NA))}"]
    lane_rows = safe(rows, None)
    out += [f"• {e(lane_line(r))}" for r in lane_rows] if lane_rows is not None else [f"• {NA}"]
    out.append(f"<b>Hermes</b> · {e(safe(gateway, NA))}")
    svc, alerts = safe(monitor, (NA, []))
    out.append(f"<b>Servicios</b> (monitor) · {e(svc)}")
    if alerts:
        out.append("<b>Últimas alertas</b>")
        out += [f"• {e(a)}" for a in alerts]
    out.append(f"<b>MigraTeam</b> develop↔master · {e(safe(drift, NA))}")
    out.append(f"<b>Equipo</b> · {e(safe(machine, NA))}")
    counts = safe(pending, None)
    if counts is None:
        out.append(f"<b>Decisiones</b> · {NA}")
    else:
        questions, stuck, cards = counts
        total = questions + stuck + cards
        out.append(f"<b>Decisiones</b> · {questions} preguntas · {stuck} atascadas · {cards} tarjetas"
                   + (" · /decisiones" if total else " · nada pendiente"))
    return "\n".join(out)[:TEXT_MAX]  # cada línea está acotada: en la práctica no pasa de ~2 KB
