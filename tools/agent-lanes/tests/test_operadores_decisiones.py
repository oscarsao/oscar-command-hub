"""María (operador) recibe las decisiones como Oscar: DM con los mismos botones, la decisión de uno tacha la copia del
otro y no se decide dos veces, /pendientes para el operador y APROBADO-MARIA aceptado como APROBADO-OSCAR."""
from __future__ import annotations

from types import SimpleNamespace

from agent_lanes.hermes import OSCAR_AUTHOR
from agent_lanes.integrator import Integrator
from agent_lanes.notices import markup_token
from agent_lanes.people import People, Person
from agent_lanes.review import aprobada_por_oscar
from agent_lanes.telegram import TelegramNotifier
from tests.test_bandeja_unica import TOPIC_BODY, _ask, _runner
from tests.test_commands import OSCAR, center, command, needs, press

MARIA = 5516823836
ANGEL = 8770041085
GENTE = People(str(OSCAR), [
    Person("maria", "María", str(MARIA), "coo", ("Píldora", "MigraTeam", "*"), operador=True),
    Person("angel", "Angel", str(ANGEL), "dev", ("Signal",), operador=True),
    Person("ingerber", "Ingerber", "8847860660", "cmo", ("Píldora",)),  # no operador: nunca recibe decisiones
])


def _en(bot, chat):
    return [m for m in bot.sent if m["chat_id"] == str(chat)]


# --- 1. las decisiones llegan también al DM de cada operador -------------------------------------------

def test_decision_goes_to_marias_dm_with_the_same_buttons(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {})
    desk.people = GENTE
    _ask(_runner(desk, bot, messages), "t_aaaaaaa1", TOPIC_BODY)
    (topic,), (oscar,), (maria,) = [_en(bot, c) for c in (topic_chat(bot), OSCAR, MARIA)]
    assert maria["text"] == topic["text"] and maria["markup"] == oscar["markup"]
    assert _en(bot, ANGEL) == []  # acotado a otra marca (Signal): no recibe lo de MigraTeam
    assert _en(bot, "8847860660") == []  # sin operador:true no hay decisiones


def topic_chat(bot):
    return bot.sent[0]["chat_id"]


def test_chats_decision_respects_brand_and_order(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {})
    desk.people = GENTE
    assert desk.chats_decision("MigraTeam") == [str(OSCAR), str(MARIA)]
    assert desk.chats_decision("Signal") == [str(OSCAR), str(MARIA), str(ANGEL)]
    assert desk.chats_decision(None) == [str(OSCAR), str(MARIA)]  # sin marca conocida: solo quien tiene "*"


def test_operator_dm_keeps_buttons_but_collaborators_do_not():
    tg = TelegramNotifier("t", "1")
    tg.collab_view = lambda text: "solo estado"
    tg.owner_id, tg.collab_chats, tg.operator_chats = str(OSCAR), {str(MARIA), "777"}, {str(MARIA)}
    assert not tg._is_collab_chat(MARIA)
    assert tg._is_collab_chat(777)


# --- 2. quien decide tacha la copia del otro y no se decide dos veces ------------------------------------

def test_maria_decides_and_oscars_copy_says_so_and_cannot_be_decided_again(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", "x")})
    desk.people = GENTE
    _ask(_runner(desk, bot, messages, hermes=h), "t_aaaaaaa1", TOPIC_BODY)
    oscar, maria = _en(bot, OSCAR)[0], _en(bot, MARIA)[0]
    press(desk, maria, 0, user=MARIA)
    assert any(e["chat"] == str(OSCAR) and e["mid"] == oscar["message_id"] and "Decidido por María" in e["text"]
               for e in bot.edits)
    assert any(c[0] == "comment" and "María" in str(c) for c in h.calls)  # el rastro DECIDIDO-POR: María
    press(desk, oscar, 0, user=OSCAR, cid="2")  # el botón de la copia de Oscar ya no vale
    assert "Ya lo decidió María" in bot.answers[-1]


def test_broadcast_decision_shares_one_token_and_strikes_the_other_copy(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {})
    desk.people = GENTE
    markup = desk.store.issue({"task_id": "x", "board": "", "lane": ""}, [[{"text": "Ok", "action": "park"}]])[1]
    assert desk.difundir("⚠️ choque", markup, marca="MigraTeam")
    oscar, maria = _en(bot, OSCAR)[0], _en(bot, MARIA)[0]
    token = markup_token(markup)
    assert oscar["markup"] == maria["markup"] and len(desk.store.copies(token)) == 2
    desk.store.consume(token)
    desk._decidido(token, {"chat_id": str(MARIA), "message_id": maria["message_id"]}, MARIA)
    assert [(e["chat"], e["text"]) for e in bot.edits] == [(str(OSCAR), "⚠️ choque\n\n✅ Decidido por María " + desk._now().strftime("%H:%M"))]
    assert desk.store.decided(token)["who"] == "María"
    press(desk, oscar, 0, user=OSCAR)
    assert "Ya lo decidió María" in bot.answers[-1]


# --- 3. /pendientes ------------------------------------------------------------------------------------------

def test_pendientes_for_maria_lists_what_waits_for_a_decision(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", "¿Publico?")})
    desk.people = GENTE
    command(desk, "/pendientes", user=MARIA, chat=MARIA)
    assert any("t_aaaaaaa1" in m["text"] for m in _en(bot, MARIA))
    assert _en(bot, OSCAR) == []


def test_pendientes_empty_says_nothing_pending(tmp_path):
    cc, desk, bot, h, messages = center(tmp_path, {})
    desk.people = GENTE
    command(desk, "/pendientes", user=MARIA, chat=MARIA)
    assert "Nada pendiente" in _en(bot, MARIA)[0]["text"]


# --- 4. APROBADO-MARIA vale como APROBADO-OSCAR --------------------------------------------------------------

def test_aprobado_maria_counts_as_oscars_approval():
    slug = "oscarsao/oscar-hq"
    body = f"APROBADO-MARIA 2026-10-24 10:32 · PR https://github.com/{slug}/pull/7"
    stub = SimpleNamespace(links=SimpleNamespace(repo_slug=lambda lane: slug))
    got = Integrator._approval(stub, object(), "t_1", [{"author": OSCAR_AUTHOR, "body": body}])
    assert got == (7, f"https://github.com/{slug}/pull/7")
    assert Integrator._approval(stub, object(), "t_1", [{"author": "lane-review", "body": body}]) is None
    assert aprobada_por_oscar({"events": [], "comments": [{"author": OSCAR_AUTHOR, "body": body, "created_at": 5}]})
