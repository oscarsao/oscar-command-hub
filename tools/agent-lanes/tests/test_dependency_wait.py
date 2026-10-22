"""«Esperar a…» (t_8125ad1d): dependencia real que el runner respeta. Todo con dobles: ni Hermes, ni Telegram, ni git."""
from __future__ import annotations

import subprocess
from pathlib import Path

from agent_lanes import deps
from agent_lanes.hermes import HermesCLI
from agent_lanes.runner import LaneRunner
from tests.test_commands import OSCAR, center, command, press, texts
from tests.test_plan_d_runtime import TriageHermes, stuck
from tests.test_runner_state import LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, ok_outcome

CHILD, PARENT = "t_eeeeeee5", "t_abcdef01"


def card_of(tid, status, branch=None, comments=()):
    return {"task": {"id": tid, "title": f"Tarea {tid}", "status": status, "branch_name": branch,
                     "assignee": "claude-migrateam"}, "comments": list(comments), "events": [], "parents": [], "children": []}


def wait_comment(parent=PARENT):
    return {"author": "oscar-telegram", "body": deps.wait_comment(parent)}


# --- deps -----------------------------------------------------------------------------------------------------

def test_declared_waits_reads_only_espera_a_comments():
    show = {"comments": [{"body": "otra cosa t_11111111"}, wait_comment(), wait_comment()]}
    assert deps.declared_waits(show) == [PARENT] and deps.declared_waits({}) == []


def test_waiting_on_depends_on_parent_state():
    shows = {CHILD: {"comments": [wait_comment()]}, PARENT: card_of(PARENT, "running")}
    h = TriageHermes(shows)
    assert [w["id"] for w in deps.waiting_on(h, shows[CHILD])] == [PARENT]
    shows[PARENT]["task"]["status"] = "done"  # hecho y con rama sin INTEGRADO: sigue bloqueando
    shows[PARENT]["task"]["branch_name"] = "lane/x"
    assert deps.waiting_on(h, shows[CHILD])
    shows[PARENT]["comments"] = [{"author": deps.INTEGRATOR_AUTHOR, "body": "INTEGRADO abc · PR #1"}]  # PR fusionado
    assert deps.waiting_on(h, shows[CHILD]) == []
    shows[PARENT]["comments"] = []
    shows[PARENT]["task"]["branch_name"] = None  # sin rama: basta con done
    assert deps.waiting_on(h, shows[CHILD]) == []


def test_resolution_is_announced_once():
    assert deps.resolution_unannounced({"comments": [wait_comment()]})
    assert not deps.resolution_unannounced({"comments": [wait_comment(), {"body": f"{deps.WAIT_RESOLVED_PREFIX} x"}]})
    assert not deps.resolution_unannounced({"comments": []})


# --- runner ---------------------------------------------------------------------------------------------------

class ShowHermes(FakeHermes):
    def __init__(self, shows, tasks):
        super().__init__(tasks=tasks)
        self.shows = shows

    def show(self, tid):
        if tid not in self.shows:
            raise RuntimeError("no existe")
        return self.shows[tid]

    def comment(self, task_id, text, author=None):
        self.calls.append(("comment", task_id, text))
        self.shows[task_id].setdefault("comments", []).append({"author": author or "agent-lanes", "body": text})
        return True


def runner_with(h):
    return LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                      notify=FakeNotifier())


def test_runner_does_not_claim_while_dependency_pending_then_releases_with_comment():
    shows = {CHILD: card_of(CHILD, "ready", comments=[wait_comment()]), PARENT: card_of(PARENT, "review", "lane/p")}
    h = ShowHermes(shows, [{"id": CHILD, "title": "T", "body": "B"}])
    r = runner_with(h)
    assert r.jobs() == [] and r.jobs() == []
    assert not [c for c in h.calls if c[0] == "claim"]
    shows[PARENT]["task"]["status"] = "done"  # hecho pero sin fusionar: sigue esperando
    assert r.jobs() == []
    shows[PARENT]["comments"] = [{"author": deps.INTEGRATOR_AUTHOR, "body": "INTEGRADO abc · PR #1"}]
    assert [tid for tid, _ in r.jobs()] == [CHILD]
    assert [c for c in h.calls if c[0] == "comment" and deps.WAIT_RESOLVED_PREFIX in c[2]] != []
    r.jobs()  # una segunda pasada no repite el comentario
    assert len([c for c in h.calls if c[0] == "comment"]) == 1


def test_ready_task_without_wait_is_untouched_and_unreadable_card_is_not_held():
    h = ShowHermes({}, [{"id": CHILD, "title": "T", "body": "B"}])  # show falla: no se retiene
    assert [tid for tid, _ in runner_with(h).jobs()] == [CHILD]
    h = ShowHermes({CHILD: card_of(CHILD, "ready")}, [{"id": CHILD, "title": "T", "body": "B"}])
    assert [tid for tid, _ in runner_with(h).jobs()] == [CHILD] and not [c for c in h.calls if c[0] == "comment"]


# --- botón ⏸ Esperar a… ---------------------------------------------------------------------------------------

class LinkHermes(TriageHermes):
    def link(self, parent, child):
        self.calls.append(("link", parent, child))
        self.shows[child]["parents"] = [parent]
        return True

    def requeue_triage(self, tid, author=None):
        self.calls.append(("requeue_triage", tid))
        self.shows[tid]["task"]["status"] = "todo"  # el padre no está hecho: Hermes la deja en todo
        return "todo"


def wait_center(tmp_path, hermes_cls=LinkHermes):
    cc, desk, bot, h, messages = center(tmp_path, {})
    th = hermes_cls({CHILD: stuck(CHILD), PARENT: card_of(PARENT, "running")})
    for obj in (cc, desk, cc.renotifier()):
        obj.hermes_for = lambda b: th
    return cc, desk, bot, th


def ask_wait(desk, bot, text):
    command(desk, "/decisiones")
    notice = next(m for m in bot.sent if m["text"].startswith(f"🧊 {CHILD}"))
    assert texts(notice["markup"])[1] == "⏸ Esperar a…"
    press(desk, notice, 1)
    prompt = bot.sent[-1]
    assert "t_xxxxxxxx" in prompt["text"]
    desk.handle_update({"update_id": 5, "message": {"message_id": 900, "from": {"id": OSCAR}, "chat": {"id": OSCAR},
                                                    "text": text, "reply_to_message": {"message_id": prompt["message_id"]}}})
    return notice


def test_esperar_a_links_comments_and_requeues_without_answering_the_question(tmp_path):
    cc, desk, bot, h = wait_center(tmp_path)
    ask_wait(desk, bot, f"espera a {PARENT} por favor")
    assert ("link", PARENT, CHILD) in h.calls and ("requeue_triage", CHILD) in h.calls
    wait = [c for c in h.calls if c[0] == "comment"]
    assert len(wait) == 1 and wait[0][2].startswith(f"{deps.WAIT_PREFIX} {PARENT}")
    assert h.shows[CHILD]["task"]["status"] == "todo"
    assert "esperando a " + PARENT in bot.edits[-1]["text"]


def test_esperar_a_rejects_bad_ids_and_keeps_the_buttons(tmp_path):
    for text in ("no sé", CHILD, "t_deadbeef"):  # sin id, la propia tarjeta, tarjeta inexistente
        cc, desk, bot, h = wait_center(tmp_path)
        ask_wait(desk, bot, text)
        assert not [c for c in h.calls if c[0] in ("link", "requeue_triage")]
        assert any("no se pudo esperar" in e["text"] for e in bot.edits)
        assert h.shows[CHILD]["task"]["status"] == "triage"


# --- CLI ------------------------------------------------------------------------------------------------------

def test_hermes_link_passes_ids_by_argv_and_rejects_bad_ids():
    calls = []

    def run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")
    h = HermesCLI("default", exe=Path("hermes.exe"), runner=run)
    assert h.link(PARENT, CHILD) and calls == [["hermes.exe", "kanban", "--board", "default", "link", PARENT, CHILD]]
    assert not h.link("--evil", CHILD) and not h.link(PARENT, "x") and len(calls) == 1
