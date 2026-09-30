"""Decisiones desde Telegram (28-09): tarjeta con contexto, botones por estado, callback corto, solo Oscar,
acciones (hermes/gh), force_reply, fallback sin bot de carriles, esquema retrocompatible y límites de Telegram."""
from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from datetime import datetime

import jsonschema
import pytest

from agent_lanes import notices, telegram
from agent_lanes.config import ROOT, Lane
from agent_lanes.decisions import (APPROVE, CHANGES, EXPLAIN, NOT_OWNER, OPTION, OTHER, PARK, RETRY, CallbackStore,
                                   DecisionDesk, UpdatePoller, keyboard_spec)
from agent_lanes.git_ops import GitOps
from agent_lanes.hermes import OSCAR_AUTHOR, REVIEW_AUTHOR, HermesCLI
from agent_lanes.notices import (LinkBuilder, MessageStore, TaskNotices, is_spec_path, normalize_questions,
                                 objective, oasp_mode, render, spec_url)
from agent_lanes.review import CHANGES_PREFIX, ReviewRunner
from agent_lanes.runner import LaneRunner
from agent_lanes.worker import build_prompt
from tests.test_phase2 import META, REVIEW, FakeReviewer, FakeReviewGit, ReviewHermes, verdict
from tests.test_runner_state import (GOOD, LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker,
                                     ok_outcome)

OSCAR = 6744452215
REMOTE = "git@github.com:oscarsao/oscar-hq.git"
BODY = ("Modo OASP: Fast-Track\n\n## Objetivo\nQue Oscar decida con un toque desde Telegram, sin escribir.\n\n"
        "## Criterios\n- botones\n")


# --- 1. formato de la tarjeta -----------------------------------------------------------------------

@pytest.mark.parametrize("lane,brand", [("claude-migrateam", "MigraTeam"), ("claude-oscarhq", "Píldora"),
                                        ("claude-scraper", "Píldora"), ("claude-nextjobs", "NextJobs")])
def test_brand_per_lane(lane, brand):
    assert render("running", "t_1", "T", lane, "en curso").split("\n")[1] == f"{brand} · {lane}"


def test_unknown_lane_has_no_brand():
    assert render("running", "t_1", "T", "otro", "en curso").split("\n")[1] == "otro"


@pytest.mark.parametrize("body,mode", [("Modo: spec-lite", "Spec-Lite"), ("OASP **Pitch**", "Pitch"),
                                       ("Fast Track, sin spec", "Fast-Track"), ("cto-360 trimestral", "CTO-360"),
                                       ("pitches/x.md", None), ("sin modo", None), (None, None)])
def test_oasp_mode_from_body(body, mode):
    assert oasp_mode(body) == mode


def test_objective_section_is_summarised_to_200_chars():
    assert objective(BODY) == "Que Oscar decida con un toque desde Telegram, sin escribir."
    long = "## Objetivo\n" + "- palabra " * 60 + "\n### Detalle\nno entra"
    what = objective(long)
    assert len(what) == 200 and what.endswith("…") and "no entra" not in what
    assert objective("## Contexto\nx") is None and objective(None) is None
    assert objective("### Objetivo (decidido el 28-09)\n**Uno** `dos`\n") == "Uno dos"


def test_full_card_has_at_most_7_lines_in_order():
    links = [("🗂 Tarjeta", "https://k/t"), ("📄 Spec/pitch", "https://g/s"), ("🔀 Cambios", "https://g/c")]
    text = render("done", "t_1", "Botones " * 20, "claude-migrateam",
                  "review aprobada · 3 archivos · tests OK · 1,9 $", links, body=BODY)
    lines = text.split("\n")
    assert len(lines) == 5 <= 7
    assert lines[0].startswith("✅ t_1 · Botones") and len(lines[0]) <= len("✅ t_1 · ") + 60
    assert lines[1] == "MigraTeam · claude-migrateam · Fast-Track"
    assert lines[2].startswith("Qué: Que Oscar decida")
    assert lines[3] == "review aprobada · 3 archivos · tests OK · 1,9 $"
    assert [l.split(">")[1].split("<")[0] for l in lines[4].split(" · ")] == ["🗂 Tarjeta", "📄 Spec/pitch", "🔀 Cambios"]


@pytest.mark.parametrize("path,ok", [("docs/specs/botones.md", True), ("pitches/x.md", True), ("specs/a.md", True),
                                     ("README.md", True), ("./HANDOFF.md", True), ("docs/otro.md", False),
                                     ("agent_lanes/x.py", False), ("notas.txt", False), ("../fuera.md", False)])
def test_is_spec_path(path, ok):
    assert is_spec_path(path) is ok


def test_spec_url_is_first_spec_in_lane_branch():
    files = ["agent_lanes/x.py", "docs/specs/botones telegram.md", "pitches/y.md"]
    assert spec_url(REMOTE, "t_9", files) == \
        "https://github.com/oscarsao/oscar-hq/blob/lane/t_9/docs/specs/botones%20telegram.md"
    assert spec_url(REMOTE, "t_9", ["a.py"]) is None
    assert spec_url("C:/local", "t_9", ["specs/a.md"]) is None


def test_link_builder_orders_card_spec_changes():
    run = lambda args, **kw: subprocess.CompletedProcess(args, 0, REMOTE + "\n", "")  # noqa: E731
    lb = LinkBuilder("https://k", runner=run)
    labels = [l for l, _ in lb(LANE, "t_1", changed_files=["specs/a.md"])]
    assert labels == ["🗂 Tarjeta", "📄 Spec/pitch", "🔀 Cambios"]
    assert [l for l, _ in lb(LANE, "t_1")] == ["🗂 Tarjeta", "🔀 Cambios"]
    assert lb.repo_slug(LANE) == "oscarsao/oscar-hq"


def test_runner_review_notice_links_the_spec():
    class Links:
        def __call__(self, lane, tid, *, branch=True, changed_files=None):
            self.files = changed_files
            return []

    links = Links()
    out = ok_outcome({**GOOD, "changed_files": ["specs/x.md", "a.py"]})
    LaneRunner(LANE, hermes=FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": BODY}]), git=FakeGit(),
               worker=FakeWorker([out]), verifier=FakeVerifier(), notify=FakeNotifier(), links=links).run_once()
    assert links.files == ["specs/x.md", "a.py"]


# --- 2. teclado por estado -------------------------------------------------------------------------

def _actions(spec):
    return [[b["action"] for b in row] for row in spec]


def test_keyboard_per_state():
    assert _actions(keyboard_spec("done")) == [[APPROVE, CHANGES, PARK]]
    assert _actions(keyboard_spec("blocked", block_kind="transient")) == [[RETRY, PARK]]
    for state, kind in (("running", None), ("review", None), ("changes", None), ("blocked", "needs_input"),
                        ("blocked", "capability")):
        assert keyboard_spec(state, block_kind=kind) is None
    # Sin opciones: [✅ Sí, adelante] [❌ No] por defecto (28-09), y siempre [💬 Explícame más].
    assert _actions(keyboard_spec("needs_input", questions=["¿libre?"])) == [[OPTION, OPTION], [OTHER, PARK], [EXPLAIN]]


def test_needs_input_buttons_only_for_first_question_with_star():
    qs = [{"question": "¿Qué BD?", "options": ["Supabase", "SQLite", "Postgres propio"], "recommended": 1},
          {"question": "¿Y la cola?", "options": ["Redis", "Ninguna"]}]
    spec = keyboard_spec("needs_input", questions=qs)
    assert [r[0]["text"] for r in spec[:3]] == ["1) Supabase", "⭐ 2) SQLite", "3) Postgres propio"]
    assert [r[0]["index"] for r in spec[:3]] == [0, 1, 2]
    assert _actions(spec)[3] == [OTHER, PARK] and _actions(spec)[4] == [EXPLAIN] and len(spec) == 5


def test_questions_are_normalised_retrocompatibly():
    qs = normalize_questions(["¿A?", {"question": "¿B?", "options": ["x" * 60, "y"], "recommended": 7},
                              {"question": "¿C?", "options": ["solo una"]}, {"question": ""}, 3])
    assert [q["question"] for q in qs] == ["¿A?", "¿B?", "¿C?", "3"]
    assert qs[0]["options"] == [] and len(qs[1]["options"][0]) == 40 and qs[1]["recommended"] is None
    assert qs[2]["options"] == []  # opciones fuera de contrato: solo texto libre
    assert notices.questions_block([{"question": "¿B?", "options": ["a", "b"]}]) == ["• ¿B?"]


# --- 3. callback_data corto y persistido --------------------------------------------------------------

def _desk(tmp_path, *, hermes=None, gh=None, tg=None, links="default", lanes=None):
    tg = tg or FakeTG()
    h = hermes or DeskHermes()
    if links == "default":
        links = LinkBuilder("https://k", runner=lambda a, **k: subprocess.CompletedProcess(a, 0, REMOTE, ""))
    desk = DecisionDesk(tg, CallbackStore(tmp_path / "cb"), lanes=lanes or {LANE.name: LANE},
                        hermes_for=lambda board: h, links=links, runner=gh or FakeGH(), gh_exe="gh",
                        spawn=lambda fn: fn(), now=lambda: datetime(2026, 9, 28, 10, 30))
    return desk, tg, h


def test_callback_data_is_short_and_persisted_without_the_text(tmp_path):
    desk, *_ = _desk(tmp_path)
    q = {"question": "¿Qué opción " + "larga " * 40 + "?", "options": ["ó" * 40, "b", "c", "d"], "recommended": 0}
    markup = desk.markup("needs_input", task={"id": "t_1", "title": "T", "body": BODY}, lane=LANE, questions=[q])
    datas = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert len(datas) == 7 and all(len(d.encode()) <= 64 for d in datas)
    assert all("ó" not in d for d in datas)
    token = datas[0].split(":")[0]
    assert len({d.split(":")[0] for d in datas}) == 1
    rec = json.loads((tmp_path / "cb" / f"{token}.json").read_text(encoding="utf-8"))
    assert (rec["task_id"], rec["board"], rec["lane"]) == ("t_1", "oscarhq", "claude-oscarhq")
    assert rec["buttons"][1] == {"action": OPTION, "index": 1, "text": "2) b"}


def test_no_markup_for_states_without_buttons(tmp_path):
    desk, *_ = _desk(tmp_path)
    assert desk.markup("review", task={"id": "t_1"}, lane=LANE) is None
    assert not (tmp_path / "cb").exists()


# --- fakes -------------------------------------------------------------------------------------------

class FakeTG:
    bot_id = "999"

    def __init__(self):
        self.answers, self.edits, self.markups, self.sent, self.deleted = [], [], [], [], []
        self._next = 500

    def answer_callback(self, cid, text=None, *, alert=False):
        self.answers.append((cid, text))
        return True

    def edit(self, chat_id, message_id, text, *, html=True, reply_markup=None):
        self.edits.append({"chat": chat_id, "mid": message_id, "text": text, "markup": reply_markup})
        return True

    def edit_markup(self, chat_id, message_id, reply_markup):
        self.markups.append(reply_markup)
        return True

    def send_to(self, chat_id, thread_id, text, **kw):
        self._next += 1
        self.sent.append({"chat": chat_id, "thread": thread_id, "text": text, **kw})
        return {"chat_id": str(chat_id), "thread_id": str(thread_id), "message_id": self._next}

    def delete(self, chat_id, message_id):
        self.deleted.append(message_id)
        return True


class DeskHermes:
    def __init__(self, ok=True, reopen_ok=True):
        self.calls = []
        self.ok = ok
        self.reopen_ok = reopen_ok

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text, author))
        return self.ok

    def assign(self, tid, profile):
        self.calls.append(("assign", tid, profile))
        return self.ok

    def unblock(self, tid):
        self.calls.append(("unblock", tid))
        return self.ok

    def reopen_review(self, tid):
        self.calls.append(("reopen_review", tid))
        return self.reopen_ok

    def kinds(self):
        return [c[0] for c in self.calls]


class FakeGH:
    def __init__(self, existing=None, create_rc=0):
        self.calls = []
        self.existing = existing or []
        self.create_rc = create_rc

    def __call__(self, args, **kw):
        self.calls.append(args)
        if args[1:3] == ["pr", "list"]:
            return subprocess.CompletedProcess(args, 0, json.dumps(self.existing), "")
        if args[1:3] == ["pr", "create"]:
            return subprocess.CompletedProcess(args, self.create_rc,
                                               "" if self.create_rc else "https://github.com/oscarsao/oscar-hq/pull/12\n",
                                               "boom" if self.create_rc else "")
        raise AssertionError(args)


def _press(desk, markup, n, *, user=OSCAR, cid="cq1"):
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row][n]
    desk.handle_update({"update_id": 1, "callback_query": {
        "id": cid, "from": {"id": user}, "data": data,
        "message": {"message_id": 77, "chat": {"id": -1003530490339}, "message_thread_id": 231}}})


def _done_markup(desk):
    return desk.markup("done", task={"id": "t_1", "title": "Botones en avisos", "body": BODY}, lane=LANE,
                       summary="Añade botones inline", changed_files=["specs/botones.md"])


# --- 4. solo Oscar -----------------------------------------------------------------------------------

def test_only_oscar_can_decide(tmp_path):
    gh = FakeGH()
    desk, tg, h = _desk(tmp_path, gh=gh)
    markup = _done_markup(desk)
    _press(desk, markup, 0, user=12345)
    assert tg.answers == [("cq1", NOT_OWNER)]
    assert h.calls == [] and gh.calls == [] and tg.edits == []
    _press(desk, markup, 0)  # el token no se gastó: Oscar aún puede decidir
    assert h.kinds() == ["comment"]


def test_unknown_or_forged_callback_is_ignored(tmp_path):
    desk, tg, h = _desk(tmp_path)
    for data in ("nope:0", "../../x:0", "", "abc"):
        desk.handle_update({"callback_query": {"id": "c", "from": {"id": OSCAR}, "data": data, "message": {}}})
    assert h.calls == [] and all(a[1] == "Esta decisión ya no está activa" for a in tg.answers)


# --- 5. acciones ---------------------------------------------------------------------------------------

def test_approve_opens_pr_comments_and_edits_without_buttons(tmp_path):
    gh = FakeGH()
    desk, tg, h = _desk(tmp_path, gh=gh)
    _press(desk, _done_markup(desk), 0)
    assert tg.answers[0][0] == "cq1"
    lst, create = gh.calls
    assert lst[:3] == ["gh", "pr", "list"] and "--head" in lst and lst[lst.index("--head") + 1] == "lane/t_1"
    assert create[:3] == ["gh", "pr", "create"]
    arg = lambda f: create[create.index(f) + 1]  # noqa: E731
    assert (arg("--repo"), arg("--head"), arg("--base"), arg("--title")) == \
        ("oscarsao/oscar-hq", "lane/t_1", "master", "Botones en avisos")
    body = arg("--body")
    assert "Añade botones inline" in body and "Aprobado por Oscar desde Telegram" in body
    assert "https://k/tasks/oscarhq/t_1" in body and "🤖 Generated with [Claude Code](https://claude.com/claude-code)" in body
    assert not any("merge" in a for call in gh.calls for a in call)  # nunca fusiona
    assert h.calls == [("comment", "t_1", "APROBADO-OSCAR 2026-09-28 10:30 · PR https://github.com/oscarsao/oscar-hq/pull/12",
                        OSCAR_AUTHOR)]
    edit = tg.edits[-1]
    assert edit["mid"] == 77 and edit["markup"] is None
    assert "✅ aprobada · PR #12" in edit["text"] and 'href="https://github.com/oscarsao/oscar-hq/pull/12"' in edit["text"]


def test_approve_reuses_existing_pr(tmp_path):
    gh = FakeGH(existing=[{"number": 5, "url": "https://github.com/oscarsao/oscar-hq/pull/5"}])
    desk, tg, h = _desk(tmp_path, gh=gh)
    _press(desk, _done_markup(desk), 0)
    assert [c[2] for c in gh.calls] == ["list"]
    assert "PR #5" in tg.edits[-1]["text"] and "pull/5" in h.calls[0][2]


def test_failed_approve_restores_buttons(tmp_path):
    desk, tg, h = _desk(tmp_path, gh=FakeGH(create_rc=1))
    markup = _done_markup(desk)
    _press(desk, markup, 0)
    assert h.calls == [] and "no se pudo abrir el PR" in tg.edits[-1]["text"]
    assert tg.markups[-1] == markup  # los mismos botones vuelven
    _press(desk, markup, 2, cid="cq2")  # y siguen funcionando
    assert ("assign", "t_1", "oscar") in h.calls


def test_double_tap_runs_the_action_once(tmp_path):
    gh = FakeGH()
    desk, tg, h = _desk(tmp_path, gh=gh)
    markup = _done_markup(desk)
    _press(desk, markup, 0)
    _press(desk, markup, 0, cid="cq2")
    _press(desk, markup, 2, cid="cq3")  # otro botón del mismo mensaje tampoco
    assert [c[2] for c in gh.calls].count("create") == 1 and h.kinds() == ["comment"]
    assert tg.answers[1][1] == "Esta decisión ya no está activa"


def test_park_assigns_to_oscar_and_comments(tmp_path):
    desk, tg, h = _desk(tmp_path)
    _press(desk, _done_markup(desk), 2)
    assert h.calls == [("assign", "t_1", "oscar"), ("comment", "t_1", "Aparcada por Oscar", OSCAR_AUTHOR)]
    assert "🗄 aparcada" in tg.edits[-1]["text"] and tg.edits[-1]["markup"] is None


def test_park_failure_is_reported_and_buttons_return(tmp_path):
    desk, tg, h = _desk(tmp_path, hermes=DeskHermes(ok=False))
    _press(desk, _done_markup(desk), 2)
    assert "no se pudo aparcar" in tg.edits[-1]["text"] and tg.markups


def test_retry_edits_before_unblocking(tmp_path):
    order = []

    class H(DeskHermes):
        def unblock(self, tid):
            order.append("unblock")
            return super().unblock(tid)

    class TG(FakeTG):
        def edit(self, *a, **k):
            order.append("edit")
            return super().edit(*a, **k)

    desk, tg, h = _desk(tmp_path, hermes=H(), tg=TG())
    markup = desk.markup("blocked", task={"id": "t_1", "title": "T"}, lane=LANE, block_kind="transient")
    _press(desk, markup, 0)
    assert h.calls == [("unblock", "t_1")] and order == ["edit", "unblock"]
    assert "🔄 reencolada" in tg.edits[-1]["text"]


def test_option_comments_answer_and_unblocks(tmp_path):
    desk, tg, h = _desk(tmp_path)
    q = {"question": "¿Qué BD?", "options": ["Supabase", "SQLite"], "recommended": 0}
    markup = desk.markup("needs_input", task={"id": "t_1", "title": "T"}, lane=LANE, questions=[q])
    _press(desk, markup, 1)
    assert h.calls == [("comment", "t_1", "Respuesta de Oscar: ¿Qué BD? → SQLite", OSCAR_AUTHOR), ("unblock", "t_1")]
    assert "💬 respondida: SQLite" in tg.edits[-1]["text"] and tg.edits[-1]["markup"] is None


def test_several_questions_go_in_sequence_on_the_same_card(tmp_path):
    """28-09 (t_6a2fdf4d): antes un botón respondía la 1ª y pedía "el resto con ✍️"; ahora la misma tarjeta pasa a la
    2ª con SUS botones y solo la última desbloquea (más casos en test_preguntas_secuencia.py)."""
    desk, tg, h = _desk(tmp_path)
    q = {"question": "¿Qué BD?", "options": ["Supabase", "SQLite"], "recommended": 0}
    markup = desk.markup("needs_input", task={"id": "t_1", "title": "T"}, lane=LANE, questions=[q, "¿Y la cola?"])
    _press(desk, markup, 0)
    assert h.calls == [("comment", "t_1", "Respuesta de Oscar: ¿Qué BD? → Supabase", OSCAR_AUTHOR)]  # sin unblock
    edit = tg.edits[-1]
    assert "💬 1ª: Supabase" in edit["text"] and "Pregunta 2/2" in edit["text"] and "• ¿Y la cola?" in edit["text"]
    step = edit["markup"]
    assert [b["text"] for row in step["inline_keyboard"] for b in row] == [
        "✅ Sí, adelante", "❌ No", "✍️ Otra respuesta", "🗄 Aparcar", "💬 Explícame más"]
    _press(desk, step, 2, cid="cq2")  # ✍️ en la 2ª
    _reply(desk, 501, "Redis")
    assert h.calls[1:] == [("comment", "t_1", "Respuesta de Oscar: ¿Y la cola? → Redis", OSCAR_AUTHOR),
                           ("unblock", "t_1")]
    assert "💬 respondida (2/2): Redis" in tg.edits[-1]["text"]


def test_free_reply_with_several_questions_answers_only_the_current_one(tmp_path):
    desk, tg, h = _desk(tmp_path)
    markup = desk.markup("needs_input", task={"id": "t_1", "title": "T"}, lane=LANE, questions=["¿A?", "¿B?"])
    _press(desk, markup, 2)  # 0 y 1 son [✅ Sí, adelante] [❌ No] de la 1ª pregunta; 2 = ✍️ Otra respuesta
    assert "(Pregunta 1/2)" in tg.sent[-1]["text"]
    _reply(desk, 501, "A sí")
    assert h.calls == [("comment", "t_1", "Respuesta de Oscar: ¿A? → A sí", OSCAR_AUTHOR)]  # sin unblock
    assert "Pregunta 2/2" in tg.edits[-1]["text"] and "• ¿B?" in tg.edits[-1]["text"]


def test_review_escalation_free_reply_still_answers_every_change(tmp_path):
    desk, tg, h = _desk(tmp_path)
    markup = desk.markup("needs_input", task={"id": "t_1", "title": "T"}, lane=LANE, questions=["cambio A", "cambio B"],
                         yes_no=False)
    _press(desk, markup, 0)  # ✍️ Otra respuesta
    _reply(desk, 501, "A sí, B no")
    assert h.calls[0][2] == "Respuesta de Oscar: todas las preguntas → A sí, B no" and h.calls[1] == ("unblock", "t_1")


# --- 6. force_reply ------------------------------------------------------------------------------------

def _reply(desk, prompt_mid, text, user=OSCAR):
    desk.handle_update({"update_id": 2, "message": {
        "message_id": 900, "from": {"id": user}, "chat": {"id": -1003530490339}, "text": text,
        "reply_to_message": {"message_id": prompt_mid}}})


def test_request_changes_asks_then_comments_and_reopens(tmp_path):
    desk, tg, h = _desk(tmp_path)
    _press(desk, _done_markup(desk), 1)
    ask = tg.sent[-1]
    assert ask["text"] == "¿Qué cambio pides para t_1?" and ask["reply_markup"]["force_reply"] is True
    assert (ask["chat"], ask["thread"], ask["reply_to"]) == ("-1003530490339", "231", 77)
    assert h.calls == []  # hasta que Oscar responda no pasa nada
    _reply(desk, ask_mid := 501, "Renombra el botón a Aprobar PR")
    assert ask_mid == 501
    comment, reopen = h.calls
    assert comment[3] == REVIEW_AUTHOR and comment[2].startswith(CHANGES_PREFIX)
    assert "Renombra el botón a Aprobar PR" in comment[2] and reopen == ("reopen_review", "t_1")
    assert "🔁 cambios pedidos" in tg.edits[-1]["text"]
    assert set(tg.deleted) == {501, 900}  # la pregunta y la respuesta no se quedan en el hilo


def test_request_changes_on_done_task_is_annotated_when_reopen_fails(tmp_path):
    desk, tg, h = _desk(tmp_path, hermes=DeskHermes(reopen_ok=False))
    _press(desk, _done_markup(desk), 1)
    _reply(desk, 501, "otro cambio")
    assert h.kinds() == ["comment", "reopen_review"]
    assert "cambios anotados" in tg.edits[-1]["text"]


def test_other_answer_via_force_reply(tmp_path):
    desk, tg, h = _desk(tmp_path)
    markup = desk.markup("needs_input", task={"id": "t_1", "title": "T"}, lane=LANE, questions=["¿Qué nombre?"])
    _press(desk, markup, 2)  # ✍️ Otra respuesta, tras [✅ Sí, adelante] [❌ No]
    assert tg.sent[-1]["text"].startswith("Tu respuesta para t_1:") and "¿Qué nombre?" in tg.sent[-1]["text"]
    _reply(desk, 501, "Carriles")
    assert h.calls == [("comment", "t_1", "Respuesta de Oscar: ¿Qué nombre? → Carriles", OSCAR_AUTHOR),
                       ("unblock", "t_1")]


def test_replies_from_others_or_to_other_messages_are_ignored(tmp_path):
    desk, tg, h = _desk(tmp_path)
    _press(desk, _done_markup(desk), 1)
    _reply(desk, 501, "yo también opino", user=111)
    _reply(desk, 12345, "respuesta suelta")
    assert h.calls == []
    _reply(desk, 501, "ahora sí")  # la pregunta sigue pendiente para Oscar
    assert h.kinds() == ["comment", "reopen_review"]


# --- 7. integración con los carriles y fallback ---------------------------------------------------------

class MarkupNotifier(FakeNotifier):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.markups = []

    def send(self, text, target=None, lane_target=None, *, silent=False, reply_markup=None):
        self.markups.append(reply_markup)
        return super().send(text, target, lane_target, silent=silent)

    def edit(self, chat_id, message_id, text, reply_markup=None):
        return super().edit(chat_id, message_id, text)


def test_runner_needs_input_alert_carries_buttons_when_desk_is_active(tmp_path):
    desk, *_ = _desk(tmp_path)
    q = {"question": "¿A o B?", "options": ["A", "B"], "recommended": 1}
    out = ok_outcome({**GOOD, "status": "needs_input", "questions": [q]})
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": BODY}])
    n = MarkupNotifier()
    LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([out]), verifier=FakeVerifier(), notify=n,
               decisions=desk).run_once()
    texts = [b["text"] for row in n.markups[-1]["inline_keyboard"] for b in row]
    assert texts == ["1) A", "⭐ 2) B", "✍️ Otra respuesta", "🗄 Aparcar", "💬 Explícame más"]
    assert "responde con un botón" in n.msgs[-1]
    block = [c for c in h.calls if c[0] == "block"][0]
    assert "¿A o B? [1) A / 2) B (recomendada)]" in block[3]  # la tarjeta ve las opciones (dicts no rompen)


def test_runner_without_lanes_bot_sends_no_buttons():
    out = ok_outcome({**GOOD, "status": "needs_input", "questions": [{"question": "¿A?", "options": ["x", "y"]}]})
    n = MarkupNotifier()
    LaneRunner(LANE, hermes=FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}]), git=FakeGit(),
               worker=FakeWorker([out]), verifier=FakeVerifier(), notify=n).run_once()
    assert all(m is None for m in n.markups) and "responde en este hilo o a Hermes" in n.msgs[-1]
    # y la notificación base (FakeNotifier sin reply_markup) sigue funcionando: publish no pasa el argumento
    plain = FakeNotifier()
    TaskNotices(plain).publish("t_1", "a")
    assert plain.msgs == ["a"]


def test_transient_block_gets_retry_only_if_the_block_succeeded(tmp_path):
    desk, *_ = _desk(tmp_path)

    class NoBlock(FakeHermes):
        def block(self, *a):
            super().block(*a)
            return False

    for hermes, expect in ((FakeHermes, True), (NoBlock, False)):
        n = MarkupNotifier()
        h = hermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
        LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome({**GOOD, "status": "failed"})]),
                   verifier=FakeVerifier(), notify=n, decisions=desk).run_once()
        assert (n.markups[-1] is not None) is expect


def test_review_approved_notice_has_decision_buttons(tmp_path):
    desk, *_ = _desk(tmp_path)
    rh = ReviewHermes([{"id": "t_1", "title": "T", "body": BODY, "assignee": "claude-oscarhq", "status": "review"}],
                      meta={**META, "changed_files": ["specs/a.md"]})
    n = MarkupNotifier()
    ReviewRunner(REVIEW, {"claude-oscarhq": LANE}, hermes_for=lambda b: rh, git=FakeReviewGit(),
                 reviewer=FakeReviewer([verdict("approve")]), verifier=FakeVerifier(), notify=n,
                 decisions=desk).run_once()
    texts = [b["text"] for row in n.markups[-1]["inline_keyboard"] for b in row]
    assert texts == ["✅ Aprobar", "🔁 Pedir cambios", "🗄 Aparcar"]
    rec = json.loads(next((tmp_path / "cb").glob("*.json")).read_text(encoding="utf-8"))
    assert rec["changed_files"] == ["specs/a.md"] and rec["summary"]


def test_switching_bot_sends_a_new_message_and_records_the_bot(tmp_path):
    class Bot(FakeNotifier):
        bot_id = "222"

    store = MessageStore(tmp_path)
    store.put("t_1", {"chat_id": "-1", "thread_id": "5", "message_id": 77, "bot": "111"})
    n = Bot()
    TaskNotices(n, store).publish("t_1", "nuevo")
    assert n.edits == [] and n.deleted == [] and n.msgs == ["nuevo"]
    assert store.get("t_1")["bot"] == "222"
    TaskNotices(n, store).publish("t_1", "editado")  # ya es suyo: edita
    assert n.edits == [(101, "editado")]


def test_worker_prompt_includes_oscar_answers():
    task = {"id": "t_1", "title": "T", "body": "B",
            "oscar_answers": ["Respuesta de Oscar: ¿Qué BD? → SQLite"]}
    prompt = build_prompt(task, LANE)
    assert "Decisiones de Oscar" in prompt and "¿Qué BD? → SQLite" in prompt
    assert "Decisiones de Oscar" not in build_prompt({"id": "t_1", "title": "T", "body": "B"}, LANE)


def test_runner_passes_oscar_answers_to_worker():
    class H(FakeHermes):
        def oscar_answers(self, tid):
            return ["Respuesta de Oscar: ¿A? → B"]

    seen = {}

    class W(FakeWorker):
        def run(self, lane, task, cwd, session_id, timeout):
            seen.update(task)
            return super().run(lane, task, cwd, session_id, timeout)

    LaneRunner(LANE, hermes=H(tasks=[{"id": "t_1", "title": "T", "body": "B"}]), git=FakeGit(),
               worker=W([ok_outcome()]), verifier=FakeVerifier(), notify=FakeNotifier()).run_once()
    assert seen["oscar_answers"] == ["Respuesta de Oscar: ¿A? → B"]


# --- 8. CLI de hermes y git ------------------------------------------------------------------------------

def _capture(stdout=""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    return calls, run


def test_hermes_assign_unblock_args():
    calls, run = _capture()
    h = HermesCLI("oscarhq", exe="hermes", runner=run)
    assert h.assign("t_1", "oscar") and h.unblock("t_1")
    assert calls[0] == ["hermes", "kanban", "--board", "oscarhq", "assign", "t_1", "oscar"]
    assert calls[1] == ["hermes", "kanban", "--board", "oscarhq", "unblock", "t_1"]


def test_hermes_oscar_answers_filters_author_and_prefix():
    show = {"comments": [{"author": OSCAR_AUTHOR, "body": "Respuesta de Oscar: ¿A? → B"},
                         {"author": OSCAR_AUTHOR, "body": "Aparcada por Oscar"},
                         {"author": "otro", "body": "Respuesta de Oscar: falsa"},
                         {"author": REVIEW_AUTHOR, "body": "CAMBIOS x"}]}
    _, run = _capture(json.dumps(show))
    assert HermesCLI("oscarhq", exe="hermes", runner=run).oscar_answers("t_1") == ["Respuesta de Oscar: ¿A? → B"]


def test_prepare_worktree_recreates_branch_from_remote_lane(tmp_path):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "")  # la rama local se borró tras done
        return subprocess.CompletedProcess(cmd, 0, "", "")

    lane = Lane(**{**LANE.__dict__, "worktree_root": str(tmp_path)})
    GitOps(runner=run).prepare_worktree(lane, "t_1")
    add = [c for c in calls if "worktree" in c][0]
    assert add[-3:] == ["-b", "lane/t_1", "origin/lane/t_1"]

    def run_no_remote(cmd, **kw):
        calls.append(cmd)
        rc = 1 if ("rev-parse" in cmd or "+refs/heads/lane/t_2:refs/remotes/origin/lane/t_2" in cmd) else 0
        return subprocess.CompletedProcess(cmd, rc, "", "")

    GitOps(runner=run_no_remote).prepare_worktree(lane, "t_2")
    assert calls[-1][-3:] == ["-b", "lane/t_2", "origin/master"]


# --- 9. esquema retrocompatible ---------------------------------------------------------------------------

SCHEMA = json.loads((ROOT / "contract" / "result.schema.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("questions,valid", [
    ([], True),
    ([{"question": "¿A?", "options": ["x", "y"], "recommended": 0}], True),
    ([{"question": "¿A?", "options": ["x", "y", "z", "w"], "recommended": 3}], True),
    # 28-09: toda pregunta lleva 2-4 opciones y una recomendada (Oscar decide con un toque). Las preguntas sueltas
    # de workers viejos se siguen aceptando al leerlas (normalize_questions + [✅ Sí, adelante] [❌ No]).
    (["¿A?"], False),
    ([{"question": "¿A?", "options": ["x", "y", "z", "w"]}], False),
    ([{"question": "¿A?", "options": ["solo"]}], False),
    ([{"question": "¿A?", "options": ["a", "b", "c", "d", "e"]}], False),
    ([{"question": "¿A?", "options": ["x" * 41, "y"]}], False),
    ([{"question": "¿A?", "options": ["x", "y"], "extra": 1}], False),
    ([{"options": ["x", "y"]}], False),
])
def test_result_schema_questions(questions, valid):
    doc = {**GOOD, "questions": questions}
    if valid:
        jsonschema.validate(doc, SCHEMA)
    else:
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(doc, SCHEMA)


def test_roles_ask_for_short_options_with_recommendation():
    for role in ("roles/implementador.md", "roles/revisor.md"):
        assert "2-4 opciones cortas y marca la recomendada" in (ROOT / role).read_text(encoding="utf-8")


def test_roles_escalate_only_business_decisions():
    for role in ("roles/implementador.md", "roles/revisor.md"):
        text = (ROOT / role).read_text(encoding="utf-8")
        assert "decisiones TÉCNICAS o de implementación en las que ya tengas una opción recomendada NO se escalan" in text
        assert "Solo pregunta a Oscar decisiones de NEGOCIO o de producto" in text
        assert "UNA" in text and "no 5 preguntas sueltas" in text


# --- 10. Telegram: límites, primitivas y escucha ----------------------------------------------------------

def test_rendered_text_never_exceeds_telegram_limit():
    bullets = ["• " + "x" * 280] * 50
    text = render("needs_input", "t_1", "T" * 500, "claude-oscarhq", "s" * 300,
                  [("🗂 Tarjeta", "https://k/" + "a" * 500)], bullets, body="## Objetivo\n" + "y" * 5000)
    assert len(text) <= 4000 < 4096 and text.count("<a href") == 1  # recorta viñetas, nunca el enlace


class Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def _capture_api(monkeypatch, result):
    seen = []

    def urlopen(req, timeout):
        seen.append({"url": req.full_url, "payload": json.loads(req.data), "timeout": timeout})
        method = req.full_url.rsplit("/", 1)[1]
        return Resp({"ok": True, "result": [] if method == "getUpdates" else result})

    monkeypatch.setattr(telegram.urllib.request, "urlopen", urlopen)
    return seen


def test_send_with_inline_keyboard_and_edit_clearing_buttons(monkeypatch):
    seen = _capture_api(monkeypatch, {"message_id": 5})
    n = telegram.TelegramNotifier("123456:SECRET", "-100", "5")
    kb = {"inline_keyboard": [[{"text": "✅ Aprobar", "callback_data": "abc:0"}]]}
    assert n.send("hola", reply_markup=kb, silent=True) == {"chat_id": "-100", "thread_id": "5", "message_id": 5}
    assert seen[0]["payload"]["reply_markup"] == kb and seen[0]["payload"]["message_thread_id"] == 5
    n.edit("-100", 5, "x")
    assert "reply_markup" not in seen[1]["payload"]  # sin reply_markup Telegram quita los botones
    n.edit_markup("-100", 5, None)
    assert seen[2]["url"].endswith("/editMessageReplyMarkup") and seen[2]["payload"]["reply_markup"] == {
        "inline_keyboard": []}
    assert n.bot_id == "123456" and "SECRET" not in repr(n)


def test_force_reply_answer_callback_and_get_updates(monkeypatch):
    seen = _capture_api(monkeypatch, {"message_id": 6})
    n = telegram.TelegramNotifier("123456:SECRET", "-100")
    n.send_to("-100", "231", "¿Qué cambio?", reply_to=77, html=False, reply_markup={"force_reply": True})
    p = seen[0]["payload"]
    assert p["reply_parameters"]["message_id"] == 77 and p["message_thread_id"] == 231 and "parse_mode" not in p
    n.answer_callback("cq", "Solo Oscar puede decidir", alert=True)
    assert seen[1]["payload"] == {"callback_query_id": "cq", "text": "Solo Oscar puede decidir", "show_alert": True}
    assert n.get_updates(42, timeout=25) == []
    assert seen[2]["payload"] == {"timeout": 25, "allowed_updates": ["callback_query", "message"], "offset": 42}
    assert seen[2]["timeout"] > 25  # el HTTP espera más que el long-polling


def test_get_updates_errors_never_leak_the_token(monkeypatch):
    def urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 409, "Conflict", {},
                                     io.BytesIO(b'{"ok":false,"description":"Conflict: terminated by other getUpdates"}'))

    monkeypatch.setattr(telegram.urllib.request, "urlopen", urlopen)
    with pytest.raises(telegram.TelegramAPIError) as err:
        telegram.TelegramNotifier("123456:SECRET", "-100").get_updates(None)
    assert "SECRET" not in str(err.value) and "409" in str(err.value)


def test_poller_persists_offset_and_survives_a_broken_update(tmp_path):
    class TG:
        def __init__(self):
            self.offsets = []

        def get_updates(self, offset, timeout):
            self.offsets.append(offset)
            return [{"update_id": 11, "message": {}}, {"update_id": 10, "callback_query": {}}]

    class Desk:
        def __init__(self):
            self.seen = []

        def handle_update(self, u):
            self.seen.append(u["update_id"])
            if u["update_id"] == 10:
                raise RuntimeError("roto")

    tg, desk = TG(), Desk()
    poller = UpdatePoller(tg, desk, tmp_path / "tg_offset")
    assert poller.poll_once() == 2 and desk.seen == [10, 11]
    assert (tmp_path / "tg_offset").read_text() == "12"
    UpdatePoller(tg, desk, tmp_path / "tg_offset").poll_once()  # reinicio: sigue desde el offset guardado
    assert tg.offsets == [None, 12]


def test_each_option_button_stores_its_own_text_not_the_default(tmp_path):
    """Regresión t_a2972b36/t_5fbf77bc: el botón N guarda el texto de SU opción, nunca "Sí, adelante"."""
    opts = ["Supabase", "SQLite", "Redis"]
    for n, expected in enumerate(opts):
        desk, tg, h = _desk(tmp_path / f"d{n}")
        q = {"question": "¿Qué BD?", "options": opts, "recommended": 0}
        markup = desk.markup("needs_input", task={"id": "t_1", "title": "T"}, lane=LANE, questions=[q])
        _press(desk, markup, n)
        assert h.calls[0] == ("comment", "t_1", f"Respuesta de Oscar: ¿Qué BD? → {expected}", OSCAR_AUTHOR)
        assert "Sí, adelante" not in h.calls[0][2]
