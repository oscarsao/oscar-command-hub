"""28-09: recordatorios 13:00/18:00, `questions` como dict (runner.log 09:34), reintento del aviso, copia al DM de
Oscar de las alertas de tareas pedidas desde su DM y "⏰ >24h" en el resumen de las 8:00."""
from __future__ import annotations

import logging
from datetime import datetime
from types import SimpleNamespace

from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.notices import MessageStore, normalize_questions, questions_block, truncate
from agent_lanes.reminders import MADRID, Reminders, due_slot, reminder_text
from agent_lanes.review import ReviewRunner
from agent_lanes.runner import LaneRunner
from agent_lanes.telegram import TelegramAPIError
from brief.brief_diario import classify, compose
from tests.test_commands import Bot
from tests.test_runner_state import GOOD, LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, \
    ok_outcome

OSCAR = "6744452215"

# Payload exacto del worker de t_dc1e9f0c (runner.log 28-09 09:34): preguntas como objetos.
DC1E_QUESTIONS = [
    {"question": "¿Apruebas este diseño (resumen diario email+in-app, escalado de vencidas, piloto en Mosquera) "
                 "para pasar a implementación?",
     "options": ["Aprobar diseño completo", "Solo activar in-app (sin email)", "Revisar alcance antes"],
     "recommended": 0},
    {"question": "¿El flag TASK_DUE_SOON_REMINDERS_ENABLED=true (activado 08-19) sigue vivo hoy en el servicio "
                 "Migrateam Backend CRON de Railway? No verificable desde este carril.",
     "options": ["Sí, sigue activo", "No lo sé, hay que comprobarlo", "No, se perdió en alguna rotación"],
     "recommended": 1},
    {"question": "¿Umbral de 'demasiadas vencidas acumuladas' que dispara el nudge de triage al activar un "
                 "despacho nuevo?",
     "options": ["20 (propuesto en la spec)", "10", "50", "Sin umbral, mostrar todas truncadas"], "recommended": 0},
]


# --- questions como dict ------------------------------------------------------------------------------

def test_truncate_and_questions_block_accept_dicts():
    assert truncate({"question": "¿Sí o no?", "options": ["a", "b"]}, 50) == "¿Sí o no?"
    assert truncate(12345, 3) == "12…" and truncate(None, 5) == ""
    assert questions_block(DC1E_QUESTIONS)[0].startswith("• ¿Apruebas este diseño")
    assert normalize_questions(DC1E_QUESTIONS[0])[0]["recommended"] == 0  # un dict suelto, no una lista
    assert normalize_questions("¿Suelta?") == [{"question": "¿Suelta?", "options": [], "recommended": None}]


def test_worker_dict_questions_block_and_notify_without_crashing():
    """Regresión de t_dc1e9f0c: el job ya no revienta con 'dict' object has no attribute 'split'."""
    h = FakeHermes(tasks=[{"id": "t_dc1e9f0c", "title": "Recordatorios", "body": "B"}])
    n = FakeNotifier()
    out = ok_outcome({**GOOD, "status": "needs_input", "questions": DC1E_QUESTIONS})
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([out]), verifier=FakeVerifier(), notify=n)
    assert r.run_once() == {"t_dc1e9f0c": "blocked:needs_input"}
    [block] = [c for c in h.calls if c[0] == "block"]
    assert "- ¿Apruebas este diseño" in block[3] and "(recomendada)" in block[3] and "{'question'" not in block[3]
    assert "• ¿Umbral de &#x27;demasiadas vencidas" in n.msgs[-1]  # HTML escapado


def test_review_escalation_with_dict_changes_does_not_crash():
    n = FakeNotifier()
    rr = ReviewRunner(SimpleNamespace(name="review", telegram=None, reviews=()), {}, hermes_for=None, git=None,
                      reviewer=None, verifier=None, notify=n)
    h = SimpleNamespace(block=lambda *a: True)
    assert rr._block(h, {"id": "t_1", "title": "T"}, "needs_input", "x", public="decides tú",
                     questions=DC1E_QUESTIONS) == "blocked:needs_input"
    assert "• ¿Apruebas este diseño" in n.msgs[-1]


# --- reintento del aviso -----------------------------------------------------------------------------------

class FlakyNotifier(FakeNotifier):
    def __init__(self, failures=1):
        super().__init__()
        self.failures = failures

    def send(self, text, target=None, lane_target=None, *, silent=False):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("telegram no alcanzable (URLError)")
        return super().send(text, target, lane_target, silent=silent)


def test_failed_notice_is_retried_once():
    n = FlakyNotifier(failures=1)
    r = LaneRunner(LANE, hermes=FakeHermes(), git=FakeGit(), worker=None, verifier=None, notify=n)
    r.notify("blocked", "t_1", {"title": "T"}, "bloqueada", alert=True)
    assert len(n.msgs) == 1


def test_notice_is_not_retried_more_than_once():
    n = FlakyNotifier(failures=5)
    r = LaneRunner(LANE, hermes=FakeHermes(), git=FakeGit(), worker=None, verifier=None, notify=n)
    r.notify("blocked", "t_1", {"title": "T"}, "bloqueada", alert=True)
    assert n.msgs == [] and n.failures == 3


# --- resultado al DM ----------------------------------------------------------------------------------------

def _runner_with_desk(tmp_path, body):
    bot = Bot()
    messages = MessageStore(tmp_path / "m")
    desk = DecisionDesk(bot, CallbackStore(tmp_path / "cb"), lanes={LANE.name: LANE}, hermes_for=lambda b: None,
                        links=None, messages=messages, owner_id=OSCAR)
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": body}])
    return h, bot, messages, desk


def test_alerts_of_a_task_asked_from_oscar_dm_also_go_to_the_dm(tmp_path):
    h, bot, messages, desk = _runner_with_desk(tmp_path, f"Origen-Telegram: chat={OSCAR} thread=0\n\nB")
    out = ok_outcome({**GOOD, "status": "needs_input", "questions": DC1E_QUESTIONS[:1]})
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([out]), verifier=FakeVerifier(), notify=bot,
                   messages=messages, decisions=desk)
    r.run_once()
    running, topic, dm = bot.sent
    assert running["chat_id"] != OSCAR and topic["chat_id"] != OSCAR  # el tema de la marca recibe su tarjeta
    assert dm["chat_id"] == OSCAR and dm["text"] == topic["text"] and dm["markup"] == topic["markup"]
    assert [m["message_id"] for m in messages.all_messages("t_1")] == [topic["message_id"], dm["message_id"]]


def test_tasks_from_other_origins_or_progress_notices_do_not_go_to_the_dm(tmp_path):
    h, bot, messages, desk = _runner_with_desk(tmp_path, "Origen-Telegram: chat=-1003530490339 thread=231\n\nB")
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=None, verifier=None, notify=bot, messages=messages,
                   decisions=desk)
    r.notify("blocked", "t_1", {"title": "T", "body": "Origen-Telegram: chat=-1003530490339 thread=231"}, "x",
             alert=True)
    r2 = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=None, verifier=None, notify=bot, messages=MessageStore(),
                    decisions=desk)
    r2.notify("running", "t_2", {"title": "T", "body": f"Origen-Telegram: chat={OSCAR} thread=0"}, "en curso")
    assert all(m["chat_id"] != OSCAR for m in bot.sent)


# --- recordatorios --------------------------------------------------------------------------------------------

def at(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=MADRID)  # 28-09-2026 es lunes


def test_due_slot_weekdays_at_13_and_18_within_the_window():
    assert due_slot(at(28, 13, 0), set()) == "2026-09-28-13"
    assert due_slot(at(28, 13, 59), set()) == "2026-09-28-13"
    assert due_slot(at(28, 12, 59), set()) is None
    assert due_slot(at(28, 14, 0), set()) is None
    assert due_slot(at(28, 18, 30), set()) == "2026-09-28-18"
    assert due_slot(at(28, 13, 5), {"2026-09-28-13"}) is None  # ya enviado (sobrevive a reinicios)
    assert due_slot(at(2, 13, 0).replace(month=10), set()) == "2026-10-02-13"  # viernes
    assert due_slot(at(26, 13, 0), set()) is None and due_slot(at(27, 18, 0), set()) is None  # sábado, domingo


class DMBot:
    def __init__(self, error=None):
        self.sent, self.error = [], error

    def send_to(self, chat_id, thread_id, text, *, html=True, **kw):
        if self.error:
            raise self.error
        self.sent.append((str(chat_id), text, html))
        return {"message_id": 1}


def _reminders(tmp_path, bot, pending, clock):
    return Reminders(bot, OSCAR, lambda: pending, tmp_path / "reminders.json", now=lambda: clock[0])


def test_reminder_only_when_there_are_pending_decisions(tmp_path):
    clock = [at(28, 13, 2)]
    bot = DMBot()
    now_ts = clock[0].timestamp()
    pending = [SimpleNamespace(since=now_ts - 5 * 3600), SimpleNamespace(since=now_ts - 3600)]
    rem = _reminders(tmp_path, bot, pending, clock)
    assert rem.tick() == "Tienes 2 preguntas de agentes (la más antigua hace 5 h) · /decisiones"
    assert bot.sent == [(OSCAR, "Tienes 2 preguntas de agentes (la más antigua hace 5 h) · /decisiones", False)]
    assert rem.tick() is None and len(bot.sent) == 1  # una vez por franja
    clock[0] = at(28, 15, 0)
    assert rem.tick() is None  # fuera de franja
    pending.clear()
    clock[0] = at(28, 18, 1)
    assert rem.tick() is None and len(bot.sent) == 1  # sin pendientes no se envía nada


def test_reminder_text_singular():
    assert reminder_text(1, 26) == "Tienes 1 pregunta de agentes (la más antigua hace 26 h) · /decisiones"


def test_closed_dm_is_logged_once(tmp_path, caplog):
    clock = [at(28, 13, 0)]
    err = TelegramAPIError("telegram HTTP 400: Bad Request: chat not found", "Bad Request: chat not found")
    rem = _reminders(tmp_path, DMBot(error=err), [SimpleNamespace(since=None)], clock)
    with caplog.at_level(logging.WARNING, logger="agent_lanes"):
        rem.tick()
        clock[0] = at(28, 18, 0)
        rem.tick()
    hits = [r for r in caplog.records if "/start" in r.getMessage()]
    assert len(hits) == 1


def test_network_error_retries_the_same_slot(tmp_path):
    clock = [at(28, 13, 0)]
    bot = DMBot(error=RuntimeError("telegram no alcanzable (URLError)"))
    rem = _reminders(tmp_path, bot, [SimpleNamespace(since=None)], clock)
    assert rem.tick() is None
    bot.error = None
    clock[0] = at(28, 13, 1)
    assert rem.tick().startswith("Tienes 1 pregunta de agentes")


# --- ⏰ >24h en el resumen de las 8:00 -------------------------------------------------------------------------

NOW = datetime(2026, 9, 28, 8, 0).timestamp()


def _blocked(tid, hours, assignee="claude-oscarhq", board="oscarhq"):
    return {"board": board, "task": {"id": tid, "status": "blocked", "assignee": assignee, "title": f"T {tid}",
                                     "priority": 0, "created_at": 0},
            "detail": {"events": [{"kind": "blocked", "payload": {"kind": "needs_input"},
                                   "created_at": NOW - hours * 3600}], "comments": [], "runs": []}}


def test_brief_marks_decisions_older_than_24h_only_on_their_line():
    text = compose([_blocked("t_viejo", 30), _blocked("t_nuevo", 2)], [], NOW)
    viejo = next(l for l in text.split("\n") if "t_viejo" in l)
    nuevo = next(l for l in text.split("\n") if "t_nuevo" in l)
    assert "⏰ >24h" in viejo and "⏰" not in nuevo
    assert text.count("⏰") == 1  # nunca en la cabecera


def test_brief_compose_filters_by_brand():
    items = [_blocked("t_ohq", 1), _blocked("t_mig", 1, assignee="claude-migrateam", board="migrateam")]
    rows = [{"lane": "claude-oscarhq", "state": "libre", "running": [], "ready": 0},
            {"lane": "claude-migrateam", "state": "libre", "running": [], "ready": 2}]
    text = compose(items, rows, NOW, brand_name="MigraTeam")
    assert "t_mig" in text and "t_ohq" not in text and "claude-oscarhq" not in text
    assert text.split("\n")[0].endswith("</b> · MigraTeam")
    assert classify(items, NOW)["decide"][0]["since"] == NOW - 3600
