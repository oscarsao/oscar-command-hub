"""Reintentar (t_370b903b): el aviso dice cómo salió el reintento (mismo motivo / otro motivo / no se pudo) y, si
vuelve a fallar, ofrece ✍️ Responder para corregir y reanudar desde Telegram. Todo con fakes."""
from __future__ import annotations

from agent_lanes.decisions import RetryBook
from agent_lanes.runner import LaneRunner
from tests.test_commands import GESTION, command, press, texts
from tests.test_plan_d_runtime import stuck, triage_center
from tests.test_runner_state import LANE

TID = "t_eeeeeee5"


def rerun_block(desk, bot, messages, h, reason):
    """El runner coge la tarea reencolada y la vuelve a bloquear con `reason`."""
    h.block = lambda t, kind, why: True
    r = LaneRunner(LANE, hermes=h, git=None, worker=None, verifier=None, notify=bot, messages=messages, decisions=desk)
    r._block(TID, "transient", reason, {"title": "T", "body": ""}, public="verificación mecánica fallida")
    return bot.sent[-1]


def press_retry(desk, bot, cc):
    """Pulsa 🔄 Reintentar de la tarjeta de bloqueo transitorio (en triage, main ofrece 🧊 Sacar de triage)."""
    markup = desk.markup("blocked", block_kind="transient", task={"id": TID, "title": "T"},
                         lane=cc.lanes["claude-migrateam"])
    press(desk, {"chat_id": GESTION, "thread_id": "230", "message_id": 77, "markup": markup}, 0)


def test_same_block_after_retry_says_same_reason_and_offers_responder(tmp_path):
    reason = "verificación mecánica fallida: test_cmd exit 1"
    cc, desk, bot, h, messages = triage_center(tmp_path, {TID: stuck(TID, reason=reason)})
    press_retry(desk, bot, cc)
    assert "te aviso aquí con el motivo" in bot.edits[-1]["text"]  # el reintento promete resultado
    # Con el historial a la vista, main escala el 2º bloqueo igual a una pregunta (_same_block_times); aquí se prueba
    # solo el veredicto del reintento, así que el historial de la tarjeta se da por limpio.
    h.shows[TID]["events"] = []
    msg = rerun_block(desk, bot, messages, h, reason + "\nPara Oscar: algo llano")
    assert "el reintento no funcionó: sigue atascada por el mismo motivo" in msg["text"]
    assert texts(msg["markup"]) == ["🔄 Reintentar", "🗄 Aparcar", "🔎 Más detalle", "✍️ Responder",
                                    "🗑 Quitar aviso"]


def test_different_block_after_retry_says_other_reason(tmp_path):
    cc, desk, bot, h, messages = triage_center(tmp_path, {TID: stuck(TID)})
    press_retry(desk, bot, cc)
    msg = rerun_block(desk, bot, messages, h, "worker status=failed")
    assert "ahora se bloquea por otro motivo" in msg["text"]


def test_block_without_previous_retry_has_no_verdict_nor_responder(tmp_path):
    cc, desk, bot, h, messages = triage_center(tmp_path, {})
    msg = rerun_block(desk, bot, messages, h, "worker status=failed")
    assert "reintento" not in msg["text"]
    assert texts(msg["markup"]) == ["🔄 Reintentar", "🗄 Aparcar", "🔎 Más detalle", "🗑 Quitar aviso"]


def test_retry_marks_expire_after_a_day(tmp_path):
    now = [1000.0]
    book = RetryBook(tmp_path / "m.json", clock=lambda: now[0])
    book.mark("t_aaaaaaa1", "motivo")
    assert book.verdict("t_aaaaaaa1", "motivo").endswith("el mismo motivo")
    now[0] += RetryBook.TTL + 1
    assert book.get("t_aaaaaaa1") is None and book.verdict("t_aaaaaaa1", "motivo") is None


def test_failed_retry_explains_and_gives_the_buttons_back(tmp_path):
    shows = {TID: stuck(TID)}
    shows[TID]["task"]["status"] = "done"
    cc, desk, bot, h, _ = triage_center(tmp_path, shows)
    markup = desk.markup("blocked", block_kind="transient", task={"id": TID, "title": "T"},
                         lane=cc.lanes["claude-migrateam"])
    press(desk, {"chat_id": GESTION, "thread_id": "230", "message_id": 78, "markup": markup}, 0)
    assert any("❌ el reintento falló" in e["text"] for e in bot.edits)
    assert bot.markups and bot.markups[-1][1] == 78  # los botones vuelven
    assert desk.retries.get(TID) is None  # el ciclo de reintento se cierra al fallar el reencolado


def test_block_after_successful_retry_and_review_has_no_verdict_nor_responder(tmp_path):
    cc, desk, bot, h, messages = triage_center(tmp_path, {TID: stuck(TID)})
    press_retry(desk, bot, cc)
    assert desk.retries.get(TID)
    r = LaneRunner(LANE, hermes=h, git=None, worker=None, verifier=None, notify=bot, messages=messages, decisions=desk)
    r._clear_retry(TID)  # el reintento salió: la tarea llegó a review
    assert desk.retries.get(TID) is None
    msg = rerun_block(desk, bot, messages, h, "worker status=failed")
    assert "reintento" not in msg["text"]
    assert "✍️ Responder" not in texts(msg["markup"])


def test_needs_input_after_retry_has_no_retry_prefix(tmp_path):
    cc, desk, bot, h, messages = triage_center(tmp_path, {TID: stuck(TID)})
    press_retry(desk, bot, cc)
    h.block = lambda t, kind, why: True
    r = LaneRunner(LANE, hermes=h, git=None, worker=None, verifier=None, notify=bot, messages=messages, decisions=desk)
    r._block(TID, "needs_input", "pregunta", {"title": "T", "body": ""}, public="necesita una decisión",
             questions=["¿A o B?"])
    assert "el reintento no funcionó" not in bot.sent[-1]["text"]
