"""Tests acotados al delta de la rama para el carril claude-hub. Standalone (cwd = worktree).

    py -3.12 <agent-lanes>/agent_lanes/tests_delta.py <BASE>

Elige con `git diff --name-only BASE...HEAD`:
- agent_lanes/<m>.py cambiado -> tests/test_<m>*.py existentes
- tests/*.py cambiado -> ese mismo fichero
- siempre test_readme_lanes y test_doctor_config
Respaldo seguro (suite completa): cambia tests/conftest.py, config.py, lanes.py, kanban_api.py, hermes.py,
lanes.yaml con claves nuevas, hay más de 25 ficheros o git no consigue calcular el diff.
La primera línea de la salida dice el modo; el código de salida es el de pytest.
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys

TIMEOUT = 600
AL = "tools/agent-lanes"
TESTS_DIR = f"{AL}/tests"
FIXED = ("test_readme_lanes", "test_doctor_config")
FULL_TRIGGERS = {
    f"{TESTS_DIR}/conftest.py", f"{AL}/agent_lanes/config.py", f"{AL}/agent_lanes/lanes.py",
    f"{AL}/agent_lanes/kanban_api.py", f"{AL}/agent_lanes/hermes.py",
}
MAX_FILES = 25
_YAML_KEY = re.compile(r"^\s*(?:-\s+)?([A-Za-z_][\w-]*)\s*:")
# Standalone script (runs by path, not as a package): no relative imports. Same no-window flag as checks.py.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0


def _git(*args: str) -> subprocess.CompletedProcess:
    kw = {"creationflags": _NO_WINDOW} if _NO_WINDOW else {}
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=TIMEOUT, **kw)


def _yaml_keys(text: str) -> set[str]:
    return {m.group(1) for line in text.splitlines() if (m := _YAML_KEY.match(line))}


def _lanes_yaml_new_keys(base: str) -> bool:
    path = f"{AL}/lanes.yaml"
    old = _git("show", f"{base}:{path}")
    try:
        with open(path, encoding="utf-8") as fh:
            new = fh.read()
    except OSError:
        return True
    if old.returncode != 0:
        return True
    return bool(_yaml_keys(new) - _yaml_keys(old.stdout))


def plan(base: str) -> tuple[str, list[str]]:
    """('completo', []) o ('delta', [ficheros de test relativos al repo])."""
    cp = _git("diff", "--name-only", f"{base}...HEAD")
    if cp.returncode != 0:
        return "completo", []
    changed = [n.strip().replace("\\", "/") for n in cp.stdout.splitlines() if n.strip()]
    if len(changed) > MAX_FILES or FULL_TRIGGERS & set(changed):
        return "completo", []
    if f"{AL}/lanes.yaml" in changed and _lanes_yaml_new_keys(base):
        return "completo", []
    chosen = {f"{TESTS_DIR}/{name}.py" for name in FIXED}
    for n in changed:
        if not n.endswith(".py"):
            continue
        folder, fname = os.path.split(n)
        if folder == TESTS_DIR:
            chosen.add(n)
        elif folder == f"{AL}/agent_lanes":
            chosen.update(p.replace("\\", "/") for p in glob.glob(f"{TESTS_DIR}/test_{fname[:-3]}*.py"))
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
