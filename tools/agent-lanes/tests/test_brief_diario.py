"""Resumen diario (brief/brief_diario.py): clasificación, render, límites y enlaces."""
import re
import subprocess
from datetime import datetime

from brief.brief_diario import (MAX_LINES, TEXT_MAX, ListsHermes, brand, classify, collect, n_lines, render,
                                run_cost)

NOW = datetime(2026, 9, 28, 8, 0).timestamp()
BASE = "https://kanban.example.com/b/{board}/t/{id}"
LANES = [{"lane": "claude-oscarhq", "board": "oscarhq", "state": "libre", "running": [], "ready": 3, "review": 0,
          "last": "-"},
         {"lane": "claude-migrateam", "board": "migrateam", "state": "ocupado", "running": ["t_run1"], "ready": 1,
          "review": 1, "last": "-"}]


def task(tid, status, assignee=None, priority=0, title="Tarea", completed_at=None, created_at=0):
    return {"id": tid, "status": status, "assignee": assignee, "priority": priority, "title": title,
            "completed_at": completed_at, "created_at": created_at}


def blocked_detail(kind):
    return {"events": [{"kind": "blocked", "payload": {"kind": "transient"}},
                       {"kind": "unblocked", "payload": None},
                       {"kind": "blocked", "payload": {"kind": kind}}], "comments": [], "runs": []}


def approved_detail(comments=(), cost=None, ended_at=None):
    md = {"cost_usd": 2.0, "review": {"status": "approve", "cost_usd": cost}}
    return {"events": [], "comments": [{"author": "x", "body": b} for b in comments],
            "runs": [{"metadata": md, "ended_at": ended_at}]}


def item(board, t, detail=None):
    return {"board": board, "task": t, "detail": detail}


SAMPLE = [
    item("oscarhq", task("t_need", "blocked", "claude-oscarhq", 6, "¿Qué marca?"), blocked_detail("needs_input")),
    item("migrateam", task("t_osc1", "ready", "oscar", 10, "Firmar DPA")),
    item("default", task("t_osc2", "blocked", "oscar", 1, "Revisar contrato"), {"events": [], "comments": []}),
    item("oscarhq", task("t_err", "blocked", "claude-oscarhq", 5, "Runner caído"), blocked_detail("transient")),
    item("oscarhq", task("t_ok", "done", "claude-oscarhq", 7, "Archivar crews", completed_at=NOW - 3600),
         approved_detail(cost=0.5, ended_at=NOW - 3600)),
    item("migrateam", task("t_intg", "done", "claude-migrateam", 4, "Ya integrada", completed_at=NOW - 7200),
         approved_detail(comments=["INTEGRADO abc123"])),
    item("default", task("t_pr", "done", "claude-nextjobs", 2, "PR aprobado", completed_at=NOW - 5 * 86400),
         {"events": [], "runs": [], "comments": [{"body": "APROBADO-OSCAR 2026-09-27 · PR https://x/1"}]}),
    item("default", task("t_old", "done", "codex", 9, "Antigua", completed_at=NOW - 3 * 86400),
         {"events": [], "comments": [], "runs": [{"metadata": {"cost_usd": 9}, "ended_at": NOW - 3 * 86400}]}),
    item("personal", task("t_rdy", "ready", None, 3, "Sin asignar")),
]


def ids(lst):
    return [i["task"]["id"] for i in lst]


def test_classify_sections():
    sec = classify(SAMPLE, NOW)
    assert ids(sec["decide"]) == ["t_osc1", "t_need", "t_osc2"]  # prioridad más alta primero
    assert [i["why"] for i in sec["decide"]] == ["oscar", "needs_input", "oscar"]
    assert ids(sec["integrate"]) == ["t_pr", "t_ok"]  # APROBADO-OSCAR primero; t_intg ya integrada
    assert ids(sec["errors"]) == ["t_err"]  # kind = el del ÚLTIMO blocked
    assert {b: ids(v) for b, v in sec["done"].items()} == {"MigraTeam": ["t_intg"], "Píldora": ["t_ok"]}
    assert sec["cost"] == 0.5  # solo el coste del revisor en la ejecución con `review`; t_old es de hace 3 días


def test_blocked_needs_input_assigned_to_oscar_is_listed_once():
    it = item("default", task("t_x", "blocked", "oscar"), blocked_detail("needs_input"))
    assert ids(classify([it], NOW)["decide"]) == ["t_x"]


def test_brand_mapping():
    assert brand("default", "claude-nextjobs") == "NextJobs"
    assert brand("migrateam", None) == "MigraTeam"
    assert brand("oscarhq", "oscar") == "Píldora"
    assert brand("default", "claude-migrateam") == "MigraTeam"
    assert brand("personal", None) == "Otros"


def test_run_cost_keys():
    assert run_cost({"total_cost_usd": "1.5"}) == 1.5
    assert run_cost({"cost": 2}) == 2
    assert run_cost({"cost_usd": 3, "review": {"status": "approve"}}) == 0  # sin coste del revisor: 0, no 3
    assert run_cost(None) == 0


def test_render_links_sections_and_cost():
    text = render(classify(SAMPLE, NOW), LANES, NOW, BASE)
    assert text.startswith("☀️ <b>Buenos días · lunes 28 sep</b>")
    assert '<a href="https://kanban.example.com/b/migrateam/t/t_osc1">t_osc1</a>' in text
    for header in ("❓ <b>Esperan tu decisión (3)", "✅ <b>Listas para integrar (2)", "⛔ <b>Bloqueadas por error (1)",
                   "🏁 <b>Hecho ayer (2)", "🛣 <b>Carriles", "💸 Coste de ayer: 0,5 $"):
        assert header in text
    assert "🔵 claude-migrateam · ocupado (t_run1) · cola 1 · review 1" in text
    assert "PR aprobado" in text
    assert "(prueba)" not in text


def test_render_without_base_url_uses_code_ids():
    text = render(classify(SAMPLE, NOW), [], NOW, None)
    assert "<a " not in text and "<code>t_osc1</code>" in text


def test_nothing_pending_and_no_cost():
    text = render(classify([item("personal", task("t_rdy", "ready"))], NOW), [], NOW, BASE, prueba=True)
    assert "Nada pendiente de ti 🎉" in text
    assert "Coste de ayer" not in text
    assert "(prueba)" in text.splitlines()[0]


def test_limits_with_oversized_input():
    evil = "<script>&" + "x" * 300
    big = []
    for n in range(50):
        big.append(item("migrateam", task(f"t_d{n:04d}", "ready", "oscar", n % 10, evil)))
        big.append(item("oscarhq", task(f"t_e{n:04d}", "blocked", "claude-oscarhq", 1, evil),
                        blocked_detail("transient")))
        big.append(item("oscarhq", task(f"t_i{n:04d}", "done", "claude-oscarhq", 1, evil, completed_at=NOW - 60),
                        approved_detail()))
    lanes = LANES * 3
    text = render(classify(big, NOW), lanes, NOW, BASE + "?pad=" + "p" * 120)
    assert len(text) <= TEXT_MAX < 4096
    assert n_lines(text) <= MAX_LINES
    assert "<script>" not in text and "&lt;script&gt;&amp;" in text
    assert re.search(r"\+\d+ más", text)
    assert text.count("<a ") == text.count("</a>")  # HTML nunca cortado a medias


def test_decide_capped_at_8_with_more_line():
    many = [item("migrateam", task(f"t_{n}", "ready", "oscar", n)) for n in range(12)]
    text = render(classify(many, NOW), [], NOW, BASE)
    assert text.count("• ") == 8 and "+4 más" in text
    assert text.index("t_11") < text.index("t_10")  # prioridad 11 antes que 10


class FakeHermes:
    def __init__(self, tasks):
        self.tasks, self.shown = tasks, []

    def _call(self, *args):
        import json
        return subprocess.CompletedProcess(args, 0, json.dumps(self.tasks), "")

    def show(self, tid):
        self.shown.append(tid)
        return {"events": [], "comments": [], "runs": []}


def test_collect_only_shows_tasks_that_need_detail():
    tasks = [task("t_a", "blocked"), task("t_b", "ready", "oscar"), task("t_c", "done", "codex", completed_at=1),
             task("t_d", "done", "claude-oscarhq", completed_at=1), task("t_e", "todo")]
    fake = FakeHermes(tasks)
    items, lists = collect(lambda b: fake, boards=("oscarhq",), lane_names={"claude-oscarhq"}, now=NOW)
    assert len(items) == 5 and lists["oscarhq"] == tasks
    assert sorted(fake.shown) == ["t_a", "t_d"]


class FakeBot:
    def __init__(self, bot_id, error=None):
        self.bot_id, self.error, self.sent = bot_id, error, []

    def send_to(self, chat, thread, text):
        if self.error:
            raise self.error
        self.sent.append((chat, thread, text))
        return {"message_id": 7}


def test_send_falls_back_on_unauthorized_and_targets_general():
    import pytest
    from agent_lanes.telegram import TelegramAPIError
    from brief.brief_diario import GESTION_CHAT, send
    bad, good = FakeBot("1", TelegramAPIError("telegram HTTP 401: Unauthorized", "Unauthorized")), FakeBot("2")
    bot, sent = send([bad, good], "hola")
    assert bot is good and sent == {"message_id": 7}
    assert good.sent == [(GESTION_CHAT, None, "hola")]  # sin thread = tema General
    with pytest.raises(TelegramAPIError):  # un 400 (HTML inválido) no se enmascara probando otro bot
        send([FakeBot("3", TelegramAPIError("telegram HTTP 400: bad", "bad")), good], "x")


def test_lists_hermes_adapter():
    h = ListsHermes([task("t_1", "done", "l", completed_at=1), task("t_2", "done", "l", completed_at=5),
                     task("t_3", "ready", "l"), task("t_4", "done", "otro")])
    assert [t["id"] for t in h.list_status("l", "done", sort="completed-desc")] == ["t_2", "t_1"]
    assert [t["id"] for t in h.list_status("l", "ready")] == ["t_3"]
