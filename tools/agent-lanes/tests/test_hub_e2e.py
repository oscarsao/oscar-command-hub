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
    assert calls[0][0] == "fetch" and calls[1][:2] == ["merge", "--no-edit"] and calls[1][-1] == "origin/master"


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
    assert [b["action"] for row in rows for b in row] == ["park"]
    assert any(b["action"] == "retry" for row in keyboard_spec("blocked", block_kind="transient") for b in row)
