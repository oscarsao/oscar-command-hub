"""Tests acotados al delta de la rama para el carril claude-hub. Standalone (cwd = worktree).

    py -3.12 <agent-lanes>/agent_lanes/tests_delta.py <BASE>

SUITE COMPLETA POR DEFECTO (revisión de la Integradora, 21-11). Solo se acota con `git diff --name-only BASE...HEAD` si
TODO lo cambiado es de uno de estos tipos:
- tests/*.py (no conftest) -> ese mismo fichero
- agent_lanes/<m>.py que tiene tests/test_<m>*.py propios -> esos tests
- ficheros fuera de tools/agent-lanes y documentación .md fuera de roles/ -> nada extra
y siempre se añaden test_readme_lanes y test_doctor_config. Cualquier otra cosa (lanes.yaml, contract/, roles/, conftest,
fixtures, runner/checks/config/lanes/kanban_api/hermes/verify/este script, un módulo sin tests propios, subcarpetas), más
de 25 ficheros, un diff que falla o que agota el tiempo -> suite completa.
La primera línea de la salida dice el modo; el código de salida es el de pytest.
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys

TIMEOUT = 600
AL = "tools/agent-lanes"
TESTS_DIR = f"{AL}/tests"
FIJOS = ("test_readme_lanes", "test_doctor_config")
# Módulos que el resto del sistema usa por todas partes: aunque tengan test propio, un cambio ahí pide la suite entera.
COMUNES = ("config", "lanes", "kanban_api", "hermes", "runner", "checks", "verify", "tests_delta", "state", "notices")
FULL_TRIGGERS = {f"{TESTS_DIR}/conftest.py", *(f"{AL}/agent_lanes/{m}.py" for m in COMUNES)}
MAX_FILES = 25
# Standalone script (runs by path, not as a package): no relative imports. Same no-window flag as checks.py.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0


def _git(*args: str) -> subprocess.CompletedProcess:
    kw = {"creationflags": _NO_WINDOW} if _NO_WINDOW else {}
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=TIMEOUT, **kw)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["git", *args], 124, "", "timeout")


def plan(base: str) -> tuple[str, list[str]]:
    """('completo', []) o ('delta', [ficheros de test relativos al repo])."""
    cp = _git("diff", "--name-only", f"{base}...HEAD")
    if cp.returncode != 0:
        return "completo", []
    changed = [n.strip().replace("\\", "/") for n in cp.stdout.splitlines() if n.strip()]
    if len(changed) > MAX_FILES or FULL_TRIGGERS & set(changed):
        return "completo", []
    chosen = {f"{TESTS_DIR}/{name}.py" for name in FIJOS}
    for n in changed:
        if not n.startswith(f"{AL}/") or (n.endswith(".md") and not n.startswith(f"{AL}/roles/")):
            continue  # fuera de agent-lanes o documentación: no cambia lo que prueban los tests
        folder, fname = os.path.split(n)
        if folder == TESTS_DIR and fname.endswith(".py"):
            chosen.add(n)
        elif folder == f"{AL}/agent_lanes" and fname.endswith(".py"):
            propios = [p.replace("\\", "/") for p in glob.glob(f"{TESTS_DIR}/test_{fname[:-3]}*.py")]
            if not propios:
                return "completo", []  # módulo sin tests propios: no sabemos qué lo prueba
            chosen.update(propios)
        else:
            return "completo", []  # lanes.yaml, contract/, roles/, fixtures, subcarpetas...
    return "delta", sorted(p for p in chosen if os.path.isfile(p))


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    mode, files = plan(argv[0])
    if mode == "completo":
        print("tests_delta: modo completo (suite entera de agent-lanes)", flush=True)
        cmd = [sys.executable, "-m", "pytest", TESTS_DIR, "-q"]
    else:
        print(f"tests_delta: modo delta, {len(files)} fichero(s) de test", flush=True)
        cmd = [sys.executable, "-m", "pytest", *files, "-q"]
    kw = {"creationflags": _NO_WINDOW} if _NO_WINDOW else {}
    return subprocess.run(cmd, **kw).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
