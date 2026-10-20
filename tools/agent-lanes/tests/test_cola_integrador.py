"""Bloqueos de LIMITACION-GUARD → cola del integrador (no a Oscar), escalada a las 2 h con botones y resumen de las
9:00 por carril (t_f8f58acb). Todo con dobles: ni Telegram ni Hermes reales."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from agent_lanes import resumen_carriles as rc
from agent_lanes.decisions import GUARD_SNOOZE, TO_INTEGRADOR, DecisionDesk, keyboard_spec
from agent_lanes.guard_route import (GuardNotifier, GuardQueue, GuardRouter, integrator_text, oscar_text,
                                     queue_view)
from agent_lanes.hermes import GUARD_LIMIT_MARK
from agent_lanes.renotify import Renotifier, guard_need
from tests.test_preguntas_bucles import LANE

REASON = f"{GUARD_LIMIT_MARK} el worker no puede hacerlo con el guard del carril:\n- ¿Instalo pytest-asyncio?"
H = 3600


def pend(tid="t_1", lane="claude-hub"):
    return SimpleNamespace(tid=tid, lane=SimpleNamespace(name=lane, board="default"), guard=True,
                           task={"id": tid, "title": "Arreglar X"}, need="Instalar pytest-asyncio", since=0)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class Sink:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, p, entry):
        self.calls.append(p.tid)
        return self.ok


def router(tmp_path, live, clock, integ=None, oscar=None):
    q = GuardQueue(tmp_path / "cola.json", clock)
    integ, oscar = integ or Sink(), oscar or Sink()
    return GuardRouter(lambda: list(live), integ, oscar, q, now=clock), q, integ, oscar


# --- enrutado -----------------------------------------------------------------------------------------------

def test_guard_block_goes_to_integrator_queue_not_to_oscar(tmp_path):
    clock = Clock()
    r, q, integ, oscar = router(tmp_path, [pend()], clock)
    assert r.tick() == ["cola t_1"]
    assert integ.calls == ["t_1"] and oscar.calls == []
    assert q.get("t_1")["need"] == "Instalar pytest-asyncio"
    assert r.tick() == [] and integ.calls == ["t_1"]  # no se anuncia dos veces


def test_failed_announcement_is_retried_next_pass(tmp_path):
    clock = Clock()
    integ = Sink(ok=False)
    r, q, _, _ = router(tmp_path, [pend()], clock, integ=integ)
    assert r.tick() == []
    integ.ok = True
    assert r.tick() == ["cola t_1"] and integ.calls == ["t_1", "t_1"]


def test_integrator_message_has_card_need_exact_command_and_who():
    text = integrator_text({"tid": "t_1", "lane": "claude-hub", "title": "Arreglar X", "need": "Instalar pip", "veces": 1},
                           "@integra")
    for part in ("t_1", "Arreglar X", "Instalar pip", "/tomar-tarea t_1", "Quién lo ejecuta", "Próximo paso", "@integra"):
        assert part in text


# --- ventana de 2 h -----------------------------------------------------------------------------------------

def test_no_escalation_before_two_hours_and_one_after(tmp_path):
    clock = Clock()
    r, q, integ, oscar = router(tmp_path, [pend()], clock)
    r.tick()
    clock.t += 2 * H - 60
    assert r.tick() == [] and oscar.calls == []
    clock.t += 120
    assert r.tick() == ["oscar t_1"] and oscar.calls == ["t_1"]
    clock.t += 5 * H
    assert r.tick() == [] and oscar.calls == ["t_1"]  # una sola vez


def test_resolved_card_leaves_queue_and_never_reaches_oscar(tmp_path):
    clock = Clock()
    live = [pend()]
    r, q, integ, oscar = router(tmp_path, live, clock)
    r.tick()
    live.clear()  # el integrador la resolvió (o la reasignó)
    clock.t += 3 * H
    assert r.tick() == ["resuelta t_1"] and q.entries() == {} and oscar.calls == []


def test_snooze_delays_escalation_24h(tmp_path):
    clock = Clock()
    r, q, integ, oscar = router(tmp_path, [pend()], clock)
    r.tick()
    clock.t += 3 * H
    r.tick()
    assert q.snooze("t_1") and q.get("t_1")["escalated_at"] is None
    clock.t += 23 * H
    assert r.tick() == [] and oscar.calls == ["t_1"]
    clock.t += 2 * H
    assert r.tick() == ["oscar t_1"] and oscar.calls == ["t_1", "t_1"]
    assert not q.snooze("t_zzz")


def test_repeated_block_is_flagged_as_repeated(tmp_path):
    clock = Clock()
    live = [pend()]
    r, q, integ, _ = router(tmp_path, live, clock)
    r.tick()
    live.clear()
    r.tick()
    live.append(pend())
    r.tick()
    e = q.get("t_1")
    assert e["veces"] == 2 and "Repetido" in integrator_text(e) and "repetido" in queue_view({"t_1": e}, clock.t)


# --- mensajes a Oscar y botones -----------------------------------------------------------------------------

class Bot:
    def __init__(self):
        self.sent = []

    def send_to(self, chat, thread, text, **kw):
        self.sent.append({"chat": chat, "thread": thread, "text": text, **kw})
        return True


class Desk:
    def markup(self, state, **kw):
        return {"state": state, **{k: kw[k] for k in ("block_kind",)}}


def test_escalation_reaches_oscar_with_mention_and_two_buttons():
    bot = Bot()
    gn = GuardNotifier(bot, Desk(), {"chat": "-100", "thread": "7", "mention": "@oscar_t", "integrator_mention": "@integra"},
                       "42", lambda: 1_000_000 + 3 * H)
    entry = {"tid": "t_1", "lane": "claude-hub", "title": "T", "need": "n", "queued_at": 1_000_000.0, "veces": 1}
    assert gn.to_integrator(pend(), entry) and "@integra" in bot.sent[-1]["text"] and "reply_markup" not in bot.sent[-1]
    assert gn.to_oscar(pend(), entry)
    dm, topic = bot.sent[-2], bot.sent[-1]
    assert dm["chat"] == "42" and topic["chat"] == "-100" and "@oscar_t" in topic["text"]
    assert "3 h" in dm["text"] and "Próximo paso" in dm["text"]
    assert dm["reply_markup"]["block_kind"] == "console_escalated"
    spec = keyboard_spec("blocked", block_kind="console_escalated")
    assert [(b["text"], b["action"]) for row in spec for b in row] == [
        ("🧑‍💻 Que lo resuelva el integrador", TO_INTEGRADOR), ("⏳ Aplazar", GUARD_SNOOZE)]
    assert "Que lo resuelva" in oscar_text(entry, 1_000_000 + 3 * H)


def test_without_attention_topic_queue_still_holds_and_oscar_gets_dm_only():
    bot = Bot()
    gn = GuardNotifier(bot, Desk(), None, "42")
    assert gn.to_integrator(pend(), {"tid": "t_1"}) and bot.sent == []
    assert gn.to_oscar(pend(), {"tid": "t_1", "queued_at": 0}) and [m["chat"] for m in bot.sent] == ["42"]


def test_guard_snooze_button_uses_queue_and_reports_when_gone(tmp_path):
    edits = []
    desk = SimpleNamespace(guard_queue=GuardQueue(tmp_path / "c.json"),
                           _edit=lambda rec, where, state, text, **k: edits.append((state, text)))
    desk.guard_queue.add(pend())
    assert DecisionDesk._guard_snooze(desk, {"task_id": "t_1"}, {}) is True and edits[-1][0] == "parked"
    assert DecisionDesk._guard_snooze(desk, {"task_id": "t_9"}, {}) is False and edits[-1][0] == "blocked"


# --- planes de renotify ---------------------------------------------------------------------------------------

def test_renotify_marks_guard_blocks_and_does_not_forward_them_to_oscar():
    show = {"task": {"id": "t_1", "title": "T", "status": "blocked"},
            "events": [{"kind": "blocked", "created_at": 10, "payload": {"kind": "needs_input", "reason": REASON}}],
            "comments": [], "runs": []}
    p = Renotifier._plan_blocked(object(), LANE, show)
    assert p.guard and "pytest-asyncio" in p.need and GUARD_LIMIT_MARK not in p.need
    assert guard_need("otra cosa") is None
    out = []
    ren = SimpleNamespace(collect=lambda **k: [p], out=out.append, messages=SimpleNamespace(get=lambda t: None))
    assert Renotifier.run(ren) == ([], []) and "cola del integrador" in out[0]
    assert Renotifier.alert_urgent(SimpleNamespace(), p) is True  # nada al DM de Oscar


# --- resumen de las 9:00 ------------------------------------------------------------------------------------

class Board:
    def __init__(self, data):
        self.data = data

    def list_status(self, name, status):
        return self.data.get((name, status), [])


def test_summary_with_empty_lanes_and_no_decisions():
    lanes = {"a": SimpleNamespace(board="b", kind="implement"), "b": SimpleNamespace(board="b", kind="review")}
    rows = rc.collect_rows(lanes, lambda board: Board({}))
    text = rc.render_summary(rows, [], 0, 0)
    assert text.count("libre · cola 0 · bloqueos 0") == 2 and "Decisiones pendientes de Oscar: ninguna" in text


def test_summary_lines_per_lane_and_decisions_with_mention():
    lanes = {"a": SimpleNamespace(board="b", kind="implement"), "x": SimpleNamespace(board="b", kind="implement")}
    board = Board({("a", "running"): [{"id": "t_1", "title": "Haciendo cosas"}], ("a", "ready"): [{"id": "t_2"}] * 3,
                   ("a", "blocked"): [{"id": "t_3"}]})
    rows = rc.collect_rows(lanes, lambda b: board)
    d = [SimpleNamespace(tid="t_9", task={"title": "¿Publicar?"}, since=0)]
    text = rc.render_summary(rows, d, 2, 3 * H, "@oscar_t")
    assert "a: Haciendo cosas · cola 3 · bloqueos 1" in text and "x: libre" in text
    assert "@oscar_t Decisiones pendientes de Oscar (1)" in text and "t_9" in text and "Próximo paso" in text
    assert "2 en la cola del integrador" in text


def test_summary_failing_lane_does_not_break_it():
    def boom(board):
        raise RuntimeError("hermes caído")
    rows = rc.collect_rows({"a": SimpleNamespace(board="b")}, boom)
    assert "sin datos" in rc.render_summary(rows, [], 0, 0)


def madrid(h, m=0, day=20):
    return datetime(2026, 10, day, h, m, tzinfo=rc.MADRID)


def test_summary_sent_once_per_day_in_the_9_to_11_window(tmp_path):
    now, sent = [madrid(8, 59)], []
    s = rc.DailyLaneSummary(lambda: [], lambda: [SimpleNamespace(tid="t_1", task={}, since=0)], lambda: 0,
                            lambda text, btn: (sent.append((text, btn)), True)[1], tmp_path / "d.json", now=lambda: now[0])
    assert s.tick() is None and sent == []
    now[0] = madrid(9, 0)
    assert s.tick() and len(sent) == 1 and sent[0][1] is True  # con botón porque hay decisiones
    now[0] = madrid(10, 30)
    assert s.tick() is None and len(sent) == 1
    now[0] = madrid(11, 0, day=21)
    assert s.tick() is None  # fuera de ventana
    now[0] = madrid(9, 5, day=21)
    assert s.tick() and len(sent) == 2


def test_summary_failed_send_is_retried(tmp_path):
    ok = [False]
    s = rc.DailyLaneSummary(lambda: [], lambda: [], lambda: 0, lambda t, b: ok[0], tmp_path / "d.json",
                            now=lambda: madrid(9, 1))
    assert s.tick() is None
    ok[0] = True
    assert s.tick() and s.tick() is None
