"""Generic mechanical gates for lanes without a usable test suite. Standalone (runs with cwd = worktree).

    py -3.12 <agent-lanes>/agent_lanes/checks.py <BASE> [--run-from-base <repo/script.py> [args...]]

- diff-check: `git diff --check BASE...HEAD` (whitespace errors, conflict markers)
- py-compile: every .py added/modified since BASE must compile (in memory, nothing written)
- --run-from-base: run a repo gate script as it is on BASE, never the worker's copy (the worker controls the
  worktree, so running its version would be arbitrary code execution in the runner).
Exit 0 when all gates pass.
"""
from __future__ import annotations

import os
import subprocess

from . import proc as _proc
import sys

TIMEOUT = 600


def _git(*args: str) -> subprocess.CompletedProcess:
    return _proc.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=TIMEOUT)


def run_from_base(base: str, script: str, args: list[str]) -> tuple[int, str]:
    src = _git("show", f"{base}:{script}")
    if src.returncode != 0:
        return 1, f"{script} no existe en {base}"
    # Next to the original so a script that derives its repo root from __file__ still resolves it.
    folder, name = os.path.split(script)
    trusted = os.path.join(folder, f".lane_trusted_{name}")
    try:
        with open(trusted, "w", encoding="utf-8", newline="") as fh:
            fh.write(src.stdout)
        cp = _proc.run([sys.executable, trusted, *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=TIMEOUT)
        return cp.returncode, (cp.stdout + cp.stderr).strip()[-1500:]
    finally:
        if os.path.exists(trusted):
            os.remove(trusted)


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    base, rest = argv[0], argv[1:]
    failures = []
    dc = _git("diff", "--check", f"{base}...HEAD")
    if dc.returncode != 0:
        failures.append(f"git diff --check:\n{dc.stdout.strip()[-1500:]}")
    names = _git("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD", "--", "*.py").stdout.split()
    for name in names:
        try:
            with open(name, "rb") as fh:
                compile(fh.read(), name, "exec")
        except (SyntaxError, ValueError) as exc:
            failures.append(f"compile {name}: {exc}")
    if rest[:1] == ["--run-from-base"] and len(rest) >= 2:
        rc, out = run_from_base(base, rest[1], rest[2:])
        print(out)
        if rc != 0:
            failures.append(f"{rest[1]} (versión de {base}) exit {rc}")
    for f in failures:
        print(f, file=sys.stderr)
    print(f"checks: {len(names)} .py compilados, {len(failures)} fallo(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
