"""¿La rama de esta tarjeta la escribió/verificó otro equipo (un host remoto por SSH)? (E2 de specs/worker-remoto-ssh.md)

Dos señales, y basta cualquiera: (1) el carril de hoy tiene `host:`; (2) la METADATA de la tarjeta dice
`verificado_en == "host"` (la escribe el runner del hub, no el host, al pasar a review). La segunda cubre el caso de que
se quite `host:` del carril o se reasigne la tarjeta a otro carril: el origen de la rama no cambia con la config.
Sin dependencias del resto del hub (la usan review, integrador y lote)."""
from __future__ import annotations

HOST_MARCA = "host"


def origen_host(lane, show: dict | None = None) -> str:
    """'' si el origen es local; si no, el motivo en llano (para el aviso y la tarjeta)."""
    host = getattr(lane, "host", None)
    if host:
        return f"carril con host ({host.destino})"
    for run in ((show or {}).get("runs") or []):
        meta = (run or {}).get("metadata") or {}
        if str(meta.get("verificado_en") or "").strip().lower() == HOST_MARCA:
            return "la tarjeta se verificó en un host remoto (verificado_en=host)"
    return ""
