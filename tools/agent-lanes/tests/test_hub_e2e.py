"""Arreglos E2E del 30-09 (t_3598b9ff): reintentos inútiles, sync con la base, fallos de configuración."""
from __future__ import annotations

from types import SimpleNamespace

from agent_lanes.decisions import keyboard_spec
from agent_lanes.git_ops import GitOps
from agent_lanes.runner import LaneRunner
from agent_lanes.verify import VerifyResult
from agent_lanes.worker import build_prompt

from tests.test_runner_state import (FakeGit, FakeHermes, FakeNotifier, FakeWorker, LANE, ok_outcome)


def cp(rc=0, out="", err=""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


def test_sync_base_fetch_then_merge_and_reports_nothing_when_clean():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd[3:])
        return cp()

    assert GitOps(runner=run).sync_base(LANE, "C:/wt/lane-t_1") is None
    assert calls[0][0] == "fetch" and calls[1][0] == "status"  # limpio: sin WIP, directo al merge
    assert calls[2][:2] == ["merge", "--no-edit"] and calls[2][-1] == "origin/master" and len(calls) == 3


def test_sync_base_returns_conflict_text_for_the_worker():
    def run(cmd, **kw):
        return cp(1, "CONFLICT (content): Merge conflict in a.py") if cmd[3] == "merge" else cp()

    assert "CONFLICT" in GitOps(runner=run).sync_base(LANE, "C:/wt/x")


def test_sync_base_survives_fetch_failure_and_non_conflict_errors():
    assert GitOps(runner=lambda cmd, **kw: cp(1, err="red caída")).sync_base(LANE, "x") is None
    run = lambda cmd, **kw: cp(1, err="dirty tree") if cmd[3] == "merge" else cp()  # noqa: E731
    assert GitOps(runner=run).sync_base(LANE, "x") is None


def test_prompt_includes_conflict_section():
    p = build_prompt({"id": "t_1", "body": "b", "merge_conflict": "CONFLICT en a.py"}, LANE)
    assert "Conflicto con origin/master" in p and "CONFLICT en a.py" in p
    assert "Conflicto" not in build_prompt({"id": "t_1", "body": "b"}, LANE)


class SyncGit(FakeGit):
    def __init__(self, conflict=None):
        super().__init__()
        self.conflict = conflict

    def sync_base(self, lane, cwd):
        return self.conflict


class SeenWorker(FakeWorker):
    def run(self, lane, task, cwd, session_id, timeout):
        self.task = task
        return super().run(lane, task, cwd, session_id, timeout)


def test_runner_hands_conflict_to_worker():
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    w = SeenWorker([ok_outcome()])
    ok = lambda lane, tid, cwd, result: VerifyResult(ok=True, test_exit=0)  # noqa: E731
    r = LaneRunner(LANE, hermes=h, git=SyncGit("CONFLICT x"), worker=w, verifier=ok, notify=FakeNotifier())
    r.run_once()
    assert w.task["merge_conflict"] == "CONFLICT x"


# --- t_312253c4: worktree sucio al reanudar → commit WIP(runner) antes del merge --------------------------------
def _git_real(cwd, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout


def _repo_con_remoto(tmp_path):
    remoto, wt = tmp_path / "remoto.git", tmp_path / "wt"
    _git_real(tmp_path, "init", "--bare", "-b", "main", str(remoto))
    _git_real(tmp_path, "clone", str(remoto), str(wt))
    (wt / "a.txt").write_text("a\n", newline="\n")
    (wt / "b.txt").write_text("b\n", newline="\n")
    _git_real(wt, "add", ".")
    _git_real(wt, "commit", "-m", "base")
    _git_real(wt, "push", "origin", "HEAD:main")
    _git_real(wt, "checkout", "-b", "lane/t_1")
    otro = tmp_path / "otro"  # alguien avanza la base tocando a.txt
    _git_real(tmp_path, "clone", str(remoto), str(otro))
    (otro / "a.txt").write_text("a2\n", newline="\n")
    _git_real(otro, "commit", "-am", "avanza base")
    _git_real(otro, "push", "origin", "HEAD:main")
    return wt


def test_sync_base_worktree_sucio_hace_wip_y_merge_y_lo_registra(tmp_path, caplog):
    import logging
    wt = _repo_con_remoto(tmp_path)
    (wt / "b.txt").write_text("b editado\n", newline="\n")  # modificado sin commitear
    (wt / "nuevo.txt").write_text("n\n", newline="\n")      # sin seguimiento
    lane = SimpleNamespace(remote="origin", base="main")
    with caplog.at_level(logging.WARNING, logger="agent_lanes"):
        assert GitOps().sync_base(lane, str(wt)) is None
    assert _git_real(wt, "status", "--porcelain").strip() == ""
    assert (wt / "a.txt").read_text() == "a2\n" and (wt / "nuevo.txt").exists()
    assert "WIP(runner):" in _git_real(wt, "log", "--format=%s", "origin/main..HEAD")
    sha = _git_real(wt, "log", "--format=%H", "--grep=^WIP(runner):", "-1").strip()
    assert any("commit WIP(runner)" in r.getMessage() and sha in r.getMessage() for r in caplog.records)


def test_sync_base_limpio_no_crea_wip(tmp_path):
    wt = _repo_con_remoto(tmp_path)
    assert GitOps().sync_base(SimpleNamespace(remote="origin", base="main"), str(wt)) is None
    assert "WIP(runner)" not in _git_real(wt, "log", "--format=%s")


def test_sync_base_merge_falla_tras_wip_lanza_giterror():
    import pytest
    from agent_lanes.git_ops import GitError

    def run(cmd, **kw):
        sub = cmd[3]
        if sub == "status":
            return cp(0, " M b.txt\n")
        if sub == "merge":
            return cp(1, err="fatal: refusing to merge unrelated histories")
        if sub == "rev-parse":
            return cp(0, "abc1234567890\n")
        return cp()

    with pytest.raises(GitError, match="tras el commit WIP"):
        GitOps(runner=run).sync_base(LANE, "x")


def test_verify_avisa_de_commits_wip_runner():
    from agent_lanes.verify import verify
    sha = "a" * 40

    def run(cmd, **kw):
        sub = cmd[3]
        if sub == "ls-remote":
            return cp(0, f"{sha}\trefs/heads/lane/t_1\n")
        if sub == "log":
            return cp(0, "feat: x\nWIP(runner): 2 cambio(s) sin commitear\n")
        if sub == "rev-list":
            return cp(0, "2\n")
        if sub == "rev-parse":
            return cp(0, sha + "\n")
        return cp()

    res = verify(LANE, "t_1", "x", {"branch": "lane/t_1", "head_sha": sha}, runner=run)
    assert not res.ok and any("WIP(runner)" in r and "squash" in r for r in res.reasons)


def test_config_failure_blocks_without_retry_button():
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    bad = lambda lane, tid, cwd, result: VerifyResult(  # noqa: E731
        ok=False, reasons=["toca rutas vetadas en este carril: tools/x.py"], test_exit=None)
    n = FakeNotifier()
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=bad, notify=n)
    assert r.run_once() == {"t_1": "blocked:transient"}
    assert any("CONFIGURACIÓN" in c[3] for c in h.calls if c[0] == "block")
    assert any("Reintentar no sirve" in m for m in n.msgs)


def test_keyboard_config_has_only_park():
    rows = keyboard_spec("blocked", block_kind="config")
    assert [b["action"] for row in rows for b in row] == ["park", "to_integrador", "quitar_aviso"]  # Oscar no puede arreglar la config
    assert any(b["action"] == "retry" for row in keyboard_spec("blocked", block_kind="transient") for b in row)
