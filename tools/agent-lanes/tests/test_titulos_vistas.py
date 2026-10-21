"""Títulos «[Proyecto] Qué · paso»: clasificación, validador, vistas por paso y cola de Telegram solo con trabajo vivo."""
from __future__ import annotations

import logging

import pytest

from agent_lanes import titulos
from agent_lanes.hermes import HermesCLI
from agent_lanes.reminders import Reminders
from tests.test_commands import KanbanHermes, center, command, needs, texts


@pytest.mark.parametrize("title,paso", [
    ("[Hub] Vistas del kanban · implementar", "implementar"),
    ("[MigraTeam] Roadmap 2026 · épica", "épica"),
    ("[Píldora] Roadmap · EPICA", "épica"),
    ("[OHQ] Ideas del bot · backlog bot", "backlog bot"),
    ("[Hub] Algo parado · retenida", "retenida"),
    ("[Personal] Renovar DNI · tuyo", "tuyo"),
    ("[Signal] Elegir proveedor · decidir", "decidir"),
    ("[Scraper] Spec del filtro · spec", "spec"),
    ("[NextJobs] Revisar PR · revisar", "revisar"),
    ("[Hub] Fusionar · integrar", "integrar"),
])
def test_classify_reads_the_step_from_the_title(title, paso):
    assert titulos.classify({"title": title}) == paso


@pytest.mark.parametrize("title", [None, "", "Sin formato", "[Hub] Sin paso", "[Hub] Qué · paso raro", "[Hub] Qué ·"])
def test_missing_or_unknown_step_is_unclassified_but_stays_live(title):
    assert titulos.classify({"title": title}) == titulos.UNCLASSIFIED
    assert titulos.is_live({"title": title})


def test_not_live_steps():
    assert [titulos.is_live({"title": f"[Hub] X · {p}"}) for p in titulos.PASOS] == \
           [False, False, False, True, True, True, True, True, True]


def test_oscar_pending_needs_assignee_and_step():
    assert titulos.is_oscar_pending({"assignee": "oscar", "title": "[Hub] X · tuyo"})
    assert titulos.is_oscar_pending({"assignee": "oscar", "title": "[Hub] X · decidir"})
    assert not titulos.is_oscar_pending({"assignee": "oscar", "title": "[Hub] X · spec"})
    assert not titulos.is_oscar_pending({"assignee": "claude-hub", "title": "[Hub] X · tuyo"})


def test_problems_flag_bad_project_step_and_format():
    assert titulos.problems("[Hub] Qué · implementar") == []
    assert any("proyecto" in p for p in titulos.problems("[Otro] Qué · implementar"))
    assert any("paso" in p for p in titulos.problems("[Hub] Qué · hacer"))
    assert any("formato" in p or "sigue" in p for p in titulos.problems("Título libre"))


def test_malformed_title_on_create_warns_but_does_not_block(caplog):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)

        class CP:
            returncode, stdout, stderr = 0, "Created t_0123abcd", ""
        return CP()

    h = HermesCLI("default", exe="hermes", runner=run)
    with caplog.at_level(logging.WARNING, logger="agent_lanes"):
        assert h.create("Título libre", "cuerpo", "claude-hub") == "t_0123abcd"
        assert "mal formado" in caplog.text
        caplog.clear()
        h.create("[Hub] Bien formado · implementar", "cuerpo", "claude-hub")
        assert "mal formado" not in caplog.text


# --- cola de Telegram y vistas -----------------------------------------------------------------------------

class ViewHermes(KanbanHermes):
    def list_status_all(self, status):
        return [{"id": t, **s["task"]} for t, s in self.shows.items() if s["task"]["status"] == status]


def _card(tid, title, *, assignee="oscar", status="ready"):
    return {"task": {"id": tid, "title": title, "body": "", "assignee": assignee, "status": status, "created_at": 10},
            "runs": [], "events": [], "comments": []}


def _center(tmp_path, shows):
    cc, desk, bot, h, messages = center(tmp_path, shows)
    vh = ViewHermes(shows)
    cc.hermes_for = lambda b: vh
    return cc, desk, bot


def _all_text(bot):
    return "\n".join(m["text"] for m in bot.sent)


def test_queue_hides_epics_retained_and_backlog_but_keeps_live_and_unclassified(tmp_path):
    shows = {
        "t_00000001": _card("t_00000001", "[Hub] Roadmap · épica"),
        "t_00000002": _card("t_00000002", "[Hub] Parada · retenida"),
        "t_00000003": _card("t_00000003", "[OHQ] Idea · backlog bot"),
        "t_00000004": _card("t_00000004", "[DECISIÓN] [Hub] Elegir · decidir"),
        "t_00000005": _card("t_00000005", "[DECISIÓN] Sin paso"),
        "t_00000006": _card("t_00000006", "[DECISIÓN] [Hub] Más · épica"),
    }
    cc, desk, bot = _center(tmp_path, shows)
    ids = [c["task"]["id"] for c in cc.decision_cards()]
    assert ids == ["t_00000004", "t_00000005"]
    cards, untagged = cc.oscar_cards()
    assert untagged == 0  # ni las épicas/retenidas/backlog sin etiqueta engrosan «tarjetas sin etiqueta»


def test_queue_and_reminders_ignore_epic_questions(tmp_path):
    shows = {"t_aaaaaaa1": needs("t_aaaaaaa1", "q", title="[Hub] Gran épica · épica"),
             "t_aaaaaaa2": needs("t_aaaaaaa2", "q", title="[Hub] Vivo · implementar")}
    cc, desk, bot = _center(tmp_path, shows)
    assert [p.tid for p in cc.pending_decisions()] == ["t_aaaaaaa2"]
    command(desk, "/decisiones")
    assert "t_aaaaaaa1" not in _all_text(bot) and "Gran épica" not in _all_text(bot)
    assert [p.tid for p in cc.pending_decisions()] == ["t_aaaaaaa2"]  # misma lista que usan los recordatorios


def test_nothing_pending_when_only_epics_exist(tmp_path):
    shows = {"t_00000001": _card("t_00000001", "[DECISIÓN] [Hub] Roadmap · épica")}
    cc, desk, bot = _center(tmp_path, shows)
    command(desk, "/decisiones")
    assert "Nada pendiente" in _all_text(bot)


def test_views_list_only_their_step(tmp_path):
    shows = {
        "t_00000001": _card("t_00000001", "[Hub] Roadmap · épica", status="todo"),
        "t_00000002": _card("t_00000002", "[Hub] Parada · retenida", status="triage"),
        "t_00000003": _card("t_00000003", "[OHQ] Idea · backlog bot", assignee="bot"),
        "t_00000004": _card("t_00000004", "[Hub] Vivo · implementar", assignee="claude-hub"),
    }
    cc, desk, bot = _center(tmp_path, shows)
    for cmd, tid, name in (("/epicas", "t_00000001", "Épicas"), ("/retenidas", "t_00000002", "Retenidas"),
                           ("/backlog", "t_00000003", "Backlog")):
        bot.sent.clear()
        command(desk, cmd)
        out = _all_text(bot)
        assert name in out and tid in out
        assert all(o not in out for o in ("t_00000004", *({"t_00000001", "t_00000002", "t_00000003"} - {tid})))


def test_pendientes_has_its_own_command_with_buttons_and_flags_unclassified(tmp_path):
    shows = {
        "t_00000001": _card("t_00000001", "[Personal] Renovar DNI · tuyo"),
        "t_00000002": _card("t_00000002", "[Signal] Elegir · decidir"),
        "t_00000003": _card("t_00000003", "[Hub] Roadmap · épica"),
        "t_00000004": _card("t_00000004", "Tarjeta mía sin paso"),
        "t_00000005": _card("t_00000005", "[Hub] De otro · tuyo", assignee="claude-hub"),
    }
    cc, desk, bot = _center(tmp_path, shows)
    command(desk, "/pendientes")
    out = _all_text(bot)
    assert "Renovar DNI" in out and "Elegir" in out
    assert "Roadmap" not in out and "De otro" not in out
    assert "sin clasificar" in out and "t_00000004" in out
    assert any(m["markup"] and texts(m["markup"]) for m in bot.sent if "Renovar DNI" in m["text"])


def test_reminder_source_excludes_epics(tmp_path):
    shows = {"t_00000001": _card("t_00000001", "[DECISIÓN] [Hub] Roadmap · épica")}
    cc, desk, bot = _center(tmp_path, shows)
    assert cc.decision_cards() == [] and cc.pending_decisions() == [] and cc.stuck_tasks() == []
    assert Reminders  # los recordatorios leen estas mismas tres listas (CommandCenter.pending_decisions/cards/stuck)
