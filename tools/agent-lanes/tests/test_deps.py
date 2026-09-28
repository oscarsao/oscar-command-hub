"""Dependencias lógicas entre tareas: '¿qué pasa si apruebo el #10 sin haber aprobado el #9?' (Oscar, 28-09)."""
from agent_lanes.deps import dependency_order, pending_parents, waiting_line

from tests.test_integrator import GH, buttons, make, press


class ShowOnly:
    def __init__(self, shows):
        self.shows = shows

    def show(self, tid):
        if tid not in self.shows:
            raise RuntimeError("no such task")
        return self.shows[tid]


def task(tid, status="done", branch=True, comments=()):
    return {"task": {"id": tid, "title": f"Tarea {tid}", "status": status,
                     "branch_name": f"lane/{tid}" if branch else None},
            "comments": list(comments), "parents": []}


INTEGRATED = {"author": "lane-integrator", "body": "INTEGRADO abc123"}


def test_parent_with_code_not_integrated_blocks():
    h = ShowOnly({"t_9": task("t_9")})
    out = pending_parents(h, "t_10", {"parents": ["t_9"]})
    assert [(w["id"], w["reason"]) for w in out] == [("t_9", "sin integrar")]


def test_integrated_parent_does_not_block():
    h = ShowOnly({"t_9": task("t_9", comments=[INTEGRATED])})
    assert pending_parents(h, "t_10", {"parents": ["t_9"]}) == []


def test_integrated_comment_from_someone_else_does_not_count():
    fake = {"author": "oscar-telegram", "body": "INTEGRADO abc123"}
    h = ShowOnly({"t_9": task("t_9", comments=[fake])})
    assert pending_parents(h, "t_10", {"parents": ["t_9"]})


def test_done_parent_without_code_does_not_block_but_running_one_does():
    h = ShowOnly({"t_spec": task("t_spec", branch=False), "t_9": task("t_9", status="running")})
    out = pending_parents(h, "t_10", {"parents": ["t_spec", "t_9"]})
    assert [(w["id"], w["reason"]) for w in out] == [("t_9", "en running")]


def test_unreadable_parent_is_fail_closed():
    out = pending_parents(ShowOnly({}), "t_10", {"parents": ["t_9"]})
    assert out and out[0]["reason"] == "no se pudo leer"


def test_waiting_line_and_order():
    assert waiting_line([{"id": "t_9", "reason": "en review"}]) == "⏸ espera a t_9 (en review)"
    items = [("t_10", ["t_9"]), ("t_11", []), ("t_9", [])]
    ordered = dependency_order(items, lambda i: i[0], lambda i: i[1])
    assert [i[0] for i in ordered] == ["t_11", "t_9", "t_10"]


def test_order_tolerates_cycles():
    items = [("a", ["b"]), ("b", ["a"]), ("c", [])]
    assert [i[0] for i in dependency_order(items, lambda i: i[0], lambda i: i[1])] == ["c", "a", "b"]


# --- integración en el Integrador ------------------------------------------------------------------------

def with_parent(h, parent):
    """El hijo t_1 depende de `parent` (show de la tarea padre)."""
    base_show = h.show

    def show(tid):
        if tid == parent["task"]["id"]:
            return parent
        return {**base_show(tid), "parents": [parent["task"]["id"]]}

    h.show = show


def test_child_waits_for_parent_and_no_merge_button(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    parent = task("t_9", status="review")
    with_parent(h, parent)
    assert integ.run_pass(force=True) == {"t_1": "waiting_deps"}
    assert "⏸" in tg.sent[-1]["text"] and "t_9" in tg.sent[-1]["text"]
    assert not buttons(tg.sent[-1]["markup"])
    assert not [a for a in w.argv(GH) if "merge" in a]
    # Se repite la pasada sin cambios: no vuelve a avisar.
    n = len(tg.sent)
    assert integ.run_pass(force=True) == {"t_1": "waiting_deps"}
    assert len(tg.sent) == n
    # El padre se integra: ahora sí se ofrece Fusionar.
    parent["task"]["status"] = "done"
    parent["comments"].append(INTEGRATED)
    assert integ.run_pass(force=True) == {"t_1": "offered"}
    assert "🔀 Fusionar" in buttons(tg.sent[-1]["markup"])


def test_merge_rechecks_parents_at_press_time(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    parent = task("t_9", comments=[INTEGRATED])
    with_parent(h, parent)
    assert integ.run_pass(force=True) == {"t_1": "offered"}
    parent["comments"].clear()  # el padre dejó de estar integrado (p. ej. se reabrió)
    press(d, tg)
    assert not [a for a in w.argv(GH) if "merge" in a]
    assert "espera a t_9" in tg.edits[-1]["text"]


def test_integrator_notes_are_not_integration():
    from agent_lanes.deps import is_integration_comment
    note = {"author": "lane-integrator", "body": "INTEGRADOR: gates OK · PR #7 · esperando a Oscar"}
    assert not is_integration_comment(note)
    assert is_integration_comment({"author": "lane-integrator", "body": "INTEGRADO abc123 · PR x"})


def test_failed_gates_note_does_not_mark_task_integrated(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    h.comments_by["t_1"].append({"author": "lane-integrator", "body": "INTEGRADOR: gates fallidos · PR #7"})
    assert integ.run_pass(force=True) == {"t_1": "offered"}
