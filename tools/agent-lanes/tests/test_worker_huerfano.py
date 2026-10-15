"""t_bdee05fe: PID del `claude` en el estado de la tarea; el reinicio mata al huérfano y nunca se lanza un segundo."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent_lanes import proc
from agent_lanes.runner import LaneRunner
from agent_lanes.worker import ClaudeWorker, record_worker_pid
from .test_runner_state import (LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, ReconHermes,
                                ok_outcome)

TASK = {"id": "t_1", "title": "A", "body": ""}


def _runner(tmp_path, hermes, alive, killed, created=None):
    return LaneRunner(LANE, hermes=hermes, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                      notify=FakeNotifier(), state_dir=tmp_path, pid_alive=lambda pid: pid in alive,
                      kill=killed.append, created_at=lambda pid: created)


def test_proc_run_on_start_gives_pid_and_keeps_run_contract():
    pids = []
    cp = proc.run([sys.executable, "-c", "import sys;print(sys.stdin.read().upper())"], input="hola",
                  capture_output=True, text=True, timeout=60, on_start=pids.append)
    assert cp.returncode == 0 and cp.stdout.strip() == "HOLA" and len(pids) == 1 and pids[0] > 0


def test_proc_run_on_start_timeout_raises():
    with pytest.raises(subprocess.TimeoutExpired):
        proc.run([sys.executable, "-c", "import time;time.sleep(30)"], capture_output=True, text=True, timeout=1,
                 on_start=lambda pid: None)


def test_worker_records_pid_in_task_state(tmp_path):
    (tmp_path / "t_9.json").write_text(json.dumps({"task_id": "t_9", "pid": 1}))

    def fake_run(args, on_start=None, **kw):
        on_start(4242)
        return subprocess.CompletedProcess(args, 0, json.dumps({"subtype": "success", "is_error": False,
                                                                "structured_output": ok_outcome().structured}), "")

    w = ClaudeWorker(runner=fake_run, pid_dir=tmp_path)
    w.run(LANE, {"id": "t_9", "title": "T", "body": ""}, "C:/wt", "sid", timeout=60)
    state = json.loads((tmp_path / "t_9.json").read_text())
    assert state["worker_pid"] == 4242 and state["pid"] == 1


def test_record_worker_pid_without_state_file_is_noop(tmp_path):
    record_worker_pid(tmp_path / "nada.json", 5)
    assert not (tmp_path / "nada.json").exists()


def test_reconcile_kills_orphan_worker_then_blocks(tmp_path):
    (tmp_path / "t_1.json").write_text(json.dumps({"pid": 999999, "worker_pid": 777}))
    h, killed = ReconHermes(running=[{"id": "t_1"}]), []
    r = _runner(tmp_path, h, alive={os.getpid(), 777}, killed=killed)
    assert r.reconcile() == ["t_1"] and killed == [777]


def test_reconcile_does_not_kill_reused_pid(tmp_path):
    (tmp_path / "t_1.json").write_text(json.dumps({"pid": 999999, "worker_pid": 777, "worker_created": 1000.0}))
    h, killed = ReconHermes(running=[{"id": "t_1"}]), []
    r = _runner(tmp_path, h, alive={777}, killed=killed, created=5000.0)  # otro proceso con el mismo PID
    assert r.reconcile() == ["t_1"] and killed == []


def test_process_does_not_claim_when_worker_still_alive(tmp_path):
    (tmp_path / "t_1.json").write_text(json.dumps({"pid": 999999, "worker_pid": 777}))
    h = FakeHermes(tasks=[TASK])
    r = _runner(tmp_path, h, alive={777}, killed=[])
    assert r.process(TASK) == "worker_vivo"
    assert "claim" not in h.kinds() and r.worker.runs == []
    assert (tmp_path / "t_1.json").exists()  # el estado se conserva: sigue sabiéndose qué worker es


def test_process_runs_normally_when_worker_dead(tmp_path):
    (tmp_path / "t_1.json").write_text(json.dumps({"pid": 999999, "worker_pid": 777}))
    h = FakeHermes(tasks=[TASK])
    r = _runner(tmp_path, h, alive=set(), killed=[])
    assert r.process(TASK) == "review"
