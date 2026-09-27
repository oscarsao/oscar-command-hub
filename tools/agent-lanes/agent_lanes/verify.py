"""Mechanical verification: never trust the worker's self-report."""
from __future__ import annotations

import subprocess

from . import proc as _proc
from dataclasses import dataclass, field

from .config import ROOT, Lane


@dataclass
class VerifyResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    test_exit: int | None = None
    remote_sha: str | None = None


def render_test_cmd(lane: Lane) -> str:
    """test_cmd comes only from lanes.yaml (never from a task body); placeholders are fixed values."""
    return lane.test_cmd.replace("{AGENT_LANES_DIR}", ROOT.as_posix()).replace("{BASE}", lane.base_ref)


def _git(cwd: str, *args: str, runner=_proc.run) -> subprocess.CompletedProcess:
    return runner(["git", "-C", cwd, *args], capture_output=True, text=True, encoding="utf-8",
                  errors="replace", timeout=120)


def verify(lane: Lane, task_id: str, cwd: str, result: dict, runner=_proc.run) -> VerifyResult:
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

    if remote_sha and lane.forbidden_paths:
        # --no-renames: a pure `git mv` out of a vetoed dir would otherwise list only the new path.
        changed = _git(cwd, "diff", "--no-renames", "--name-only", f"{lane.base_ref}...{remote_sha}",
                       runner=runner).stdout.split()
        hits = [f for f in changed if any(f.replace("\\", "/").startswith(p) for p in lane.forbidden_paths)]
        if hits:
            reasons.append(f"toca rutas vetadas en este carril: {', '.join(hits[:10])}")

    test_exit = None
    if reasons or not lane.test_cmd:
        # Never run test_cmd (it executes code from the worktree) on a branch that already failed the checks.
        return VerifyResult(ok=not reasons, reasons=reasons, test_exit=None, remote_sha=remote_sha)
    try:
        t = runner(render_test_cmd(lane), shell=True, cwd=cwd, capture_output=True, text=True,
                   encoding="utf-8", errors="replace", timeout=1800)
        test_exit = t.returncode
        if t.returncode != 0:
            reasons.append(f"test_cmd falló (exit {t.returncode}): {(t.stdout + t.stderr).strip()[-300:]}")
    except subprocess.TimeoutExpired:
        reasons.append("test_cmd superó 30 min")
    return VerifyResult(ok=not reasons, reasons=reasons, test_exit=test_exit, remote_sha=remote_sha)
