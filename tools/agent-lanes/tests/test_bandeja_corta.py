"""Bandeja de decisiones más corta (t_169d4fd5): ajustes en bandeja.yaml, desduplicado, resumen por origen, auto-aplazado,
botones con texto corto, respuesta libre "A, pero con X", aceptar el lote y plazo de veto. Dobles en memoria."""
from __future__ import annotations

from datetime import datetime

from agent_lanes import bandeja
from agent_lanes.bandeja import Bandeja, VetoBook
from agent_lanes.decisions import DecisionDesk
from agent_lanes.reminders import MADRID, Reminders
from tests.test_botones_tarjetas import BODY, card, comments_of, decisiones, setup
from tests.test_commands import OSCAR, command, press, texts

REV = BODY + "Reversible: sí\n"


def _dm(bot):
    """Avisos propios de la bandeja al DM de Oscar (aplazadas solas / veto), sin las tarjetas de /decisiones."""
    return [m["text"] for m in bot.sent if m["chat_id"] == str(OSCAR) and m["text"].startswith(("⏳ Aplazadas", "⏱ Plazo"))]


# --- ajustes ---------------------------------------------------------------------------------------------

def test_load_reads_the_yaml_and_falls_back_to_defaults(tmp_path):
    assert bandeja.load(tmp_path / "no-existe.yaml") == Bandeja()
    f = tmp_path / "b.yaml"
    f.write_text("top: 3\ndedupe: false\nautosnooze_dias: 30\nveto_horas: 24\nveto_modo: ejecutar\n", encoding="utf-8")
    cfg = bandeja.load(f)
    assert (cfg.top, cfg.dedupe, cfg.autosnooze_dias, cfg.veto_horas, cfg.veto_modo) == (3, False, 30, 24, "ejecutar")
    f.write_text("top: -4\nveto_modo: yolo\nautosnooze_dias: mucho\n: : :", encoding="utf-8")
    assert bandeja.load(f) == Bandeja()  # inválido -> defaults, y un modo desconocido nunca ejecuta
    f.write_text("top: 0\nveto_modo: yolo\nprioridad_baja_max: 2\n", encoding="utf-8")
    cfg = bandeja.load(f)
    assert cfg.top == 5 and cfg.veto_modo == "aviso" and cfg.prioridad_baja_max == 2


def test_the_shipped_bandeja_yaml_matches_the_code_defaults():
    assert bandeja.load() == Bandeja()
    assert Bandeja().veto_modo == "aviso"  # el veto empieza solo avisando (decisión de Oscar 13-10)


# --- desduplicar, ordenar, agrupar -----------------------------------------------------------------------

def test_title_key_ignores_tags_accents_and_punctuation():
    assert bandeja.title_key("[DECISIÓN] ¿Precio del plan?") == bandeja.title_key("[SEMANA] Precio del plan") == "precio del plan"


def test_dedupe_keeps_the_oldest_and_links_the_repeats():
    def c(tid, title, since, board="migrateam"):
        return {"board": board, "task": {"id": tid, "title": title}, "since": since}
    out = bandeja.dedupe([c("t_1", "[DECISIÓN] Precio", 1), c("t_2", "[SEMANA] Precio", 2), c("t_3", "[DECISIÓN] Precio", 3,
                                                                                            "oscarhq"),
                          c("t_4", "", 4), c("t_5", "", 5)])
    assert [x["task"]["id"] for x in out] == ["t_1", "t_3", "t_4", "t_5"]  # otro origen / sin título: no se funden
    assert out[0]["duplicates"] == ["t_2"] and "duplicates" not in out[1]


def test_rank_puts_priority_first_then_oldest():
    cards = [{"board": "b", "task": {"id": "t_a", "priority": 0}, "since": 1},
             {"board": "b", "task": {"id": "t_b", "priority": 5}, "since": 9},
             {"board": "b", "task": {"id": "t_c"}, "since": 0}]
    assert [c["task"]["id"] for c in bandeja.rank(cards)] == ["t_b", "t_c", "t_a"]


def test_duplicated_cards_become_one_message_that_names_its_twins(tmp_path):
    shows = {"t_d0000001": card("t_d0000001", body=BODY, created_at=1.0),
             "t_d0000002": card("t_d0000002", "[SEMANA] Precio", body=BODY, created_at=2.0)}
    cc, desk, bot, h = setup(tmp_path, shows)
    assert [c["task"]["id"] for c in cc.decision_cards()] == ["t_d0000001"]
    decisiones(desk)
    [msg] = [m for m in bot.sent if m["text"].startswith("📌 <a")]
    assert "Repetida en: t_d0000002" in msg["text"]
    cc.bandeja = Bandeja(dedupe=False)
    assert len(cc.decision_cards()) == 2


def test_summary_shows_top_n_and_the_rest_grouped_by_origin(tmp_path):
    shows = {f"t_d00000{i:02d}": card(f"t_d00000{i:02d}", f"[DECISIÓN] Tema {i}", created_at=10.0 + i, body=BODY)
             for i in range(6)}
    cc, desk, bot, h = setup(tmp_path, shows)
    cc.bandeja = Bandeja(top=2)
    decisiones(desk)
    assert len([m for m in bot.sent if m["text"].startswith("📌 <a")]) == 2
    assert bot.sent[-1]["text"] == "➕ 4 más (MigraTeam 4) · ver todas"
    assert "⭐ 2 tarjetas con recomendada" in [m["text"] for m in bot.sent]  # el lote enseñado


# --- auto-aplazar ----------------------------------------------------------------------------------------

def test_low_priority_old_cards_snooze_themselves_once_with_a_notice(tmp_path):
    shows = {"t_d0000001": card("t_d0000001", "[DECISIÓN] Baja vieja", created_at=1000.0, body=BODY),
             "t_d0000002": card("t_d0000002", "[DECISIÓN] Alta vieja", created_at=1000.0, body=BODY),
             "t_d0000003": card("t_d0000003", "[DECISIÓN] Sin prioridad", created_at=1000.0, body=BODY),
             "t_d0000004": card("t_d0000004", "[DECISIÓN] Baja reciente", created_at=1000.0 + 20 * 86400, body=BODY),
             "t_d0000005": card("t_d0000005", "[DECISIÓN] Prioridad 0 vieja", created_at=1000.0, body=BODY)}
    for tid, p in (("t_d0000001", -1), ("t_d0000002", 3), ("t_d0000004", -1), ("t_d0000005", 0)):
        shows[tid]["task"]["priority"] = p
    cc, desk, bot, h = setup(tmp_path, shows)
    cc._now = lambda: 1000.0 + 20 * 86400 + 5
    cc.bandeja = Bandeja(autosnooze_dias=14)
    ids = [c["task"]["id"] for c in cc.decision_cards()]
    assert sorted(ids) == ["t_d0000002", "t_d0000003", "t_d0000004", "t_d0000005"]  # solo la 1 (prio -1) se aplaza; la 5 (prio 0, 20 días) no
    assert ids[0] == "t_d0000002"
    assert "t_d0000001" in desk.snoozes.hidden()
    [notice] = _dm(bot)
    assert "Aplazadas solas 1 tarjeta" in notice and "t_d0000001" in notice
    cc.decision_cards()
    assert len(_dm(bot)) == 1  # aplazada: no reaparece ni se vuelve a avisar


def test_autosnooze_can_be_switched_off_and_never_touches_unknown_priority(tmp_path):
    shows = {"t_d0000001": card("t_d0000001", created_at=1.0, body=BODY)}
    shows["t_d0000001"]["task"]["priority"] = -1
    cc, desk, bot, h = setup(tmp_path, shows)
    cc._now = lambda: 1000.0 * 86400
    cc.bandeja = Bandeja(autosnooze_dias=0)
    assert len(cc.decision_cards()) == 1 and not _dm(bot)


def test_default_priority_zero_card_waiting_30_days_is_not_snoozed_with_defaults(tmp_path):
    shows = {"t_d0000001": card("t_d0000001", created_at=1.0, body=BODY),
             "t_d0000002": card("t_d0000002", "[DECISIÓN] Sin prioridad", created_at=1.0, body=BODY)}
    shows["t_d0000001"]["task"]["priority"] = 0
    cc, desk, bot, h = setup(tmp_path, shows)
    cc._now = lambda: 1.0 + 30 * 86400
    cc.bandeja = Bandeja()
    assert len(cc.decision_cards()) == 2 and not _dm(bot) and not desk.snoozes.hidden()
    cc.bandeja = Bandeja(prioridad_baja_max=5)  # aunque se suba el umbral, la 0 nunca se aplaza sola
    assert len(cc.decision_cards()) == 2


# --- botones con texto corto y respuesta libre -----------------------------------------------------------

def test_expand_free_text_only_picks_a_letter_when_it_stands_alone():
    opts = ["9 euros", "19 euros", "Gratis"]
    f = DecisionDesk.expand_free_text
    assert f("B, pero con descuento", opts) == "B) 19 euros — pero con descuento"
    assert f("a", opts) == "A) 9 euros"
    assert f("C: solo el primer año", opts) == "C) Gratis — solo el primer año"
    assert f("A mí me da igual", opts) == "A mí me da igual"  # preposición, no la opción A
    assert f("D, pero…", opts) == "D, pero…" and f("Mejor 15 euros", opts) == "Mejor 15 euros"


def test_free_text_reply_a_pero_con_x_is_recorded_with_the_option_text(tmp_path):
    cc, desk, bot, h = setup(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    decisiones(desk)
    press(desk, bot.sent[-1], 6)  # ✍️ Responder
    ask = bot.sent[-1]
    desk.handle_update({"update_id": 7, "message": {"message_id": 900, "from": {"id": OSCAR}, "chat": {"id": OSCAR},
                                                    "text": "B, pero con descuento",
                                                    "reply_to_message": {"message_id": ask["message_id"]}}})
    assert comments_of(h, "comment")[0][2] == "RESPUESTA-OSCAR: B) 19 euros — pero con descuento"
    assert len(comments_of(h, "complete")) == 1


def test_long_option_text_is_cut_in_its_button():
    spec = __import__("agent_lanes.decisions", fromlist=["x"]).card_keyboard_spec(
        2, 0, ["Una opción larguísima que no cabe en un botón de Telegram", "Corta"])
    assert [b["text"] for row in spec[:2] for b in row][1] == "B · Corta"
    assert len(spec[0][0]["text"]) <= 4 + 22 and spec[0][0]["text"].endswith("…")


# --- aceptar el lote -------------------------------------------------------------------------------------

def test_accept_all_recommended_applies_each_pending_card(tmp_path):
    shows = {"t_d0000001": card("t_d0000001", "[DECISIÓN] Precio", body=BODY, created_at=1.0),
             "t_d0000002": card("t_d0000002", "[DECISIÓN] Plazo", body=BODY, created_at=2.0),
             "t_d0000003": card("t_d0000003", "[DECISIÓN] Sin opciones", body="Solo título", created_at=3.0)}
    cc, desk, bot, h = setup(tmp_path, shows)
    decisiones(desk)
    batch = bot.sent[-1]
    assert batch["text"] == "⭐ 2 tarjetas con recomendada" and texts(batch["markup"]) == ["✅ Aceptar las recomendadas (2)"]
    h.shows["t_d0000002"]["task"]["status"] = "done"  # decidida entretanto: se salta sola
    press(desk, batch, 0)
    answers = [c[2] for c in comments_of(h, "comment")]
    assert answers == ["RESPUESTA-OSCAR: B) 19 euros (la recomendada)"]
    assert [c[1] for c in comments_of(h, "complete")] == ["t_d0000001"]
    assert "Aplicada la recomendada en 2 tarjetas" in bot.edits[-1]["text"]


def test_no_accept_all_line_with_a_single_recommended_card(tmp_path):
    cc, desk, bot, h = setup(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    decisiones(desk)
    assert not any("con recomendada" in m["text"] for m in bot.sent)


# --- plazo de veto ---------------------------------------------------------------------------------------

def _veto_setup(tmp_path, body=REV, **cfg):
    cc, desk, bot, h = setup(tmp_path, {"t_d0000001": card("t_d0000001", body=body)})
    clock = [1_000_000.0]
    desk.vetos = VetoBook(tmp_path / "veto.json", clock=lambda: clock[0])
    cc.bandeja = Bandeja(**cfg)
    decisiones(desk)
    return cc, desk, bot, h, clock


def test_reversible_card_shows_the_deadline_and_a_veto_button(tmp_path):
    cc, desk, bot, h, clock = _veto_setup(tmp_path)
    msg = bot.sent[-1]
    assert "⏱ Reversible: si no decides antes del" in msg["text"] and "aún no se aplica sola" in msg["text"]
    assert texts(msg["markup"])[-2:] == ["⛔ Vetar", "🗑 Quitar aviso"]
    cc2, desk2, bot2, h2, _ = _veto_setup(tmp_path / "x", body=BODY)  # sin `Reversible: sí`: ni plazo ni botón
    assert "⏱" not in bot2.sent[-1]["text"] and "⛔ Vetar" not in texts(bot2.sent[-1]["markup"])
    cc3, desk3, bot3, h3, _ = _veto_setup(tmp_path / "y", veto_horas=0)  # desactivado
    assert "⏱" not in bot3.sent[-1]["text"]


def test_veto_in_notice_mode_only_tells_oscar_and_runs_once(tmp_path):
    cc, desk, bot, h, clock = _veto_setup(tmp_path)
    assert cc.veto_tick() == []
    clock[0] += 47 * 3600
    assert cc.veto_tick() == []  # antes del plazo
    clock[0] += 2 * 3600
    assert cc.veto_tick() == ["t_d0000001"]
    assert not comments_of(h, "complete") and not comments_of(h, "comment")  # modo aviso: no ejecuta nada
    [notice] = _dm(bot)
    assert "Modo aviso" in notice and "B) 19 euros" in notice
    assert cc.veto_tick() == [] and len(_dm(bot)) == 1


def test_veto_in_execute_mode_applies_the_recommended_after_the_deadline(tmp_path):
    cc, desk, bot, h, clock = _veto_setup(tmp_path, veto_modo="ejecutar")
    clock[0] += 49 * 3600
    assert cc.veto_tick() == ["t_d0000001"]
    assert comments_of(h, "comment")[0][2] == "RESPUESTA-OSCAR: B) 19 euros (la recomendada)"
    assert len(comments_of(h, "complete")) == 1
    assert cc.veto_tick() == [] and len(comments_of(h, "complete")) == 1
    assert "apliqué la recomendada" in _dm(bot)[0]


def test_veto_button_cancels_the_auto_run_and_keeps_the_other_buttons(tmp_path):
    cc, desk, bot, h, clock = _veto_setup(tmp_path, veto_modo="ejecutar")
    msg = bot.sent[-1]
    press(desk, msg, 7)  # ⛔ Vetar
    assert "VETO-OSCAR" in comments_of(h, "comment")[0][2]
    assert "⛔ vetada" in bot.edits[-1]["text"] and bot.edits[-1]["markup"]
    clock[0] += 49 * 3600
    assert cc.veto_tick() == [] and not comments_of(h, "complete")
    press(desk, msg, 0, cid="otro")  # el teclado sigue vivo: Oscar aún puede decidir a mano
    assert len(comments_of(h, "complete")) == 1


def test_veto_skips_a_card_decided_by_hand_and_never_runs_a_non_reversible_one(tmp_path):
    cc, desk, bot, h, clock = _veto_setup(tmp_path, veto_modo="ejecutar")
    press(desk, bot.sent[-1], 0)  # Oscar decide la A a mano
    clock[0] += 49 * 3600
    assert cc.veto_tick() == [] and len(comments_of(h, "complete")) == 1
    assert desk.vetos.state("t_d0000001") == "cerrada"
    # sin la línea `Reversible`, aunque alguien la armase a mano, no se ejecuta
    cc2, desk2, bot2, h2, clock2 = _veto_setup(tmp_path / "n", body=BODY, veto_modo="ejecutar")
    desk2.vetos.arm("t_d0000001")
    clock2[0] += 49 * 3600
    assert cc2.veto_tick() == [] and not comments_of(h2, "complete")


def test_reversible_flag_only_counts_in_the_card_body():
    assert bandeja.is_reversible("Contexto: x\nReversible: sí\nOpciones:")
    assert bandeja.is_reversible("- **Reversible:** true") is False  # formato no soportado: ante la duda, no
    assert not bandeja.is_reversible("Es reversible: no\nReversible: no")


def test_reminders_run_the_veto_check_every_pass(tmp_path):
    calls = []
    rem = Reminders(type("B", (), {"send_to": lambda *a, **k: None})(), "1", lambda: [], tmp_path / "r.json",
                    now=lambda: datetime(2026, 10, 14, 10, 0, tzinfo=MADRID), veto=lambda: calls.append(1) or rem.stop())
    rem.run()
    assert calls == [1]
