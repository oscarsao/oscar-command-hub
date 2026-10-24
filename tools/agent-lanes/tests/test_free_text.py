"""Texto libre de Oscar como respuesta a la decisión pendiente del tema (t_dddcc232, P1): una pendiente -> cuenta
(eco + Deshacer); varias -> pregunta cuál; ninguna -> como antes; desconocidos y acciones de producción, nunca."""
from __future__ import annotations

import json
import time
from datetime import datetime

from agent_lanes.config import load_telegram_settings
from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.hermes import OSCAR_AUTHOR
from agent_lanes.notices import MessageStore, markup_token
from tests.test_decisions import LANE, FakeTG

OSCAR = 6744452215
GROUP, TOPIC = -1003530490339, 231
FREE = {"window_hours": 72, "max_choices": 5}


class FH:
    """Kanban de pega: estado por tarea; show() lo refleja."""

    def __init__(self, **status):
        self.status = dict(status) or {"t_1": "blocked"}
        self.calls = []

    def show(self, tid):
        return {"task": {"status": self.status[tid]}, "comments": [],
                "events": [{"kind": "blocked", "created_at": 100, "payload": {"kind": "needs_input", "reason": "¿A o B?"}}]}

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text, author))
        return True

    def unblock(self, tid):
        self.calls.append(("unblock", tid))
        self.status[tid] = "ready"
        return True

    def block(self, tid, kind, reason):
        self.calls.append(("block", tid, kind, reason))
        self.status[tid] = "blocked"
        return True

    def complete(self, tid, result, metadata):
        self.calls.append(("complete", tid))
        self.status[tid] = "done"
        return True, ""


def _setup(tmp_path, h=None, free=FREE):
    tg, h = FakeTG(), h or FH()
    messages = MessageStore()
    desk = DecisionDesk(tg, CallbackStore(tmp_path / "cb"), lanes={LANE.name: LANE}, hermes_for=lambda b: h, links=None,
                        spawn=lambda fn: fn(), now=lambda: datetime(2026, 10, 15, 10, 0), messages=messages,
                        free_text=free)
    return desk, tg, h, messages


def _notice(desk, messages, tid="t_1", *, mid=77, thread=TOPIC, chat=GROUP):
    markup = desk.markup("needs_input", task={"id": tid, "title": f"Tarea {tid}"}, lane=LANE, questions=["¿A o B?"])
    messages.put(tid, {"chat_id": str(chat), "thread_id": str(thread), "message_id": mid, "token": markup_token(markup)})
    return markup


BOT_MSG = {"message_id": 1, "from": {"id": int(FakeTG.bot_id), "is_bot": True}}  # un mensaje cualquiera de Trabajos


def _say(desk, text, *, user=OSCAR, chat=GROUP, thread=TOPIC, mid=900, private=False, bot_reply=True, **extra):
    """Por defecto, `text` va como respuesta (reply_to) a un mensaje del bot: desde el 24-10 es la única forma de que el
    texto libre cuente como respuesta a una decisión (Trabajos = motor silencioso). bot_reply=False = texto suelto."""
    if bot_reply and "reply_to_message" not in extra:
        extra["reply_to_message"] = BOT_MSG
    msg = {"message_id": mid, "from": {"id": user}, "chat": {"id": chat, "type": "private" if private else "supergroup"},
           "text": text, **extra}
    if thread:
        msg.update(message_thread_id=thread, is_topic_message=True)
    desk.handle_update({"update_id": 3, "message": msg})


def _press(desk, markup, data_index, *, mid, thread=TOPIC):
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row][data_index]
    desk.handle_update({"update_id": 4, "callback_query": {
        "id": "cq", "from": {"id": OSCAR}, "data": data,
        "message": {"message_id": mid, "chat": {"id": GROUP}, "message_thread_id": thread}}})


def test_one_pending_decision_asks_before_taking_the_free_text(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages)
    _say(desk, "otra cosa distinta")
    assert h.calls == []  # no asume
    ask = tg.sent[-1]
    labels = [b["text"] for row in ask["reply_markup"]["inline_keyboard"] for b in row]
    assert "es tu respuesta" in ask["text"] and "t_1" in ask["text"] and labels[-1] == "✖ Es un mensaje nuevo"
    _press(desk, ask["reply_markup"], 1, mid=503)
    assert h.calls == [] and "no lo tomo" in tg.edits[-1]["text"]


def test_one_pending_decision_takes_the_free_text_as_answer_with_echo_and_undo(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages)
    _say(desk, "la B, pero con tests")
    assert h.calls == []
    _press(desk, tg.sent[-1]["reply_markup"], 0, mid=503)
    assert h.calls == [("comment", "t_1", "Respuesta de Oscar: ¿A o B? → la B, pero con tests", OSCAR_AUTHOR),
                       ("unblock", "t_1")]
    echo = tg.edits[-1]
    assert echo["mid"] == 503 and echo["text"] == "Entendido: «la B, pero con tests» para t_1"
    assert [b["text"] for row in echo["markup"]["inline_keyboard"] for b in row] == ["↩️ Deshacer"]


def test_undo_reblocks_the_task_while_the_worker_has_not_picked_it_up(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    old = _notice(desk, messages)
    _say(desk, "la B")
    _press(desk, tg.sent[-1]["reply_markup"], 0, mid=500)
    h.calls.clear()
    _press(desk, tg.edits[-1]["markup"], 0, mid=501)
    assert h.calls[0] == ("block", "t_1", "needs_input", "¿A o B?")
    assert h.calls[1][:2] == ("comment", "t_1") and h.calls[1][2].startswith("ANULADA-OSCAR:") and h.calls[1][3] == OSCAR_AUTHOR
    assert any("Deshecho" in e["text"] for e in tg.edits if e["mid"] == 501)
    # el aviso original vuelve a tener sus botones vivos
    assert desk.store.get(markup_token(old)) is not None


def test_undo_says_it_is_too_late_once_the_worker_took_the_task(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages)
    _say(desk, "la B")
    _press(desk, tg.sent[-1]["reply_markup"], 0, mid=500)
    h.status["t_1"] = "running"
    h.calls.clear()
    _press(desk, tg.edits[-1]["markup"], 0, mid=501)
    assert h.calls == []
    assert "ya no se puede deshacer" in tg.edits[-1]["text"]


def test_two_pending_decisions_ask_which_one_and_the_button_decides(tmp_path):
    desk, tg, h, messages = _setup(tmp_path, FH(t_1="blocked", t_2="blocked"))
    _notice(desk, messages, "t_1", mid=77)
    _notice(desk, messages, "t_2", mid=78)
    _say(desk, "adelante con la B")
    assert h.calls == []  # no adivina
    ask = tg.sent[-1]
    labels = [b["text"] for row in ask["reply_markup"]["inline_keyboard"] for b in row]
    assert "2 decisiones" in ask["text"] and len(labels) == 3 and labels[-1] == "✖ Ninguna"
    assert {l.split(" · ")[0] for l in labels[:2]} == {"t_1", "t_2"}
    pick = labels.index(next(l for l in labels if l.startswith("t_2")))
    _press(desk, ask["reply_markup"], pick, mid=502)
    assert h.calls == [("comment", "t_2", "Respuesta de Oscar: ¿A o B? → adelante con la B", OSCAR_AUTHOR),
                       ("unblock", "t_2")]
    assert tg.edits[-1]["mid"] == 502 and tg.edits[-1]["text"].startswith("Entendido: «adelante con la B» para t_2")


def test_picker_cancel_does_not_touch_the_kanban(tmp_path):
    desk, tg, h, messages = _setup(tmp_path, FH(t_1="blocked", t_2="blocked"))
    _notice(desk, messages, "t_1", mid=77)
    _notice(desk, messages, "t_2", mid=78)
    _say(desk, "jaja vale")
    _press(desk, tg.sent[-1]["reply_markup"], 2, mid=502)
    assert h.calls == [] and "no lo tomo" in tg.edits[-1]["text"]


def test_no_pending_decision_keeps_todays_behaviour(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    _say(desk, "hola", chat=GROUP, thread=TOPIC)  # grupo: silencio
    assert tg.sent == [] and h.calls == []
    _say(desk, "hola", chat=OSCAR, thread=0, private=True)  # DM: ayuda de siempre
    assert len(tg.sent) == 1 and "no tengo una respuesta pendiente tuya" in tg.sent[0]["text"] and h.calls == []


def test_pending_decision_in_another_topic_does_not_count(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages, thread=230)
    _say(desk, "la B", thread=TOPIC)
    assert h.calls == [] and tg.sent == []


def test_text_from_someone_else_never_counts(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages)
    _say(desk, "la B", user=111222333)
    assert h.calls == [] and tg.sent == []


def test_commands_are_not_free_text(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages)
    _say(desk, "/inexistente")
    assert h.calls == []


def test_production_actions_are_never_answered_with_free_text(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    for i, action in enumerate(("promo_start", "int_merge", "int_deploy", "deploy_ask", "approve")):
        token, markup = desk.store.issue({"task_id": f"t_p{i}", "board": "oscarhq", "lane": LANE.name},
                                         [[{"text": "Ok", "action": action}]])
        messages.put(f"t_p{i}", {"chat_id": str(GROUP), "thread_id": str(TOPIC), "message_id": 80 + i, "token": token})
    _say(desk, "sí, promueve")
    assert h.calls == [] and tg.sent == []


def test_old_notice_outside_the_window_does_not_count(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    markup = _notice(desk, messages)
    path = tmp_path / "cb" / f"{markup_token(markup)}.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    rec["created"] = time.time() - 73 * 3600
    path.write_text(json.dumps(rec), encoding="utf-8")
    _say(desk, "la B")
    assert h.calls == []


def test_already_answered_task_is_not_pending(tmp_path):
    desk, tg, h, messages = _setup(tmp_path, FH(t_1="ready"))
    _notice(desk, messages)
    _say(desk, "la B")
    assert h.calls == [] and tg.sent == []


def test_feature_is_off_without_config(tmp_path):
    desk, tg, h, messages = _setup(tmp_path, free=None)
    _notice(desk, messages)
    _say(desk, "la B")
    assert h.calls == [] and tg.sent == []


def test_reply_to_force_reply_still_works_and_is_not_applied_twice(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    markup = _notice(desk, messages)
    _press(desk, markup, 2, mid=77)  # ✍️ Otra respuesta (0 y 1 = Sí/No)
    ask_mid = tg._next  # id que FakeTG dio a la pregunta force_reply
    _say(desk, "la B", reply_to_message={"message_id": ask_mid})
    assert [c[0] for c in h.calls] == ["comment", "unblock"]
    assert h.calls[0][2] == "Respuesta de Oscar: ¿A o B? → la B"
    assert not any(s["text"].startswith("Entendido") for s in tg.sent)  # sin eco: ya hay su camino


def test_decision_card_takes_the_free_text_and_closes_without_undo(tmp_path):
    desk, tg, h, messages = _setup(tmp_path, FH(t_c="ready"))
    card = {"board": "oscarhq", "task": {"id": "t_c", "title": "Elegir proveedor"}}
    markup = desk.card_markup(card, ["Uno", "Dos"], 0, "<b>Elegir proveedor</b>")
    messages.put("t_c", {"chat_id": str(GROUP), "thread_id": str(TOPIC), "message_id": 90, "token": markup_token(markup)})
    _say(desk, "el dos, y que sea barato")
    _press(desk, tg.sent[-1]["reply_markup"], 0, mid=504)
    assert h.calls[0][:2] == ("comment", "t_c") and h.calls[0][2].endswith("el dos, y que sea barato")
    assert h.calls[1] == ("complete", "t_c")
    echo = tg.edits[-1]
    assert echo["text"].startswith("Entendido: «el dos, y que sea barato» para t_c") and "reply_markup" not in echo


def test_lanes_yaml_declares_the_window_and_choices():
    cfg = load_telegram_settings()["free_text"]
    assert cfg and cfg["window_hours"] > 0 and cfg["max_choices"] >= 2


# --- Trabajos = motor silencioso (24-10, Oscar): Hermes conversa; Trabajos solo lee respuestas a sus mensajes ---------

def test_loose_text_in_a_topic_with_a_pending_decision_is_ignored(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages)
    _say(desk, "la B", bot_reply=False)
    assert h.calls == [] and tg.sent == []


def test_reply_to_someone_elses_message_is_ignored(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages)
    _say(desk, "la B", reply_to_message={"message_id": 55, "from": {"id": 123456, "is_bot": True}})  # p. ej. Hermes
    _say(desk, "la B", mid=901, reply_to_message={"message_id": 56, "from": {"id": 111222333}})
    assert h.calls == [] and tg.sent == []


def test_reply_to_the_notice_itself_is_its_answer_without_asking(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages, mid=77)
    _say(desk, "la B", reply_to_message={"message_id": 77, "from": {"id": int(FakeTG.bot_id), "is_bot": True}})
    assert h.calls == [("comment", "t_1", "Respuesta de Oscar: ¿A o B? → la B", OSCAR_AUTHOR), ("unblock", "t_1")]
    assert tg.sent[-1]["text"] == "Entendido: «la B» para t_1"


def test_without_a_known_bot_id_free_text_never_counts(tmp_path):
    desk, tg, h, messages = _setup(tmp_path)
    tg.bot_id = None
    _notice(desk, messages)
    _say(desk, "la B")
    assert h.calls == [] and tg.sent == []


def test_loose_text_in_a_topic_created_by_the_bot_is_not_a_reply(tmp_path):
    # Telegram pone como reply_to_message el aviso de creación del tema (lo creó Trabajos): no es una respuesta.
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages)
    creation = {"message_id": TOPIC, "from": BOT_MSG["from"], "forum_topic_created": {"name": "🤖 Asistente"}}
    _say(desk, "la B", reply_to_message=creation)
    _say(desk, "la B", mid=901, reply_to_message={"message_id": TOPIC, "from": BOT_MSG["from"]})
    assert h.calls == [] and tg.sent == []


def test_reply_to_a_thread_root_notice_in_a_non_forum_group_still_counts(tmp_path):
    # Supergrupo sin foro: message_thread_id = id del mensaje raíz respondido, sin is_topic_message.
    desk, tg, h, messages = _setup(tmp_path)
    _notice(desk, messages, mid=77, thread=0)
    _say(desk, "la B", thread=0, message_thread_id=77,
         reply_to_message={"message_id": 77, "from": BOT_MSG["from"]})
    assert [c[0] for c in h.calls] == ["comment", "unblock"]
