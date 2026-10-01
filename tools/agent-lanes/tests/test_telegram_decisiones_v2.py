"""Telegram decisiones v2 (t_40fe70c3, puntos 1-2-4-5): opciones A/B/C escritas en el mensaje, ✅ Acepto todas las
recomendadas en una tarjeta con varias preguntas, mensaje NUEVO cuando a una ficha le sale un botón, y /decisiones con
las 5 más importantes + "N más · ver todas". Dobles en memoria: ni red ni Telegram ni hermes reales."""
from __future__ import annotations

from agent_lanes import renotify
from agent_lanes.hermes import ANSWER_PREFIX, OSCAR_AUTHOR
from agent_lanes.notices import markup_token
from tests import test_integrator as ti
from tests.test_commands import Q_NOREC, Q_REC, Q_TWO, center, command, needs, press, texts
from tests.test_preguntas_secuencia import QS, THREE, ask, setup

ACCEPT = "✅ Acepto todas las recomendadas"


def _card(bot, tid):
    return next(m for m in bot.sent if m["text"].startswith(f"❓ {tid}"))


# --- 1. opciones escritas, botones genéricos ------------------------------------------------------------

def test_options_are_written_in_the_message_and_buttons_are_generic(tmp_path):
    cc, desk, bot, h, _ = center(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC, blocked_at=1000.0),
                                            "t_bbbbbbb2": needs("t_bbbbbbb2", Q_NOREC, blocked_at=2000.0)})
    command(desk, "/decisiones")
    rec, norec = _card(bot, "t_aaaaaaa1"), _card(bot, "t_bbbbbbb2")
    assert "• ¿Publicar ya?\n   A) Sí ⭐\n   B) No" in rec["text"]
    assert "• ¿Qué despacho primero?\n   A) Mosquera\n   B) Otro" in norec["text"] and "⭐" not in norec["text"]
    assert texts(rec["markup"]) == ["A", "B", "⭐ Recomendada", "✍️ Otra", "🗄 Aparcar", "💬 Explícame más"]
    assert texts(norec["markup"]) == ["A", "B", "✍️ Otra", "🗄 Aparcar", "💬 Explícame más"]  # sin recomendada

    press(desk, rec, 2)  # ⭐ Recomendada: se registra el TEXTO de la opción, no la letra
    assert h.calls[0] == ("comment", "t_aaaaaaa1", f"{ANSWER_PREFIX} ¿Publicar ya? → Sí", OSCAR_AUTHOR)
    press(desk, norec, 1, cid="b")  # botón B
    assert h.calls[2] == ("comment", "t_bbbbbbb2", f"{ANSWER_PREFIX} ¿Qué despacho primero? → Otro", OSCAR_AUTHOR)


# --- 2. aceptar todas las recomendadas en una tarjeta -----------------------------------------------------

def test_accept_all_recommended_answers_every_pending_question_of_the_card(tmp_path):
    cc, desk, bot, h, messages, _ = setup(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_TWO)})
    topic, dm = ask(desk, bot, messages, h, questions=renotify.parse_questions(Q_TWO))
    assert texts(topic["markup"])[-1] == ACCEPT
    press(desk, topic, texts(topic["markup"]).index(ACCEPT))
    assert [c[2] for c in h.calls if c[0] == "comment"] == [f"{ANSWER_PREFIX} ¿Publicar ya? → Sí",
                                                             f"{ANSWER_PREFIX} ¿Precio? → 19 €"]
    assert [c for c in h.calls if c[0] == "unblock"] == [("unblock", "t_aaaaaaa1")]  # un único desbloqueo
    for e in bot.edits[-2:]:  # el tema y el DM
        assert "respondida (lo recomendado): Sí · 19 €" in e["text"] and e["markup"] is None


def test_accept_all_button_only_when_every_pending_question_has_a_recommendation(tmp_path):
    cc, desk, bot, h, messages, _ = setup(tmp_path, {"t_aaaaaaa1": needs("t_aaaaaaa1", THREE)})
    topic, _dm = ask(desk, bot, messages, h, questions=QS)  # la 3ª no tiene recomendada
    assert ACCEPT not in texts(topic["markup"])
    cc, desk, bot, h, _ = center(tmp_path / "x", {"t_aaaaaaa1": needs("t_aaaaaaa1", Q_REC)})
    command(desk, "/decisiones")
    assert ACCEPT not in texts(_card(bot, "t_aaaaaaa1")["markup"])  # una sola pregunta: ya está ⭐ Recomendada


# --- 4. botón nuevo, mensaje nuevo ------------------------------------------------------------------------

def _with_send_to(monkeypatch):
    sent = []

    def send_to(self, chat_id, thread_id, text, *, html=True, silent=False, reply_markup=None, reply_to=None):
        sent.append({"chat": str(chat_id), "thread": thread_id, "text": text, "markup": reply_markup})
        return {"chat_id": str(chat_id), "thread_id": str(thread_id or "0"), "message_id": 900 + len(sent)}

    monkeypatch.setattr(ti.FakeTg, "send_to", send_to, raising=False)
    return sent


def test_new_button_on_a_card_sends_a_new_message_to_the_topic_and_the_dm(tmp_path, monkeypatch):
    sent = _with_send_to(monkeypatch)
    integ, w, h, tg, d, _ = ti.make(tmp_path)
    integ.run_pass()
    ti.press(d, tg)  # 🔀 Fusionar: la ficha gana [🚀 Desplegar]
    assert ti.buttons(tg.edits[-1]["markup"]) == ["🚀 Desplegar"]  # la ficha original sigue editándose
    assert [(m["chat"], m["thread"]) for m in sent] == [("-100", "231"), (str(ti.OSCAR), None)]
    for m in sent:
        assert m["text"].startswith("🔔 t_1") and "Ahora puedes pulsar: 🚀 Desplegar" in m["text"]
        assert ti.buttons(m["markup"]) == ["🚀 Desplegar"]
    assert len({markup_token(m["markup"]) for m in sent}) == 1
    # el mismo teclado no se vuelve a anunciar
    integ._announce_buttons(d, {"task_id": "t_1", "title": "x"}, sent[0]["markup"], "otra vez")
    assert len(sent) == 2
    # el mensaje nuevo es pulsable y queda sincronizado con la ficha (copias en el store)
    assert len(integ._notices.store.all_messages("t_1")) == 3


def test_new_button_message_failure_does_not_break_the_edit(tmp_path, monkeypatch):
    def boom(self, *a, **k):
        raise RuntimeError("chat not found")

    monkeypatch.setattr(ti.FakeTg, "send_to", boom, raising=False)
    integ, w, h, tg, d, _ = ti.make(tmp_path)
    integ.run_pass()
    ti.press(d, tg)
    assert "✅ fusionado" in tg.edits[-1]["text"] and ti.buttons(tg.edits[-1]["markup"]) == ["🚀 Desplegar"]


# --- 5. /decisiones top 5 -------------------------------------------------------------------------------

def _pending(n):
    return {f"t_aaaaaa{i:02d}": needs(f"t_aaaaaa{i:02d}",
                                      f"El worker necesita decisión:\n- ¿Publicar {i}? [1) Sí (recomendada) / 2) No]",
                                      blocked_at=1000.0 + i) for i in range(n)}


def test_decisiones_shows_the_top_5_each_with_buttons_and_a_see_all_line(tmp_path):
    cc, desk, bot, h, _ = center(tmp_path, _pending(7))
    command(desk, "/decisiones")
    head, *cards, more = bot.sent
    assert len(cards) == 5 and head["text"].startswith("❓ 7 decisiones")  # "aceptar todo" sigue cubriendo las 7
    assert [c["text"].split(" · ")[0] for c in cards] == [f"❓ t_aaaaaa{i:02d}" for i in range(5)]  # las más antiguas
    assert all(texts(c["markup"])[0] == "A" for c in cards)
    assert more["text"] == "➕ 2 más · ver todas" and texts(more["markup"]) == ["📋 Ver todas"]
    press(desk, more, 0)
    full = bot.sent[7:]  # tras la línea "N más": cabecera + las 7 tarjetas, sin otra línea "ver todas"
    assert len(full) == 8 and [c["text"].split(" · ")[0] for c in full[1:]] == [f"❓ t_aaaaaa{i:02d}" for i in range(7)]
    assert "ver todas" not in "\n".join(m["text"] for m in full)


def test_decisiones_ranks_agent_blockers_before_oscar_cards_and_keeps_up_to_5_as_before(tmp_path):
    shows = _pending(4)
    for i in range(3):
        card = needs(f"t_cccccc{i:02d}", "x", assignee="oscar", title=f"[DECISIÓN] Tema {i}")
        card["task"]["created_at"] = 1 + i  # mucho más antiguas que las preguntas de los agentes
        shows[card["task"]["id"]] = card
    cc, desk, bot, h, _ = center(tmp_path, shows)
    command(desk, "/decisiones")
    body = [m["text"] for m in bot.sent]
    assert sum(t.startswith("❓ t_aaaaaa") for t in body) == 4  # primero lo que bloquea agentes
    assert sum(t.startswith("📌 ") for t in body) == 1 and "t_cccccc00" in body[-2]  # luego la tarjeta más antigua
    assert body[-1] == "➕ 2 más · ver todas"

    cc, desk, bot, h, _ = center(tmp_path / "few", _pending(5))
    command(desk, "/decisiones")
    assert len(bot.sent) == 6 and not any("ver todas" in m["text"] for m in bot.sent)  # ≤5: la bandeja de siempre
