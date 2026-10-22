"""Chats «solo resultado final» (lanes.yaml telegram.final_only, grupo de desarrollo de Angel): inicio y entrega salen en
el grupo; las vueltas de proceso (needs_input, blocked...) van al DM de Oscar y no borran el aviso del grupo."""
from __future__ import annotations

from agent_lanes.config import Lane, load_telegram_settings
from agent_lanes.runner import LaneRunner
from agent_lanes.telegram import in_scope
from tests.test_commands import OSCAR, Bot, center

ANGEL = "-1003731090094"
DM = str(OSCAR)
BODY = f"Origen-Telegram: chat={ANGEL} thread=1\n\n## Objetivo\nHeartbeat"
GESTION_BODY = "Origen-Telegram: chat=-1003530490339 thread=231\n\n## Objetivo\nX"
LANE = Lane(name="claude-oscarhq", board="oscarhq", repo="C:/oscarhq", base="main", telegram=("-1003530490339", "231"))
Q = [{"question": "¿Publicar ya?", "options": ["Sí", "No"], "recommended": 0}]


def _runner(tmp_path, final_only=((ANGEL, "*"),)):
    cc, desk, bot, h, messages = center(tmp_path, {})
    return LaneRunner(LANE, hermes=None, git=None, worker=None, verifier=None, notify=bot, messages=messages,
                      decisions=desk, final_only=final_only), bot, messages


def test_in_scope_matches_chat_and_optional_thread():
    assert in_scope((ANGEL, "1"), {(ANGEL, "*")})
    assert in_scope((ANGEL, "7"), {(ANGEL, "7")}) and not in_scope((ANGEL, "1"), {(ANGEL, "7")})
    assert not in_scope(None, {(ANGEL, "*")}) and not in_scope(("-100", "1"), {(ANGEL, "*")})
    assert not in_scope((ANGEL, "1"), ())


def test_lanes_yaml_declares_the_angel_group_as_final_only():
    assert (ANGEL, "*") in load_telegram_settings()["final_only"]


def test_process_noise_goes_to_the_dm_not_to_the_group(tmp_path):
    r, bot, messages = _runner(tmp_path)
    task = {"title": "Heartbeat", "body": BODY}
    r.notify("running", "t_aaaaaaa1", task, "en curso")
    r.notify("needs_input", "t_aaaaaaa1", task, "necesita tu decisión", alert=True, buttons=True, questions=Q,
             bullets=["• ¿Publicar ya?"])
    r.notify("blocked", "t_aaaaaaa1", task, "bloqueada", alert=True)
    assert [m["chat_id"] for m in bot.sent] == ["-1003530490339", DM, DM]  # el inicio sale por el carril (el Bot falso ignora target)
    assert bot.sent[0]["message_id"] not in bot.deleted  # el aviso de inicio del grupo no se borra


def test_final_states_still_reach_the_group(tmp_path):
    r, bot, _ = _runner(tmp_path)
    seen = []
    orig = bot.send
    bot.send = lambda text, target=None, lane_target=None, **kw: (seen.append((target, lane_target)),
                                                                   orig(text, target, lane_target, **kw))[1]
    task = {"title": "Heartbeat", "body": BODY}
    for state in ("running", "review", "done"):
        r.notify(state, f"t_{state[:3]}aaaa", task, "x", alert=state != "running")
    assert all(lt == LANE.telegram and t == (ANGEL, "1") for t, lt in seen)


def test_other_chats_and_disabled_filter_keep_the_old_behaviour(tmp_path):
    for final_only, body in (((ANGEL, "*"),), GESTION_BODY), ((), BODY):
        r, bot, _ = _runner(tmp_path, final_only=set(final_only) if final_only else ())
        r.notify("blocked", "t_bbbbbbb2", {"title": "T", "body": body}, "bloqueada", alert=True)
        assert [m["chat_id"] for m in bot.sent] == ["-1003530490339"]
