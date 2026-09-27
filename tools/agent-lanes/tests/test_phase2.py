"""Fase 2 (W1b): Telegram origin routing, review lane, cleanup, scheduler, single instance, status."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time

import pytest

from agent_lanes.config import Lane
from agent_lanes.review import REVIEW_AUTHOR, ReviewRunner
from agent_lanes.runner import LaneRunner
from agent_lanes.service import Service, acquire_single_instance
from agent_lanes.status import lane_rows
from agent_lanes.telegram import telegram_target
from agent_lanes.verify import VerifyResult, verify
from agent_lanes.worker import WorkerOutcome, build_prompt
from tests.test_runner_state import (GOOD, LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker,
                                     ok_outcome)

REVIEW = Lane(name="review", kind="review", reviews=("claude-oscarhq",), model="sonnet", effort="medium",
              max_budget_usd=1.0, role="roles/revisor.md", max_runtime_seconds=1800, max_review_rounds=2)
META = {"lane": "claude-oscarhq", "branch": "lane/t_1", "head_sha": "a" * 40, "worktree": "C:/wt/lane-t_1"}
ORIGIN_BODY = "## Objetivo\nX\n\nOrigen-Telegram: chat=-100200 thread=7\n"


# --- Origen-Telegram contract with W3b ----------------------------------------------------------

@pytest.mark.parametrize("body,expected", [
    (ORIGIN_BODY, ("-100200", "7")),
    ("Origen-Telegram: chat=-1003530490339 thread=5", ("-1003530490339", "5")),
    ("  origen-telegram:  chat=123   thread=0  ", ("123", "0")),
    ("sin origen", None),
    ("Origen-Telegram: chat=abc thread=5", None),
    (None, None),
])
def test_telegram_target_parsing(body, expected):
    assert telegram_target(body) == expected


def test_notifications_go_to_origin_thread_when_present():
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": ORIGIN_BODY}])
    n = FakeNotifier()
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(), notify=n)
    assert r.run_once() == {"t_1": "review"}
    assert n.targets == [("-100200", "7"), ("-100200", "7")]


def test_notifications_default_target_without_origin():
    r_h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    n = FakeNotifier()
    LaneRunner(LANE, hermes=r_h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
               notify=n).run_once()
    assert n.targets == [None, None]


def test_telegram_notifier_uses_target_over_default(monkeypatch):
    from agent_lanes import telegram

    sent = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout):
        sent.append(json.loads(req.data))
        return Resp()

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)
    n = telegram.TelegramNotifier("tok", "-1", "5", allowed_chats={"-100200"})
    n("a")
    n("b", ("-100200", "7"))
    n("c", ("-100200", "0"))
    assert (sent[0]["chat_id"], sent[0]["message_thread_id"]) == ("-1", 5)
    assert (sent[1]["chat_id"], sent[1]["message_thread_id"]) == ("-100200", 7)
    assert sent[2]["chat_id"] == "-100200" and "message_thread_id" not in sent[2]  # thread 0 = General


# --- implementer gets reviewer feedback on a reopened task ---------------------------------------

def test_prompt_includes_review_feedback():
    p = build_prompt({"id": "t_1", "title": "T", "body": "B", "review_feedback": ["CAMBIOS: falta test"]}, LANE)
    assert "falta test" in p and "revisión" in p.lower()


def test_runner_passes_feedback_from_hermes_to_worker():
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    h.feedback = ["CAMBIOS (ronda 1): renombra x"]
    seen = {}

    class Spy(FakeWorker):
        def run(self, lane, task, cwd, session_id, timeout):
            seen.update(task)
            return ok_outcome()

    LaneRunner(LANE, hermes=h, git=FakeGit(), worker=Spy([]), verifier=FakeVerifier(), notify=FakeNotifier()).run_once()
    assert seen["review_feedback"] == ["CAMBIOS (ronda 1): renombra x"]


# --- review lane ----------------------------------------------------------------------------

class ReviewHermes:
    def __init__(self, tasks, comments=None, meta=META):
        self.tasks = tasks
        self.comments = comments or []
        self.meta = meta
        self.calls = []

    def list_status(self, assignee, status):
        return [t for t in self.tasks if t["assignee"] == assignee and t.get("status", "review") == status]

    def show(self, tid):
        runs = [{"outcome": "review_requested", "metadata": self.meta}, {"outcome": None, "metadata": {}}]
        return {"task": next(t for t in self.tasks if t["id"] == tid), "comments": self.comments, "runs": runs,
                "events": getattr(self, "show_events", [])}

    def complete(self, tid, result, metadata):
        self.calls.append(("complete", tid, result, metadata))
        return True, ""

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text, author))
        return True

    def reopen_review(self, tid):
        self.calls.append(("reopen", tid))
        return True

    def block(self, tid, kind, reason):
        self.calls.append(("block", tid, kind, reason))
        return True

    def kinds(self):
        return [c[0] for c in self.calls]


class FakeReviewGit(FakeGit):
    def __init__(self):
        super().__init__()
        self.cleaned = []

    def prepare_review_worktree(self, lane, tid):
        return f"{lane.worktree_root}/lane-{tid}"

    def cleanup(self, lane, tid):
        self.cleaned.append(tid)
        return True, "ok"


class FakeReviewer:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def run(self, review_lane, lane, task, meta, cwd, session_id, timeout):
        self.calls += 1
        return self.outcomes.pop(0)

    def resume(self, review_lane, lane, task, cwd, session_id, timeout):
        self.calls += 1
        return self.outcomes.pop(0)


def verdict(status, changes=()):
    return WorkerOutcome(True, "success", {"status": status, "summary": f"veredicto {status}", "findings": [],
                                            "required_changes": list(changes)}, 0.2, None)


def make_review(outcomes, *, comments=None, verifier_ok=True, body="B", meta=META):
    h = ReviewHermes([{"id": "t_1", "title": "T", "body": body, "assignee": "claude-oscarhq", "status": "review"}],
                     comments=comments, meta=meta)
    g = FakeReviewGit()
    n = FakeNotifier()
    rv = FakeReviewer(outcomes)
    r = ReviewRunner(REVIEW, {"claude-oscarhq": LANE}, hermes_for=lambda board: h, git=g, reviewer=rv,
                     verifier=FakeVerifier(verifier_ok), notify=n)
    return r, h, g, n, rv


def test_review_approve_completes_and_cleans_up():
    r, h, g, n, _ = make_review([verdict("approve")])
    assert r.run_once() == {"t_1": "done"}
    done = [c for c in h.calls if c[0] == "complete"][0]
    assert done[3]["review"]["status"] == "approve" and done[3]["head_sha"] == "a" * 40
    assert g.cleaned == ["t_1"]
    assert "reopen" not in h.kinds()
    assert len(n.msgs) == 1


def test_review_request_changes_comments_and_reopens():
    r, h, g, n, _ = make_review([verdict("request_changes", ["añade test de x"])])
    assert r.run_once() == {"t_1": "changes"}
    comment = [c for c in h.calls if c[0] == "comment"][0]
    assert comment[3] == REVIEW_AUTHOR and "añade test de x" in comment[2] and "ronda 1" in comment[2]
    assert ("reopen", "t_1") in h.calls
    assert "complete" not in h.kinds() and g.cleaned == []


def test_review_rounds_exhausted_escalates_to_needs_input():
    r, h, g, n, _ = make_review([verdict("request_changes", ["c"])])
    h.show_events = [{"kind": "review_reopened", "created_at": 1}, {"kind": "review_reopened", "created_at": 2}]
    assert r.run_once() == {"t_1": "blocked:needs_input"}
    assert "reopen" not in h.kinds()


def test_review_mechanical_verification_failure_blocks_without_llm():
    r, h, g, n, rv = make_review([verdict("approve")], verifier_ok=False)
    assert r.run_once() == {"t_1": "blocked:transient"}
    assert rv.calls == 0 and "complete" not in h.kinds()


def test_review_without_implementation_metadata_blocks():
    r, h, *_ = make_review([verdict("approve")], meta={})
    assert r.run_once() == {"t_1": "blocked:transient"}


def test_review_reviewer_failure_blocks_transient():
    cut = WorkerOutcome(False, "error_max_budget_usd", None, 1.0, "budget")
    r, h, *_ = make_review([cut, cut, cut])
    assert r.run_once() == {"t_1": "blocked:transient"}


def test_review_notifies_origin_thread():
    r, h, g, n, _ = make_review([verdict("approve")], body=ORIGIN_BODY)
    r.run_once()
    assert n.targets == [("-100200", "7")]


def test_review_ignores_tasks_of_unknown_lanes():
    h = ReviewHermes([{"id": "t_9", "title": "T", "body": "", "assignee": "someone-else", "status": "review"}])
    r = ReviewRunner(REVIEW, {"claude-oscarhq": LANE}, hermes_for=lambda b: h, git=FakeReviewGit(),
                     reviewer=FakeReviewer([]), verifier=FakeVerifier(), notify=FakeNotifier())
    assert r.run_once() == {}


# --- forbidden paths + test_cmd placeholders in verification ---------------------------------------

class GitRun:
    def __init__(self, names="docs/a.md\n"):
        self.names = names
        self.shell_cmds = []

    def __call__(self, args, **kw):
        if isinstance(args, str):
            self.shell_cmds.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")
        sub = args[3]
        out = {"ls-remote": "a" * 40 + "\trefs/heads/lane/t_1\n", "rev-list": "1\n", "rev-parse": "a" * 40 + "\n",
               "diff": self.names}.get(sub, "")
        return subprocess.CompletedProcess(args, 0, out, "")


def test_verify_rejects_forbidden_paths():
    lane = Lane(**{**LANE.__dict__, "forbidden_paths": ("backend/alembic/versions/",)})
    r = verify(lane, "t_1", "C:/wt", GOOD, runner=GitRun("docs/a.md\nbackend/alembic/versions/0001_x.py\n"))
    assert not r.ok and any("backend/alembic/versions/0001_x.py" in x for x in r.reasons)


def test_verify_renders_test_cmd_placeholders():
    lane = Lane(**{**LANE.__dict__, "test_cmd": 'py "{AGENT_LANES_DIR}/agent_lanes/checks.py" {BASE}'})
    g = GitRun()
    assert verify(lane, "t_1", "C:/wt", GOOD, runner=g).ok
    assert "{" not in g.shell_cmds[0] and "origin/master" in g.shell_cmds[0] and "agent_lanes/checks.py" in g.shell_cmds[0]


# --- scheduler: global worker cap, per-lane cap, review lane in the same pass ---------------------------

class SlowLane:
    def __init__(self, name, n_tasks, tracker):
        self.name = name
        self.n = n_tasks
        self.tracker = tracker

    def jobs(self):
        return [(f"{self.name}-{i}", self.work) for i in range(self.n)]

    def work(self):
        with self.tracker["lock"]:
            self.tracker["now"] += 1
            self.tracker["peak"] = max(self.tracker["peak"], self.tracker["now"])
        time.sleep(0.05)
        with self.tracker["lock"]:
            self.tracker["now"] -= 1
        return "review"


@pytest.mark.parametrize("max_workers,expected_peak", [(1, 1), (2, 2)])
def test_service_caps_concurrent_workers(max_workers, expected_peak):
    tr = {"now": 0, "peak": 0, "lock": threading.Lock()}
    svc = Service([SlowLane("a", 2, tr), SlowLane("b", 2, tr)], max_workers=max_workers)
    results = svc.run_pass()
    assert len(results) == 4 and tr["peak"] == expected_peak


def test_single_instance_lock(tmp_path):
    lock = tmp_path / "runner.lock"
    assert acquire_single_instance(lock, pid_alive=lambda p: False)
    assert json.loads(lock.read_text())["pid"] == os.getpid()
    lock.write_text(json.dumps({"pid": 999999}))                            # another runner owns it
    assert not acquire_single_instance(lock, pid_alive=lambda p: True)       # live owner -> refuse
    assert acquire_single_instance(lock, pid_alive=lambda p: False)          # stale owner -> take over
    assert json.loads(lock.read_text())["pid"] == os.getpid()


# --- lanes status -----------------------------------------------------------------------------

class StatusHermes:
    def __init__(self, by_status):
        self.by_status = by_status

    def list_status(self, assignee, status, sort=None):
        return self.by_status.get(status, [])


def test_status_rows(tmp_path):
    (tmp_path / "t_run.json").write_text(json.dumps({"task_id": "t_run", "lane": LANE.name, "pid": 4242}))
    h = StatusHermes({"running": [{"id": "t_run", "title": "R"}], "ready": [{"id": "a"}, {"id": "b"}],
                      "review": [{"id": "c"}], "done": [{"id": "t_old", "title": "Vieja", "completed_at": 100}]})
    rows = lane_rows({LANE.name: LANE}, hermes_for=lambda b: h, state_dir=tmp_path, pid_alive=lambda p: p == 4242)
    row = rows[0]
    assert row["lane"] == LANE.name and row["state"] == "ocupado" and row["running"] == ["t_run"]
    assert row["ready"] == 2 and row["review"] == 1 and row["last"].startswith("t_old")
    rows = lane_rows({LANE.name: LANE}, hermes_for=lambda b: h, state_dir=tmp_path, pid_alive=lambda p: False)
    assert rows[0]["state"] == "huérfano"
    rows = lane_rows({LANE.name: LANE}, hermes_for=lambda b: StatusHermes({}), state_dir=tmp_path,
                     pid_alive=lambda p: False)
    assert rows[0]["state"] == "libre"


# --- cleanup of done tasks -------------------------------------------------------------------------

def test_sweep_cleans_only_done_tasks_with_worktree(tmp_path):
    from agent_lanes.review import sweep_done

    (tmp_path / "lane-t_done").mkdir()
    lane = Lane(**{**LANE.__dict__, "worktree_root": str(tmp_path)})
    h = StatusHermes({"done": [{"id": "t_done"}, {"id": "t_gone"}]})
    g = FakeReviewGit()
    assert sweep_done(lane, h, g) == ["t_done"]
    assert g.cleaned == ["t_done"]


# --- fixes from code review of 87a83b4 -----------------------------------------------------------

def test_forbidden_paths_short_circuit_before_test_cmd_and_ignore_renames():
    lane = Lane(**{**LANE.__dict__, "forbidden_paths": ("backend/alembic/versions/",)})
    g = GitRun("backend/alembic/versions/0001_x.py\n")
    r = verify(lane, "t_1", "C:/wt", GOOD, runner=g)
    assert not r.ok and g.shell_cmds == []  # worker-controlled test_cmd never runs on a vetoed diff


def test_forbidden_diff_uses_no_renames():
    seen = []

    class Rec(GitRun):
        def __call__(self, args, **kw):
            if not isinstance(args, str) and args[3] == "diff":
                seen.append(args)
            return super().__call__(args, **kw)

    lane = Lane(**{**LANE.__dict__, "forbidden_paths": ("x/",)})
    verify(lane, "t_1", "C:/wt", GOOD, runner=Rec())
    assert "--no-renames" in seen[0]


class EventHermes(ReviewHermes):
    def __init__(self, events, comments, comment_ok=True, reopen_ok=True):
        super().__init__([{"id": "t_1", "title": "T", "body": "B", "assignee": "claude-oscarhq", "status": "review"}],
                         comments=comments)
        self.events = events
        self.comment_ok = comment_ok
        self.reopen_ok = reopen_ok

    def show(self, tid):
        d = super().show(tid)
        d["events"] = self.events
        return d

    def comment(self, tid, text, author=None):
        super().comment(tid, text, author)
        return self.comment_ok

    def reopen_review(self, tid):
        self.calls.append(("reopen", tid))
        return self.reopen_ok


def review_with(h, outcomes):
    return ReviewRunner(REVIEW, {"claude-oscarhq": LANE}, hermes_for=lambda b: h, git=FakeReviewGit(),
                        reviewer=FakeReviewer(outcomes), verifier=FakeVerifier(), notify=FakeNotifier())


def test_rounds_counted_from_reopen_events_not_comments():
    ev = [{"kind": "review_requested", "created_at": 10}, {"kind": "review_reopened", "created_at": 20},
          {"kind": "review_requested", "created_at": 30}, {"kind": "review_reopened", "created_at": 40},
          {"kind": "review_requested", "created_at": 50}]
    h = EventHermes(ev, comments=[])
    assert review_with(h, [verdict("request_changes", ["x"])]).run_once() == {"t_1": "blocked:needs_input"}


def test_pending_round_comment_is_not_reposted_when_only_reopen_failed_before():
    ev = [{"kind": "review_requested", "created_at": 10}]
    pending = [{"author": REVIEW_AUTHOR, "body": "CAMBIOS (ronda 1) pedidos por revisión:\n- x", "created_at": 15}]
    h = EventHermes(ev, comments=pending)
    assert review_with(h, [verdict("request_changes", ["x"])]).run_once() == {"t_1": "changes"}
    assert "comment" not in h.kinds() and ("reopen", "t_1") in h.calls


def test_comment_failure_blocks_without_reopen():
    h = EventHermes([{"kind": "review_requested", "created_at": 10}], comments=[], comment_ok=False)
    assert review_with(h, [verdict("request_changes", ["x"])]).run_once() == {"t_1": "blocked:transient"}
    assert "reopen" not in h.kinds()


def test_reopen_failure_blocks_transient():
    h = EventHermes([{"kind": "review_requested", "created_at": 10}], comments=[], reopen_ok=False)
    assert review_with(h, [verdict("request_changes", ["x"])]).run_once() == {"t_1": "blocked:transient"}


def test_telegram_origin_outside_allowlist_falls_back_to_default(monkeypatch):
    from agent_lanes import telegram

    sent = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda req, timeout: (sent.append(json.loads(req.data)), Resp())[1])
    n = telegram.TelegramNotifier("tok", "-1", "5", allowed_chats={"-100200"})
    n("a", ("-100200", "7"))
    n("b", ("-999", "3"))  # not allowed -> default destination
    assert sent[0]["chat_id"] == "-100200"
    assert (sent[1]["chat_id"], sent[1]["message_thread_id"]) == ("-1", 5)


# --- Destino Telegram por carril (27-09): origen de marca > destino del carril > .env ------------

GESTION = "-1003530490339"
GENERIC = {("6744452215", "*"), (GESTION, "5")}
ALLOWED = {GESTION, "6744452215", "-100200"}


@pytest.mark.parametrize("origin,fallback,expected", [
    ((GESTION, "230"), (GESTION, "231"), (GESTION, "230")),      # origen de marca permitido gana al carril
    (("-100200", "7"), None, ("-100200", "7")),                  # sin destino de carril: comportamiento previo
    (("6744452215", "0"), (GESTION, "230"), (GESTION, "230")),   # DM de Oscar -> tema de la marca
    (("6744452215", "12"), (GESTION, "230"), (GESTION, "230")),  # DM en cualquier hilo
    ((GESTION, "5"), (GESTION, "231"), (GESTION, "231")),        # Gestión · General -> tema de la marca
    (None, (GESTION, "5"), (GESTION, "5")),                      # sin origen -> carril
    (("-999", "3"), (GESTION, "230"), (GESTION, "230")),         # origen no permitido -> carril
    (("-999", "3"), None, None),                                 # ... y sin carril -> .env
    (("6744452215", "0"), None, None),                           # genérico sin carril -> .env
    (None, None, None),
])
def test_resolve_target_priority(origin, fallback, expected):
    from agent_lanes.telegram import resolve_target
    assert resolve_target(origin, fallback, allowed_chats=ALLOWED, generic_origins=GENERIC) == expected


def _capture(monkeypatch):
    from agent_lanes import telegram
    sent = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    monkeypatch.setattr(telegram.urllib.request, "urlopen", lambda req, timeout: (sent.append(json.loads(req.data)), Resp())[1])
    return telegram, sent


def test_notifier_routes_dm_origin_to_lane_thread(monkeypatch):
    telegram, sent = _capture(monkeypatch)
    n = telegram.TelegramNotifier("tok", GESTION, "5", allowed_chats={"6744452215"}, generic_origins=GENERIC)
    n("a", ("6744452215", "0"), (GESTION, "230"))
    n("b", None, None)
    n("c", (GESTION, "231"), (GESTION, "230"))
    assert (sent[0]["chat_id"], sent[0]["message_thread_id"]) == (GESTION, 230)
    assert (sent[1]["chat_id"], sent[1]["message_thread_id"]) == (GESTION, 5)
    assert (sent[2]["chat_id"], sent[2]["message_thread_id"]) == (GESTION, 231)


def test_notifier_lane_destination_is_trusted_even_if_not_allowlisted(monkeypatch):
    telegram, sent = _capture(monkeypatch)
    n = telegram.TelegramNotifier("tok", "-1", "5", allowed_chats=set(), generic_origins=GENERIC)
    n("a", None, ("-555", "9"))
    assert (sent[0]["chat_id"], sent[0]["message_thread_id"]) == ("-555", 9)


def test_lane_runner_passes_lane_destination():
    lane = Lane(name="claude-oscarhq", board="oscarhq", repo="C:/x", base="master", telegram=(GESTION, "231"))
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    n = FakeNotifier()
    LaneRunner(lane, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
               notify=n).run_once()
    assert n.fallbacks == [(GESTION, "231"), (GESTION, "231")]


def test_review_uses_destination_of_reviewed_lane():
    lane = Lane(name="claude-migrateam", board="migrateam", repo="C:/x", base="master", telegram=(GESTION, "230"))
    h = ReviewHermes([{"id": "t_1", "title": "T", "body": "Origen-Telegram: chat=6744452215 thread=0",
                       "assignee": "claude-migrateam", "status": "review"}], meta=META)
    n = FakeNotifier()
    review = Lane(name="review", kind="review", reviews=("claude-migrateam",), role="roles/revisor.md",
                  telegram=(GESTION, "5"))
    ReviewRunner(review, {"claude-migrateam": lane}, hermes_for=lambda b: h, git=FakeReviewGit(),
                 reviewer=FakeReviewer([verdict("approve")]), verifier=FakeVerifier(), notify=n).run_once()
    assert n.targets == [("6744452215", "0")] and n.fallbacks == [(GESTION, "230")]


def test_load_lanes_and_telegram_settings(tmp_path):
    from agent_lanes.config import load_lanes, load_telegram_settings
    p = tmp_path / "lanes.yaml"
    p.write_text(
        "telegram:\n  generic_origins:\n    - {chat: 6744452215}\n    - {chat: -1003530490339, thread: 5}\n"
        "lanes:\n  a:\n    telegram: {chat: -1003530490339, thread: 230}\n  b: {}\n", encoding="utf-8")
    lanes = load_lanes(p)
    assert lanes["a"].telegram == (GESTION, "230") and lanes["b"].telegram is None
    assert load_telegram_settings(p)["generic_origins"] == {("6744452215", "*"), (GESTION, "5")}


def test_real_lanes_yaml_destinations():
    from agent_lanes.config import load_lanes
    lanes = load_lanes()
    assert lanes["claude-migrateam"].telegram == (GESTION, "230")
    assert lanes["claude-oscarhq"].telegram == lanes["claude-scraper"].telegram == (GESTION, "231")
    assert lanes["claude-nextjobs"].telegram == (GESTION, "5")
