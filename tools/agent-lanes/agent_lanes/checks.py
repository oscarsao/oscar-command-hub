"""Generic mechanical gates for lanes without a usable test suite. Standalone (runs with cwd = worktree).

    py -3.12 <agent-lanes>/agent_lanes/checks.py <BASE>

- diff-check: `git diff --check BASE...HEAD` (whitespace errors, conflict markers)
- py-compile: every .py added/modified since BASE must compile
Exit 0 when all gates pass.
"""
from __future__ import annotations

import subprocess
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    base = argv[0]
    failures = []
    dc = subprocess.run(["git", "diff", "--check", f"{base}...HEAD"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace")
    if dc.returncode != 0:
        failures.append(f"git diff --check:\n{dc.stdout.strip()[-1500:]}")
    names = subprocess.run(["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD", "--", "*.py"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.split()
    for name in names:
        try:  # compile in memory: no .pyc written into the worktree
            with open(name, "rb") as fh:
                compile(fh.read(), name, "exec")
        except (SyntaxError, ValueError) as exc:
            failures.append(f"compile {name}: {exc}")
    for f in failures:
        print(f, file=sys.stderr)
    print(f"checks: {len(names)} .py compilados, {len(failures)} fallo(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
