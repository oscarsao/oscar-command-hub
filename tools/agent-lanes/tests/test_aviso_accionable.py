"""Autonomía F0: aviso de tarjeta atascada con historial y 3 botones, @mención en el tema Atención, recordatorio a las
24 h, callbacks que siempre contestan y contrato de aviso accionable. Todo con dobles: ni Telegram ni Hermes reales."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_lanes.aviso import AvisoAccionable, AvisoSinAccion, StuckWatcher, actions_of, mention
from agent_lanes.config import load_telegram_settings
from agent_lanes.decisions import keyboard_spec
from tests.test_commands import press, texts
from tests.test_plan_d_runtime import TriageHermes, stuck, triage_center

TID = "t_eeeeeee5"
ATENCION = {"chat": "-100777", "thread": "12", "mention": "@oscar_test"}


def pending(cc, tid=TID):
    return next(p for p in cc.stuck_tasks() if p.tid == tid)


def attention_center(tmp_path, shows):
    cc, desk, bot, h, messages = triage_center(tmp_path, shows)
    ren = cc.renotifier()
    ren.attention = ATENCION
    targets = []
    plain_send = bot.send
    bot.send = lambda text, target=None, lane_target=None, **kw: (targets.append(target), plain_send(
        text, target, lane_target, **kw))[1]
    return cc, desk, bot, h, ren, targets


def test_a_triage_con_block_loop_da_aviso_con_historial_tres_botones_y_mencion(tmp_path):
    show = stuck(TID, reason="El worker necesita decisión:\n- ¿Borro la tabla vieja?", kind="needs_input")
    show["comments"] = [{"author": "oscar", "body": "Respuesta de Oscar: ¿Borro la tabla vieja? → no", "created_at": 500}]
    cc, desk, bot, h, ren, targets = attention_center(tmp_path, {TID: show})
    p = pending(cc)
    assert ren._publish(p, None)
    msg = bot.sent[-1]
    assert targets == [("-100777", "12")]  # tema Atención del grupo
    assert "@oscar_test" in msg["text"]
    assert "Lo que se preguntó: «¿Borro la tabla vieja?»" in msg["text"] and "veces" in msg["text"]
    assert "Falta:" in msg["text"]
    assert texts(msg["markup"]) == ["🧊 Sacar de triage", "✍️ Responder", "🗄 Archivar"]  # exactamente 3


def test_sin_config_de_atencion_no_hay_mencion_ni_cambia_el_destino(tmp_path):
    cc, desk, bot, h, messages = triage_center(tmp_path, {TID: stuck(TID)})
    p = pending(cc)
    assert "@" not in p.status and cc.renotifier().attention_target() is None


def test_b_sacar_de_triage_usa_specify_y_no_unblock(tmp_path):
    cc, desk, bot, h, ren, _ = attention_center(tmp_path, {TID: stuck(TID)})
    ren._publish(pending(cc), None)
    press(desk, bot.sent[-1], 0)
    assert h.calls == [("show", TID)] or ("requeue_triage", TID) in h.calls
    assert ("requeue_triage", TID) in h.calls and not any(c[0] == "unblock" for c in h.calls)
    assert h.shows[TID]["task"]["status"] == "ready"
    assert "sacada de triage" in bot.edits[-1]["text"]


class RechazaHermes(TriageHermes):
    last_error = "Hermes rechazó specify_triage_task (prueba)"

    def requeue_triage(self, tid, author=None):
        self.calls.append(("requeue_triage", tid))
        return None


def test_c_error_de_hermes_edita_el_aviso_con_la_causa_y_conserva_los_botones(tmp_path):
    cc, desk, bot, h, ren, _ = attention_center(tmp_path, {TID: stuck(TID)})
    th = RechazaHermes({TID: stuck(TID)})
    for obj in (cc, desk, ren):
        obj.hermes_for = lambda b: th
    ren._publish(pending(cc), None)
    press(desk, bot.sent[-1], 0)
    assert any("no se pudo sacar de triage: Hermes rechazó specify_triage_task (prueba)" in e["text"] for e in bot.edits)
    assert bot.markups and bot.markups[-1][2] is not None  # botones devueltos para poder repetir
    assert th.shows[TID]["task"]["status"] == "triage"


def test_d_callback_repetido_no_duplica(tmp_path):
    cc, desk, bot, h, ren, _ = attention_center(tmp_path, {TID: stuck(TID)})
    ren._publish(pending(cc), None)
    sent = bot.sent[-1]
    press(desk, sent, 0, cid="c1")
    press(desk, sent, 0, cid="c2")
    assert sum(1 for c in h.calls if c == ("requeue_triage", TID)) == 1
    assert bot.answers[-1] == "Esta decisión ya no está activa"


def test_pulsar_en_otra_copia_con_la_tarea_ya_resuelta_dice_que_ya_estaba_resuelta(tmp_path):
    cc, desk, bot, h, ren, _ = attention_center(tmp_path, {TID: stuck(TID)})
    ren._publish(pending(cc), None)
    h.shows[TID]["task"]["status"] = "ready"  # otra vía la sacó de triage
    press(desk, bot.sent[-1], 0)
    assert any("ya estaba resuelta" in e["text"] for e in bot.edits)
    assert not any(c[0] == "requeue_triage" for c in h.calls)


def test_responder_y_archivar_son_las_acciones_existentes():
    assert [b["action"] for row in keyboard_spec("stuck") for b in row] == ["untriage", "other", "park"]


def test_e_aviso_sin_acciones_falla_el_lint():
    with pytest.raises(AvisoSinAccion):
        AvisoAccionable(motivo="algo pasa", contexto_corto=("x",), acciones=()).lint()
    with pytest.raises(AvisoSinAccion):
        AvisoAccionable(motivo="algo pasa", contexto_corto=(), acciones=({"text": "", "action": "x"},)).lint()
    ok = AvisoAccionable(motivo="algo pasa", contexto_corto=(), acciones=actions_of(keyboard_spec("stuck"))).lint()
    assert ok.dueño == "Oscar" and ok.plazo == "24 h" and "no aprueba" in ok.escalado


def test_mencion_solo_con_usuario_valido():
    assert mention("@oscar_test") == "@oscar_test" and mention("oscar_test") == "@oscar_test"
    assert mention(None) == "" and mention("no válido!") == "" and mention("a b") == ""


def test_config_attention_telegram(tmp_path):
    yml = tmp_path / "lanes.yaml"
    yml.write_text("attention_telegram: {chat: -100777, thread: 12, mention: oscar_test}\n", encoding="utf-8")
    assert load_telegram_settings(yml)["attention"]["mention"] == "oscar_test"
    yml.write_text("telegram: {}\n", encoding="utf-8")
    assert load_telegram_settings(yml)["attention"] is None


def test_vigilante_avisa_una_vez_por_ronda_y_recuerda_a_las_24h_sin_ejecutar_nada(tmp_path):
    cc, desk, bot, h, ren, _ = attention_center(tmp_path, {TID: stuck(TID)})
    clock = [1000.0]
    calls = {"publish": 0, "remind": 0}

    def publish(p):
        calls["publish"] += 1
        return True

    def remind(p, hours):
        calls["remind"] += 1
        return True

    w = StuckWatcher(cc.stuck_tasks, publish, remind, tmp_path / "s.json", now=lambda: clock[0])
    assert w.tick() == [f"aviso {TID}"] and w.tick() == []  # el segundo aviso de la misma ronda no sale
    clock[0] += 23 * 3600
    assert w.tick() == []
    clock[0] += 2 * 3600
    assert w.tick() == [f"recordatorio {TID}"] and w.tick() == []
    assert calls == {"publish": 1, "remind": 1}
    assert ("requeue_triage", TID) not in h.calls  # el recordatorio no hace nada por Oscar
    # nueva ronda (otro block_loop_detected) = aviso nuevo
    h.shows[TID]["events"][-1]["created_at"] = 9999.0
    assert w.tick() == [f"aviso {TID}"]


def test_vigilante_reintenta_si_el_envio_falla(tmp_path):
    cc, *_ = attention_center(tmp_path, {TID: stuck(TID)})
    ok = [False]
    w = StuckWatcher(cc.stuck_tasks, lambda p: ok[0], lambda p, h: True, tmp_path / "s.json", now=lambda: 1.0)
    assert w.tick() == []
    ok[0] = True
    assert w.tick() == [f"aviso {TID}"]


def test_remind_dm_lleva_los_botones_y_dice_que_no_se_hizo_nada(tmp_path):
    cc, desk, bot, h, ren, _ = attention_center(tmp_path, {TID: stuck(TID)})
    assert ren.remind_dm(pending(cc), 24)
    dm = bot.sent[-1]
    assert "recordatorio: no se ha hecho nada por ti" in dm["text"] and "@oscar_test" not in dm["text"]
    assert texts(dm["markup"]) == ["🧊 Sacar de triage", "✍️ Responder", "🗄 Archivar"]


def test_f_prioridad_del_runner_de_oscar_hq_esta_documentada():
    """El runner de oscar-hq ordena con el número MAYOR = MÁS prioritario. No se cambia: solo se documenta, para que
    ninguna cola o escalada del hub la invierta por error."""
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "mayor número = más prioritario" in readme
