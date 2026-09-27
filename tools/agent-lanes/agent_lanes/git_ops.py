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

    def _branch_exists(self, repo: str, branch: str) -> bool:
        return self._run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
                         capture_output=True, text=True, timeout=60).returncode == 0

    def prepare_review_worktree(self, lane: Lane, task_id: str) -> str:
        """Worktree at the pushed lane/<id>, recreated from the remote branch if it was removed."""
        branch = f"lane/{task_id}"
        path = Path(lane.worktree_root) / f"lane-{task_id}"
        self._git(lane.repo, "fetch", lane.remote, lane.base, f"+refs/heads/{branch}:refs/remotes/{lane.remote}/{branch}")
        if path.exists():
            return str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._branch_exists(lane.repo, branch):
            self._git(lane.repo, "worktree", "add", str(path), branch)
        else:
            self._git(lane.repo, "worktree", "add", str(path), "-b", branch, f"{lane.remote}/{branch}")
        return str(path)

    def cleanup(self, lane: Lane, task_id: str) -> tuple[bool, str]:
        """After done: `worktree remove` WITHOUT --force (a dirty worktree is kept and reported) and delete the
        local branch only when the remote has exactly the same commit. The remote branch stays until the merge."""
        branch = f"lane/{task_id}"
        path = Path(lane.worktree_root) / f"lane-{task_id}"
        if path.exists():
            cp = self._run(["git", "-C", lane.repo, "worktree", "remove", str(path)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
            if cp.returncode != 0:
                return False, f"worktree no eliminado (¿cambios sin commitear?): {cp.stderr.strip()[:200]}"
        if not self._branch_exists(lane.repo, branch):
            return True, "sin rama local"
        local = self._run(["git", "-C", lane.repo, "rev-parse", f"refs/heads/{branch}"], capture_output=True,
                          text=True, timeout=60).stdout.strip()
        remote = self._run(["git", "-C", lane.repo, "ls-remote", lane.remote, f"refs/heads/{branch}"],
                           capture_output=True, text=True, timeout=120).stdout.split()
        if not remote or remote[0] != local:
            return False, f"rama local {branch} conservada: no coincide con {lane.remote} (commits sin empujar)"
        # -D is safe here: the exact same commit is on the remote, which is kept until the merge.
        cp = self._run(["git", "-C", lane.repo, "branch", "-D", branch], capture_output=True, text=True, timeout=60)
        return cp.returncode == 0, "limpio" if cp.returncode == 0 else cp.stderr.strip()[:200]
