"""/tareas desde el móvil (23-10): número -> ficha, final del id, respuesta libre a la ficha, permisos y botones por
estado. Sin red ni Telegram real: el `center` de test_commands con un kanban en memoria ampliado."""
from __future__ import annotations

from agent_lanes.tareas_movil import MapaMensajes, coincidencias, es_pregunta
from tests.test_commands import KanbanHermes, OSCAR, center, command

MARIA = 777


def press(desk, sent_msg, n):
    """Pulsa el botón n-ésimo con callback (los de URL, como «🗂 Tarjeta», no cuentan)."""
    data = [b["callback_data"] for row in sent_msg["markup"]["inline_keyboard"] for b in row if "callback_data" in b][n]
    desk.handle_update({"update_id": 99, "callback_query": {
        "id": "cq", "from": {"id": OSCAR}, "data": data,
        "message": {"message_id": sent_msg["message_id"], "chat": {"id": int(sent_msg["chat_id"])},
                    "message_thread_id": int(sent_msg["thread_id"])}}})


class Kanban(KanbanHermes):
    def list_status_all(self, status):
        return [{"id": t, **s["task"]} for t, s in self.shows.items() if s["task"]["status"] == status]

    def set_priority(self, tid, prio):
        self.calls.append(("prio", tid, prio))
        self.shows[tid]["task"]["priority"] = prio
        return True

    def archive(self, tid):
        self.calls.append(("archive", tid))
        return True

    def reopen_review(self, tid):
        self.calls.append(("reopen", tid))
        return True


def task(tid, status="ready", assignee="claude-migrateam", title=None, **extra):
    return {"task": {"id": tid, "title": title or f"Tarea {tid}", "body": "## Objetivo\nX\n", "assignee": assignee,
                     "status": status, "created_at": 10, "priority": 0, **extra},
            "runs": [], "events": [], "comments": []}


def make(tmp_path, tasks):
    cc, desk, bot, h, messages = center(tmp_path, {t["task"]["id"]: t for t in tasks})
    h.__class__ = Kanban
    return cc, desk, bot, h


def reply(desk, to_msg, text, *, user=OSCAR, uid=200):
    desk.handle_update({"update_id": uid, "message": {
        "message_id": 900 + uid, "from": {"id": user}, "chat": {"id": int(to_msg["chat_id"]), "type": "private"},
        "text": text, "reply_to_message": {"message_id": to_msg["message_id"]}}})


def test_coincidencias_prefers_the_end_and_falls_back_to_anywhere():
    ids = ["t_d3d243d4", "t_aaaa3d4b", "t_11113d4f"]
    assert coincidencias(ids, "3d4") == ["t_d3d243d4"]  # solo uno termina en 3d4
    assert coincidencias(ids, "3d4B") == ["t_aaaa3d4b"]
    assert coincidencias(ids, "3d4f") == ["t_11113d4f"]
    assert coincidencias(ids, "aaaa") == ["t_aaaa3d4b"]  # sin final: en cualquier posición
    assert coincidencias(ids, "zzz") == [] and coincidencias(ids, "--board") == [] and coincidencias(ids, "12") == []
    assert coincidencias(ids, "d243d4") == ["t_d3d243d4"]


def test_es_pregunta():
    assert es_pregunta("¿qué falta?") and es_pregunta("cuándo acaba") and es_pregunta("está bloqueada?")
    assert not es_pregunta("añade un test para el borde") and not es_pregunta("cambia el título")


def test_mapa_mensajes_expires_after_24h_and_persists(tmp_path):
    path = tmp_path / "m.json"
    MapaMensajes(path).put("1", 5, "lista", {"n": {"1": "t_a"}}, now=1000.0)
    again = MapaMensajes(path)  # otro proceso: lee del disco
    assert again.get("1", 5, 1000.0 + 23 * 3600)["data"]["n"] == {"1": "t_a"}
    assert again.get("1", 5, 1000.0 + 25 * 3600) is None and again.get("1", 6, 1000.0) is None


def test_tareas_numbers_lines_and_reply_with_number_sends_the_card(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_aaaaaaa1", "running"), task("t_bbbbbbb2", "ready", title="Segunda")])
    command(desk, "/tareas")
    page = bot.sent[-1]
    assert "1. ▶️" in page["text"] and "2. ⏳" in page["text"]
    reply(desk, page, "2")
    assert "Segunda" in bot.sent[-1]["text"] and "t_bbbbbbb2" in bot.sent[-1]["text"]
    reply(desk, page, "9", uid=201)
    assert "No hay línea 9" in bot.sent[-1]["text"]
    reply(desk, page, "hola", uid=202)
    assert "número de la línea" in bot.sent[-1]["text"]


def test_cola_numbers_globally_and_reply_opens_the_card(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_aaaaaaa1", "running"), task("t_bbbbbbb2", "ready")])
    command(desk, "/cola")
    msg = bot.sent[-1]
    assert "1. ▶️ <code>t_aaaaaaa1" in msg["text"] and "2. <code>t_bbbbbbb2" in msg["text"]
    reply(desk, msg, "1")
    assert "t_aaaaaaa1" in bot.sent[-1]["text"]


def test_number_map_expires_after_24h(tmp_path):
    clock = {"t": 1000.0 + 30 * 3600}
    cc, desk, bot, h = make(tmp_path, [task("t_aaaaaaa1", "ready")])
    cc._now = lambda: clock["t"]
    command(desk, "/tareas")
    page = bot.sent[-1]
    clock["t"] += 25 * 3600
    msg = {"chat": {"id": int(page["chat_id"])}, "reply_to_message": {"message_id": page["message_id"]}}
    assert cc.reply_target(msg) is None  # caducado: la respuesta ya no abre la ficha
    clock["t"] -= 2 * 3600
    assert cc.reply_target(msg)["kind"] == "lista"


def test_suffix_single_match_sends_the_card_for_tarea_and_tareas(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_d3d243d4", "ready", title="Única"), task("t_aaaaaaa1", "ready")])
    command(desk, "/tarea 3d4")
    assert "t_d3d243d4" in bot.sent[-1]["text"] and "Única" in bot.sent[-1]["text"]
    command(desk, "/tareas 243d4", uid=2)
    assert "t_d3d243d4" in bot.sent[-1]["text"]


def test_suffix_several_matches_offers_buttons_and_none_says_so(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_1111abc1", "ready", title="Uno"),
                                       task("t_2222abc1", "done", title="Dos")])
    command(desk, "/tarea abc1")
    ask = bot.sent[-1]
    labels = [b["text"] for row in ask["markup"]["inline_keyboard"] for b in row]
    assert labels == ["t_1111abc1 · Uno", "t_2222abc1 · Dos"]
    press(desk, ask, 1)
    assert "t_2222abc1" in bot.sent[-1]["text"] and "Dos" in bot.sent[-1]["text"]
    command(desk, "/tarea fff", uid=3)
    assert "No encuentro ninguna tarea" in bot.sent[-1]["text"]


def test_suffix_with_too_many_matches_shows_at_most_eight(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task(f"t_{i:04x}abcd", "ready") for i in range(12)])
    command(desk, "/tarea abcd")
    buttons = [b for row in bot.sent[-1]["markup"]["inline_keyboard"] for b in row]
    assert len(buttons) == 8


def test_free_text_is_never_passed_to_the_cli_as_a_suffix(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_aaaaaaa1", "ready")])
    seen = []
    h.list_status_all = lambda status: seen.append(status) or []
    command(desk, "/tarea --board x")
    command(desk, "/tarea zz", uid=2)  # no es hex
    assert seen == [] and all("Uso" in s["text"] for s in bot.sent)


def test_reply_to_card_with_instruction_comments_and_offers_buttons(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_aaaaaaa1", "blocked")])
    command(desk, "/tarea t_aaaaaaa1")
    ficha = bot.sent[-1]
    reply(desk, ficha, "añade un test para el caso vacío")
    assert ("comment", "t_aaaaaaa1", "Nota de Oscar desde Telegram: añade un test para el caso vacío",
            "oscar-telegram") in h.calls
    ask = bot.sent[-1]
    assert [b["text"] for row in ask["markup"]["inline_keyboard"] for b in row] == ["🔁 Relanzar con esto",
                                                                                  "📝 Solo anotar"]
    press(desk, ask, 0)
    assert ("unblock", "t_aaaaaaa1") in h.calls
    assert "relanzada" in bot.edits[-1]["text"]


def test_reply_to_card_solo_anotar_does_not_touch_the_task(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_aaaaaaa1", "review")])
    command(desk, "/tarea t_aaaaaaa1")
    reply(desk, bot.sent[-1], "pide que revise los logs")
    press(desk, bot.sent[-1], 1)
    assert not any(c[0] in ("reopen", "unblock") for c in h.calls) and "Solo anotado" in bot.edits[-1]["text"]


def test_reply_to_card_with_question_answers_with_the_real_state(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_aaaaaaa1", "ready", priority=40)])
    h.shows["t_aaaaaaa1"]["comments"] = [{"author": "lane-review", "body": "Falta cubrir el borde"}]
    command(desk, "/tarea t_aaaaaaa1")
    n = len(h.calls)
    reply(desk, bot.sent[-1], "¿qué falta?")
    text = bot.sent[-1]["text"]
    assert "en cola" in text and "prioridad 40" in text and "Falta cubrir el borde" in text
    assert len(h.calls) == n  # una pregunta no escribe nada


def test_card_buttons_for_every_state(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_e0000001", "ready"), task("t_e0000002", "running"),
                                       task("t_e0000003", "review"), task("t_e0000004", "triage")])
    expected = {"t_e0000001": ["⬆️ Prioridad", "⏸ Pausar", "🔗 Fusionar con…", "🗂 Tarjeta"],
                "t_e0000002": ["⬆️ Prioridad", "⏸ Pausar", "🔗 Fusionar con…", "🗂 Tarjeta"],
                "t_e0000003": ["✅ Aprobar", "🔁 Pedir cambios", "🗂 Tarjeta"],
                "t_e0000004": ["▶️ Sacar de triage", "🗂 Archivar", "🗂 Tarjeta"]}
    for i, (tid, labels) in enumerate(expected.items()):
        command(desk, f"/tarea {tid}", uid=i + 1)
        got = [b["text"] for row in bot.sent[-1]["markup"]["inline_keyboard"] for b in row]
        propios = [t for t in labels if t != "🗂 Tarjeta"]
        assert got[:len(propios)] == propios and got[-1] == "🗂 Tarjeta", tid
        # botonera estándar (F1-14): ningún estado sin Responder, Integrador, Dar por hecha y Archivar
        assert {"✍️ Responder", "🧑‍💻 Integrador", "✅ Dar por hecha", "➕ Más"} <= set(got), tid
        assert any("Archivar" in t or "Pausar" in t for t in got), tid


def test_priority_button_raises_and_restores_the_keyboard(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_e0000001", "ready", priority=5)])
    command(desk, "/tarea t_e0000001")
    press(desk, bot.sent[-1], 0)
    assert ("prio", "t_e0000001", 15) in h.calls and "prioridad 5 → 15" in bot.sent[-1]["text"]
    assert bot.markups  # teclado repuesto para poder subir otra vez


def test_archive_button_in_triage(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_e0000004", "triage")])
    command(desk, "/tarea t_e0000004")
    press(desk, bot.sent[-1], 1)
    assert ("archive", "t_e0000004") in h.calls and "archivada" in bot.edits[-1]["text"]


def test_fusion_button_asks_for_the_other_task_and_leaves_a_note_in_both(tmp_path):
    cc, desk, bot, h = make(tmp_path, [task("t_e0000001", "ready"), task("t_d3d243d4", "ready")])
    command(desk, "/tarea t_e0000001")
    press(desk, bot.sent[-1], 2)
    prompt = bot.sent[-1]
    assert "¿Con qué tarea se fusiona" in prompt["text"]
    reply(desk, prompt, "3d4")
    notes = [c for c in h.calls if c[0] == "comment"]
    assert {c[1] for c in notes} == {"t_e0000001", "t_d3d243d4"} and "duplicada con" in notes[0][2]
    assert "no se fusiona nada solo" in bot.sent[-1]["text"]


def test_restricted_operator_cannot_act_on_other_brands(tmp_path, monkeypatch):
    cc, desk, bot, h = make(tmp_path, [task("t_e0000005", "ready"),
                                       task("t_e0000006", "ready", assignee="claude-oscarhq")])
    desk.restringido = lambda uid: str(uid) == str(MARIA)
    desk.puede_en = lambda uid, marca: str(uid) != str(MARIA) or marca == "MigraTeam"
    desk.es_operador = lambda uid: True
    command(desk, "/tareas", user=MARIA, chat=MARIA)
    page = bot.sent[-1]
    n = page["text"]
    reply(desk, page, "1", user=MARIA)  # la primera es la de su marca: ficha
    assert "t_e0000005" in bot.sent[-1]["text"] or "t_e0000006" in bot.sent[-1]["text"]
    command(desk, "/tarea e0000006", user=MARIA, chat=MARIA, uid=5)  # sufijo de otra marca: no se ve
    assert "No encuentro ninguna tarea" in bot.sent[-1]["text"]
    ficha = None
    command(desk, "/tarea t_e0000005", user=MARIA, chat=MARIA, uid=6)
    ficha = bot.sent[-1]
    reply(desk, ficha, "añade algo", user=MARIA, uid=7)
    assert any(c[0] == "comment" for c in h.calls)  # en su marca sí puede
    # una ficha de otra marca (conservada por Oscar) no acepta notas de María
    command(desk, "/tarea t_e0000006", uid=8)
    other = bot.sent[-1]
    before = len([c for c in h.calls if c[0] == "comment"])
    reply(desk, other, "añade algo", user=MARIA, uid=9)
    assert len([c for c in h.calls if c[0] == "comment"]) == before
    assert "No tienes permiso" in bot.sent[-1]["text"]
