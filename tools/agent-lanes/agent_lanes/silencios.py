"""Silencio de alertas del monitor: `/silenciar <clave> <horas>` (retro del 22-10: las mismas alertas saltaron cientos
de veces en 7 días sin que nadie actuara).

El monitor (tools/monitor, proceso aparte) lee `silenced.json` de su .state; este módulo es el lado del bot: escribe ese
fichero y lista las claves que el monitor ha avisado últimamente (`alert_daily.json`). Formato:
    silenced.json   {clave: epoch de fin}
    alert_daily.json {clave: epoch del último aviso}
Una clave silenciada cubre también las que empiezan por ella (`runner` silencia `runner-dead` y `runner-dead-ok`).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .health import MONITOR_STATE_DIR

MAX_HORAS = 24 * 14


def _leer(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def activos(state_dir: Path = MONITOR_STATE_DIR, now: float | None = None) -> dict[str, float]:
    """{clave: epoch de fin} de los silencios vigentes."""
    now = time.time() if now is None else now
    out = {}
    for k, v in _leer(Path(state_dir) / "silenced.json").items():
        try:
            if float(v) > now:
                out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def silenciar(clave: str, horas: float, state_dir: Path = MONITOR_STATE_DIR, now: float | None = None) -> float:
    """Silencia `clave` durante `horas` (0 = quitar el silencio). Devuelve el epoch de fin. ValueError si no es válido."""
    clave = (clave or "").strip().lower()
    if not clave or len(clave) > 80 or any(c.isspace() for c in clave):
        raise ValueError("La clave no es válida (una palabra, como «ram» o «drift-migrateam»).")
    if not 0 <= horas <= MAX_HORAS:
        raise ValueError(f"Las horas tienen que estar entre 0 y {MAX_HORAS}.")
    now = time.time() if now is None else now
    vals = activos(state_dir, now)
    vals.pop(clave, None)
    hasta = now + horas * 3600
    if horas:
        vals[clave] = hasta
    path = Path(state_dir) / "silenced.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(vals), encoding="utf-8")
    os.replace(tmp, path)
    return hasta


def claves_recientes(state_dir: Path = MONITOR_STATE_DIR, limit: int = 12) -> list[str]:
    """Claves que el monitor avisó últimamente, las más recientes primero (para que Oscar sepa qué silenciar)."""
    vals = _leer(Path(state_dir) / "alert_daily.json")
    ordered = sorted(((float(v), k) for k, v in vals.items() if isinstance(v, (int, float))), reverse=True)
    return [k for _, k in ordered[:limit]]
