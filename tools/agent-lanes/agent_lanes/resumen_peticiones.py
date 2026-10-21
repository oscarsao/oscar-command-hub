"""Segundo parte de las 9:00: cómo va cada cosa que Oscar pidió (pitch «Seguimiento de peticiones» §4.4 · A4).

Sale JUNTO al resumen por carril (`resumen_carriles`, intacto) y no lo repite: no lleva detalle de carril (solo
«ver carriles» -> /hoy) y no sale si no hay novedad desde el último parte (una petición nueva o cuyo estado cambió).
Misma franja (9:00–11:00 de Madrid), mismo patrón de estado en un JSON propio: un reinicio no lo repite.
Solo lee: no cambia nada en el kanban.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable

from .formato_aviso import linea_peticion, ordenar, paginas
from .peticiones import PROYECTOS, Estado
from .resumen_carriles import MADRID, due_date


def novedades(items: list[tuple[dict, Estado]], previo: dict) -> list[tuple[dict, Estado]]:
    """Peticiones nuevas o cuyo estado cambió desde el último parte. Las `hecha` que ya constaban no vuelven."""
    return [(r, e) for r, e in items if previo.get(r["id"]) != e.etiqueta]


def render_parte(items: list[tuple[dict, Estado]], ahora: float, total: int) -> str:
    """Agrupado por proyecto, sin detalle de carril."""
    lineas = [f"☀️ Parte de las 9:00 por petición · {len(items)} con novedad de {total}"]
    for proyecto in PROYECTOS:
        del_proyecto = ordenar([it for it in items if it[0]["proyecto"] == proyecto])
        if del_proyecto:
            lineas += ["", f"▸ {proyecto}"] + [linea_peticion(r, e, ahora) for r, e in del_proyecto]
    lineas += ["", "Detalle por carril: /hoy · ver carriles. Una petición en concreto: /estado <proyecto>"]
    return "\n".join(lineas)


class DailyRequestSummary:
    """`items()` -> [(petición, Estado)]; `send(text)` -> True si salió. Estado: {"sent": [días], "estados": {req: etiqueta}}.
    Sin novedad el día se da por atendido (no se vuelve a consultar el kanban) y no se envía nada."""

    def __init__(self, items: Callable[[], list], send: Callable[[str], bool], state_path: Path | str, *,
                 now: Callable[[], datetime] = lambda: datetime.now(MADRID)):
        self.items, self.send, self.state_path, self._now = items, send, Path(state_path), now

    def _state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        return {"sent": list(data.get("sent") or ()), "estados": dict(data.get("estados") or {})}

    def _save(self, state: dict, key: str, estados: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"sent": sorted(set(state["sent"]) | {key})[-14:], "estados": estados},
                                              ensure_ascii=False), encoding="utf-8")

    def tick(self) -> str | None:
        """Una comprobación. Devuelve el texto enviado (o None)."""
        now = self._now()
        state = self._state()
        key = due_date(now, set(state["sent"]))
        if not key:
            return None
        items = self.items()
        estados = {r["id"]: e.etiqueta for r, e in items}
        nuevas = novedades(items, state["estados"])
        if not nuevas:
            self._save(state, key, estados)
            return None
        text = render_parte(nuevas, now.timestamp(), len(items))
        if not all(self.send(p) for p in paginas(text)):
            return None
        self._save(state, key, estados)
        return text
