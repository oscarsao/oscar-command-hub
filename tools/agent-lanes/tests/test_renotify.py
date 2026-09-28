"""renotify (28-09): reenviar con el bot de carriles (con botones) los avisos pendientes de decisión que salieron
antes con el bot de Hermes, sin volver a bloquear ni tocar el estado de las tareas."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from agent_lanes import renotify
from agent_lanes.config import Lane
from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.notices import MessageStore
from tests.test_decisions import OSCAR, DeskHermes, FakeGH
from tests.test_runner_state import LANE

MIG = Lane(name="claude-migrateam", board="migrateam", repo="C:/mig", base="master",
           telegram=("-1003530490339", "230"))
REVIEW_LANE = Lane(name="review", kind="review", reviews=("claude-oscarhq", "claude-migrateam"))
LANES = {LANE.name: LANE, MIG.name: MIG, REVIEW_LANE.name: REVIEW_LANE}
BODY = "Origen-Telegram: chat=6744452215 thread=0\n\n## Objetivo\nSpec de OCR de documentos de EEUU.\n"
HERMES_MSG = {"chat_id": "-1003530490339", "thread_id": "230", "message_id": 289}  # aviso viejo, sin `bot`


def blocked_show(tid, kind, reason, *, assignee="claude-migrateam", meta=None):
    return {"task": {"id": tid, "title": f"Tarea {tid}", "body": BODY, "assignee": assignee, "status": "blocked"},
            "runs": [{"status": "blocked", "outcome": "blocked", "summary": reason, "metadata": meta}],
            "events": [{"kind": "claimed"}, {"kind": "blocked", "payload": {"kind": kind, "reason": reason}}],
            "comments": [{"author": "default", "body": "BLOCKED: " + reason}]}


def done_show(tid, *, assignee="claude-oscarhq", review="approve", comments=()):
    meta = {"branch": f"lane/{tid}", "head_sha": "b" * 40, "changed_files": ["docs/specs/x.md", "a.py"],
            "runner_test_exit": 0, "cost_usd": 1.2, "summary": "impl"}
    if review:
        meta["review"] = {"status": review, "summary": "Revisión OK: spec clara", "cost_usd": 0.4}
    return {"task": {"id": tid, "title": f"Tarea {tid}", "body": BODY, "assignee": assignee, "status": "done"},
            "runs": [{"summary": "impl", "metadata": meta}, {"summary": "Aprobada", "metadata": meta}],
            "events": [{"kind": "completed"}], "comments": list(comments)}


NEEDS = ("El worker necesita decisión:\n- ¿Opción A o B para los códigos? (A recomendada)\n"
         "- ¿Añadir el code I-797?\n- ¿Apruebas la spec?")


class BoardHermes:
    def __init__(self, shows: dict[str, dict]):
        self.shows = shows
        self.calls = []

    def list_status(self, assignee, status, sort="priority"):
        return [{"id": tid, **s["task"]} for tid, s in self.shows.items()
                if s["task"]["assignee"] == assignee and s["task"]["status"] == status]

    def show(self, tid):
        return self.shows[tid]

    def __getattr__(self, name):  # cualquier escritura en el kanban sería un fallo del renotify
        raise AssertionError(f"renotify no debe llamar a hermes.{name}")


class Bot:
    """TelegramNotifier del bot de carriles: send con reply_markup, edit/delete registrados."""
    bot_id = "999"

    def __init__(self, fail=False):
        self.sent, self.edits, self.deleted = [], [], []
        self.fail = fail
        self._next = 1000

    def send(self, text, target=None, lane_target=None, *, silent=False, reply_markup=None):
        if self.fail:
            raise RuntimeError("telegram no alcanzable")
        self._next += 1
        self.sent.append({"text": text, "target": target, "lane_target": lane_target, "silent": silent,
                          "markup": reply_markup})
        chat, thread = lane_target or ("-1", "5")
        return {"chat_id": chat, "thread_id": thread, "message_id": self._next}

    def edit(self, *a, **k):
        self.edits.append(a)
        return True

    def delete(self, chat_id, message_id):
        self.deleted.append(message_id)
        return True


def make(tmp_path, shows, *, bot=None, branches=True, merged=False):
    h = BoardHermes(shows)
    bot = bot or Bot()
    messages = MessageStore(tmp_path / "messages")
    desk = DecisionDesk(bot, CallbackStore(tmp_path / "callbacks"), lanes=LANES, hermes_for=lambda b: h, links=None)
    out = []
    r = renotify.Renotifier(LANES, hermes_for=lambda b: h, notifier=bot, messages=messages, links=None, desk=desk,
                            branch_state=lambda lane, tid, sha: (branches, merged), out=out.append)
    return r, bot, messages, out


def texts(markup):
    return [b["text"] for row in markup["inline_keyboard"] for b in row]


# --- needs_input -------------------------------------------------------------------------------------

def test_needs_input_is_resent_with_buttons_and_questions_from_the_block_reason(tmp_path):
    r, bot, messages, _ = make(tmp_path, {"t_c3": blocked_show("t_c3", "needs_input", NEEDS)})
    messages.put("t_c3", HERMES_MSG)
    sent, failed = r.run()
    assert (sent, failed) == (["t_c3"], [])
    [msg] = bot.sent
    assert msg["text"].startswith("❓ t_c3") and "necesita tu decisión" in msg["text"]
    # varias preguntas: en secuencia, solo la pregunta en curso ("Pregunta 1/3"); las siguientes salen al responder
    assert "Pregunta 1/3" in msg["text"] and "• ¿Opción A o B para los códigos? (A recomendada)" in msg["text"]
    assert "¿Añadir el code I-797?" not in msg["text"] and "¿Apruebas la spec?" not in msg["text"]
    # preguntas sin opciones: [✅ Sí, adelante] [❌ No] por defecto
    assert texts(msg["markup"]) == ["✅ Sí, adelante", "❌ No", "✍️ Otra respuesta", "🗄 Aparcar", "💬 Explícame más"]
    assert msg["lane_target"] == ("-1003530490339", "230") and msg["silent"] is False
    # nunca se edita ni se borra el mensaje del bot de Hermes; el almacén apunta al mensaje nuevo
    assert bot.edits == [] and bot.deleted == []
    stored = messages.get("t_c3")
    assert stored.pop("token") == msg["markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[0]
    assert stored == {"chat_id": "-1003530490339", "thread_id": "230", "message_id": 1001, "bot": "999"}
    [cb] = list((tmp_path / "callbacks").glob("*.json"))
    rec = json.loads(cb.read_text(encoding="utf-8"))
    assert (rec["task_id"], rec["board"], rec["lane"], rec["n_questions"]) == ("t_c3", "migrateam",
                                                                               "claude-migrateam", 3)


def test_options_written_by_the_runner_become_option_buttons(tmp_path):
    reason = "El worker necesita decisión:\n- ¿Qué BD? [1) SQLite / 2) Postgres (recomendada)]"
    r, bot, *_ = make(tmp_path, {"t_1": blocked_show("t_1", "needs_input", reason)})
    r.run()
    assert texts(bot.sent[0]["markup"]) == ["1) SQLite", "⭐ 2) Postgres", "✍️ Otra respuesta", "🗄 Aparcar", "💬 Explícame más"]
    assert "• ¿Qué BD?" in bot.sent[0]["text"]


def test_dict_reprs_left_by_the_old_runner_become_option_buttons(tmp_path):
    q1 = {"question": "¿Apruebas este diseño?", "options": ["Aprobar", "Solo in-app"], "recommended": 0}
    reason = f"El worker necesita decisión:\n- {q1!r}\n- {{'question': \"¿Umbral?\", 'options': ['20', '10'"
    qs = renotify.parse_questions(reason)  # la segunda quedó cortada por el límite de 1500 del bloqueo
    assert qs[0] == {"question": "¿Apruebas este diseño?", "options": ["Aprobar", "Solo in-app"], "recommended": 0}
    assert qs[1]["question"] == "¿Umbral?" and qs[1]["options"] == []
    r, bot, *_ = make(tmp_path, {"t_1": blocked_show("t_1", "needs_input", reason)})
    r.run()
    assert texts(bot.sent[0]["markup"]) == ["⭐ 1) Aprobar", "2) Solo in-app", "✍️ Otra respuesta", "🗄 Aparcar", "💬 Explícame más"]
    assert "• ¿Apruebas este diseño?" in bot.sent[0]["text"] and "'options'" not in bot.sent[0]["text"]


def test_questions_in_run_metadata_win_over_the_reason_text(tmp_path):
    meta = {"questions": [{"question": "¿X o Y?", "options": ["X", "Y"], "recommended": 0}]}
    r, bot, *_ = make(tmp_path, {"t_1": blocked_show("t_1", "needs_input", NEEDS, meta=meta)})
    r.run()
    assert texts(bot.sent[0]["markup"]) == ["⭐ 1) X", "2) Y", "✍️ Otra respuesta", "🗄 Aparcar", "💬 Explícame más"]


def test_review_escalation_keeps_its_public_line(tmp_path):
    reason = "3ª petición de cambios: decide Oscar.\n- renombra la función\n- añade test"
    r, bot, *_ = make(tmp_path, {"t_1": blocked_show("t_1", "needs_input", reason)})
    r.run()
    assert "3ª petición de cambios: decides tú" in bot.sent[0]["text"]
    assert "• añade test" in bot.sent[0]["text"]


@pytest.mark.parametrize("reason,expected", [
    (["El worker necesita decisión:", "- a", "- b"], ["a", "b"]),
    (["¿Seguimos?"], ["¿Seguimos?"]),
    (["x: [1) uno / 2) dos]"], ["x: [1) uno / 2) dos]"]),  # formato de opciones solo tras "- "
])
def test_parse_questions(reason, expected):
    assert [q["question"] for q in renotify.parse_questions("\n".join(reason))] == expected


# --- transient ---------------------------------------------------------------------------------------

def test_transient_is_resent_with_retry_and_park_and_no_technical_detail(tmp_path):
    reason = ("el worker no terminó (tras 2 resume(s)): error_max_budget C:\\Users\\oscar\\x "
              "(session_id=f4c72037)")
    r, bot, *_ = make(tmp_path, {"t_2": blocked_show("t_2", "transient", reason, assignee="claude-oscarhq")})
    r.run()
    msg = bot.sent[0]
    assert msg["text"].startswith("⛔ t_2")
    assert "bloqueada · el worker no terminó (tiempo o presupuesto agotado) · detalle en la tarjeta" in msg["text"]
    assert "session_id" not in msg["text"] and "C:\\" not in msg["text"]
    assert texts(msg["markup"]) == ["🔄 Reintentar", "🗄 Aparcar"]
    assert msg["lane_target"] is None  # LANE de tests sin destino: default del .env


def test_unknown_transient_reason_gets_a_generic_line(tmp_path):
    r, bot, *_ = make(tmp_path, {"t_2": blocked_show("t_2", "transient", "Traceback: boom en /tmp/x",
                                                     assignee="claude-oscarhq")})
    r.run()
    assert "bloqueada · detalle en la tarjeta" in bot.sent[0]["text"] and "Traceback" not in bot.sent[0]["text"]


def test_unknown_block_kind_is_skipped(tmp_path):
    shows = {"t_1": blocked_show("t_1", "dependency", "espera a t_0")}
    shows["t_2"] = {**blocked_show("t_2", "needs_input", NEEDS), "events": []}
    r, bot, _, out = make(tmp_path, shows)
    assert r.run() == ([], [])
    assert bot.sent == [] and any("t_1" in o and "dependency" in o for o in out)


# --- lista para merge --------------------------------------------------------------------------------

def test_done_approved_by_review_gets_approve_changes_park(tmp_path):
    r, bot, messages, _ = make(tmp_path, {"t_d": done_show("t_d")})
    assert r.run() == (["t_d"], [])
    msg = bot.sent[0]
    assert msg["text"].startswith("✅ t_d")
    assert "review aprobada · 2 archivos · tests OK · 1,6 $ · lista para merge" in msg["text"]
    assert texts(msg["markup"]) == ["✅ Aprobar", "🔁 Pedir cambios", "🗄 Aparcar"]
    rec = json.loads(next((tmp_path / "callbacks").glob("*.json")).read_text(encoding="utf-8"))
    assert rec["summary"] == "Revisión OK: spec clara" and rec["lane"] == "claude-oscarhq"
    assert rec["changed_files"] == ["docs/specs/x.md", "a.py"]
    assert messages.get("t_d")["bot"] == "999"


@pytest.mark.parametrize("show,branches,merged", [
    (done_show("t_d", comments=[{"author": "oscar-telegram", "body": "APROBADO-OSCAR 2026-09-28 · PR x"}]),
     True, False),
    (done_show("t_d", comments=[{"author": "lane-integrator", "body": "INTEGRADO en master"}]), True, False),
    (done_show("t_d", review=None), True, False),       # done sin review del carril (E2E antiguos)
    (done_show("t_d", review="changes"), True, False),
    (done_show("t_d"), False, False),                   # rama lane/<id> ya no está en el remote
    (done_show("t_d"), True, True),                     # ya fusionada en la base
])
def test_done_not_ready_for_merge_is_skipped(tmp_path, show, branches, merged):
    r, bot, *_ = make(tmp_path, {"t_d": show}, branches=branches, merged=merged)
    assert r.run() == ([], []) and bot.sent == []


# --- filtros, idempotencia, fallos -------------------------------------------------------------------

def test_lane_and_task_filters(tmp_path):
    shows = {"t_a": blocked_show("t_a", "needs_input", NEEDS),
             "t_b": blocked_show("t_b", "transient", "error del runner: x", assignee="claude-oscarhq"),
             "t_c": done_show("t_c")}
    r, *_ = make(tmp_path, shows)
    assert [p.tid for p in r.collect(lane="claude-oscarhq")] == ["t_b", "t_c"]
    assert [p.tid for p in r.collect(task="t_a")] == ["t_a"]
    with pytest.raises(SystemExit):
        r.collect(lane="no-existe")


def test_second_run_skips_tasks_already_notified_by_the_lanes_bot(tmp_path):
    r, bot, _, out = make(tmp_path, {"t_c3": blocked_show("t_c3", "needs_input", NEEDS)})
    r.run()
    assert r.run() == ([], []) and len(bot.sent) == 1
    assert any("ya tiene aviso del bot de carriles" in o for o in out)
    assert r.run(force=True) == (["t_c3"], []) and len(bot.sent) == 2


def test_failed_send_is_reported_and_keeps_the_old_record(tmp_path):
    r, _bot, messages, _ = make(tmp_path, {"t_c3": blocked_show("t_c3", "needs_input", NEEDS)}, bot=Bot(fail=True))
    messages.put("t_c3", HERMES_MSG)
    assert r.run() == ([], ["t_c3"])
    assert messages.get("t_c3") == HERMES_MSG


def test_refuses_without_the_lanes_bot(tmp_path):
    class HermesBot(Bot):
        bot_id = None

    with pytest.raises(SystemExit):
        make(tmp_path, {}, bot=HermesBot())
    h = BoardHermes({})
    with pytest.raises(SystemExit):
        renotify.Renotifier(LANES, hermes_for=lambda b: h, notifier=Bot(), messages=MessageStore(tmp_path),
                            links=None, desk=None)


# --- dry-run -----------------------------------------------------------------------------------------

def test_dry_run_prints_and_writes_nothing(tmp_path):
    shows = {"t_c3": blocked_show("t_c3", "needs_input", NEEDS),
             "t_b": blocked_show("t_b", "transient", "error del runner: x", assignee="claude-oscarhq"),
             "t_d": done_show("t_d")}
    r, bot, messages, out = make(tmp_path, shows)
    messages.put("t_c3", HERMES_MSG)
    assert r.run(dry_run=True) == (["t_b", "t_d", "t_c3"], [])  # orden de lanes.yaml: blocked y luego done
    assert bot.sent == [] and bot.edits == [] and bot.deleted == []
    assert not (tmp_path / "callbacks").exists()
    assert messages.get("t_c3") == HERMES_MSG and messages.get("t_d") is None
    text = "\n".join(out)
    assert "[dry-run] t_c3 · claude-migrateam · ❓ needs_input → chat -1003530490339 tema 230" in text
    assert "✍️ Otra respuesta | 🗄 Aparcar" in text
    assert "🔄 Reintentar | 🗄 Aparcar" in text and "✅ Aprobar | 🔁 Pedir cambios | 🗄 Aparcar" in text


# --- el runner-servicio reconoce los botones creados por otro proceso -----------------------------------

def test_service_desk_handles_callbacks_issued_by_another_process(tmp_path):
    r, bot, *_ = make(tmp_path, {"t_2": blocked_show("t_2", "transient", "error del runner: x",
                                                     assignee="claude-oscarhq")})
    r.run()
    data = bot.sent[0]["markup"]["inline_keyboard"][0][0]["callback_data"]  # 🔄 Reintentar
    # Proceso del servicio: su propio CallbackStore/DecisionDesk sobre el mismo .state/callbacks.
    from tests.test_decisions import FakeTG
    h, tg = DeskHermes(), FakeTG()
    service_desk = DecisionDesk(tg, CallbackStore(tmp_path / "callbacks"), lanes=LANES, hermes_for=lambda b: h,
                                links=None, runner=FakeGH(), gh_exe="gh", spawn=lambda fn: fn(),
                                now=lambda: datetime(2026, 9, 28, 12, 0))
    service_desk.handle_update({"update_id": 1, "callback_query": {
        "id": "cq", "from": {"id": OSCAR}, "data": data,
        "message": {"message_id": 1001, "chat": {"id": -1003530490339}, "message_thread_id": 231}}})
    assert tg.answers == [("cq", "Hecho, lo aplico")]
    assert ("unblock", "t_2") in h.calls


# --- CLI ---------------------------------------------------------------------------------------------

def test_cli_args():
    a = renotify.parse_args(["--lane", "claude-migrateam", "--task", "t_1", "--dry-run"])
    assert (a.lane, a.task, a.dry_run, a.force) == ("claude-migrateam", "t_1", True, False)
    assert renotify.parse_args([]).dry_run is False
