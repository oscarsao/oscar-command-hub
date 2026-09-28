"""28-09: respuestas de Oscar vía Hermes (RESPUESTA-OSCAR:, formato estricto) y árbol padre/hijas en los avisos,
/tarea y /tareas."""
from __future__ import annotations

import json
import subprocess

import pytest

from agent_lanes.config import Lane
from agent_lanes.deps import ShowCache, TreeReader, child_bucket, family, tree_lines
from agent_lanes.hermes import (HERMES_ANSWER_PREFIX, HermesCLI, hermes_answer_text, oscar_answers_from,
                                pending_hermes_answer)
from agent_lanes.hermes_answers import ANSWERED_VIA_HERMES, HermesAnswers
from agent_lanes.integration import RISKS, render_ficha
from agent_lanes.notices import render
from agent_lanes.worker import build_prompt
from tests.test_commands import GESTION, MIG, OSCAR, center, command, needs, press

TOPIC_BODY = f"Origen-Telegram: chat={GESTION} thread=230\n\n## Objetivo\nAlgo.\n"
Q = [{"question": "¿Publicar ya?", "options": ["Sí", "No"], "recommended": 0}]


def c(body, author="default", at=2000.0):
    return {"author": author, "body": body, "created_at": at}


def blocked(tid="t_aaaaaaa1", *, kind="needs_input", at=1000.0, comments=(), status="blocked", **task):
    return {"task": {"id": tid, "title": f"Tarea {tid}", "body": TOPIC_BODY, "assignee": "claude-migrateam",
                     "status": status, "created_at": 10, **task},
            "runs": [{"status": "blocked", "summary": "x", "metadata": []}],
            "events": [{"kind": "blocked", "payload": {"kind": kind, "reason": "El worker necesita decisión:\n"
                                                                            "- ¿Publicar ya?"}, "created_at": at}],
            "comments": list(comments), "parents": [], "children": []}


# --- 1. formato estricto --------------------------------------------------------------------------------

@pytest.mark.parametrize("author", ["default", "oscar-telegram"])
def test_strict_prefix_from_allowed_authors_is_an_answer(author):
    assert hermes_answer_text(c("RESPUESTA-OSCAR: 1) Sí\n2) 19 €", author)) == "1) Sí\n2) 19 €"


@pytest.mark.parametrize("comment", [
    c("Respuesta de Oscar: sí"),                        # formato de los botones, pero escrito por Hermes
    c("Oscar decide (28-09): sí"),                      # lo que Hermes escribía el 28-09
    c("respuesta-oscar: sí"),                           # minúsculas
    c("RESPUESTA-OSCAR sí"),                            # sin los dos puntos
    c("RESPUESTA OSCAR: sí"),
    c(" RESPUESTA-OSCAR: sí"),                          # no empieza por el prefijo
    c("Nota: RESPUESTA-OSCAR: sí"),
    c("RESPUESTA-OSCAR:   \n "),                        # sin texto detrás
    c("RESPUESTA-OSCAR: sí", "agent-lanes"),            # workers
    c("RESPUESTA-OSCAR: sí", "lane-review"),
    c("RESPUESTA-OSCAR: sí", "lane-claude-migrateam"),
    c("RESPUESTA-OSCAR: sí", "lane-integrator"),        # integrador
    c("RESPUESTA-OSCAR: sí", None),
    c("RESPUESTA-OSCAR: sí", "Default"),
    {"author": "default", "body": None},
])
def test_anything_else_is_not_an_answer(comment):
    assert hermes_answer_text(comment) is None


def test_answer_must_be_strictly_after_the_last_needs_input_block():
    ok = c("RESPUESTA-OSCAR: sí", at=1001)
    assert pending_hermes_answer(blocked(comments=[ok])) is ok
    assert pending_hermes_answer(blocked(comments=[c("RESPUESTA-OSCAR: sí", at=1000)])) is None  # mismo instante
    assert pending_hermes_answer(blocked(comments=[c("RESPUESTA-OSCAR: sí", at=999)])) is None   # ronda anterior
    assert pending_hermes_answer(blocked(kind="transient", comments=[ok])) is None
    assert pending_hermes_answer(blocked(status="ready", comments=[ok])) is None
    assert pending_hermes_answer(blocked(comments=[{**ok, "created_at": None}])) is None


def test_answer_from_a_previous_round_does_not_unblock_a_new_block():
    show = blocked(at=3000, comments=[c("RESPUESTA-OSCAR: sí", at=2000)])
    show["events"].insert(0, {"kind": "blocked", "payload": {"kind": "needs_input"}, "created_at": 1000})
    assert pending_hermes_answer(show) is None


def test_latest_valid_answer_wins_and_invalid_ones_are_skipped():
    last = c("RESPUESTA-OSCAR: definitivo", at=1003)
    show = blocked(comments=[c("RESPUESTA-OSCAR: primero", at=1001), last,
                             c("RESPUESTA-OSCAR: yo mando", "agent-lanes", at=1004)])
    assert pending_hermes_answer(show) is last


def test_worker_receives_button_and_hermes_answers_in_order():
    show = blocked(comments=[c("Respuesta de Oscar: ¿A? → B", "oscar-telegram", 1001),
                             c("Oscar decide (28-09): C", "default", 1002),
                             c("RESPUESTA-OSCAR: 1) Sí 2) 19 €", "default", 1003),
                             c("RESPUESTA-OSCAR: truco", "agent-lanes", 1004)])
    assert oscar_answers_from(show) == ["Respuesta de Oscar: ¿A? → B",
                                        "Respuesta de Oscar (vía Hermes): 1) Sí 2) 19 €"]


def test_hermes_cli_oscar_answers_reach_the_worker_prompt():
    show = blocked(comments=[c("RESPUESTA-OSCAR: publica el viernes", at=1001)])

    def run(args, **kw):
        return subprocess.CompletedProcess(args, 0, json.dumps(show), "")

    answers = HermesCLI("migrateam", runner=run).oscar_answers("t_aaaaaaa1")
    prompt = build_prompt({"id": "t_aaaaaaa1", "title": "T", "body": "B", "oscar_answers": answers}, MIG)
    assert "Decisiones de Oscar" in prompt and "(vía Hermes): publica el viernes" in prompt


# --- 1b. el vigilante desbloquea y edita todas las copias -------------------------------------------------

def _asked(tmp_path, show):
    """Tarea bloqueada en needs_input con su aviso con botones en el tema y la copia en el DM."""
    from agent_lanes.runner import LaneRunner
    cc, desk, bot, h, messages = center(tmp_path, {show["task"]["id"]: show})
    r = LaneRunner(MIG, hermes=h, git=None, worker=None, verifier=None, notify=bot, messages=messages, decisions=desk)
    r.notify("needs_input", show["task"]["id"], show["task"], "necesita tu decisión", alert=True,
             bullets=["• ¿Publicar ya?"], buttons=True, questions=Q)
    return cc, desk, bot, h, messages


def test_watcher_unblocks_and_edits_every_copy_without_buttons(tmp_path):
    show = blocked(comments=[c("RESPUESTA-OSCAR: sí, publica", at=1001)])
    cc, desk, bot, h, messages = _asked(tmp_path, show)
    topic, dm = bot.sent
    assert dm["chat_id"] == str(OSCAR) and topic["markup"]
    assert HermesAnswers({MIG.name: MIG}, hermes_for=lambda b: h, desk=desk).tick() == ["t_aaaaaaa1"]
    assert ("unblock", "t_aaaaaaa1") in h.calls and show["task"]["status"] == "ready"
    edited = {(e["chat"], e["mid"]) for e in bot.edits}
    assert edited == {(topic["chat_id"], topic["message_id"]), (dm["chat_id"], dm["message_id"])}
    assert all(ANSWERED_VIA_HERMES in e["text"] and e["markup"] is None for e in bot.edits)
    assert "sí, publica" in bot.edits[0]["text"]
    # un toque tardío en cualquier copia ya no hace nada
    press(desk, dm, 0)
    assert bot.answers[-1] == "Esta decisión ya no está activa"
    assert [x for x in h.calls if x[0] == "comment"] == []


def test_watcher_ignores_invalid_or_stale_comments(tmp_path):
    show = blocked(comments=[c("Oscar decide (28-09): sí", at=1001), c("RESPUESTA-OSCAR: sí", "lane-review", 1002),
                             c("RESPUESTA-OSCAR: viejo", at=900)])
    cc, desk, bot, h, messages = _asked(tmp_path, show)
    assert HermesAnswers({MIG.name: MIG}, hermes_for=lambda b: h, desk=desk).tick() == []
    assert ("unblock", "t_aaaaaaa1") not in h.calls and bot.edits == []


def test_watcher_retries_a_failed_unblock_and_works_without_the_lanes_bot(tmp_path):
    show = blocked(comments=[c("RESPUESTA-OSCAR: sí", at=1001)])
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": show})
    real = h.unblock
    h.unblock = lambda tid: False
    w = HermesAnswers({MIG.name: MIG}, hermes_for=lambda b: h)  # sin desk: solo desbloquea
    assert w.tick() == []
    h.unblock = real
    assert w.tick() == ["t_aaaaaaa1"] and show["task"]["status"] == "ready"


def test_answered_task_leaves_the_decisions_inbox_before_the_watcher_runs(tmp_path):
    other = needs("t_bbbbbbb2", "El worker necesita decisión:\n- ¿Otra?")
    show = blocked(comments=[c("RESPUESTA-OSCAR: sí", at=1001)])
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": show, "t_bbbbbbb2": other})
    assert [p.tid for p in cc.pending_decisions()] == ["t_bbbbbbb2"]


# --- 2. árbol en los avisos -----------------------------------------------------------------------------

class Board:
    """Tablero en memoria con enlaces padre -> hija; cuenta las lecturas."""

    def __init__(self, tasks, links=()):
        self.board = "migrateam"
        self.tasks = {t["id"]: {"assignee": "claude-migrateam", "body": "", **t} for t in tasks}
        self.links = list(links)
        self.reads = []
        self.comments = {}

    def show(self, tid):
        self.reads.append(tid)
        if tid not in self.tasks:
            raise RuntimeError("no existe")
        return {"task": self.tasks[tid], "comments": self.comments.get(tid, []), "events": [],
                "parents": [p for p, ch in self.links if ch == tid], "children": [ch for p, ch in self.links if p == tid]}

    def list_status(self, assignee, status, sort="priority"):
        return [t for t in self.tasks.values() if t["assignee"] == assignee and t["status"] == status]


def fam_board():
    return Board([{"id": "t_e0000001", "title": "Épica <portal>", "status": "todo"},
                  {"id": "t_5ec00001", "title": "Spec", "status": "review", "branch_name": "lane/t_5ec00001"},
                  {"id": "t_d0000001", "title": "Pieza central", "status": "running"},
                  {"id": "t_c0000001", "title": "Hija 1", "status": "done"},
                  {"id": "t_c0000002", "title": "Hija 2", "status": "running"},
                  {"id": "t_c0000003", "title": "Hija 3", "status": "ready"},
                  {"id": "t_b0000001", "title": "Hermana", "status": "blocked"}],
                 [("t_e0000001", "t_d0000001"), ("t_5ec00001", "t_d0000001"), ("t_e0000001", "t_b0000001"),
                  ("t_d0000001", "t_c0000001"), ("t_d0000001", "t_c0000002"), ("t_d0000001", "t_c0000003")])


def test_tree_lines_parent_dependency_and_children():
    b = fam_board()
    b.tasks["t_e0000001"]["status"] = "done"  # épica sin rama y terminada: contexto, no bloquea
    assert tree_lines(b, "t_d0000001") == [
        "🔗 Parte de: t_e0000001 · Épica <portal>",
        "⏸ Depende de: t_5ec00001 (en review)",
        "↳ 3 subtareas: ✅ 1 · ▶️ 1 · ⏳ 1",
    ]


def test_a_parent_appears_in_one_line_only_and_waiting_can_be_left_out():
    b = fam_board()
    lines = tree_lines(b, "t_d0000001")
    assert sum("t_e0000001" in l for l in lines) == 1 and "t_e0000001 (en todo)" in lines[0]
    no_wait = tree_lines(b, "t_d0000001", with_waiting=False)
    assert not any("t_5ec00001" in l for l in no_wait) and no_wait[-1].startswith("↳ 3 subtareas")


def test_tree_lines_empty_without_links_and_never_raise():
    b = fam_board()
    assert tree_lines(b, "t_c0000003") == ["⏸ Depende de: t_d0000001 (en running)"]
    assert tree_lines(Board([{"id": "t_5010a001", "title": "S", "status": "ready"}]), "t_5010a001") == []
    assert tree_lines(b, "t_nope0000") == []
    assert tree_lines(object(), "t_x") == []


@pytest.mark.parametrize("status,emoji", [("done", "✅"), ("archived", "✅"), ("running", "▶️"), ("review", "▶️"),
                                          ("blocked", "▶️"), ("ready", "⏳"), ("todo", "⏳"), ("triage", "⏳"),
                                          ("?", "⏳")])
def test_child_buckets(status, emoji):
    assert child_bucket(status) == emoji


def test_show_cache_reads_once_per_pass_and_expires():
    b, now = fam_board(), [0.0]
    cache = ShowCache(ttl=60, clock=lambda: now[0])
    tree_lines(b, "t_d0000001", cache=cache)
    first = len(b.reads)
    tree_lines(b, "t_d0000001", cache=cache)
    assert len(b.reads) == first and len(set(b.reads)) == len(b.reads)  # cada tarea leída una sola vez
    now[0] = 61
    tree_lines(b, "t_d0000001", cache=cache)
    assert len(b.reads) == 2 * first
    cache.clear()
    tree_lines(b, "t_d0000001", cache=cache)
    assert len(b.reads) == 3 * first


def test_render_puts_escaped_tree_lines_before_the_links():
    text = render("running", "t_1", "T", "claude-migrateam", "en curso", [("🗂 Tarjeta", "https://k/t_1")],
                  tree=["🔗 Parte de: t_e · Épica <portal>"])
    lines = text.split("\n")
    assert lines[-2] == "🔗 Parte de: t_e · Épica &lt;portal&gt;" and lines[-1].startswith("<a href")
    assert render("running", "t_1", "T", "l", "en curso", tree=[]) == render("running", "t_1", "T", "l", "en curso")


def test_lane_notice_and_button_edit_keep_the_tree(tmp_path):
    from agent_lanes.runner import LaneRunner
    show = blocked(comments=[c("RESPUESTA-OSCAR: sí", at=1001)])
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": show})
    tree = lambda board, tid, show=None, **kw: ["↳ 2 subtareas: ✅ 1 · ⏳ 1"] if board == "migrateam" else []  # noqa
    desk.tree = tree
    r = LaneRunner(MIG, hermes=h, git=None, worker=None, verifier=None, notify=bot, messages=messages, decisions=desk,
                   tree=tree)
    r.notify("needs_input", "t_aaaaaaa1", show["task"], "necesita tu decisión", alert=True, buttons=True, questions=Q)
    assert "↳ 2 subtareas: ✅ 1 · ⏳ 1" in bot.sent[0]["text"]
    HermesAnswers({MIG.name: MIG}, hermes_for=lambda b: h, desk=desk).tick()
    assert all("↳ 2 subtareas" in e["text"] for e in bot.edits) and bot.edits


def test_tree_reader_uses_the_lane_board_and_shared_cache():
    b = fam_board()
    reader = TreeReader(lambda board: b if board == "migrateam" else None)
    assert reader("migrateam", "t_d0000001")[-1] == "↳ 3 subtareas: ✅ 1 · ▶️ 1 · ⏳ 1"
    n = len(b.reads)
    reader("migrateam", "t_d0000001")
    assert len(b.reads) == n
    assert reader(None, "t_d0000001") == [] and reader("otro", "t_d0000001") == []


def test_integration_ficha_shows_tree_lines():
    text = render_ficha(risk=RISKS["none"], phase="PARA APROBAR", tid="t_1", title="T", repo="r", base="main", status="s",
                        deps=["t_9 · Base · en review"], tree=["🔗 Parte de: t_e · Épica", "↳ 2 subtareas: ✅ 2"])
    assert "Depende de: t_9" in text and "🔗 Parte de: t_e · Épica" in text and "↳ 2 subtareas: ✅ 2" in text


def test_renotified_notice_carries_the_tree(tmp_path):
    from agent_lanes import renotify
    from tests.test_commands import LANES
    show = needs("t_aaaaaaa1", "El worker necesita decisión:\n- ¿Publicar ya? [1) Sí (recomendada) / 2) No]")
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": show})
    tree = lambda board, tid, show=None, **kw: ["↳ 2 subtareas: ✅ 2"]  # noqa: E731
    r = renotify.Renotifier(LANES, hermes_for=lambda b: h, notifier=bot, messages=messages, links=None, desk=desk,
                            out=lambda s: None, tree=tree)
    assert r.run(task="t_aaaaaaa1")[0] == ["t_aaaaaaa1"]
    assert "↳ 2 subtareas: ✅ 2" in bot.sent[0]["text"]


# --- 2b. /tarea y /tareas -------------------------------------------------------------------------------

def _kanban(tasks, links):
    """Shows para el KanbanHermes de test_commands con enlaces padre -> hija."""
    shows = {}
    for t in tasks:
        tid = t["id"]
        shows[tid] = {"task": {"assignee": "claude-migrateam", "body": "", **t}, "comments": [], "events": [],
                      "runs": [], "parents": [p for p, ch in links if ch == tid],
                      "children": [ch for p, ch in links if p == tid]}
    return shows


def test_tarea_shows_parent_siblings_children_with_state_and_panel_links(tmp_path):
    b = fam_board()
    shows = _kanban(b.tasks.values(), b.links)
    cc, desk, bot, h, messages = center(tmp_path, shows)
    command(desk, "/tarea t_d0000001")
    text = bot.sent[-1]["text"]
    tree = text[text.index("🌳"):].split("\n")
    assert tree[0] == "🌳 <b>Árbol</b>"
    assert tree[1].startswith('🔗 Padre: <a href="https://k/tasks/migrateam/t_e0000001">t_e0000001</a>')
    assert "Épica &lt;portal&gt;" in tree[1] and "⏳ pendiente" in tree[1]
    assert tree[2].startswith("🔗 Padre:") and "t_5ec00001" in tree[2] and "▶️ en review" in tree[2]
    assert tree[3].startswith("  • ") and "t_b0000001" in tree[3] and "▶️ bloqueada" in tree[3]
    assert tree[4].startswith("  👉 ") and "<b>Pieza central</b>" in tree[4] and tree[4].endswith("(esta)")
    kids = [l for l in tree if "↳" in l]
    assert [("t_c000000" + n) in k for n, k in zip("123", kids)] == [True] * 3
    assert "✅ terminada" in kids[0] and "▶️ en curso" in kids[1] and "⏳ en cola" in kids[2]
    assert len(text) <= 4000


def test_tarea_without_links_has_no_tree_and_long_families_are_capped(tmp_path):
    shows = _kanban([{"id": "t_5010a001", "title": "Sola", "status": "ready"}], [])
    cc, desk, bot, h, messages = center(tmp_path, shows)
    command(desk, "/tarea t_5010a001")
    assert "🌳" not in bot.sent[-1]["text"]
    many = [{"id": "t_fa000001", "title": "Padre", "status": "running"}] + [
        {"id": f"t_k{i:07d}", "title": "Hija muy larga " * 5, "status": "ready"} for i in range(40)]
    shows = _kanban(many, [("t_fa000001", t["id"]) for t in many[1:]])
    cc, desk, bot, h, messages = center(tmp_path / "b", shows)
    command(desk, "/tarea t_fa000001")
    text = bot.sent[-1]["text"]
    assert "🌳" in text and "más en el panel" in text and len(text) <= 4000
    assert family(Board([]), "t_x")["parents"] == []


def test_tareas_groups_children_under_their_parent(tmp_path):
    tasks = [{"id": "t_fa000001", "title": "Padre", "status": "running"},
             {"id": "t_hija0001", "title": "Hija A", "status": "ready"},
             {"id": "t_suel0001", "title": "Suelta", "status": "ready"},
             {"id": "t_niet0001", "title": "Nieta", "status": "ready"},
             {"id": "t_huer0001", "title": "Padre fuera de la lista", "status": "done"},
             {"id": "t_hija0002", "title": "Hija de padre no listado", "status": "ready"}]
    links = [("t_fa000001", "t_hija0001"), ("t_hija0001", "t_niet0001"), ("t_huer0001", "t_hija0002")]
    cc, desk, bot, h, messages = center(tmp_path, _kanban(tasks, links))
    command(desk, "/tareas")
    lines = bot.sent[-1]["text"].split("\n")
    i = next(n for n, l in enumerate(lines) if "t_fa000001" in l)
    assert lines[i].startswith("▶️ ")
    assert lines[i + 1].startswith("   ↳ ⏳ ") and "t_hija0001" in lines[i + 1]
    assert lines[i + 2].startswith("      ↳ ⏳ ") and "t_niet0001" in lines[i + 2]
    assert sum("t_hija0001" in l for l in lines) == 1
    assert any(l.startswith("⏳ ") and "t_hija0002" in l for l in lines)  # su padre no está en la lista: suelta
    assert any(l.startswith("⏳ ") and "t_suel0001" in l for l in lines)


def test_tareas_parent_child_cycle_does_not_hide_both(tmp_path):
    tasks = [{"id": "t_aaaa0001", "title": "A", "status": "ready"}, {"id": "t_bbbb0001", "title": "B", "status": "ready"}]
    cc, desk, bot, h, messages = center(tmp_path, _kanban(tasks, [("t_aaaa0001", "t_bbbb0001"),
                                                                  ("t_bbbb0001", "t_aaaa0001")]))
    command(desk, "/tareas")
    lines = bot.sent[-1]["text"].split("\n")
    assert sum("t_aaaa0001" in l for l in lines) == 1 and sum("t_bbbb0001" in l for l in lines) == 1
