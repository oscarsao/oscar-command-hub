"""State-machine tests for the lane runner (fakes only: no hermes, git, claude or network)."""
from __future__ import annotations

import threading
import time

import pytest

from agent_lanes.config import Lane
from agent_lanes.runner import LaneRunner
from agent_lanes.worker import WorkerOutcome
from agent_lanes.verify import VerifyResult

LANE = Lane(
    name="claude-oscarhq", board="oscarhq", repo="C:/repo", base="master", tool="claude",
    model="sonnet", effort="medium", max_budget_usd=3.0, max_parallel=1,
    role="roles/implementador.md", test_cmd="git diff --check origin/master...HEAD",
    max_runtime_seconds=7200, worktree_root="C:/wt", max_resumes=2, heartbeat_seconds=240,
)

GOOD = {
    "status": "done", "summary": "ok", "branch": "lane/t_1", "head_sha": "a" * 40,
    "changed_files": ["docs/x.md"], "tests": {"command": "true", "exit_code": 0},
    "questions": [], "next_steps": [], "risks": [],
}


class FakeHermes:
    def __init__(self, tasks=None, claim_ok=True):
        self.tasks = tasks or []
        self.claim_ok = claim_ok
        self.calls: list[tuple] = []
        self.heartbeats = 0
        self.feedback: list[str] = []
        self._lock = threading.Lock()

    def review_feedback(self, task_id):
        return list(self.feedback)

    def list_ready(self, assignee):
        self.calls.append(("list", assignee))
        return list(self.tasks)

    def claim(self, task_id, ttl):
        self.calls.append(("claim", task_id, ttl))
        ok, self.claim_ok = self.claim_ok, False  # second claim of the same task always loses
        return ok

    def heartbeat(self, task_id, note=None):
        with self._lock:
            self.heartbeats += 1
        return True

    def comment(self, task_id, text):
        self.calls.append(("comment", task_id, text))

    def block(self, task_id, kind, reason):
        self.calls.append(("block", task_id, kind, reason))
        return True

    def request_review(self, task_id, summary, metadata):
        self.calls.append(("review", task_id, summary, metadata))
        return True, ""

    def kinds(self):
        return [c[0] for c in self.calls]


class FakeGit:
    def __init__(self):
        self.prepared = []

    def prepare_worktree(self, lane, task_id):
        self.prepared.append(task_id)
        return f"{lane.worktree_root}/lane-{task_id}"


class FakeWorker:
    """Returns queued outcomes; optional delay simulates a long claude run."""

    def __init__(self, outcomes, delay=0.0):
        self.outcomes = list(outcomes)
        self.delay = delay
        self.runs: list[str] = []

    def run(self, lane, task, cwd, session_id, timeout):
        self.runs.append("run")
        time.sleep(self.delay)
        return self.outcomes.pop(0)

    def resume(self, lane, cwd, session_id, timeout, task_id):
        self.runs.append("resume")
        time.sleep(self.delay)
        return self.outcomes.pop(0)


class FakeVerifier:
    def __init__(self, ok=True):
        self.ok = ok

    def __call__(self, lane, task_id, cwd, result):
        return VerifyResult(ok=self.ok, reasons=[] if self.ok else ["remote sha mismatch"], test_exit=0)


class FakeNotifier:
    def __init__(self):
        self.msgs = []
        self.targets = []
        self.fallbacks = []

    def __call__(self, text, target=None, lane_target=None):
        self.msgs.append(text)
        self.targets.append(target)
        self.fallbacks.append(lane_target)


def ok_outcome(structured=None):
    return WorkerOutcome(ok=True, subtype="success", structured=structured or dict(GOOD), cost_usd=0.1, raw_error=None)


def cut_outcome(subtype="error_max_budget_usd"):
    return WorkerOutcome(ok=False, subtype=subtype, structured=None, cost_usd=0.5, raw_error=subtype)


def make(outcomes, *, hermes=None, verifier_ok=True, delay=0.0, hb=240):
    lane = LANE if hb == 240 else Lane(**{**LANE.__dict__, "heartbeat_seconds": hb})
    h = hermes or FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    n = FakeNotifier()
    w = FakeWorker(outcomes, delay=delay)
    r = LaneRunner(lane, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(verifier_ok), notify=n)
    return r, h, w, n


def test_happy_path_goes_to_review_with_metadata():
    r, h, w, n = make([ok_outcome()])
    assert r.run_once() == {"t_1": "review"}
    review = [c for c in h.calls if c[0] == "review"][0]
    assert review[3]["head_sha"] == "a" * 40
    assert review[3]["session_id"]
    assert any("session_id=" in c[2] for c in h.calls if c[0] == "comment")
    assert len(n.msgs) == 2  # start + review


def test_double_claim_second_runner_skips():
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    r1, *_ = make([ok_outcome()], hermes=h)
    r2, _, w2, _ = make([ok_outcome()], hermes=h)
    assert r1.run_once() == {"t_1": "review"}
    assert r2.run_once() == {"t_1": "claim_lost"}
    assert w2.runs == []


def test_heartbeat_runs_while_claude_is_slow():
    # 0.35 s run with a 0.05 s interval stands in for a >15 min run with a 240 s interval.
    r, h, w, _ = make([ok_outcome()], delay=0.35, hb=0.05)
    r.run_once()
    assert h.heartbeats >= 4


def test_resume_after_budget_cut_then_success():
    r, h, w, _ = make([cut_outcome(), ok_outcome()])
    assert r.run_once() == {"t_1": "review"}
    assert w.runs == ["run", "resume"]


def test_resume_limit_then_transient_block():
    r, h, w, _ = make([cut_outcome(), cut_outcome(), cut_outcome()])
    assert r.run_once() == {"t_1": "blocked:transient"}
    assert w.runs == ["run", "resume", "resume"]
    assert "review" not in h.kinds()


def test_failed_verification_blocks_transient_never_review():
    r, h, w, n = make([ok_outcome()], verifier_ok=False)
    assert r.run_once() == {"t_1": "blocked:transient"}
    block = [c for c in h.calls if c[0] == "block"][0]
    assert block[2] == "transient" and "remote sha mismatch" in block[3]
    assert "review" not in h.kinds()


def test_needs_input_blocks_with_questions():
    out = ok_outcome({**GOOD, "status": "needs_input", "questions": ["¿A o B? Recomiendo A"]})
    r, h, w, n = make([out])
    assert r.run_once() == {"t_1": "blocked:needs_input"}
    block = [c for c in h.calls if c[0] == "block"][0]
    assert block[2] == "needs_input" and "¿A o B?" in block[3]


def test_worker_failed_status_blocks_transient():
    out = ok_outcome({**GOOD, "status": "failed", "summary": "no compila"})
    r, h, *_ = make([out])
    assert r.run_once() == {"t_1": "blocked:transient"}


def test_worker_exception_blocks_transient_and_stops_heartbeat():
    class Boom(FakeWorker):
        def run(self, *a, **k):
            raise RuntimeError("claude crashed")

    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=Boom([]), verifier=FakeVerifier(), notify=FakeNotifier())
    assert r.run_once() == {"t_1": "blocked:transient"}
    before = h.heartbeats
    time.sleep(0.05)
    assert h.heartbeats == before


def test_max_parallel_limits_tasks_per_pass():
    h = FakeHermes(tasks=[{"id": "t_1", "title": "A", "body": ""}, {"id": "t_2", "title": "B", "body": ""}])
    r, *_ = make([ok_outcome()], hermes=h)
    assert list(r.run_once()) == ["t_1"]


def test_notifier_failure_does_not_break_flow():
    def bad(*_):
        raise OSError("telegram down")

    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(), notify=bad)
    assert r.run_once() == {"t_1": "review"}


# --- lease + reconciliation (approved by the Coordinator 2026-09-27) ---------------------------

def test_claim_ttl_is_max_runtime_plus_margin():
    r, h, *_ = make([ok_outcome()])
    r.run_once()
    claim = [c for c in h.calls if c[0] == "claim"][0]
    assert claim[2] == 7200 + 300


def test_max_runtime_reached_blocks_transient_without_resume():
    t = {"now": 0.0}

    class SlowWorker(FakeWorker):
        def run(self, *a, **k):
            t["now"] += 7200  # the claude call consumed the whole runtime budget
            return cut_outcome("runner_timeout")

    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    w = SlowWorker([])
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(), notify=FakeNotifier(),
                   clock=lambda: t["now"])
    assert r.run_once() == {"t_1": "blocked:transient"}
    assert w.runs == []  # SlowWorker.run doesn't record; no resume was attempted either
    assert "max_runtime" in [c for c in h.calls if c[0] == "block"][0][3]


class ReconHermes(FakeHermes):
    def __init__(self, running):
        super().__init__(tasks=[])
        self.running = running

    def list_status(self, assignee, status):
        return list(self.running) if status == "running" else []


def test_state_file_written_during_run_and_removed_after(tmp_path):
    seen = {}

    class Peek(FakeWorker):
        def run(self, lane, task, cwd, session_id, timeout):
            seen["files"] = [p.name for p in tmp_path.iterdir()]
            return ok_outcome()

    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=Peek([]), verifier=FakeVerifier(), notify=FakeNotifier(),
                   state_dir=tmp_path)
    assert r.run_once() == {"t_1": "review"}
    assert seen["files"] == ["t_1.json"]
    assert list(tmp_path.iterdir()) == []


def test_reconcile_blocks_orphans_and_skips_live(tmp_path):
    import json, os
    (tmp_path / "t_dead.json").write_text(json.dumps({"task_id": "t_dead", "lane": LANE.name, "pid": 999999}))
    (tmp_path / "t_live.json").write_text(json.dumps({"task_id": "t_live", "lane": LANE.name, "pid": os.getpid()}))
    h = ReconHermes(running=[{"id": "t_dead"}, {"id": "t_live"}, {"id": "t_nofile"}])
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([]), verifier=FakeVerifier(),
                   notify=FakeNotifier(), state_dir=tmp_path, pid_alive=lambda pid: pid == os.getpid())
    assert sorted(r.reconcile()) == ["t_dead", "t_nofile"]
    blocked = {c[1]: c for c in h.calls if c[0] == "block"}
    assert set(blocked) == {"t_dead", "t_nofile"}
    assert all(c[2] == "transient" and "runner reiniciado" in c[3] for c in blocked.values())
    assert not (tmp_path / "t_dead.json").exists() and (tmp_path / "t_live.json").exists()


def test_excluded_tasks_are_never_claimed():
    h = FakeHermes(tasks=[{"id": "t_skip", "title": "A", "body": ""}, {"id": "t_new", "title": "B", "body": ""}])
    w = FakeWorker([ok_outcome()])
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(), notify=FakeNotifier(),
                   exclude={"t_skip"})
    assert r.run_once() == {"t_new": "review"}
    assert all(c[1] != "t_skip" for c in h.calls if c[0] == "claim")
