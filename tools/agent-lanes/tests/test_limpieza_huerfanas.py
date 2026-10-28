"""Carpetas huérfanas de worktree (28-10): borrado acotado a _lanes/lane-t_<id> y un intento cada 6 h por tarjeta."""
import dataclasses
import logging
import os
import stat
import subprocess

from agent_lanes import review
from agent_lanes.git_ops import GitOps
from agent_lanes.review import REINTENTO_LIMPIEZA, sweep_done

from .test_runner_state import LANE as BASE_LANE


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


class Runner:
    """Dobla git: `worktree list` devuelve lo indicado; el resto, ok sin rama."""

    def __init__(self, conocidos=(), list_rc=0):
        self.conocidos, self.list_rc, self.calls = list(conocidos), list_rc, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[3:5] == ["worktree", "list"]:
            return cp(self.list_rc, "".join(f"worktree {p}\nHEAD abc\n\n" for p in self.conocidos))
        if "show-ref" in cmd or "rev-parse" in cmd:
            return cp(1)
        return cp(0)


def lane_en(root):
    return dataclasses.replace(BASE_LANE, worktree_root=str(root), repo=str(root.parent / "repo"))


def huerfana(tmp_path, tid="t_aaaaaaaa"):
    root = tmp_path / "_lanes"
    d = root / f"lane-{tid}" / "node_modules" / "x"
    d.mkdir(parents=True)
    f = d / "ro.txt"
    f.write_text("x")
    os.chmod(f, stat.S_IREAD)  # solo lectura, como lo que deja pnpm
    return root, root / f"lane-{tid}"


def test_borra_carpeta_huerfana_aunque_tenga_solo_lectura(tmp_path):
    root, path = huerfana(tmp_path)
    run = Runner(conocidos=[str(tmp_path / "repo")])
    ok, _ = GitOps(runner=run).cleanup(lane_en(root), "t_aaaaaaaa")
    assert ok and not path.exists()
    assert not any(c[3:5] == ["worktree", "remove"] for c in run.calls)  # ya no se intenta el remove fatal


def test_worktree_conocido_por_git_no_se_borra_a_mano(tmp_path):
    root, path = huerfana(tmp_path)
    run = Runner(conocidos=[str(path).replace(os.sep, "/")])  # git lista con barras normales
    GitOps(runner=run).cleanup(lane_en(root), "t_aaaaaaaa")
    assert any(c[3:5] == ["worktree", "remove"] for c in run.calls)


def test_no_borra_fuera_de_lanes_ni_si_git_no_responde(tmp_path):
    fuera = tmp_path / "otras"
    (fuera / "lane-t_bbbbbbbb").mkdir(parents=True)
    GitOps(runner=Runner()).cleanup(lane_en(fuera), "t_bbbbbbbb")
    assert (fuera / "lane-t_bbbbbbbb").exists()
    root, path = huerfana(tmp_path, "t_cccccccc")
    GitOps(runner=Runner(list_rc=128)).cleanup(lane_en(root), "t_cccccccc")
    assert path.exists()  # sin saber qué worktrees hay, no se borra nada


def test_no_borra_si_el_nombre_no_es_de_tarjeta(tmp_path):
    root = tmp_path / "_lanes"
    (root / "lane-otra").mkdir(parents=True)
    assert GitOps(runner=Runner())._huerfana(lane_en(root), root / "lane-otra") is False


class Hermes:
    def list_status(self, assignee, status, sort=None):
        return [{"id": "t_dddddddd"}]


class Git:
    def __init__(self):
        self.n = 0

    def cleanup(self, lane, tid):
        self.n += 1
        return False, "no se pudo"


def test_limpieza_fallida_se_reintenta_cada_6h_y_loguea_una_vez(tmp_path, caplog):
    review._limpieza_fallida.clear()
    root = tmp_path / "_lanes"
    (root / "lane-t_dddddddd").mkdir(parents=True)
    lane, g, t = lane_en(root), Git(), [1000.0]
    with caplog.at_level(logging.WARNING, logger="agent_lanes"):
        for _ in range(5):
            sweep_done(lane, Hermes(), g, clock=lambda: t[0])
            t[0] += 600
        assert g.n == 1 and sum("limpieza pendiente" in r.message for r in caplog.records) == 1
        t[0] += REINTENTO_LIMPIEZA
        sweep_done(lane, Hermes(), g, clock=lambda: t[0])
    assert g.n == 2 and sum("limpieza pendiente" in r.message for r in caplog.records) == 2
    review._limpieza_fallida.clear()
