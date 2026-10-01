"""Comandos del bot de carriles (28-09): /hoy, /decisiones (bandeja, aceptar todo, deduplicación), /aprobar, /tareas,
/tarea, solo Oscar, filtro por marca del tema, sincronización tema <-> DM y registro con setMyCommands."""
from __future__ import annotations

import pytest

from agent_lanes import renotify
from agent_lanes.commands import COMMAND_SCOPES, CommandCenter, parse_command, register_commands, scope_brand
from agent_lanes.config import Lane
from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.hermes import ANSWER_PREFIX, OSCAR_AUTHOR
from agent_lanes.notices import MessageStore, TaskNotices
from tests.test_renotify import done_show

OSCAR = 6744452215
GESTION = "-1003530490339"
MIG = Lane(name="claude-migrateam", board="migrateam", repo="C:/mig", base="master", telegram=(GESTION, "230"))
OHQ = Lane(name="claude-oscarhq", board="oscarhq", repo="C:/ohq", base="master", telegram=(GESTION, "231"))
SCR = Lane(name="claude-scraper", board="oscarhq", repo="C:/scr", base="main", telegram=(GESTION, "231"))
NJ = Lane(name="claude-nextjobs", board="default", repo="C:/nj", base="master", telegram=(GESTION, "5"))
REV = Lane(name="review", kind="review", reviews=("claude-oscarhq", "claude-migrateam"))
LANES = {l.name: l for l in (MIG, OHQ, SCR, NJ, REV)}
GENERIC = {("6744452215", "*"), (GESTION, "5")}
BODY = "## Objetivo\nAlgo concreto.\n"

Q_REC = ("El worker necesita decisión:\n- ¿Publicar ya? [1) Sí (recomendada) / 2) No]")
Q_NOREC = ("El worker necesita decisión:\n- ¿Qué despacho primero? [1) Mosquera / 2) Otro]")
Q_TWO = ("El worker necesita decisión:\n- ¿Publicar ya? [1) Sí (recomendada) / 2) No]\n"
         "- ¿Precio? [1) 9 € / 2) 19 € (recomendada)]")


def needs(tid, reason, *, assignee="claude-migrateam", blocked_at=1000.0, title=None):
    return {"task": {"id": tid, "title": title or f"Tarea {tid}", "body": BODY, "assignee": assignee,
                     "status": "blocked", "created_at": 10},
            "runs": [{"status": "blocked", "summary": reason, "metadata": []}],
            "events": [{"kind": "blocked", "payload": {"kind": "needs_input", "reason": reason},
                        "created_at": blocked_at}],
            "comments": []}


class KanbanHermes:
    """Kanban en memoria: lecturas del renotify y escrituras de las decisiones (unblock cambia el estado)."""

    def __init__(self, shows):
        self.shows = shows
        self.calls = []

    def list_status(self, assignee, status, sort="priority"):
        return [{"id": t, **s["task"]} for t, s in self.shows.items()
                if s["task"]["assignee"] == assignee and s["task"]["status"] == status]

    def show(self, tid):
        if tid not in self.shows:
            raise RuntimeError("no existe")
        return self.shows[tid]

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text, author))
        return True

    def unblock(self, tid):
        self.calls.append(("unblock", tid))
        self.shows[tid]["task"]["status"] = "ready"
        return True

    def assign(self, tid, profile):
        self.calls.append(("assign", tid, profile))
        return True


class Bot:
    bot_id = "999"

    def __init__(self):
        self.sent, self.edits, self.markups, self.answers, self.deleted, self.commands = [], [], [], [], [], {}
        self._next = 700

    def _msg(self, chat, thread):
        self._next += 1
        return {"chat_id": str(chat), "thread_id": str(thread or "0"), "message_id": self._next}

    def send(self, text, target=None, lane_target=None, *, silent=False, reply_markup=None):
        chat, thread = lane_target or (GESTION, "5")
        m = self._msg(chat, thread)
        self.sent.append({**m, "text": text, "markup": reply_markup})
        return m

    def send_to(self, chat_id, thread_id, text, *, html=True, silent=False, reply_markup=None, reply_to=None):
        m = self._msg(chat_id, thread_id)
        self.sent.append({**m, "text": text, "markup": reply_markup})
        return m

    def edit(self, chat_id, message_id, text, *, html=True, reply_markup=None):
        self.edits.append({"chat": str(chat_id), "mid": message_id, "text": text, "markup": reply_markup})
        return True

    def edit_markup(self, chat_id, message_id, reply_markup):
        self.markups.append((str(chat_id), message_id, reply_markup))
        return True

    def delete(self, chat_id, message_id):
        self.deleted.append(message_id)
        return True

    def answer_callback(self, cid, text=None, *, alert=False):
        self.answers.append(text)
        return True

    def set_my_commands(self, commands, scope="default"):
        self.commands[scope] = commands
        return True


def center(tmp_path, shows, *, brief=None):
    h = KanbanHermes(shows)
    bot = Bot()
    messages = MessageStore(tmp_path / "messages")
    desk = DecisionDesk(bot, CallbackStore(tmp_path / "cb"), lanes=LANES, hermes_for=lambda b: h, links=None,
                        spawn=lambda fn: fn(), messages=messages)
    ren = renotify.Renotifier(LANES, hermes_for=lambda b: h, notifier=bot, messages=messages, links=None, desk=desk,
                              branch_state=lambda lane, tid, sha: (True, False), out=lambda s: None)
    cc = CommandCenter(bot, desk, lanes=LANES, hermes_for=lambda b: h, messages=messages, generic_origins=GENERIC,
                       bot_username="pildora_carriles_bot", owner_id=str(OSCAR), base_url="https://k",
                       brief=brief, renotifier=ren, now=lambda: 1000.0 + 30 * 3600)
    desk.commands = cc
    return cc, desk, bot, h, messages


def command(desk, text, *, user=OSCAR, chat=OSCAR, thread=None, uid=1):
    msg = {"message_id": 50 + uid, "from": {"id": user}, "chat": {"id": chat}, "text": text}
    if thread:
        msg.update({"message_thread_id": thread, "is_topic_message": True})
    desk.handle_update({"update_id": uid, "message": msg})


def press(desk, sent_msg, n, *, user=OSCAR, cid="cq"):
    data = [b["callback_data"] for row in sent_msg["markup"]["inline_keyboard"] for b in row][n]
    desk.handle_update({"update_id": 99, "callback_query": {
        "id": cid, "from": {"id": user}, "data": data,
        "message": {"message_id": sent_msg["message_id"], "chat": {"id": int(sent_msg["chat_id"])},
                    "message_thread_id": int(sent_msg["thread_id"])}}})


def texts(markup):
    return [b["text"] for row in (markup or {}).get("inline_keyboard", []) for b in row]


# --- parseo y ámbito ---------------------------------------------------------------------------------

@pytest.mark.parametrize("text,out", [
    ("/decisiones", ("decisiones", "")),
    ("/decisiones@pildora_carriles_bot", ("decisiones", "")),
    ("/Tareas@Pildora_Carriles_Bot  pildora ", ("tareas", "pildora")),
    ("/tarea t_1234abcd", ("tarea", "t_1234abcd")),
    ("/hoy@hermes_bot", None),             # para otro bot: se ignora
    ("/otro", None), ("hola", None), ("", None), (None, None)])
def test_parse_command(text, out):
    assert parse_command(text, "pildora_carriles_bot") == out


@pytest.mark.parametrize("chat,thread,brand", [(GESTION, 230, "MigraTeam"), (GESTION, 231, "Píldora"),
                                               (GESTION, 5, None), (GESTION, None, None), (OSCAR, None, None)])
def test_scope_brand_by_topic(chat, thread, brand):
    assert scope_brand(chat, thread, LANES, GENERIC) == brand


def test_register_commands_in_three_scopes_with_spanish_descriptions():
    bot = Bot()
    assert register_commands(bot) == list(COMMAND_SCOPES)
    assert set(bot.commands) == {"default", "all_private_chats", "all_group_chats"}
    names = [c for c, _ in bot.commands["all_group_chats"]]
    assert names == ["hoy", "decisiones", "aprobar", "tareas", "tarea", "salud"]
    assert all(d and len(d) <= 256 for _, d in bot.commands["default"])


# --- solo Oscar ----------------------------------------------------------------------------------------

def test_only_oscar_can_use_commands(tmp_path):
    cc, desk, bot, h, _ = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC)})
    command(desk, "/decisiones@pildora_carriles_bot", user=1234, chat=GESTION, thread=230)
    assert [m["text"] for m in bot.sent] == ["Solo Oscar"]
    assert h.calls == []


def test_non_command_text_still_reaches_force_reply_handling(tmp_path):
    cc, desk, bot, h, _ = center(tmp_path, {})
    command(desk, "/no_es_nuestro")
    assert bot.sent == []


# --- /decisiones ---------------------------------------------------------------------------------------

def test_decisiones_header_and_one_card_per_task_with_buttons(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC, blocked_at=1000.0),
             "t_bbbbbbb2": needs("t_bbbbbbb2", Q_NOREC, assignee="claude-oscarhq", blocked_at=50000.0)}
    cc, desk, bot, h, messages = center(tmp_path, shows)
    command(desk, "/decisiones")
    head, *cards = bot.sent
    assert head["text"].startswith("❓ 2 decisiones · la más antigua hace 30 h")
    assert "1 con opción recomendada" in head["text"]
    assert texts(head["markup"]) == ["✅ Aceptar todo lo recomendado"]
    assert [c["text"].split("\n")[0].split(" · ")[0] for c in cards] == ["❓ t_aaaaaaa1", "❓ t_bbbbbbb2"]
    assert texts(cards[0]["markup"]) == ["A", "B", "⭐ Recomendada", "✍️ Otra", "🗄 Aparcar", "💬 Explícame más"]
    assert "hace 30 h" in cards[0]["text"]
    # cada tarjeta queda como copia del aviso de su tarea (sincronización)
    assert messages.all_messages("t_aaaaaaa1")[0]["message_id"] == cards[0]["message_id"]


def test_decisiones_in_a_brand_topic_only_shows_that_brand(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC),
             "t_bbbbbbb2": needs("t_bbbbbbb2", Q_NOREC, assignee="claude-oscarhq")}
    cc, desk, bot, h, _ = center(tmp_path, shows)
    command(desk, "/decisiones@pildora_carriles_bot", chat=GESTION, thread=231)
    assert bot.sent[0]["text"].startswith("❓ 1 decisión") and "Píldora" in bot.sent[0]["text"]
    assert "t_bbbbbbb2" in bot.sent[1]["text"] and len(bot.sent) == 2
    assert all(m["thread_id"] == "231" for m in bot.sent)  # responde en el mismo tema


def test_decisiones_without_pending(tmp_path):
    cc, desk, bot, h, _ = center(tmp_path, {})
    command(desk, "/decisiones")
    assert bot.sent[0]["text"].startswith("Nada pendiente de ti") and bot.sent[0]["markup"] is None


def test_no_accept_all_button_when_nothing_is_recommended(tmp_path):
    cc, desk, bot, h, _ = center(tmp_path, {"t_bbbbbbb2": needs("t_bbbbbbb2", Q_NOREC)})
    command(desk, "/decisiones")
    assert bot.sent[0]["markup"] is None and "Ninguna tiene opción recomendada" in bot.sent[0]["text"]


def test_accept_all_applies_recommended_and_leaves_the_rest(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC), "t_bbbbbbb2": needs("t_bbbbbbb2", Q_NOREC),
             "t_ccccccc3": needs("t_ccccccc3", Q_TWO)}
    cc, desk, bot, h, messages = center(tmp_path, shows)
    command(desk, "/decisiones")
    head = bot.sent[0]
    assert "2 con opción recomendada" in head["text"]
    press(desk, head, 0)
    assert ("comment", "t_aaaaaaa1", f"{ANSWER_PREFIX} ¿Publicar ya? → Sí", OSCAR_AUTHOR) in h.calls
    assert ("comment", "t_ccccccc3", f"{ANSWER_PREFIX} ¿Precio? → 19 €", OSCAR_AUTHOR) in h.calls
    assert [c for c in h.calls if c[0] == "unblock"] == [("unblock", "t_aaaaaaa1"), ("unblock", "t_ccccccc3")]
    assert not any(c[1] == "t_bbbbbbb2" for c in h.calls)  # sin recomendada: intacta
    header_edit = [e for e in bot.edits if e["mid"] == head["message_id"]][-1]
    assert header_edit["text"].startswith("✅ Aplicado lo recomendado en 2 tareas")
    assert "sin tocar: t_bbbbbbb2" in header_edit["text"]
    # las tarjetas de la bandeja de esas tareas se editan con la decisión y sin botones
    card_a = next(m for m in bot.sent if m["text"].startswith("❓ t_aaaaaaa1"))
    edit_a = [e for e in bot.edits if e["mid"] == card_a["message_id"]][-1]
    assert edit_a["text"].startswith("💬 t_aaaaaaa1") and edit_a["markup"] is None
    # sus botones individuales ya no valen (token consumido)
    press(desk, card_a, 0, cid="tarde")
    assert bot.answers[-1] == "Esta decisión ya no está activa"


def test_after_accept_all_the_skipped_tasks_keep_working_buttons(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC), "t_bbbbbbb2": needs("t_bbbbbbb2", Q_NOREC)}
    cc, desk, bot, h, _ = center(tmp_path, shows)
    command(desk, "/decisiones")
    card_b = next(m for m in bot.sent if m["text"].startswith("❓ t_bbbbbbb2"))
    press(desk, bot.sent[0], 0)
    press(desk, card_b, 0, cid="despues")  # 1) Mosquera en la tarea sin recomendada
    assert bot.answers[-1] == "Hecho, lo aplico"
    assert ("comment", "t_bbbbbbb2", f"{ANSWER_PREFIX} ¿Qué despacho primero? → Mosquera", OSCAR_AUTHOR) in h.calls


def test_keyboard_issued_during_the_action_stays_active(tmp_path):
    """Un botón que publica un teclado nuevo de la MISMA tarea (Fusionar -> [🚀 Desplegar]) no se lo auto-anula."""
    cc, desk, bot, h, messages = center(tmp_path, {})
    fresh = {}

    class Integrator:
        def on_button(self, action, rec, where, d):
            _, markup = d.store.issue(rec, [[{"text": "🚀 Desplegar", "action": "int_deploy"}]])
            TaskNotices(bot, messages).publish(rec["task_id"], "🔀 fusionada", None, MIG.telegram, alert=True,
                                               reply_markup=markup)
            fresh["token"] = markup["inline_keyboard"][0][0]["callback_data"].split(":")[0]
            return True

    desk.integrator = Integrator()
    rec = {"task_id": "t_1", "board": "migrateam", "lane": MIG.name, "title": "T"}
    _, merge = desk.store.issue(rec, [[{"text": "🔀 Fusionar", "action": "int_merge"}]])
    TaskNotices(bot, messages).publish("t_1", "✅ t_1", None, MIG.telegram, alert=True, reply_markup=merge)
    press(desk, bot.sent[-1], 0)
    assert desk.store.get(fresh["token"]) is not None


def test_accept_all_skips_tasks_already_decided(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC)}
    cc, desk, bot, h, _ = center(tmp_path, shows)
    command(desk, "/decisiones")
    shows["t_aaaaaaa1"]["task"]["status"] = "ready"  # la decidió en Hermes mientras tanto
    press(desk, bot.sent[0], 0)
    assert h.calls == []


def test_identical_questions_are_asked_once_and_applied_to_all(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_NOREC),
             "t_ddddddd4": needs("t_ddddddd4", Q_NOREC.replace("¿Qué", "¿qué  "), assignee="claude-oscarhq"),
             "t_bbbbbbb2": needs("t_bbbbbbb2", Q_REC)}
    cc, desk, bot, h, _ = center(tmp_path, shows)
    command(desk, "/decisiones")
    head, *cards = bot.sent
    assert len(cards) == 2 and "1 pregunta repetida agrupada" in head["text"]
    group = next(c for c in cards if c["text"].startswith("❓ Misma pregunta en 2 tareas"))
    assert "t_aaaaaaa1" in group["text"] and "t_ddddddd4" in group["text"]
    press(desk, group, 0)  # 1) Mosquera
    assert [c[:3] for c in h.calls if c[0] == "comment"] == [
        ("comment", "t_aaaaaaa1", f"{ANSWER_PREFIX} ¿Qué despacho primero? → Mosquera"),
        ("comment", "t_ddddddd4", f"{ANSWER_PREFIX} ¿qué   despacho primero? → Mosquera")]
    assert [c for c in h.calls if c[0] == "unblock"] == [("unblock", "t_aaaaaaa1"), ("unblock", "t_ddddddd4")]
    assert bot.edits[-1]["text"].startswith("💬 respondida en 2 tareas (t_aaaaaaa1, t_ddddddd4): Mosquera")


def test_group_free_reply_applies_to_all(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_NOREC), "t_ddddddd4": needs("t_ddddddd4", Q_NOREC)}
    cc, desk, bot, h, _ = center(tmp_path, shows)
    command(desk, "/decisiones")
    group = bot.sent[1]
    press(desk, group, 2)  # ✍️ Otra respuesta
    prompt = bot.sent[-1]
    desk.handle_update({"update_id": 5, "message": {
        "message_id": 900, "from": {"id": OSCAR}, "chat": {"id": OSCAR}, "text": "El de Madrid",
        "reply_to_message": {"message_id": prompt["message_id"]}}})
    assert [c[1] for c in h.calls if c[0] == "unblock"] == ["t_aaaaaaa1", "t_ddddddd4"]


# --- sincronización tema <-> DM ---------------------------------------------------------------------------

def _topic_notice(desk, bot, messages, tid, questions):
    """Aviso ❓ en el tema de la marca, como lo publica el runner (con su teclado)."""
    markup = desk.markup("needs_input", task={"id": tid, "title": f"Tarea {tid}", "body": BODY}, lane=MIG,
                         questions=questions)
    TaskNotices(bot, messages).publish(tid, f"❓ {tid}", None, MIG.telegram, alert=True, reply_markup=markup)
    return bot.sent[-1]


def test_deciding_in_the_dm_card_edits_the_topic_notice_and_vice_versa(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC), "t_eeeeeee5": needs("t_eeeeeee5", Q_TWO)}
    cc, desk, bot, h, messages = center(tmp_path, shows)
    q = [{"question": "¿Publicar ya?", "options": ["Sí", "No"], "recommended": 0}]
    topic_a = _topic_notice(desk, bot, messages, "t_aaaaaaa1", q)
    topic_e = _topic_notice(desk, bot, messages, "t_eeeeeee5", q)
    command(desk, "/decisiones")  # en el DM
    card_a = next(m for m in bot.sent if m["text"].startswith("❓ t_aaaaaaa1") and m["chat_id"] == str(OSCAR))
    card_e = next(m for m in bot.sent if m["text"].startswith("❓ t_eeeeeee5") and m["chat_id"] == str(OSCAR))
    # DM -> tema
    press(desk, card_a, 0)
    topic_edit = [e for e in bot.edits if e["mid"] == topic_a["message_id"]][-1]
    assert topic_edit["chat"] == GESTION and topic_edit["text"].startswith("💬 t_aaaaaaa1")
    assert topic_edit["markup"] is None
    press(desk, topic_a, 0, cid="otra-vez")  # el botón del tema ya no vale
    assert bot.answers[-1] == "Esta decisión ya no está activa"
    # tema -> DM
    press(desk, topic_e, 1)
    dm_edit = [e for e in bot.edits if e["mid"] == card_e["message_id"]][-1]
    assert dm_edit["chat"] == str(OSCAR) and dm_edit["text"].startswith("💬 t_eeeeeee5 ") and dm_edit["markup"] is None
    assert ("comment", "t_eeeeeee5", f"{ANSWER_PREFIX} ¿Publicar ya? → No", OSCAR_AUTHOR) in h.calls


def test_message_store_keeps_every_message_of_a_task(tmp_path):
    store = MessageStore(tmp_path / "m")
    store.put("t_1", {"chat_id": GESTION, "thread_id": "230", "message_id": 1, "bot": "999", "token": "A"})
    store.add_mirror("t_1", {"chat_id": str(OSCAR), "thread_id": "0", "message_id": 2}, token="B", bot="999")
    store.add_mirror("t_1", {"chat_id": str(OSCAR), "thread_id": "0", "message_id": 2}, token="C")  # mismo msg
    assert [(m["message_id"], m.get("token")) for m in store.all_messages("t_1")] == [(1, "A"), (2, "C")]
    assert store.get("t_1")["message_id"] == 1  # el registro principal no cambia de forma


def test_progress_edits_follow_to_the_copies_and_new_alert_retires_their_buttons(tmp_path):
    bot, store = Bot(), MessageStore()
    notices = TaskNotices(bot, store)
    notices.publish("t_1", "❓ t_1", None, MIG.telegram, alert=True)
    store.add_mirror("t_1", {"chat_id": str(OSCAR), "thread_id": "0", "message_id": 555}, token="X", bot="999")
    notices.publish("t_1", "▶️ t_1 en curso", None, MIG.telegram)
    assert {e["mid"] for e in bot.edits} == {bot.sent[0]["message_id"], 555}
    notices.publish("t_1", "❓ t_1 otra vez", None, MIG.telegram, alert=True)
    assert (str(OSCAR), 555, None) in bot.markups and "mirrors" not in store.get("t_1")


# --- /aprobar, /tareas, /tarea, /hoy ------------------------------------------------------------------

def test_aprobar_lists_ready_tasks_with_approve_buttons(tmp_path):
    shows = {"t_fffffff6": done_show("t_fffffff6", assignee="claude-oscarhq")}
    shows["t_fffffff6"]["task"]["id"] = "t_fffffff6"
    cc, desk, bot, h, messages = center(tmp_path, shows)
    command(desk, "/aprobar")
    head, card = bot.sent
    assert head["text"] == "✅ 1 tarea lista para integrar"
    assert card["text"].startswith("✅ t_fffffff6") and "lista para merge" in card["text"]
    assert texts(card["markup"]) == ["✅ Aprobar", "🔁 Pedir cambios", "🗄 Aparcar"]
    assert messages.all_messages("t_fffffff6")[0]["message_id"] == card["message_id"]


def test_tareas_one_message_per_brand_filter(tmp_path):
    shows = {"t_run00001": {"task": {"id": "t_run00001", "title": "Corre", "assignee": "claude-migrateam",
                                     "status": "running"}},
             "t_rdy00001": {"task": {"id": "t_rdy00001", "title": "Espera", "assignee": "claude-migrateam",
                                     "status": "ready"}},
             "t_rdy00002": {"task": {"id": "t_rdy00002", "title": "Píldora", "assignee": "claude-oscarhq",
                                     "status": "ready"}}}
    cc, desk, bot, h, _ = center(tmp_path, shows)
    command(desk, "/tareas migrateam")
    [msg] = bot.sent
    assert "<b>claude-migrateam</b>" in msg["text"] and "claude-oscarhq" not in msg["text"]
    assert "▶️ <a href=\"https://k/tasks/migrateam/t_run00001\">t_run00001</a> · Corre" in msg["text"]
    assert "⏳ " in msg["text"]
    command(desk, "/tareas", uid=2)
    assert all(l in bot.sent[-1]["text"] for l in ("claude-migrateam", "claude-oscarhq", "claude-nextjobs"))
    command(desk, "/tareas marte", uid=3)
    assert bot.sent[-1]["text"].startswith("Marca desconocida")


def test_tarea_validates_id_and_shows_card_with_state_buttons(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC)}
    cc, desk, bot, h, messages = center(tmp_path, shows)
    command(desk, "/tarea --board x")
    assert bot.sent[-1]["text"] == "Uso: /tarea t_xxxxxxxx"
    command(desk, "/tarea t_00000000", uid=2)
    assert bot.sent[-1]["text"].startswith("No encuentro t_00000000")
    command(desk, "/tarea t_aaaaaaa1", uid=3)
    card = bot.sent[-1]
    assert card["text"].startswith("❓ t_aaaaaaa1") and texts(card["markup"])[0] == "A"
    assert messages.all_messages("t_aaaaaaa1")[-1]["message_id"] == card["message_id"]


def test_hoy_reuses_the_brief_with_the_topic_brand(tmp_path):
    calls = []
    cc, desk, bot, h, _ = center(tmp_path, {}, brief=lambda brand: calls.append(brand) or "☀️ resumen")
    command(desk, "/hoy@pildora_carriles_bot", chat=GESTION, thread=230)
    command(desk, "/hoy", uid=2)
    assert calls == ["MigraTeam", None] and [m["text"] for m in bot.sent] == ["☀️ resumen", "☀️ resumen"]


def test_command_failure_is_reported_without_details(tmp_path):
    def boom(brand):
        raise RuntimeError("C:/ruta/secreta")
    cc, desk, bot, h, _ = center(tmp_path, {}, brief=boom)
    command(desk, "/hoy")
    assert bot.sent[-1]["text"] == "No pude completar /hoy; detalle en el log"


def test_pending_decisions_are_oldest_first_with_since(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC, blocked_at=9000.0),
             "t_bbbbbbb2": needs("t_bbbbbbb2", Q_REC, blocked_at=2000.0)}
    cc, *_ = center(tmp_path, shows)
    assert [(p.tid, p.since) for p in cc.pending_decisions()] == [("t_bbbbbbb2", 2000.0), ("t_aaaaaaa1", 9000.0)]


def test_aprobar_orders_by_dependency_and_marks_waiting(tmp_path):
    # t_fffffff6 (iría primero por ID) necesita el código de t_fffffff7, aún sin integrar.
    shows = {"t_fffffff7": done_show("t_fffffff7", assignee="claude-oscarhq"),
             "t_fffffff6": done_show("t_fffffff6", assignee="claude-oscarhq")}
    for tid, s in shows.items():
        s["task"]["id"] = tid
        s["task"]["branch_name"] = f"lane/{tid}"
    shows["t_fffffff6"]["parents"] = ["t_fffffff7"]
    cc, desk, bot, h, messages = center(tmp_path, shows)
    command(desk, "/aprobar")
    head, first, second = bot.sent
    assert "Orden recomendado" in head["text"]
    assert first["text"].startswith("✅ t_fffffff7") and "⏸" not in first["text"]
    assert second["text"].startswith("✅ t_fffffff6") and "⏸ espera a t_fffffff7 (sin integrar)" in second["text"]
