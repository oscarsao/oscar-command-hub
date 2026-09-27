"""Worktree preparation: one worktree + branch lane/<task_id> per task, outside the repo."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import Lane


class GitError(RuntimeError):
    pass


class GitOps:
    def __init__(self, runner=subprocess.run):
        self._run = runner

    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        cp = self._run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=300)
        if cp.returncode != 0:
            raise GitError(f"git {' '.join(args[:2])} falló: {cp.stderr.strip()[:300]}")
        return cp

    def prepare_worktree(self, lane: Lane, task_id: str) -> str:
        branch = f"lane/{task_id}"
        path = Path(lane.worktree_root) / f"lane-{task_id}"
        self._git(lane.repo, "fetch", lane.remote, lane.base)
        if path.exists():  # retry of the same task: reuse its worktree and branch
            current = self._git(str(path), "branch", "--show-current").stdout.strip()
            if current != branch:
                raise GitError(f"{path} existe pero está en '{current}', no en {branch}")
            return str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = self._run(["git", "-C", lane.repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                           capture_output=True, text=True, timeout=60).returncode == 0
        if exists:
            self._git(lane.repo, "worktree", "add", str(path), branch)
        else:
            self._git(lane.repo, "worktree", "add", str(path), "-b", branch, f"{lane.remote}/{lane.base}")
        return str(path)
