"""Ayuda al dueño ante comandos desconocidos / texto libre en el bot de carriles, y log de updates entrantes
(una línea INFO por update, chat enmascarado, nunca el texto)."""
from __future__ import annotations

import logging

import pytest

from agent_lanes.commands import command_target
from tests.test_commands import OSCAR, center

GROUP = -1003530490339
SECRET = "mi-secreto-sk-ant-api03-XYZ"


def send(desk, text, *, user=OSCAR, chat=OSCAR, ctype="private", mid=60, is_bot=False, **extra):
    frm = {"id": user, **({"is_bot": True} if is_bot else {})}
    msg = {"message_id": mid, "from": frm, "chat": {"id": chat, "type": ctype}, **extra}
    if text is not None:
        msg["text"] = text
    desk.handle_update({"update_id": mid, "message": msg})


@pytest.fixture
def env(tmp_path):
    cc, desk, bot, h, _ = center(tmp_path, {})
    return desk, bot, h


def update_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("update ")]


# --- command_target ----------------------------------------------------------------------------------

@pytest.mark.parametrize("text,out", [
    ("/foo", ("foo", False, False)),
    ("/hoy", ("hoy", True, False)),
    ("/foo@pildora_carriles_bot x", ("foo", False, True)),
    ("/hoy@otro_bot", None),
    ("hola", None),
])
def test_command_target(text, out):
    assert command_target(text, "pildora_carriles_bot") == out


# --- ayuda -------------------------------------------------------------------------------------------

def test_unknown_command_in_owner_dm_gets_help(env):
    desk, bot, _ = env
    send(desk, "/foo bar")
    assert len(bot.sent) == 1
    text = bot.sent[0]["text"]
    assert "/foo" in text and "/hazlo" in text and "/decisiones" in text and "/estado" in text
    assert bot.sent[0]["chat_id"] == str(OSCAR)


def test_free_text_in_owner_dm_gets_help_without_echoing_it(env):
    desk, bot, _ = env
    send(desk, f"quiero algo {SECRET}")
    assert len(bot.sent) == 1 and "/hazlo <texto>" in bot.sent[0]["text"]
    assert SECRET not in bot.sent[0]["text"]


def test_help_is_sent_once_per_burst(env):
    desk, bot, _ = env
    send(desk, "hola", mid=1)
    send(desk, "otra vez", mid=2)
    send(desk, "/nada", mid=3)
    assert len(bot.sent) == 1


def test_free_text_in_group_or_topic_is_ignored(env):
    desk, bot, _ = env
    send(desk, "hola equipo", chat=GROUP, ctype="supergroup", message_thread_id=231, is_topic_message=True)
    send(desk, "hola equipo", chat=GROUP, ctype="supergroup")
    assert bot.sent == []


def test_unknown_command_in_group_needs_explicit_bot_suffix(env):
    desk, bot, _ = env
    send(desk, "/foo", chat=GROUP, ctype="supergroup")  # sin sufijo: puede ser de Hermes
    send(desk, "/foo@otro_bot", chat=GROUP, ctype="supergroup", mid=61)
    assert bot.sent == []
    send(desk, "/foo@pildora_carriles_bot", chat=GROUP, ctype="supergroup", mid=62, message_thread_id=231,
         is_topic_message=True)
    assert len(bot.sent) == 1 and bot.sent[0]["thread_id"] == "231"


def test_non_owner_and_other_bots_get_no_help(env):
    desk, bot, _ = env
    send(desk, "hola", user=111, chat=111)
    send(desk, "/foo", user=111, chat=111, mid=61)
    send(desk, "hola", user=222, chat=OSCAR, is_bot=True, mid=62)
    assert bot.sent == []


def test_media_without_text_is_ignored(env):
    desk, bot, _ = env
    send(desk, None, photo=[{}])
    assert bot.sent == []


def test_pending_force_reply_is_not_answered_with_help(env):
    desk, bot, h = env
    markup = desk.markup("needs_input", task={"id": "t_1", "title": "T"}, lane=__import__(
        "tests.test_commands", fromlist=["MIG"]).MIG, questions=["¿Qué nombre?"])
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    labels = [b["text"] for row in markup["inline_keyboard"] for b in row]
    desk.handle_update({"callback_query": {"id": "c", "from": {"id": OSCAR}, "data": data[next(i for i, t in enumerate(labels) if t.startswith("✍️"))],
                                           "message": {"message_id": 77, "chat": {"id": OSCAR},
                                                       "message_thread_id": 0}}})
    ask = bot.sent[-1]
    n = len(bot.sent)
    send(desk, "Carriles", reply_to_message={"message_id": ask["message_id"]}, mid=70)
    assert all("Comandos" not in s["text"] for s in bot.sent[n:])
    assert any(c[0] == "comment" for c in h.calls)


def test_known_command_still_works_and_does_not_trigger_help(env):
    desk, bot, _ = env
    send(desk, "/ayuda")
    assert len(bot.sent) == 1 and bot.sent[0]["text"].startswith("Comandos del bot")
    assert "Entendí" not in bot.sent[0]["text"]


# --- log ---------------------------------------------------------------------------------------------

def test_one_info_line_per_update_masked_and_without_text(env, caplog):
    desk, bot, _ = env
    caplog.set_level(logging.INFO, logger="agent_lanes")
    send(desk, f"/foo {SECRET}", mid=1)
    send(desk, f"texto {SECRET}", mid=2)
    send(desk, "/hoy", user=111, chat=111, mid=3)
    send(desk, "hola", chat=GROUP, ctype="supergroup", mid=4)
    lines = update_lines(caplog)
    assert lines == [
        "update chat=***2215 tipo=comando cmd=foo resultado=respondido",
        "update chat=***2215 tipo=texto cmd=- resultado=ignorado",  # enfriamiento de la ayuda
        "update chat=*** tipo=comando cmd=hoy resultado=rechazado",
        "update chat=***0339 tipo=texto cmd=- resultado=ignorado",
    ]
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert SECRET not in blob and str(OSCAR) not in blob and str(GROUP) not in blob


def test_log_for_known_command_and_callbacks(env, caplog):
    desk, bot, _ = env
    caplog.set_level(logging.INFO, logger="agent_lanes")
    send(desk, "/ayuda")
    desk.handle_update({"callback_query": {"id": "c1", "from": {"id": 111}, "data": "zz:0",
                                           "message": {"message_id": 1, "chat": {"id": 111}}}})
    desk.handle_update({"callback_query": {"id": "c2", "from": {"id": OSCAR}, "data": "zz:0",
                                           "message": {"message_id": 1, "chat": {"id": OSCAR}}}})
    assert update_lines(caplog) == [
        "update chat=***2215 tipo=comando cmd=ayuda resultado=respondido",
        "update chat=*** tipo=callback cmd=- resultado=rechazado",
        "update chat=***2215 tipo=callback cmd=- resultado=ignorado",
    ]
