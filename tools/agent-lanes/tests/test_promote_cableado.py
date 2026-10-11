"""Cableado de la promoción (01-10): DecisionDesk._act, /promover, teclado 🚀 del fijado (reply_markup al editar y
crear, y botón que se puede pulsar más de una vez). Todo con dobles."""
from __future__ import annotations

from agent_lanes.integration import PinnedSummary
from tests.test_commands import GESTION, OSCAR, center, command
from tests.test_integration_topic import INTEG, build, press

WHERE = {"chat_id": "-100", "thread_id": "0"}


class FakePromoter:
    configs = {"migrateam": object()}

    def __init__(self):
        self.started, self.buttons = [], []

    def start(self, key, where):
        self.started.append((key, where))
        return "ok"

    def on_button(self, action, rec, where, desk):
        self.buttons.append(action)
        return True


# --- DecisionDesk._act -----------------------------------------------------------------------------------

def test_act_promo_start_calls_promoter_start(tmp_path):
    cc, desk, *_ = center(tmp_path, {})
    desk.promoter = FakePromoter()
    assert desk._act({"key": "migrateam"}, {"action": "promo_start"}, WHERE) is True
    assert desk.promoter.started == [("migrateam", WHERE)]


def test_act_promo_review_and_confirm_go_to_on_button(tmp_path):
    cc, desk, *_ = center(tmp_path, {})
    desk.promoter = FakePromoter()
    assert desk._act({}, {"action": "promo_review"}, WHERE) is True
    assert desk._act({}, {"action": "promo_confirm"}, WHERE) is True
    assert desk.promoter.buttons == ["promo_review", "promo_confirm"] and not desk.promoter.started


def test_act_promo_without_promoter_is_false(tmp_path):
    cc, desk, *_ = center(tmp_path, {})
    desk.promoter = None
    for action in ("promo_start", "promo_review", "promo_confirm"):
        assert desk._act({"key": "migrateam"}, {"action": action}, WHERE) is False


# --- /promover -------------------------------------------------------------------------------------------

def test_cmd_promover_unknown_project_absent_promoter_and_valid(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    cc._spawn = lambda fn: fn()
    desk.promoter = None
    command(desk, "/promover migrateam")
    assert "no está activa" in bot.sent[-1]["text"]
    desk.promoter = FakePromoter()
    command(desk, "/promover nada", uid=2)
    assert "Dime qué promover" in bot.sent[-1]["text"] and not desk.promoter.started
    command(desk, "/promover migrateam", uid=3)
    assert desk.promoter.started and desk.promoter.started[0][0] == "migrateam"
    assert "Preparando la promoción" in bot.sent[-1]["text"]


# --- teclado del fijado ----------------------------------------------------------------------------------

def test_pinned_update_sends_markup_on_create_and_edit(tmp_path):
    from tests.test_integration_topic import Tg
    tg = Tg()
    p = PinnedSummary(tg, INTEG, tmp_path / "pinned.json")
    mk = {"inline_keyboard": [[{"text": "🚀", "callback_data": "t:0"}]]}
    assert p.update("a", lambda: mk) == "created" and tg.sent[-1]["markup"] == mk
    assert p.update("b", lambda: mk) == "edited" and tg.edits[-1]["markup"] == mk
    assert p.update("b", lambda: mk) == "unchanged"
    assert p.reissue(lambda: mk) == "edited" and tg.edits[-1]["text"] == "b" and tg.edits[-1]["markup"] == mk


def test_promote_markup_reaches_the_pinned_message_and_can_be_pressed_twice(tmp_path):
    integ, w, h, tg, desk, messages = build(tmp_path)
    desk.promoter = FakePromoter()
    integ.run_pass()
    pinned = [m for m in tg.sent if "PENDIENTE DE INTEGRAR" in m["text"]][0]
    assert pinned["markup"] and "Promover Migrateam" in pinned["markup"]["inline_keyboard"][0][0]["text"]
    press(desk, pinned)  # 1ª pulsación: gasta el token
    assert desk.promoter.started and desk.promoter.started[0][0] == "migrateam"
    fresh = tg.edits[-1]["markup"]  # el fijado se editó con un teclado nuevo, mismo texto
    assert tg.edits[-1]["text"] == pinned["text"] and fresh and fresh != pinned["markup"]
    press(desk, {**pinned, "markup": fresh})  # 2ª pulsación con el teclado repuesto
    assert len(desk.promoter.started) == 2
    assert tg.edits[-1]["markup"] not in (None, fresh)
