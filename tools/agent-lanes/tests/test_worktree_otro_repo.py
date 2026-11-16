"""Tarjeta reasignada a un carril de otro repo: no se reutiliza el worktree del repo anterior (16-11, t_8f32efcc)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_lanes.git_ops import GitOps, WorktreeAjeno


def _g(cwd, *args):
    cp = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    return cp.stdout.strip()


def _repo(tmp_path, nombre):
    """Repo con su remoto bare (origin) y un commit en main empujado."""
    bare = tmp_path / f"{nombre}.git"
    work = tmp_path / nombre
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], capture_output=True, check=True)
    subprocess.run(["git", "init", "-b", "main", str(work)], capture_output=True, check=True)
    _g(work, "config", "user.email", "t@t")
    _g(work, "config", "user.name", "t")
    (work / "a.txt").write_text(nombre, newline="\n")
    _g(work, "add", "a.txt")
    _g(work, "commit", "-m", "init")
    _g(work, "remote", "add", "origin", str(bare))
    _g(work, "push", "origin", "main")
    return work


def _lane(repo, tmp_path, nombre):
    return SimpleNamespace(name=nombre, repo=str(repo), remote="origin", base="main",
                           worktree_root=str(tmp_path / "_lanes"))


@pytest.fixture
def dos(tmp_path):
    a, b = _repo(tmp_path, "repo_a"), _repo(tmp_path, "repo_b")
    return a, b, _lane(a, tmp_path, "carril-a"), _lane(b, tmp_path, "carril-b")


def test_reasignada_limpia_se_rehace_en_el_repo_nuevo(dos):
    a, b, lane_a, lane_b = dos
    ops = GitOps()
    path = ops._prepare_worktree(lane_a, "t_1")
    assert _g(path, "rev-parse", "--git-common-dir").endswith(".git") and (Path(path) / "a.txt").read_text() == "repo_a"
    nuevo = ops._prepare_worktree(lane_b, "t_1")
    assert nuevo == path and (Path(nuevo) / "a.txt").read_text() == "repo_b"
    assert _g(nuevo, "remote", "get-url", "origin").endswith("repo_b.git")
    assert "carril-b" in ops.rehechos["t_1"] and "repo_a.git" in ops.rehechos["t_1"]
    assert _g(a, "rev-parse", "--verify", "refs/heads/lane/t_1")  # la rama del repo anterior no se borra


def test_reasignada_con_cambios_sin_guardar_no_se_toca(dos):
    a, b, lane_a, lane_b = dos
    ops = GitOps()
    path = ops._prepare_worktree(lane_a, "t_2")
    (Path(path) / "a.txt").write_text("trabajo a medias", newline="\n")
    with pytest.raises(WorktreeAjeno, match="cambios sin guardar"):
        ops._prepare_worktree(lane_b, "t_2")
    assert (Path(path) / "a.txt").read_text() == "trabajo a medias" and not ops.rehechos


def test_reasignada_con_commits_sin_empujar_no_se_toca(dos):
    a, b, lane_a, lane_b = dos
    ops = GitOps()
    path = ops._prepare_worktree(lane_a, "t_3")
    (Path(path) / "nuevo.txt").write_text("x", newline="\n")
    _g(path, "add", "nuevo.txt")
    _g(path, "commit", "-m", "sin empujar")
    with pytest.raises(WorktreeAjeno, match="commits sin empujar"):
        ops._prepare_worktree(lane_b, "t_3")
    assert (Path(path) / "nuevo.txt").exists()


def test_misma_tarjeta_mismo_repo_reutiliza_el_worktree(dos):
    a, b, lane_a, _ = dos
    ops = GitOps()
    path = ops._prepare_worktree(lane_a, "t_4")
    (Path(path) / "a.txt").write_text("cambio", newline="\n")  # sucio: si reutiliza, no lo toca
    assert ops._prepare_worktree(lane_a, "t_4") == path
    assert (Path(path) / "a.txt").read_text() == "cambio" and not ops.rehechos
