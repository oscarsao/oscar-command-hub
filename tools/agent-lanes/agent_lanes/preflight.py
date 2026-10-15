"""Chequeo previo de permisos: antes de gastar una pasada del worker, compara el texto de la tarjeta con los límites
del carril (forbidden_paths y allowed_tools de lanes.yaml) y avisa de lo que el worker no podrá hacer solo.

Solo avisa: nunca bloquea ni cambia la config. Un aviso de más cuesta una línea; una tarjeta parada a mitad
(npm install, alembic/versions/, gh) cuesta una pasada entera del worker."""
from __future__ import annotations

import re

from .config import Lane

# (patrón en el texto de la tarjeta, prefijos de allowed_tools que lo permitirían, qué se pide, qué hace falta)
_NEEDS_TOOL = (
    (re.compile(r"\b(?:npm|pnpm)\s+(?:install|i|ci|add|update)\b|\byarn\s+(?:add|install)\b|\bpip\s+install\b",
                re.IGNORECASE),
     ("Bash(npm", "Bash(pnpm", "Bash(yarn", "Bash(pip"),
     "instalar dependencias (npm/pip install)", "permiso de instalación en el carril o una consola interactiva"),
    (re.compile(r"\bgh\s+(?:pr|issue|api|run|release)\b|\bgithub\s+cli\b", re.IGNORECASE),
     ("Bash(gh",),
     "usar la CLI de GitHub (gh)", "permiso de gh en el carril o una consola interactiva"),
)
_MIGRATION = re.compile(r"\balembic\b|\bmigraci(?:ón|on|ones)\b|\bmigrations?\b", re.IGNORECASE)
_MIGRATION_PATHS = ("alembic", "migrations")


def _has_tool(lane: Lane, prefixes: tuple[str, ...]) -> bool:
    return any(t.startswith(p) for t in lane.allowed_tools for p in prefixes)


def preflight_warnings(lane: Lane, task: dict) -> list[str]:
    """Líneas llanas (una por problema) con lo que el worker no podrá hacer en este carril. Vacío = todo en regla."""
    if lane.kind != "implement":
        return []
    text = f"{task.get('title') or ''}\n{task.get('body') or ''}"
    low = text.lower()
    warnings: list[str] = []
    covered: set[str] = set()

    if _MIGRATION.search(text):
        blocked = [p for p in lane.forbidden_paths if any(m in p.lower() for m in _MIGRATION_PATHS)]
        if blocked:
            covered.update(blocked)
            warnings.append(f"migraciones de base de datos: {blocked[0]} está vetado al worker; "
                            "hará falta el integrador (propietario de las migraciones)")
    for pattern, prefixes, what, remedy in _NEEDS_TOOL:
        if pattern.search(text) and not _has_tool(lane, prefixes):
            warnings.append(f"{what}: el carril no lo permite; hará falta {remedy}")
    for path in lane.forbidden_paths:
        if path not in covered and path.strip("/").lower() in low:
            warnings.append(f"la tarjeta menciona {path}, vetado al worker; hará falta el integrador o ajustar el carril")
    return warnings
