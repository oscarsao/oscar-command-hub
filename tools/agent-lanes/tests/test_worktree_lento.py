"""`git worktree add` lento: tiempo configurable, reintento con prune y bloqueo sin escalada (git simulado)."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from agent_lanes.git_ops import GitOps, WorktreeLento
from tests.test_runner_state import LANE, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, ok_outcome
from agent_lanes.runner import LaneRunner


def _lane(tmp_path):
    return SimpleNamespace(repo=str(tmp_path / "repo"), remote="origin", base="main", worktree_root=str(tmp_path / "_lanes"))


class GitLento:
    """Los primeros `lentos` `worktree add` agotan el tiempo (TimeoutExpired); los siguientes responden."""

    def __init__(self, lentos):
        self.lentos = lentos
        self.cmds = []
        self.timeouts_add = []

    def __call__(self, cmd, **kw):
        self.cmds.append(list(cmd))
        if "add" in cmd and "worktree" in cmd:
            self.timeouts_add.append(kw.get("timeout"))
            if self.lentos > 0:
                self.lentos -= 1
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        if "list" in cmd and "--porcelain" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "worktree a\n\nworktree b\n\nworktree c\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def de(self, *palabras):
        return [c for c in self.cmds if all(p in c for p in palabras)]


def test_exito_al_primer_intento_sin_prune_y_con_timeout_por_defecto(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_LANES_WORKTREE_TIMEOUT", raising=False)
    git = GitLento(0)
    esperas = []
    GitOps(runner=git, sleep=esperas.append).prepare_worktree(_lane(tmp_path), "t_1")
    assert git.timeouts_add == [600.0] and not git.de("prune") and esperas == []


def test_timeout_configurable_por_entorno(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LANES_WORKTREE_TIMEOUT", "900")
    git = GitLento(0)
    GitOps(runner=git).prepare_worktree(_lane(tmp_path), "t_1")
    assert git.timeouts_add == [900.0]


def test_exito_al_segundo_intento_hace_prune_y_espera(tmp_path):
    git = GitLento(1)
    esperas = []
    ruta = GitOps(runner=git, sleep=esperas.append).prepare_worktree(_lane(tmp_path), "t_1")
    assert ruta.endswith("lane-t_1")
    assert len(git.timeouts_add) == 2 and len(git.de("worktree", "prune")) == 1
    assert esperas == [30.0]
    # el prune va entre los dos intentos y no se borra nada
    idx = [i for i, c in enumerate(git.cmds) if "add" in c or "prune" in c]
    assert "add" in git.cmds[idx[0]] and "prune" in git.cmds[idx[1]] and "add" in git.cmds[idx[2]]
    assert not git.de("remove") and not git.de("-D")


def test_fallo_total_lanza_worktreelento_con_datos(tmp_path):
    git = GitLento(99)
    esperas = []
    with pytest.raises(WorktreeLento) as ei:
        GitOps(runner=git, sleep=esperas.append).prepare_worktree(_lane(tmp_path), "t_1")
    e = ei.value
    assert len(git.timeouts_add) == 3 and len(git.de("worktree", "prune")) == 2 and esperas == [30.0, 90.0]
    assert e.registrados == 3 and len(e.duraciones) == 3
    c = e.comentario()
    assert "Worktrees registrados: 3" in c and "intento 3" in c and str(tmp_path / "repo") in c


def test_un_fallo_normal_de_git_no_se_reintenta_por_tiempo(tmp_path):
    def run(cmd, **kw):
        if "add" in cmd and "worktree" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: la rama no existe")
        return subprocess.CompletedProcess(cmd, 1 if "rev-parse" in cmd else 0, "", "")
    with pytest.raises(Exception) as ei:
        GitOps(runner=run, sleep=lambda s: None).prepare_worktree(_lane(tmp_path), "t_1")
    assert not isinstance(ei.value, WorktreeLento)


# --- runner: la tarjeta continúa o se bloquea sin escalar ---------------------------------------

class GitRunner:
    def __init__(self, excepcion=None):
        self.excepcion = excepcion

    def prepare_worktree(self, lane, task_id):
        if self.excepcion:
            raise self.excepcion
        return f"{lane.worktree_root}/lane-{task_id}"


class HermesConPrevios(FakeHermes):
    def block(self, task_id, kind, reason):
        self.calls.append(("block", task_id, kind, reason))
        return True

    def show(self, task_id):
        ev = {"kind": "blocked", "payload": {"kind": "transient", "reason": "git worktree add sin respuesta: x"}}
        return {"events": [ev, ev], "task": {"status": "running"}}


def test_runner_fallo_total_comenta_y_bloquea_sin_escalar_a_triage():
    exc = WorktreeLento("C:/repo", 164, [600.0, 601.0, 602.0], 600.0)
    h = HermesConPrevios(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    r = LaneRunner(LANE, hermes=h, git=GitRunner(exc), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                   notify=FakeNotifier())
    assert r.run_once() == {"t_1": "blocked:transient"}
    comentarios = [c[2] for c in h.calls if c[0] == "comment"]
    assert any("Worktrees registrados: 164" in c and "C:/repo" in c and "intento 2: 601 s" in c for c in comentarios)
    bloqueos = [c for c in h.calls if c[0] == "block"]
    assert len(bloqueos) == 1 and bloqueos[0][2] == "transient"  # no se escala a needs_input por «N veces por lo mismo»
    assert "por lo mismo" not in bloqueos[0][3]
