"""Rutas del carril ops: normalización, contención y destinos declarados.

Solo stdlib: lo importa también el hook `contract/ops_guard.py`, que corre en cada llamada a herramienta.
La misma función de contención decide qué puede escribir el worker (hook) y qué evidencias acepta el runner.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

HOME = Path.home()

# Líneas de confianza del cuerpo de la tarea (mismo estilo que `Origen-Telegram:`). Solo las lee el runner.
ORIGIN_LINE = re.compile(r"^\s*Origen-Ops:\s*(.+?)\s*$", re.M | re.I)
DEST_LINE = re.compile(r"^\s*Destino-Ops:\s*(.+?)\s*$", re.M | re.I)

# Ficheros/rutas con secretos: el worker ops no los lee, ni los imprime, ni los escribe.
SECRET_RE = re.compile(
    r"(?i)(\.env\b|\bid_(rsa|ed25519|ecdsa)\b|\.ssh([\\/]|\b)|\.git-credentials|\.netrc|\bcredentials?\b"
    r"|\bsecrets?\b|\btokens?\b|\.pem\b|\.pfx\b|\.p12\b|\.key\b|\bprintenv\b|\bapi[_-]?key)"
)

# Nunca destino de escritura, aunque la tarea lo declare (sistema, configuración de agentes, estado del runner).
_FORBIDDEN_DEST = [
    "C:/Windows", "C:/Program Files", "C:/Program Files (x86)", "C:/ProgramData",
    str(HOME / "AppData"), str(HOME / ".claude"), str(HOME / ".ssh"), str(HOME / ".config"),
    str(HOME / "oscar-command-hub"),
]
_FORBIDDEN_DEST_REAL = tuple(_FORBIDDEN_DEST)  # copia inmutable (los tests parchean la lista)


def normalize(path: str, cwd: str | os.PathLike | None = None) -> str:
    """Ruta absoluta canónica (realpath + normcase). Acepta `/c/x` (Git Bash), `C:\\x`, `~` y relativas a `cwd`."""
    p = str(path).strip().strip("\"'")
    if p == "~" or p.startswith(("~/", "~\\")):
        p = str(HOME) + p[1:]
    m = re.match(r"^/([a-zA-Z])(/|$)(.*)$", p)
    if m:  # Git Bash: /c/Users -> C:/Users
        p = f"{m.group(1).upper()}:/{m.group(3)}"
    if not os.path.isabs(p) or re.match(r"^[a-zA-Z]:(?![\\/])", p):  # "C:foo" es relativa a la unidad
        p = os.path.join(str(cwd or os.getcwd()), p)
    return os.path.normcase(os.path.realpath(p))


def inside(path: str, root: str) -> bool:
    """`path` es `root` o cuelga de él (ambos normalizados). Nunca por prefijo de texto: ops/t_1 no cubre ops/t_10."""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # otra unidad
        return False


def inside_any(path: str, roots) -> bool:
    return any(inside(path, r) for r in roots)


def is_secret(text: str) -> bool:
    return bool(text) and bool(SECRET_RE.search(str(text)))


def parse_targets(body: str | None) -> tuple[list[str], list[str]]:
    """(orígenes, destinos) declarados en el cuerpo con `Origen-Ops:` / `Destino-Ops:` (una ruta por línea)."""
    body = body or ""
    return ([m.strip().strip("\"'") for m in ORIGIN_LINE.findall(body)],
            [m.strip().strip("\"'") for m in DEST_LINE.findall(body)])


def validate_dests(dests, origins, dest_roots, workspace_root) -> tuple[list[str], list[str]]:
    """Destinos válidos (normalizados) y motivos de los rechazados.

    Un destino debe ser absoluto, colgar de una de `dest_roots` (lanes.yaml, config de confianza), no ser una ruta
    del sistema/agentes, no ser la raíz de los workspaces ops y no solaparse con un origen (escribir ahí tocaría
    el original)."""
    ok, bad = [], []
    roots = [normalize(r) for r in dest_roots or ()]
    forbidden = [normalize(f) for f in _FORBIDDEN_DEST] + [normalize(workspace_root)]
    origins_n = [normalize(o) for o in origins or () if o]
    for raw in dests or ():
        raw_s = str(raw).strip()
        if not (os.path.isabs(raw_s) or re.match(r"^/[a-zA-Z]/", raw_s)) or re.match(r"^[a-zA-Z]:(?![\\/])", raw_s):
            bad.append(f"{raw_s}: no es una ruta absoluta")
            continue
        d = normalize(raw_s)
        if os.path.dirname(d) == d:
            bad.append(f"{raw_s}: raíz de unidad")
        elif not inside_any(d, roots):
            bad.append(f"{raw_s}: fuera de dest_roots del carril")
        elif any(inside(d, f) or inside(f, d) for f in forbidden):
            bad.append(f"{raw_s}: ruta protegida")
        elif any(inside(d, o) or inside(o, d) for o in origins_n):
            bad.append(f"{raw_s}: se solapa con un origen (no se escribe sobre el original)")
        elif is_secret(raw_s):
            bad.append(f"{raw_s}: parece una ruta de secretos")
        else:
            ok.append(d)
    return ok, bad
