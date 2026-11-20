"""Chequeo previo de permisos: antes de gastar una pasada del worker, compara el texto de la tarjeta con los límites
del carril (forbidden_paths y allowed_tools de lanes.yaml) y avisa de lo que el worker no podrá hacer solo.

Solo avisa: nunca bloquea ni cambia la config. Un aviso de más cuesta una línea; una tarjeta parada a mitad
(npm install, alembic/versions/, gh) cuesta una pasada entera del worker."""
from __future__ import annotations

import re
from typing import Callable

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


# --- Deriva de la zona (t_6e4a5029) -------------------------------------------------------------------------------
# Una tarjeta redactada hace días puede apuntar a una zona que ya cambió en la base (movida, borrada, reescrita) o a algo
# ya resuelto. La tarjeta nueva lleva «SHA base de comprobación: <sha>» y «Zona: <rutas>»; aquí se compara esa zona entre
# el SHA y origin/<base>. Solo lectura sobre git. Un fallo propio (SHA inexistente, git roto) NUNCA frena: avisa y deja pasar.
DERIVA_DEFAULTS = {"deriva_activa": True, "dias_revalidar": 5, "deriva_max_lineas": 200}
_SHA_LINE = re.compile(r"^[ \t>*\-]*SHA\s+base(?:\s+de\s+comprobaci[óo]n)?\s*:\s*`?([0-9a-f]{7,40})`?",
                       re.IGNORECASE | re.MULTILINE)
_ZONA_LINE = re.compile(r"^[ \t>*\-]*Zonas?\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


def parse_deriva(body: str | None) -> tuple[str | None, list[str]]:
    """(sha, zonas) leídos del cuerpo. Sin línea de SHA o sin zona: (None, []) → no hay comprobación y tampoco error.
    La zona admite varias rutas separadas por comas; se limpian comillas y comillas invertidas."""
    text = body or ""
    sha = _SHA_LINE.search(text)
    zona = _ZONA_LINE.search(text)
    if not sha or not zona:
        return None, []
    paths = [p.strip().strip("`'\"").strip() for p in zona.group(1).split(",")]
    return sha.group(1).lower(), [p for p in paths if p and not p.startswith("-")]


def load_deriva_settings(raw: dict | None) -> dict:
    """Ajustes del bloque `preflight:` de lanes.yaml con sus valores por defecto; un valor inválido cae al por defecto."""
    raw = raw or {}
    out = dict(DERIVA_DEFAULTS)
    if isinstance(raw.get("deriva_activa"), bool):
        out["deriva_activa"] = raw["deriva_activa"]
    for key in ("dias_revalidar", "deriva_max_lineas"):
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            out[key] = v
    return out


class Deriva:
    """Resultado de comparar la zona: `derivada` (hay que revalidar), `motivos` (texto llano) y `aviso` (fallo propio)."""

    def __init__(self, derivada: bool = False, motivos: list[str] | None = None, aviso: str = "", sha: str = "",
                 ref: str = ""):
        self.derivada, self.motivos, self.aviso, self.sha, self.ref = derivada, motivos or [], aviso, sha, ref

    def texto(self) -> str:
        return f"la zona cambió en {self.ref} desde el SHA base {self.sha}: " + "; ".join(self.motivos)


def calcular_deriva(repo: str, sha: str, ref: str, zonas: list[str], max_lineas: int, run) -> Deriva:
    """`git diff <sha>..<ref> -- <zonas>` (solo lectura; el fetch lo hace quien llama). Deriva si hay ficheros borrados o
    movidos, o más de `max_lineas` líneas cambiadas. `run(cmd, ...)` = subprocess.run (inyectable). Si git falla o el SHA
    no existe: Deriva(derivada=False, aviso=...)."""
    def git(*args):
        return run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=120)
    res = Deriva(sha=sha, ref=ref)
    try:
        if git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
            res.aviso = f"no pude comprobar la deriva: el SHA base {sha} no existe en el repo"
            return res
        names = git("diff", "--name-status", "-M", f"{sha}..{ref}", "--", *zonas)
        nums = git("diff", "--numstat", "-M", f"{sha}..{ref}", "--", *zonas)
        if names.returncode != 0 or nums.returncode != 0:
            err = (names.stderr or nums.stderr or "").strip()[:150]
            res.aviso = f"no pude comprobar la deriva: git diff falló ({err})"
            return res
        borrados, movidos = [], []
        for line in (names.stdout or "").splitlines():
            parts = line.split("\t")
            if parts[0].startswith("D") and len(parts) > 1:
                borrados.append(parts[1])
            elif parts[0].startswith("R") and len(parts) > 2:
                movidos.append(f"{parts[1]} → {parts[2]}")
        lineas = 0
        for line in (nums.stdout or "").splitlines():
            cols = line.split("\t")
            if len(cols) >= 3:  # los binarios dan «-»
                lineas += (int(cols[0]) if cols[0].isdigit() else 0) + (int(cols[1]) if cols[1].isdigit() else 0)
    except Exception as exc:  # nunca frena por un fallo propio
        res.aviso = f"no pude comprobar la deriva: {exc}"
        return res
    if borrados:
        res.motivos.append(f"ficheros borrados ({len(borrados)}): " + ", ".join(borrados[:5]))
    if movidos:
        res.motivos.append(f"ficheros movidos ({len(movidos)}): " + ", ".join(movidos[:5]))
    if lineas > max_lineas:
        res.motivos.append(f"{lineas} líneas cambiadas (umbral {max_lineas})")
    res.derivada = bool(res.motivos)
    return res


def revisar_deriva(task: dict, repo: str, ref: str, ajustes: dict, ahora: float, run,
                   fetch: Callable[[], object] | None = None) -> tuple[str, str]:
    """(accion, texto) para una tarjeta: «ok» (nada que decir), «aviso» (derivó pero es reciente, o fallo propio: se
    lanza) o «bloquear» (derivó y lleva más de `dias_revalidar` días: NO se lanza). Sin línea de SHA/zona: ok."""
    if not ajustes.get("deriva_activa", True):
        return "ok", ""
    sha, zonas = parse_deriva(task.get("body"))
    if not sha or not zonas:
        return "ok", ""
    if fetch is not None:
        try:
            fetch()
        except Exception:
            pass  # sin fetch se compara con lo que haya; calcular_deriva avisa si falla
    d = calcular_deriva(repo, sha, ref, zonas, int(ajustes["deriva_max_lineas"]), run)
    if d.aviso:
        return "aviso", d.aviso
    if not d.derivada:
        return "ok", ""
    creada = task.get("created_at")
    dias = (ahora - float(creada)) / 86400 if isinstance(creada, (int, float)) and not isinstance(creada, bool) else None
    if dias is not None and dias > ajustes["dias_revalidar"]:
        return "bloquear", f"tarjeta de {dias:.0f} días en cola: {d.texto()}"
    return "aviso", d.texto()
