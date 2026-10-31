"""Chequeo previo de permisos: antes de gastar una pasada del worker, compara el texto de la tarjeta con los límites
del carril (forbidden_paths y allowed_tools de lanes.yaml) y avisa de lo que el worker no podrá hacer solo.

Solo avisa: nunca bloquea ni cambia la config. Un aviso de más cuesta una línea; una tarjeta parada a mitad
(npm install, alembic/versions/, gh) cuesta una pasada entera del worker."""
from __future__ import annotations

import re

from .config import Lane

# Regla nueva (la propone la retro, la acepta Oscar): cita su lección de docs/lecciones/ en un comentario (# L-NNNN).
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


# Lo que un worker NUNCA puede hacer en su carril (guard): (patrón sobre UNA línea, qué se pide). Prudentes: piden
# acción + objetivo remoto en la misma línea; una mención suelta («producción» a secas) no dispara.
_REMOTE = r"(?:supabase|staging|producci[óo]n|\bprod\b|remot[ao])"
# Objetivo de un despliegue: sin él («despliega el menú», «los tests pasan antes de desplegar») no se pide nada imposible.
_PROD_TARGET = r"(?:producci[óo]n|\bprod\b|\bvercel\b|\brailway\b|\bdokploy\b)"
_DEPLOY = r"(?:desplegar|despliega\w*|redespliega\w*|redeploy\w*|deploy\w*|despliegue)"
_IMPOSIBLE = (
    (re.compile(r"\bexecute_sql\b|\bapply_migration\b", re.IGNORECASE),
     "ejecutar SQL o migraciones contra una base remota (Supabase/Alembic)"),
    (re.compile(rf"\b(?:aplic\w+|ejecut\w+|corre|lanza)\b[^\n]*\bmigraci(?:ón|on|ones)\b[^\n]*{_REMOTE}"
                rf"|\b(?:aplic\w+|ejecut\w+|corre|lanza)\b[^\n]*{_REMOTE}[^\n]*\bmigraci(?:ón|on|ones)\b"
                rf"|\balembic\s+upgrade\b[^\n]*{_REMOTE}"
                rf"|\b(?:ejecut\w+|corre|lanza|aplic\w+)\b[^\n]*\bsql\b[^\n]*{_REMOTE}", re.IGNORECASE),
     "ejecutar SQL o migraciones contra una base remota (Supabase/Alembic)"),
    (re.compile(rf"\b{_DEPLOY}\b[^\n]*{_PROD_TARGET}|{_PROD_TARGET}[^\n]*\b{_DEPLOY}\b"
                r"|\bvercel\s+(?:deploy|promote|--prod)\b|\brailway\s+(?:up|deploy|redeploy)\b",
                re.IGNORECASE),
     "producción o despliegue (Dokploy, Vercel, Railway)"),
    (re.compile(r"\b(?:cambi\w+|a[ñn]ad\w+|actualiz\w+|configur\w+|modific\w+|rota\w*|set|pon\w*)\b[^\n]*"
                r"(?:variables?\s+de\s+entorno|\benv(?:ironment)?\s+vars?\b)[^\n]*(?:producci[óo]n|\bprod\b)", re.IGNORECASE),
     "variables de entorno de producción"),
)
# Línea que niega o es un límite («Límites: nada de producción», «sin desplegar», «no cambies…»).
_LIMITE = re.compile(r"^\s*[-*>]*\s*l[ií]mites?\s*:", re.IGNORECASE)
# Cabecera que cierra un bloque «Límites:» multilínea («## Criterio», «Pruebas:»).
_CABECERA = re.compile(r"^\s*(?:#+\s|[^\s\-*>][^\n:]{0,40}:\s*$)")
_NEGADO = re.compile(r"\b(?:sin|nada\s+de|no|ni|nunca|jam[áa]s|tampoco|evita\w*|prohibido)\b", re.IGNORECASE)


def pide_lo_imposible(task: dict) -> str | None:
    """Qué pide la tarjeta que un worker nunca puede hacer en su carril (SQL/migraciones contra base remota,
    producción o despliegue, env vars de producción), o None. Ignora las líneas «Límites:» y las que niegan antes de
    la coincidencia («sin desplegar», «nada de producción», «no apliques la migración en prod»)."""
    text = f"{task.get('title') or ''}\n{task.get('body') or ''}"
    en_limites = False  # bajo una cabecera «Límites:» vacía: hasta la línea en blanco o la siguiente cabecera
    for line in text.splitlines():
        if _LIMITE.match(line):
            en_limites = not _LIMITE.sub("", line).strip()
            continue
        if en_limites:
            if line.strip() and not _CABECERA.match(line):
                continue
            en_limites = False
        for pattern, what in _IMPOSIBLE:
            m = pattern.search(line)
            if not m or _NEGADO.search(line[:m.start()]):
                continue
            if "despliegue" in what and _NEGADO.search(line[m.end():]):  # «despliega en prod: no», «prod, nunca»
                continue
            return what
    return None


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
