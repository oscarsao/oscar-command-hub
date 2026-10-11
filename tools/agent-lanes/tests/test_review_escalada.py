"""Escalada de review sobre una tarea en `review` (h.block solo mueve running/ready): una sola alerta, con botones."""
from __future__ import annotations

from agent_lanes.decisions import keyboard_spec
from agent_lanes.review import ReviewRunner
from tests.test_phase2 import LANE, REVIEW, FakeReviewGit, FakeVerifier, ReviewHermes, META, verdict
from tests.test_integrator import FakeTg


class FailingBlockHermes(ReviewHermes):
    def block(self, tid, kind, reason):
        self.calls.append(("block", tid, kind, reason))
        return False


class SpyDecisions:
    def __init__(self):
        self.markups = []

    def markup(self, state, **kw):
        self.markups.append((state, kw.get("block_kind")))
        return {"inline_keyboard": [[{"text": "x", "callback_data": "t:0"}]]}

    owner_id = "1"


def build(tmp_path, state_dir=True):
    h = FailingBlockHermes([{"id": "t_1", "title": "T", "body": "B", "assignee": "claude-oscarhq", "status": "review"}])
    h.show_events = [{"kind": "review_reopened", "created_at": 1}, {"kind": "review_reopened", "created_at": 2}]
    n, d = FakeTg(), SpyDecisions()

    class Rv:
        def run(self, *a, **k):
            return verdict("request_changes", ["c"])

        resume = run

    r = ReviewRunner(REVIEW, {"claude-oscarhq": LANE}, hermes_for=lambda b: h, git=FakeReviewGit(), reviewer=Rv(),
                     verifier=FakeVerifier(True), notify=n, decisions=d,
                     state_dir=(tmp_path / "rb") if state_dir else None)
    return r, h, n, d


def test_escalation_with_failed_block_alerts_once_with_buttons(tmp_path):
    r, h, n, d = build(tmp_path)
    assert r.run_once() == {"t_1": "blocked:needs_input"}
    assert d.markups == [("blocked", "review_stuck")]
    assert len(n.sent) == 1
    for _ in range(5):
        assert r.run_once() == {"t_1": "blocked:already_escalated"}
    assert len(n.sent) == 1 and len([c for c in h.calls if c[0] == "block"]) == 1


def test_state_survives_restart_and_new_head_reprocesses(tmp_path):
    r, h, n, d = build(tmp_path)
    r.run_once()
    r2, h2, n2, _ = build(tmp_path)  # runner nuevo (reinicio) con el mismo .state
    assert r2.run_once() == {"t_1": "blocked:already_escalated"} and n2.sent == []
    h2.meta = {**META, "head_sha": "b" * 40}  # el implementador empujó otra cabeza: se revisa de nuevo
    assert r2.run_once() == {"t_1": "blocked:needs_input"}


def test_review_stuck_keyboard_offers_approve_changes_park():
    spec = keyboard_spec("blocked", block_kind="review_stuck")
    assert [b["action"] for b in spec[0]] == ["approve", "changes", "park"]
