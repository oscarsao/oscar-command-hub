"""Cola autónoma del integrador (entrega C4; pitch seguimiento-peticiones §4.7).

Procesa la cola con o sin sesión del integrador abierta, con un reparto CERRADO: lo de AUTOMATICAS se ejecuta solo; lo de
PIDE_OK (y cualquier acción desconocida) nunca se ejecuta aquí: se devuelve como petición de OK a Oscar con botones.
Este módulo solo decide y delega en los `ejecutores` que se le pasan (git/gh reales los aporta el integrador); no toca git.
"""
from __future__ import annotations

import logging
from typing import Callable

log = logging.getLogger("agent_lanes")

AUTOMATICAS = frozenset({"montar_lote", "pasar_gates", "abrir_pr_lote", "reabrir_lote", "dejar_fuera_rama",
                         "reintentar_gates", "cerrar_pr_integrado", "escribir_integrado", "actualizar_registro",
                         "escalar_2h", "recordar_24h"})
PIDE_OK = {"aplicar_migracion": "Aplicar la migración (Alembic)",
           "resolver_conflicto": "Resolver un conflicto de código",
           "fusionar_main": "Fusionar a main",
           "desplegar_prod": "Desplegar a producción",
           "borrar": "Borrar ramas o archivos",
           "tocar_hooks": "Tocar hooks o el contrato de seguridad"}


def es_automatica(accion: str) -> bool:
    return accion in AUTOMATICAS


def escalado(horas_bloqueada: float, escalar_horas: float, recordar_horas: float) -> str | None:
    """Qué toca hacer con un bloqueo: 'recordar_24h', 'escalar_2h' o nada. Los umbrales vienen de workflows.yaml."""
    if horas_bloqueada >= recordar_horas:
        return "recordar_24h"
    return "escalar_2h" if horas_bloqueada >= escalar_horas else None


def peticion_ok(item: dict) -> dict:
    """Petición de OK a Oscar para algo que la cola no hace sola, con motivo y botones."""
    accion = item.get("accion", "")
    que = PIDE_OK.get(accion, f"Acción fuera de la lista automática: {accion or '?'}")
    ref = item.get("ref", "")
    return {"accion": accion, "ref": ref, "texto": f"🔐 Necesito tu OK: {que}" + (f" ({ref})" if ref else ""),
            "botones": [[("✅ Dar OK", f"ci:ok:{accion}:{ref}"), ("🗄 Aparcar", f"ci:park:{accion}:{ref}")]]}


def procesar(cola: list[dict], ejecutores: dict[str, Callable[[dict], object]]) -> dict:
    """Recorre la cola. Devuelve {hechas, fallidas, pide_ok}. Un fallo no bloquea al resto; sin ejecutor = no se hace."""
    out: dict = {"hechas": [], "fallidas": [], "pide_ok": []}
    for item in cola:
        accion = item.get("accion", "")
        if not es_automatica(accion):
            out["pide_ok"].append(peticion_ok(item))
            continue
        fn = ejecutores.get(accion)
        if fn is None:
            out["fallidas"].append({"accion": accion, "motivo": "sin ejecutor"})
            continue
        try:
            fn(item)
            out["hechas"].append(accion)
        except Exception as exc:  # una rama que rompe no bloquea al resto de la cola
            log.warning("cola integrador: %s falló: %s", accion, exc)
            out["fallidas"].append({"accion": accion, "motivo": str(exc)[:200]})
    return out
