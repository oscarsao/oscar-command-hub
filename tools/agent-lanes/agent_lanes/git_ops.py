"""Worktree preparation: one worktree + branch lane/<task_id> per task, outside the repo."""
from __future__ import annotations

import logging
import subprocess

from . import proc as _proc
from pathlib import Path

from .config import Lane

log = logging.getLogger("agent_lanes")


class GitError(RuntimeError):
    pass


class GitOps:
    def __init__(self, runner=_proc.run):
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
        elif self._fetch_remote_lane(lane, branch):
            # Tarea reabierta tras la limpieza de done (Oscar pidió cambios): la rama local ya no existe pero la
            # remota conserva el trabajo. Partir de la base lo perdería y el push sería non-fast-forward.
            self._git(lane.repo, "worktree", "add", str(path), "-b", branch, f"{lane.remote}/{branch}")
        else:
            self._git(lane.repo, "worktree", "add", str(path), "-b", branch, f"{lane.remote}/{lane.base}")
        return str(path)

    def _fetch_remote_lane(self, lane: Lane, branch: str) -> bool:
        """True si <remote>/lane/<id> existe (y queda actualizada en refs/remotes)."""
        cp = self._run(["git", "-C", lane.repo, "fetch", lane.remote,
                        f"+refs/heads/{branch}:refs/remotes/{lane.remote}/{branch}"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        return cp.returncode == 0

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

    def _out(self, *args: str, timeout: int = 120) -> str | None:
        cp = self._run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return cp.stdout if cp.returncode == 0 else None

    def _remote_sha(self, lane: Lane, branch: str) -> str | None:
        out = self._out("git", "-C", lane.repo, "ls-remote", lane.remote, f"refs/heads/{branch}")
        parts = (out or "").split()
        return parts[0] if parts else None

    def cleanup(self, lane: Lane, task_id: str) -> tuple[bool, str]:
        """Solo para tareas en done. `worktree remove`; si falla por archivos sin commitear/sin seguimiento y el
        HEAD del worktree es exactamente el commit de la rama remota (el trabajo está en el remoto), se registra en
        el log lo que se descarta y se repite con --force. Nunca --force si el remoto no coincide o no se sabe.
        La rama local se borra solo si el remoto tiene su mismo commit; la remota se conserva hasta el merge."""
        branch = f"lane/{task_id}"
        path = Path(lane.worktree_root) / f"lane-{task_id}"
        if path.exists():
            cp = self._run(["git", "-C", lane.repo, "worktree", "remove", str(path)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300)
            if cp.returncode != 0:
                head = (self._out("git", "-C", str(path), "rev-parse", "HEAD") or "").strip()
                remote = self._remote_sha(lane, branch)
                if not head or not remote or remote != head:
                    return False, (f"worktree no eliminado: tiene cambios y la rama remota ({remote or 'ausente'}) "
                                   f"no coincide con el HEAD local ({head[:10] or '?'}): no se fuerza. "
                                   f"git: {cp.stderr.strip()[:200]}")
                dirty = (self._out("git", "-C", str(path), "status", "--porcelain", "--untracked-files=all")
                         or "").splitlines()
                log.warning("%s: HEAD %s ya está en %s/%s; se descartan %d archivo(s) locales al limpiar: %s",
                            task_id, head[:10], lane.remote, branch, len(dirty), dirty[:200])
                cp = self._run(["git", "-C", lane.repo, "worktree", "remove", "--force", str(path)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
                if cp.returncode != 0:
                    return False, f"worktree no eliminado ni con --force: {cp.stderr.strip()[:200]}"
        if not self._branch_exists(lane.repo, branch):
            return True, "sin rama local"
        local = (self._out("git", "-C", lane.repo, "rev-parse", f"refs/heads/{branch}", timeout=60) or "").strip()
        remote = self._remote_sha(lane, branch)
        if not remote or remote != local:
            return False, f"rama local {branch} conservada: no coincide con {lane.remote} (commits sin empujar)"
        # -D is safe here: the exact same commit is on the remote, which is kept until the merge.
        cp = self._run(["git", "-C", lane.repo, "branch", "-D", branch], capture_output=True, text=True, timeout=60)
        return cp.returncode == 0, "limpio" if cp.returncode == 0 else cp.stderr.strip()[:200]
