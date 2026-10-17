"""P3 de la auditoría t_dddcc232: los avisos no recortan en silencio. Lo que no cabe se marca y se ofrece entero (segundo
mensaje), el motivo largo del bloqueo se marca sin perder la acción esperada, la autocuración distingue "recortado" de
"sin cabecera" y avisa, las opciones fuera de contrato se anuncian y los topes salen de lanes.yaml."""
from __future__ import annotations

import pytest

from agent_lanes import notices
from agent_lanes.config import load_notice_limits
from agent_lanes.hermes_answers import HermesAnswers
from agent_lanes.notices import (CUT_MARK, LIMITS, MessageStore, TaskNotices, fit_reason, normalize_questions,
                                 questions_block, render, split_text)
from tests.test_preguntas_secuencia import MIG, answered, needs, setup
from tests.test_runner_state import FakeNotifier


@pytest.fixture(autouse=True)
def default_limits():
    saved = dict(LIMITS)
    yield
    LIMITS.clear()
    LIMITS.update(saved)


def test_seven_questions_show_five_a_mark_and_the_rest_in_a_second_message():
    qs = [f"¿Pregunta {i}?" for i in range(7)]
    block = questions_block(qs)
    assert [b for b in block if b.startswith("• ")] == [f"• ¿Pregunta {i}?" for i in range(5)]
    assert block[-1] == "➕ +2 preguntas más (completas en la tarjeta)"
    assert block.overflow == ["• ¿Pregunta 5?", "• ¿Pregunta 6?"]
    n = FakeNotifier()
    text = render("needs_input", "t_1", "T", "claude-hub", "necesita tu decisión", bullets=block)
    TaskNotices(n).publish("t_1", text, None, None, alert=True, followup=block.overflow)
    assert "+2 preguntas más" in n.msgs[0]
    assert n.msgs[1] == "• ¿Pregunta 5?\n• ¿Pregunta 6?" and n.silent == [False, True]


def test_a_long_question_is_marked_and_its_full_text_goes_in_the_followup():
    block = questions_block(["¿Larga? " + "palabra " * 80, "¿Corta?"])
    assert block[0].endswith(CUT_MARK) and "¿Corta?" in block[1]
    assert block.overflow[0].startswith("• ¿Larga? palabra") and len(block.overflow[0]) > 500


def test_questions_that_fit_leave_no_mark_and_no_followup():
    block = questions_block([{"question": "¿Sí?", "options": ["a", "b"], "recommended": 0}])
    assert block == ["• ¿Sí?", "   A) a ⭐", "   B) b"] and block.overflow == []
    n = FakeNotifier()
    TaskNotices(n).publish("t_1", "x", None, None, alert=True, followup=block.overflow)
    assert n.msgs == ["x"]


def test_followup_is_split_under_the_telegram_limit_and_never_on_edits():
    lines = [f"• pregunta {i} " + "y" * 900 for i in range(12)]
    parts = split_text(lines)
    assert len(parts) > 1 and all(len(p) <= notices.TEXT_MAX < 4096 for p in parts)
    assert "".join(parts).replace("\n", "").count("pregunta") == 12
    assert [len(p) for p in split_text(["z" * 9000])] == [4000, 4000, 1000]
    n = FakeNotifier()
    store = MessageStore()
    notices_ = TaskNotices(n, store)
    notices_.publish("t_1", "a", None, None)  # sin alerta: se edita y no hay segundo mensaje
    notices_.publish("t_1", "b", None, None, followup=lines)
    assert n.msgs == ["a"] and [t for _, t in n.edits] == ["b"]


def test_followup_also_reaches_the_dm_copy():
    class Bot(FakeNotifier):
        bot_id = "9"

        def send_to(self, chat, thread, text, **kw):
            self.msgs.append(f"DM:{text}")
            self.silent.append(False)
            return {"chat_id": chat, "thread_id": "0", "message_id": 500 + len(self.msgs)}

    n = Bot()
    TaskNotices(n).publish("t_1", "aviso", None, None, alert=True, mirror_to="42", followup=["• resto"])
    assert n.msgs == ["aviso", "DM:aviso", "• resto", "DM:• resto"]


def test_render_marks_bullets_dropped_for_size():
    bullets = [f"• {i} " + "w" * 900 for i in range(6)]
    text = render("needs_input", "t_1", "T", "l", "estado", bullets=bullets)
    assert len(text) <= notices.TEXT_MAX and CUT_MARK in text and "completo en la tarjeta" in text


def test_fit_reason_marks_the_cut_and_keeps_actions_and_for_oscar():
    reason = ("El worker necesita decisión:\n" + "\n".join(f"- ¿P{i}? " + "x" * 200 for i in range(10))
              + "\nAcciones propuestas (no ejecutadas):\n- [a1] borrar X · tool {}\nPara Oscar: decide ya.")
    out = fit_reason(reason)
    assert len(out) <= notices.REASON_MAX and CUT_MARK in out
    assert "Acciones propuestas (no ejecutadas):" in out and "[a1] borrar X" in out and out.endswith("Para Oscar: decide ya.")
    short = "El worker necesita decisión:\n- ¿A?"
    assert fit_reason(short) == short and CUT_MARK not in fit_reason(short)
    huge_tail = "- ¿q?\nAcciones propuestas (no ejecutadas):\n" + "- [a] acción larga\n" * 200
    out = fit_reason(huge_tail)
    assert len(out) <= notices.REASON_MAX and "Acciones propuestas" in out and CUT_MARK in out


def test_options_out_of_contract_are_announced_not_hidden():
    qs = normalize_questions([{"question": "¿Cuál?", "options": ["solo una"]},
                              {"question": "¿Otra?", "options": ["a", "b", "c", "d", "e"]},
                              {"question": "¿Ok?", "options": ["a", "b"]}])
    assert qs[0]["options"] == [] and qs[0]["options_dropped"] == 1 and qs[1]["options_dropped"] == 5
    assert "options_dropped" not in qs[2]
    block = questions_block(qs)
    assert "⚠️ 1 opción descartada" in block[1] and "texto libre" in block[1]
    assert any("5 opciones descartadas" in b for b in block)


def test_limits_come_from_lanes_yaml_and_can_be_overridden():
    cfg = load_notice_limits()
    assert cfg["questions_max_items"] == 5 and cfg["reason_max"] == 1500 and cfg["text_max"] == 4000
    notices.configure({"questions_max_items": 2, "reason_max": "x", "text_max": 0, "inventado": 3})
    assert LIMITS["questions_max_items"] == 2 and LIMITS["reason_max"] == 1500 and LIMITS["text_max"] == 4000
    block = questions_block([f"¿{i}?" for i in range(4)])
    assert len([b for b in block if b.startswith("• ")]) == 2 and block[-1].startswith("➕ +2")


# --- autocuración ------------------------------------------------------------------------------------

def _healing(tmp_path, reason):
    show = needs("t_aaaaaaa1", reason)
    show["comments"] = [answered("t_aaaaaaa1", "¿Publicar ya?", "Sí")]
    cc, desk, bot, h, messages, _ = setup(tmp_path, {"t_aaaaaaa1": show})
    alerts = []
    return HermesAnswers({MIG.name: MIG}, hermes_for=lambda b: h, desk=desk, alert=alerts.append), h, alerts


def test_healing_with_a_marked_cut_does_not_unblock_and_warns_oscar_once(tmp_path):
    reason = fit_reason("El worker necesita decisión:\n- ¿Publicar ya?\n" + "- ¿Otra? " + "x" * 2000)
    assert CUT_MARK in reason
    ha, h, alerts = _healing(tmp_path, reason)
    assert ha.tick() == [] and ha.tick() == []
    assert ("unblock", "t_aaaaaaa1") not in h.calls
    assert len(alerts) == 1 and "t_aaaaaaa1" in alerts[0] and "marca de recorte" in alerts[0]


def test_healing_with_a_legacy_cut_at_the_limit_also_warns(tmp_path):
    reason = ("El worker necesita decisión:\n- ¿Publicar ya? " + "x" * 1500)[:1500]
    ha, h, alerts = _healing(tmp_path, reason)
    assert ha.tick() == []
    assert ("unblock", "t_aaaaaaa1") not in h.calls
    assert len(alerts) == 1 and "límite de tamaño" in alerts[0] and "marca de recorte" not in alerts[0]


def test_healing_of_a_short_reason_still_unblocks_and_does_not_warn(tmp_path):
    ha, h, alerts = _healing(tmp_path, "El worker necesita decisión:\n- ¿Publicar ya?")
    assert ha.tick() == ["t_aaaaaaa1"] and ("unblock", "t_aaaaaaa1") in h.calls and alerts == []


def test_a_failing_alert_never_breaks_the_tick(tmp_path):
    reason = fit_reason("El worker necesita decisión:\n- ¿Publicar ya? " + "x" * 2000)
    ha, h, _ = _healing(tmp_path, reason)
    ha.alert = lambda text: 1 / 0
    assert ha.tick() == []
