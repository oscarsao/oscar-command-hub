"""Mechanical verification: never trust the worker's self-report."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from .config import Lane


@dataclass
class VerifyResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    test_exit: int | None = None
    remote_sha: str | None = None


def _git(cwd: str, *args: str, runner=subprocess.run) -> subprocess.CompletedProcess:
    return runner(["git", "-C", cwd, *args], capture_output=True, text=True, encoding="utf-8",
                  errors="replace", timeout=120)


def verify(lane: Lane, task_id: str, cwd: str, result: dict, runner=subprocess.run) -> VerifyResult:
    branch = f"lane/{task_id}"
    reasons: list[str] = []
    claimed = (result.get("head_sha") or "").strip().lower()
    if result.get("branch") != branch:
        reasons.append(f"branch declarada '{result.get('branch')}' != '{branch}'")

    ls = _git(cwd, "ls-remote", lane.remote, f"refs/heads/{branch}", runner=runner)
    remote_sha = ls.stdout.split()[0].lower() if ls.returncode == 0 and ls.stdout.strip() else None
    if not remote_sha:
        reasons.append(f"{branch} no existe en {lane.remote}")
    elif len(claimed) < 7 or not remote_sha.startswith(claimed):
        reasons.append(f"head_sha declarado '{claimed[:12]}' != remoto '{remote_sha[:12]}'")

    if remote_sha:
        if _git(cwd, "cat-file", "-e", f"{remote_sha}^{{commit}}", runner=runner).returncode != 0:
            reasons.append(f"commit {remote_sha[:12]} no existe localmente")
        ahead = _git(cwd, "rev-list", "--count", f"{lane.remote}/{lane.base}..{remote_sha}", runner=runner)
        if ahead.returncode != 0 or ahead.stdout.strip() in ("", "0"):
            reasons.append(f"{branch} no tiene commits sobre {lane.remote}/{lane.base}")
        head = _git(cwd, "rev-parse", "HEAD", runner=runner).stdout.strip().lower()
        if head != remote_sha:
            reasons.append(f"HEAD local '{head[:12]}' != remoto '{remote_sha[:12]}' (cambios sin empujar)")

    test_exit = None
    try:
        t = runner(lane.test_cmd, shell=True, cwd=cwd, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=1800)
        test_exit = t.returncode
        if t.returncode != 0:
            reasons.append(f"test_cmd falló (exit {t.returncode}): {(t.stdout + t.stderr).strip()[-300:]}")
    except subprocess.TimeoutExpired:
        reasons.append("test_cmd superó 30 min")
    return VerifyResult(ok=not reasons, reasons=reasons, test_exit=test_exit, remote_sha=remote_sha)
