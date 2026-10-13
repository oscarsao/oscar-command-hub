"""Guarda de tablero: tarjetas abiertas en un tablero que su carril no lee (tableros falsos, sin hermes real)."""
from types import SimpleNamespace

from agent_lanes import board_guard as bg
from agent_lanes.board_guard import BoardGuardTicker, run_guard, scan

LANES = {
    "claude-oscarhq": SimpleNamespace(board="oscarhq", kind="implement"),
    "claude-migrateam": SimpleNamespace(board="migrateam", kind="implement"),
    "claude-hub": SimpleNamespace(board="default", kind="implement"),
    "review": SimpleNamespace(board="", kind="review"),
}


class FakeBoard:
    def __init__(self, name, world):
        self.name, self.world = name, world

    @property
    def cards(self):
        return self.world.setdefault(self.name, {})

    def list_status_all(self, status):
        return [dict(id=i, **{k: v for k, v in c["task"].items() if k != "id"}) for i, c in self.cards.items()
                if c["task"]["status"] == status]

    def show(self, tid):
        return self.cards[tid]

    def create(self, title, body, assignee, *, priority=None, key=None, created_by=None, parents=None):
        for i, c in self.cards.items():  # idempotency-key
            if c.get("key") == key:
                return i
        tid = f"t_{len(self.world.setdefault('_n', [])) + 1:08x}"
        self.world["_n"].append(tid)
        self.cards[tid] = {"task": {"id": tid, "title": title, "body": body, "assignee": assignee,
                                    "priority": priority, "status": "ready"}, "comments": [], "key": key}
        return tid

    def comment(self, tid, text, author=None):
        self.cards[tid]["comments"].append({"body": text, "author": author or "agent-lanes"})
        return True

    def archive(self, tid):
        self.cards[tid]["task"]["status"] = "archived"
        return True


def world_with(card, board="default", **extra):
    task = {"id": "t_0000aaaa", "title": "Arreglar X", "body": "cuerpo", "assignee": "claude-oscarhq",
            "priority": 2, "status": "ready", **extra}
    world = {board: {"t_0000aaaa": {"task": task, "comments": card.get("comments", []),
                                    "parents": card.get("parents", []), "children": []}}}
    return world, lambda b: FakeBoard(b, world)


def test_dry_run_detects_and_changes_nothing():
    world, hermes_for = world_with({})
    out = run_guard(LANES, hermes_for)
    assert [(o.action, o.item.target) for o in out] == [("would_move", "oscarhq")]
    assert world["default"]["t_0000aaaa"]["task"]["status"] == "ready"
    assert not world.get("oscarhq")


def test_apply_recreates_copies_comments_and_archives_original():
    world, hermes_for = world_with({"comments": [{"body": "nota de Oscar", "author": "oscar"}]})
    [o] = run_guard(LANES, hermes_for, apply=True)
    assert o.action == "moved" and o.new_id
    new = world["oscarhq"][o.new_id]
    assert new["task"]["title"] == "Arreglar X" and new["task"]["body"] == "cuerpo"
    assert new["task"]["assignee"] == "claude-oscarhq" and new["task"]["priority"] == 2
    assert any("default:t_0000aaaa" in c["body"] for c in new["comments"])
    assert any("nota de Oscar" in c["body"] and c["author"] == "oscar" for c in new["comments"])
    orig = world["default"]["t_0000aaaa"]
    assert orig["task"]["status"] == "archived"
    assert orig["comments"][-1]["body"] == f"movida a oscarhq:{o.new_id}"


def test_apply_is_idempotent_after_partial_failure():
    world, hermes_for = world_with({"comments": [{"body": "c1", "author": "oscar"}]})
    boards = {}

    def flaky(b):
        h = boards.setdefault(b, hermes_for(b))
        if b == "default" and not world.get("_fail_done"):
            h.archive = lambda tid: False  # el archivado falla la primera vez
        return h

    [o1] = run_guard(LANES, flaky, apply=True)
    assert o1.action == "warn"
    world["_fail_done"] = True
    boards.clear()
    [o2] = run_guard(LANES, flaky, apply=True)
    assert o2.action == "moved"
    assert len([c for c in world["oscarhq"][o2.new_id]["comments"] if "c1" in c["body"]]) == 1
    assert len([k for k in world["oscarhq"] if k != "_n"]) == 1
    assert run_guard(LANES, flaky, apply=True) == []  # la original ya está archivada


def test_card_in_its_own_board_is_untouched():
    world, hermes_for = world_with({}, board="oscarhq")
    assert run_guard(LANES, hermes_for, apply=True) == []
    assert world["oscarhq"]["t_0000aaaa"]["task"]["status"] == "ready"


def test_oscar_and_unknown_assignees_are_never_moved():
    for who in ("oscar", "otro", "review"):
        world, hermes_for = world_with({}, assignee=who)
        assert scan(LANES, hermes_for) == []


def test_running_blocked_claimed_or_with_parents_are_only_reported():
    for extra, card in [({"status": "running"}, {}), ({"status": "blocked"}, {}),
                        ({"claim_lock": "runner-1"}, {}), ({}, {"parents": ["t_00000001"]})]:
        world, hermes_for = world_with(card, **extra)
        [o] = run_guard(LANES, hermes_for, apply=True)
        assert o.action == "warn" and o.reason
        assert world["default"]["t_0000aaaa"]["task"]["status"] == extra.get("status", "ready")
        assert not world.get("oscarhq")


def test_unreadable_board_does_not_stop_the_rest():
    world, hermes_for = world_with({})

    def broken(b):
        if b == "migrateam":
            raise RuntimeError("boom")
        return hermes_for(b)

    assert [o.action for o in run_guard(LANES, broken)] == ["would_move"]


def test_ticker_notifies_once_per_card_and_respects_interval(tmp_path):
    world, hermes_for = world_with({})
    sent, now = [], [0.0]
    t = BoardGuardTicker(LANES, hermes_for, apply=False, send=sent.append, state_path=tmp_path / "g.json",
                         minutes=10, clock=lambda: now[0])
    t.tick()
    assert len(sent) == 1 and "dry-run" in sent[0] and "t_0000aaaa" in sent[0]
    now[0] = 30
    t.tick()  # dentro del intervalo
    now[0] = 700
    t.tick()  # misma tarjeta, mismo resultado: sin aviso repetido
    assert len(sent) == 1


def test_ticker_apply_reports_move_and_survives_send_errors(tmp_path):
    world, hermes_for = world_with({})

    def boom(text):
        raise RuntimeError("telegram caído")

    t = BoardGuardTicker(LANES, hermes_for, apply=True, send=boom, state_path=tmp_path / "g.json")
    t.tick()  # no lanza
    assert world["default"]["t_0000aaaa"]["task"]["status"] == "archived"
    sent = []
    text = bg.notice_text(run_guard(LANES, lambda b: FakeBoard(b, world_with({})[0])), True)
    sent.append(text)
    assert "dry-run" not in text


def test_lane_boards_ignores_review_and_boardless():
    assert bg.lane_boards(LANES) == {"claude-oscarhq": "oscarhq", "claude-migrateam": "migrateam",
                                     "claude-hub": "default"}
