"""Nivel de avisos del bot de carriles (`avisos.nivel` en ajustes_hub; decisión de Oscar 30-10).

- `normal` (por defecto) y `todo`: sale todo, exactamente como antes.
- `esencial`: solo problemáticas, finalizadas, integraciones, urgentes y los partes fijos (9:00, retro del lunes,
  13:00/18:00). Los tipos de APAGADOS_ESENCIAL NO mandan mensaje; siguen contando en el contador fijado de 🎯 Decisiones
  y están a demanda como comando (COMANDO_DE).

Un solo sitio decide qué tipo se apaga; la tabla tipo → clase → nivel está en el README del hub.
"""
from __future__ import annotations

from . import ajustes_hub

NIVELES = ("esencial", "normal", "todo")
DEFECTO = "normal"

# Tipo (canal_avisos.CLASES) -> comando que lo da a demanda. Son los tipos que `esencial` no envía.
COMANDO_DE: dict[str, str] = {
    "empezando": "/tareas",     # «▶️ empezando»: la tarjeta terminada sí sale; lo en curso está en /tareas
    "revision": "/revision",
    "choque": "/choques",
    "solape": "/choques",
    "paradas": "/paradas",
    "limpieza": "/limpieza",
    "reconcile": "/limpieza",
    "board_guard": "/salud",
    "pr_conflicto": "/integrador",
}
APAGADOS_ESENCIAL = frozenset(COMANDO_DE)


def nivel() -> str:
    """Nivel vigente (recarga en caliente); un valor desconocido cuenta como `normal`."""
    v = ajustes_hub.valor("avisos.nivel")
    return v if v in NIVELES else DEFECTO


def activo(tipo: str, nivel_: str | None = None) -> bool:
    """¿Este tipo de aviso se envía con el nivel dado (o el vigente)?"""
    return not ((nivel_ or nivel()) == "esencial" and tipo in APAGADOS_ESENCIAL)
