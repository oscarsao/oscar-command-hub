"""Un mismo aviso de una tarea nunca sale dos veces seguidas (bug del 21-10: «SISTEMA · LISTO · PR #58» duplicado)."""
from __future__ import annotations

from agent_lanes import notices
from agent_lanes.notices import DEDUP_WINDOW, MessageStore, TaskNotices
from tests.test_commands import Bot

TEXT = "<b>⚙️ SISTEMA · LISTO · PR #58</b>\nt_edafbfb6 · Rebasar #44"
WHERE = ("-100", "5")


def _clock(monkeypatch, t):
    monkeypatch.setattr(notices.time, "time", lambda: t[0])


def test_identical_alert_twice_in_a_row_is_sent_once(monkeypatch):
    t = [1000.0]
    _clock(monkeypatch, t)
    bot, store = Bot(), MessageStore()
    n = TaskNotices(bot, store)
    n.publish("t_edafbfb6", TEXT, None, WHERE, alert=True)
    t[0] += 3
    n.publish("t_edafbfb6", TEXT, None, WHERE, alert=True)
    assert len(bot.sent) == 1


def test_a_second_process_sharing_the_store_does_not_repeat_it(monkeypatch, tmp_path):
    t = [1000.0]
    _clock(monkeypatch, t)
    bot = Bot()
    TaskNotices(bot, MessageStore(tmp_path)).publish("t_1", TEXT, None, WHERE, alert=True)
    TaskNotices(bot, MessageStore(tmp_path)).publish("t_1", TEXT, None, WHERE, alert=True)
    assert len(bot.sent) == 1


def test_changed_text_or_expired_window_sends_again(monkeypatch):
    t = [1000.0]
    _clock(monkeypatch, t)
    bot, store = Bot(), MessageStore()
    n = TaskNotices(bot, store)
    n.publish("t_1", TEXT, None, WHERE, alert=True)
    n.publish("t_1", TEXT + " (aplicado)", None, WHERE, alert=True)
    assert len(bot.sent) == 2
    t[0] += DEDUP_WINDOW + 1
    n.publish("t_1", TEXT + " (aplicado)", None, WHERE, alert=True)
    assert len(bot.sent) == 3


def test_other_tasks_are_independent(monkeypatch):
    _clock(monkeypatch, [1000.0])
    bot, store = Bot(), MessageStore()
    n = TaskNotices(bot, store)
    n.publish("t_1", TEXT, None, WHERE, alert=True)
    n.publish("t_2", TEXT, None, WHERE, alert=True)
    assert len(bot.sent) == 2
