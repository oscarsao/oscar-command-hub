"""Hash de contract/ (solo biblioteca estándar: lo usan el hub y el lanzador del host, que lo ejecuta desde su clon).

Detecta DERIVA entre el clon del host y el del hub (E2 de specs/worker-remoto-ssh.md §5.5): no es una frontera de
seguridad, porque quien controla el host puede responder lo que quiera. Los fines de línea se normalizan (git en Windows
con autocrlf da bytes distintos del mismo commit) y se ignoran __pycache__ y .pyc."""
from __future__ import annotations

import hashlib
from pathlib import Path


def contract_hash(root: Path) -> str:
    """sha256 hexadecimal de los ficheros de `<root>/contract` (nombre en posix + contenido con LF), en orden. '' si falta."""
    base = Path(root) / "contract"
    if not base.is_dir():
        return ""
    h = hashlib.sha256()
    for f in sorted(p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"):
        h.update(f.relative_to(base).as_posix().encode("utf-8") + b"\0")
        h.update(f.read_bytes().replace(b"\r\n", b"\n") + b"\0")
    return h.hexdigest()
