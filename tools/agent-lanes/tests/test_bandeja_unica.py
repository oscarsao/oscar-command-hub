"""Bandeja única de decisiones (28-09): toda decisión llega también al DM de Oscar como copia sincronizada,
/decisiones y los recordatorios cuentan además sus tarjetas [DECISIÓN…]/[SEMANA…]/[IDEA…], preguntas sin opciones
con [✅ Sí, adelante] [❌ No], botón [💬 Explícame más] y `for_oscar` (explicación llana) en los avisos."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import jsonschema
import pytest

from agent_lanes import explain
from agent_lanes.config import ROOT, Lane
from agent_lanes.decisions import EXPLAIN_FAILED, NOT_OWNER
from agent_lanes.hermes import ANSWER_PREFIX, OSCAR_AUTHOR
from agent_lanes.notices import MessageStore, TaskNotices, is_decision_card, render, with_default_options
from agent_lanes.reminders import Reminders, reminder_text
from agent_lanes.renotify import split_for_oscar
from agent_lanes.review import ReviewRunner
from agent_lanes.runner import FOR_OSCAR_PREFIX, LaneRunner
from tests.test_commands import BODY, GESTION, LANES, MIG, OSCAR, Bot, center, command, needs, press, texts
from tests.test_reminders_questions import DMBot, at
from tests.test_runner_state import GOOD, FakeGit, FakeHermes, FakeVerifier, FakeWorker, ok_outcome

DM = str(OSCAR)
Q = [{"question": "¿Publicar ya?", "options": ["Sí", "No"], "recommended": 0}]
TOPIC_BODY = f"Origen-Telegram: chat={GESTION} thread=230\n\n{BODY}"
DM_BODY = f"Origen-Telegram: chat={DM} thread=0\n\n{BODY}"


def _runner(desk, bot, messages, lane=MIG, hermes=None):
    return LaneRunner(lane, hermes=hermes, git=None, worker=None, verifier=None, notify=bot, messages=messages,
                      decisions=desk)


def _ask(r, tid, body, questions=Q, **kw):
    r.notify("needs_input", tid, {"title": f"Tarea {tid}", "body": body}, "necesita tu decisión", alert=True,
             bullets=["• ¿Publicar ya?"], buttons=True, questions=questions, **kw)


def _in_dm(bot):
    return [m for m in bot.sent if m["chat_id"] == DM]


# --- A. toda decisión, también en el DM ---------------------------------------------------------------

def test_decision_asked_from_a_topic_is_mirrored_to_the_dm_with_the_same_buttons(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {})
    _ask(_runner(desk, bot, messages), "t_aaaaaaa1", TOPIC_BODY)
    topic, dm = bot.sent
    assert (topic["chat_id"], topic["thread_id"]) == (GESTION, "230")
    assert dm["chat_id"] == DM and dm["text"] == topic["text"] and dm["markup"] == topic["markup"]
    assert [m["message_id"] for m in messages.all_messages("t_aaaaaaa1")] == [topic["message_id"], dm["message_id"]]


def test_non_decision_alerts_from_a_topic_stay_in_the_topic(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {})
    r = _runner(desk, bot, messages)
    r.notify("blocked", "t_aaaaaaa1", {"title": "T", "body": TOPIC_BODY}, "bloqueada", alert=True)
    r.notify("running", "t_bbbbbbb2", {"title": "T", "body": TOPIC_BODY}, "en curso")
    r.notify("done", "t_ccccccc3", {"title": "T", "body": TOPIC_BODY}, "lista", alert=True)
    assert _in_dm(bot) == []


def test_decision_asked_from_the_dm_is_not_duplicated(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {})
    _ask(_runner(desk, bot, messages), "t_aaaaaaa1", DM_BODY)
    assert len(_in_dm(bot)) == 1 and len(bot.sent) == 2  # tema de la marca + una sola copia en el DM


def test_no_copy_when_the_notice_itself_lands_in_the_dm(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {})
    dm_lane = Lane(name="claude-migrateam", board="migrateam", repo="C:/mig", base="master", telegram=(DM, "0"))
    _ask(_runner(desk, bot, messages, lane=dm_lane), "t_aaaaaaa1", TOPIC_BODY)
    assert len(bot.sent) == 1 and bot.sent[0]["chat_id"] == DM
    assert "mirrors" not in messages.get("t_aaaaaaa1")


def test_task_notices_skip_the_mirror_when_the_main_message_is_already_there():
    bot, store = Bot(), MessageStore()
    TaskNotices(bot, store).publish("t_1", "❓ t_1", None, (DM, "0"), alert=True, mirror_to=DM)
    assert len(bot.sent) == 1


def test_review_escalation_is_also_mirrored_to_the_dm(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {})
    rr = ReviewRunner(LANES["review"], LANES, hermes_for=lambda b: h, git=None, reviewer=None, verifier=None,
                      notify=bot, messages=messages, decisions=desk)
    rr.notify("needs_input", {"id": "t_aaaaaaa1", "title": "T", "body": TOPIC_BODY, "assignee": MIG.name},
              "3ª petición de cambios: decides tú", alert=True, buttons=True, questions=["Cambia el botón"])
    assert len(_in_dm(bot)) == 1 and bot.sent[0]["chat_id"] == GESTION


def test_deciding_in_the_dm_copy_edits_the_topic_notice(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", "x")})
    _ask(_runner(desk, bot, messages, hermes=h), "t_aaaaaaa1", TOPIC_BODY)
    topic, dm = bot.sent
    press(desk, dm, 0)
    assert ("comment", "t_aaaaaaa1", f"{ANSWER_PREFIX} ¿Publicar ya? → Sí", OSCAR_AUTHOR) in h.calls
    assert ("unblock", "t_aaaaaaa1") in h.calls
    topic_edit = [e for e in bot.edits if e["mid"] == topic["message_id"]][-1]
    assert topic_edit["chat"] == GESTION and topic_edit["text"].startswith("💬 t_aaaaaaa1") and topic_edit["markup"] is None
    press(desk, topic, 0, cid="tarde")  # el botón del tema ya no vale
    assert bot.answers[-1] == "Esta decisión ya no está activa"


def test_runner_needs_input_from_a_topic_reaches_the_dm_end_to_end(tmp_path):
    cc, desk, bot, _, messages = center(tmp_path, {})
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": TOPIC_BODY}])
    out = ok_outcome({**GOOD, "status": "needs_input", "questions": Q})
    LaneRunner(MIG, hermes=h, git=FakeGit(), worker=FakeWorker([out]), verifier=FakeVerifier(), notify=bot,
               messages=messages, decisions=desk).run_once()
    running, topic, dm = bot.sent
    assert running["chat_id"] == GESTION and topic["chat_id"] == GESTION and dm["chat_id"] == DM
    assert dm["markup"] == topic["markup"]


# --- B. /decisiones: tus tarjetas de decisión -----------------------------------------------------------

def card(tid, title, *, status="ready", assignee="oscar", created_at=1000.0):
    return {"task": {"id": tid, "title": title, "assignee": assignee, "status": status, "created_at": created_at}}


CARDS = {
    "t_d0000001": card("t_d0000001", "[DECISIÓN] Precio del plan Pro", created_at=1000.0 + 3600),
    "t_d0000002": card("t_d0000002", "Revisar DPA [SEMANA 40]", status="blocked", created_at=1000.0),
    "t_d0000003": card("t_d0000003", "Llamar a la gestoría"),                           # sin etiqueta
    "t_d0000004": card("t_d0000004", "[IDEA] Webinar", assignee="claude-oscarhq"),      # no es de Oscar
    "t_d0000005": card("t_d0000005", "[DECISIÓN] Ya hecha", status="done"),            # no pendiente
}


def test_decisiones_lists_oscar_decision_cards_when_no_agent_asks(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, dict(CARDS))
    command(desk, "/decisiones")
    [msg] = bot.sent
    text = msg["text"]
    assert text.startswith("📌 <b>Tus tarjetas de decisión (2)</b>") and "Nada pendiente" not in text
    # más antigua primero, enlazada al panel, título y antigüedad; una sola vez aunque 3 tableros compartan kanban
    assert text.index("t_d0000002") < text.index("t_d0000001")
    assert text.count("t_d0000001") == 2  # texto del enlace + URL
    assert '<a href="https://k/tasks/' in text and "Precio del plan Pro" in text and "hace 30 h" in text
    for tid in ("t_d0000003", "t_d0000004", "t_d0000005"):
        assert tid not in text
    assert msg["markup"] is None  # sin botones en v1


def test_decisiones_shows_agent_questions_first_then_the_cards(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", "El worker necesita decisión:\n- ¿Publicar ya? [1) Sí (recomendada) / 2) No]"),
             **CARDS}
    cc, desk, bot, h, messages = center(tmp_path, shows)
    command(desk, "/decisiones")
    assert bot.sent[0]["text"].startswith("❓ 1 decisión")
    assert bot.sent[1]["text"].startswith("❓ t_aaaaaaa1")
    assert bot.sent[-1]["text"].startswith("📌 <b>Tus tarjetas de decisión (2)</b>")


def test_decisiones_in_a_brand_topic_only_shows_that_brand_board(tmp_path):
    boards = {"migrateam": {"t_d0000001": card("t_d0000001", "[DECISIÓN] MigraTeam")},
              "default": {"t_d0000002": card("t_d0000002", "[DECISIÓN] General")}}
    cc, desk, bot, h, messages = center(tmp_path, {})
    cc.hermes_for = lambda b: SimpleNamespace(list_status=lambda a, s: [
        {"id": t, **c["task"]} for t, c in boards.get(b, {}).items() if c["task"]["status"] == s])
    assert [c["task"]["id"] for c in cc.decision_cards("MigraTeam")] == ["t_d0000001"]
    assert [c["task"]["id"] for c in cc.decision_cards()] == ["t_d0000001", "t_d0000002"]
    assert cc.decision_cards("Píldora") == []


def test_nothing_pending_only_when_both_lists_are_empty(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {"t_d0000003": CARDS["t_d0000003"]})
    command(desk, "/decisiones")
    # t_d0000003 no es una decisión (sin etiqueta), pero se cuenta en la línea 📋 con enlace al panel
    assert bot.sent[-1]["text"] == ('Nada pendiente de ti 🎉\n📋 1 tarjeta más a tu nombre sin etiqueta · '
                                    '<a href="https://k/">panel</a>')


def test_cards_text_never_cuts_the_html(tmp_path):
    cc, *_ = center(tmp_path, {})
    many = [{"board": "default", "task": {"id": f"t_{i:08x}", "title": "[DECISIÓN] " + "x" * 80}, "since": 1.0}
            for i in range(200)]
    text = cc.cards_text(many, 1000.0)
    assert len(text) <= 4000 and text.endswith("más en el panel") and text.count("<a ") == text.count("</a>")


def test_is_decision_card_is_the_brief_rule():
    assert is_decision_card({"assignee": "oscar", "status": "ready", "title": "Algo · DECISIÓN]"})
    assert not is_decision_card({"assignee": "oscar", "status": "todo", "title": "[DECISIÓN] x"})


# --- C. recordatorios ---------------------------------------------------------------------------------

@pytest.mark.parametrize("n,cards,text", [
    (2, 20, "Tienes 2 preguntas de agentes (la más antigua hace 5 h) y 20 tarjetas de decisión · /decisiones"),
    (1, 1, "Tienes 1 pregunta de agentes (la más antigua hace 5 h) y 1 tarjeta de decisión · /decisiones"),
    (0, 3, "Tienes 3 tarjetas de decisión · /decisiones"),
])
def test_reminder_text_counts_both(n, cards, text):
    assert reminder_text(n, 5, cards) == text


def test_reminder_is_sent_with_only_decision_cards(tmp_path):
    clock = [at(28, 18, 5)]
    bot = DMBot()
    rem = Reminders(bot, DM, lambda: [], tmp_path / "r.json", now=lambda: clock[0], cards=lambda: [1, 2])
    assert rem.tick() == "Tienes 2 tarjetas de decisión · /decisiones"
    assert bot.sent == [(DM, "Tienes 2 tarjetas de decisión · /decisiones", False)]


def test_reminder_survives_a_failing_cards_source(tmp_path):
    clock = [at(28, 13, 0)]
    pending = [SimpleNamespace(since=clock[0].timestamp() - 3600)]

    def boom():
        raise RuntimeError("hermes caído")
    rem = Reminders(DMBot(), DM, lambda: pending, tmp_path / "r.json", now=lambda: clock[0], cards=boom)
    assert rem.tick() == "Tienes 1 pregunta de agentes (la más antigua hace 1 h) · /decisiones"


# --- D. preguntas sin opciones: [✅ Sí, adelante] [❌ No] ------------------------------------------------

def test_question_without_options_gets_yes_no_buttons_and_the_answer_goes_back(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", "x")})
    _ask(_runner(desk, bot, messages, hermes=h), "t_aaaaaaa1", TOPIC_BODY,
         questions=["¿Doy luz verde para implementar la spec tal cual?"])
    topic = bot.sent[0]
    assert texts(topic["markup"]) == ["✅ Sí, adelante", "❌ No", "✍️ Otra respuesta", "🗄 Aparcar", "💬 Explícame más"]
    press(desk, topic, 0)
    assert h.calls[:2] == [("comment", "t_aaaaaaa1",
                            f"{ANSWER_PREFIX} ¿Doy luz verde para implementar la spec tal cual? → Sí, adelante",
                            OSCAR_AUTHOR), ("unblock", "t_aaaaaaa1")]


def test_default_options_only_fill_questions_without_options():
    qs = with_default_options(["¿A?", {"question": "¿B?", "options": ["x", "y"], "recommended": 1}])
    assert qs[0]["options"] == ["Sí, adelante", "No"] and qs[0]["recommended"] is None and qs[0]["default_options"]
    assert qs[1] == {"question": "¿B?", "options": ["x", "y"], "recommended": 1}


# --- E. [💬 Explícame más] ------------------------------------------------------------------------------

def test_explain_replies_to_the_notice_and_keeps_the_other_buttons_alive(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", "x")})
    seen = []
    desk.explainer = lambda rec: seen.append(rec) or "Decides si se publica ya. Recomienda que sí."
    _ask(_runner(desk, bot, messages, hermes=h), "t_aaaaaaa1", TOPIC_BODY, summary="Spec lista en specs/x.md",
         for_oscar="Necesito tu OK para publicar.")
    topic = bot.sent[0]
    press(desk, topic, texts(topic["markup"]).index("💬 Explícame más"))
    assert bot.answers[-1] == "Te lo explico en un momento"
    reply = bot.sent[-1]
    assert reply["chat_id"] == GESTION and reply["thread_id"] == "230"
    assert reply["text"] == "💬 t_aaaaaaa1 · Decides si se publica ya. Recomienda que sí."
    rec = seen[0]
    assert rec["questions"][0]["question"] == "¿Publicar ya?" and rec["summary"] == "Spec lista en specs/x.md"
    assert rec["for_oscar"] == "Necesito tu OK para publicar." and rec["body"] == TOPIC_BODY
    assert h.calls == []  # explicar no decide nada
    press(desk, topic, 0, cid="despues")  # la opción sigue valiendo
    assert ("unblock", "t_aaaaaaa1") in h.calls


def test_explain_failure_and_non_owner(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", "x")})
    desk.explainer = lambda rec: None
    _ask(_runner(desk, bot, messages, hermes=h), "t_aaaaaaa1", TOPIC_BODY)
    topic = bot.sent[0]
    n = texts(topic["markup"]).index("💬 Explícame más")
    press(desk, topic, n, user=111)
    assert bot.answers[-1] == NOT_OWNER
    press(desk, topic, n)
    assert bot.sent[-1]["text"] == EXPLAIN_FAILED


class FakeRun:
    def __init__(self, stdout="", rc=0, exc=None):
        self.stdout, self.rc, self.exc, self.calls = stdout, rc, exc, []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.exc:
            raise self.exc
        return subprocess.CompletedProcess(args, self.rc, self.stdout, "")


def test_explain_is_a_cheap_isolated_claude_call():
    run = FakeRun(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                              "result": "  Explicación llana.  "}))
    rec = {"task_id": "t_1", "title": "Spec OCR", "body": "## Objetivo\nOCR de EEUU", "summary": "spec lista",
           "questions": [{"question": "¿Luz verde?", "options": ["Sí, adelante", "No"], "recommended": None}]}
    assert explain.explain(rec, runner=run) == "Explicación llana."
    args, kw = run.calls[0]
    assert args[:4] == ["claude", "-p", "--model", "haiku"]
    assert args[args.index("--tools") + 1] == "" and "--strict-mcp-config" in args
    assert args[args.index("--mcp-config") + 1] == '{"mcpServers":{}}' and "--no-session-persistence" in args
    assert float(args[args.index("--max-budget-usd") + 1]) <= 0.1 and kw["timeout"] <= 120
    assert "¿Luz verde?" in kw["input"] and "spec lista" in kw["input"] and "OCR de EEUU" in kw["input"]


@pytest.mark.parametrize("run", [FakeRun("no es json", rc=1), FakeRun(exc=subprocess.TimeoutExpired("claude", 90)),
                                 FakeRun(json.dumps({"subtype": "error_max_budget_usd", "is_error": True}))])
def test_explain_returns_none_on_failure(run):
    assert explain.explain({"task_id": "t_1"}, runner=run) is None


# --- F. for_oscar: descripción llana en vez del "Qué:" técnico ---------------------------------------------

def test_render_prefers_for_oscar_over_the_technical_objective():
    text = render("needs_input", "t_1", "T", "claude-migrateam", "necesita tu decisión", body=BODY,
                  for_oscar="Necesito tu OK para publicar la web nueva.")
    assert "Para ti: Necesito tu OK para publicar la web nueva." in text and "Qué:" not in text
    assert "Qué: Algo concreto." in render("needs_input", "t_1", "T", "claude-migrateam", "x", body=BODY)


def test_worker_for_oscar_reaches_the_notice_the_card_and_renotify(tmp_path):
    cc, desk, bot, _, messages = center(tmp_path, {})
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": TOPIC_BODY}])
    out = ok_outcome({**GOOD, "status": "needs_input", "questions": Q,
                      "for_oscar": "Necesito saber si publicamos ya."})
    LaneRunner(MIG, hermes=h, git=FakeGit(), worker=FakeWorker([out]), verifier=FakeVerifier(), notify=bot,
               messages=messages, decisions=desk).run_once()
    assert "Para ti: Necesito saber si publicamos ya." in bot.sent[1]["text"]
    reason = [c for c in h.calls if c[0] == "block"][0][3]
    assert f"{FOR_OSCAR_PREFIX} Necesito saber si publicamos ya." in reason
    rest, plain = split_for_oscar(reason)
    assert plain == "Necesito saber si publicamos ya." and FOR_OSCAR_PREFIX not in rest


def test_renotify_and_decisiones_use_for_oscar_from_the_block_reason(tmp_path):
    reason = ("El worker necesita decisión:\n- ¿Publicar ya? [1) Sí (recomendada) / 2) No]\n"
              f"{FOR_OSCAR_PREFIX} Necesito saber si publicamos ya.")
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", reason)})
    [p] = cc.pending_decisions()
    assert p.for_oscar == "Necesito saber si publicamos ya." and len(p.questions) == 1
    command(desk, "/decisiones")
    assert "Para ti: Necesito saber si publicamos ya." in bot.sent[1]["text"]


SCHEMA = json.loads((ROOT / "contract" / "result.schema.json").read_text(encoding="utf-8"))


def test_schema_accepts_short_for_oscar_and_rejects_long():
    jsonschema.validate({**GOOD, "for_oscar": "Necesito tu OK."}, SCHEMA)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**GOOD, "for_oscar": "x" * 301}, SCHEMA)


def test_role_requires_options_and_explains_for_oscar():
    role = (ROOT / "roles" / "implementador.md").read_text(encoding="utf-8")
    assert "OBLIGATORIO en TODA pregunta" in role and "`for_oscar`" in role


# --- G. escaladas de review: sin Sí/No por defecto (28-09) --------------------------------------------------

REVIEW_KB = ["✍️ Otra respuesta", "🗄 Aparcar", "💬 Explícame más"]


def test_review_escalation_has_no_default_yes_no(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {})
    rr = ReviewRunner(LANES["review"], LANES, hermes_for=lambda b: h, git=None, reviewer=None, verifier=None,
                      notify=bot, messages=messages, decisions=desk)
    h.block = lambda tid, kind, reason: True
    rr._block(h, {"id": "t_aaaaaaa1", "title": "T", "body": TOPIC_BODY, "assignee": MIG.name}, "needs_input",
              "3ª petición de cambios: decide Oscar.\n- Cambia el botón", public="3ª petición de cambios: decides tú",
              questions=["Cambia el botón"])
    topic, dm = bot.sent
    assert texts(topic["markup"]) == REVIEW_KB and texts(dm["markup"]) == REVIEW_KB


def test_review_escalation_keeps_no_yes_no_in_decisiones(tmp_path):
    reason = "3ª petición de cambios: decide Oscar.\n- Cambia el botón"
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", reason)})
    [p] = cc.pending_decisions()
    assert p.yes_no is False
    command(desk, "/decisiones")
    assert texts(bot.sent[1]["markup"]) == REVIEW_KB
