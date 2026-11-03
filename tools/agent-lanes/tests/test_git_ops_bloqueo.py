"""Serialización de fetch + worktree add por repo y reintento ante el bloqueo de .git/config (31-10)."""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from agent_lanes import git_ops
from agent_lanes.git_ops import GitError, GitOps

BLOQUEO = "error: could not lock config file .git/config: File exists"


def _lane(tmp_path):
    return SimpleNamespace(repo=str(tmp_path / "repo"), remote="origin", base="main", worktree_root=str(tmp_path / "_lanes"))


class FakeGit:
    """Runner de git con dobles: cuenta cuántas llamadas están en vuelo a la vez y, si un `worktree add` se solapa con
    otra llamada git del mismo repo, responde con el bloqueo real de .git/config."""

    def __init__(self, fallos_bloqueo=0, stderr_fijo=None):
        self.mutex = threading.Lock()
        self.dentro = 0
        self.max_dentro = 0
        self.fallos_bloqueo = fallos_bloqueo
        self.stderr_fijo = stderr_fijo
        self.worktree_adds = 0

    def __call__(self, cmd, **kw):
        with self.mutex:
            self.dentro += 1
            self.max_dentro = max(self.max_dentro, self.dentro)
            solapada = self.dentro > 1
        try:
            time.sleep(0.005)  # ventana para que, sin lock, los hilos se solapen de verdad
            if "worktree" in cmd and "add" in cmd:
                with self.mutex:
                    self.worktree_adds += 1
                    forzar = self.fallos_bloqueo > 0
                    if forzar:
                        self.fallos_bloqueo -= 1
                if forzar or solapada:
                    return subprocess.CompletedProcess(cmd, 128, "", BLOQUEO)
                if self.stderr_fijo:
                    return subprocess.CompletedProcess(cmd, 128, "", self.stderr_fijo)
            if "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 1, "", "")  # la rama local no existe
            if "+refs/heads/" in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, 0, "", "")  # la rama remota existe
            return subprocess.CompletedProcess(cmd, 0, "", "")
        finally:
            with self.mutex:
                self.dentro -= 1


def test_siete_workers_sobre_el_mismo_repo_no_se_solapan(tmp_path):
    fake = FakeGit()
    ops = GitOps(runner=fake)
    lane = _lane(tmp_path)
    barrera = threading.Barrier(7)
    errores = []

    def worker(i):
        try:
            barrera.wait()
            ops._prepare_worktree(lane, f"t_{i:08x}")
        except Exception as exc:  # noqa: BLE001 - se recoge para aseverar después
            errores.append(exc)

    hilos = [threading.Thread(target=worker, args=(i,)) for i in range(7)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=30)
    assert errores == []
    assert fake.worktree_adds == 7
    assert fake.max_dentro == 1  # el lock por repo deja una sola llamada git en vuelo


def test_reintenta_el_bloqueo_y_no_avisa_si_se_recupera(tmp_path, caplog):
    esperas = []
    fake = FakeGit(fallos_bloqueo=1)
    ops = GitOps(runner=fake, sleep=esperas.append)
    with caplog.at_level(logging.WARNING, logger="agent_lanes"):
        ruta = ops._prepare_worktree(_lane(tmp_path), "t_aaaaaaaa")
    assert ruta.endswith("lane-t_aaaaaaaa")
    assert esperas == [git_ops._REINTENTOS_BLOQUEO[0]]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_agotados_los_reintentos_un_solo_aviso(tmp_path, caplog):
    esperas = []
    ops = GitOps(runner=FakeGit(fallos_bloqueo=10**6), sleep=esperas.append)
    with caplog.at_level(logging.WARNING, logger="agent_lanes"), pytest.raises(GitError, match="could not lock"):
        ops._prepare_worktree(_lane(tmp_path), "t_bbbbbbbb")
    assert esperas == list(git_ops._REINTENTOS_BLOQUEO)
    avisos = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(avisos) == 1


def test_otro_error_de_git_no_se_reintenta(tmp_path):
    esperas = []
    ops = GitOps(runner=FakeGit(stderr_fijo="fatal: la rama no existe"), sleep=esperas.append)
    with pytest.raises(GitError):
        ops._prepare_worktree(_lane(tmp_path), "t_cccccccc")
    assert esperas == []
