"""Mensaje vivo del tema de Integración (panel_vivo.py): render, edición idempotente, líneas que desaparecen,
botones y recreación. Dobles: sin red, sin Telegram real, sin hermes real."""
import time
from types import SimpleNamespace

from agent_lanes import titulos
from agent_lanes.integration import PinnedSummary
from agent_lanes.panel_vivo import (PV_APARCAR, PV_RESOLVER, PV_VER, LivePanel, PanelSettings, compose,
                                    render_sections)
from tests.test_integration_topic import INTEG, Tg

NOW = 1_000_000.0
CFG = PanelSettings(enabled=True)


def _render(**kw):
    base = dict(running=[], queue=[], done=[], conflicts=[], now=NOW, cfg=CFG)
    return render_sections(**{**base, **kw})


def test_settings_defaults_and_yaml_block():
    assert not PanelSettings.from_cfg(None).enabled
    s = PanelSettings.from_cfg({"enabled": True, "queue": 2, "done": 0})
    assert (s.enabled, s.queue, s.done, s.done_hours) == (True, 2, 0, 24.0)  # 0 es un valor válido, no «usa el defecto»


def test_render_lists_the_four_sections_with_limits():
    text = _render(
        running=[{"lane": "claude-hub", "id": "t_run", "title": "Panel vivo", "status": "running",
                  "since": NOW - 17 * 60}],
        queue=[{"id": f"t_q{n}", "title": f"cola {n}"} for n in range(5)],
        done=[{"id": f"t_d{n}", "title": f"hecha {n}"} for n in range(8)],
        conflicts=[{"tid": "t_c", "pr": 97, "title": "Rebase", "text": "conflicto con la base"}])
    assert "claude-hub: <code>t_run</code> · Panel vivo · implementando · 15 min" in text  # minutos en saltos de 5
    assert "/tarea t_q2" in text and "t_q3" not in text  # siguientes 3
    assert "/tarea t_d4" in text and "t_d5" not in text  # últimas 5
    assert "#97" in text and "conflicto con la base" in text
    empty = _render()
    assert "nada en marcha" in empty and "cola vacía" in empty and "ninguno" in empty


def test_render_escapes_html_and_fits_in_a_message():
    text = _render(queue=[{"id": "t_x", "title": "<b>&"}])
    assert "<b>&" not in text and "&lt;b&gt;&amp;" in text
    assert len(compose(text, "x\n" * 5000)) <= 4000


def test_edit_is_idempotent_and_recreated_when_pinned_message_is_gone(tmp_path):
    tg = Tg()
    pin = PinnedSummary(tg, INTEG, tmp_path / "pinned.json")
    text = compose(_render(), "✅ Nada pendiente de integrar")
    assert pin.update(text) == "created" and len(tg.pins) == 1
    assert pin.update(text) == "unchanged" and not tg.edits  # mismo texto: ni una edición
    assert pin.update(text + "\nmás") == "edited"
    tg.edit_ok = False  # el mensaje se borró
    assert pin.update(text) == "created" and len(tg.pins) == 2  # recreado y fijado de nuevo


def test_429_backs_off_instead_of_recreating(tmp_path):
    tg = Tg(edit_ok=False)
    pin = PinnedSummary(tg, INTEG, tmp_path / "pinned.json")
    pin.update("a")
    tg.rate_limited_until = time.time() + 60
    assert pin.update("b") == "error" and len(tg.sent) == 1 and not tg.edits  # ni edita ni duplica
    tg.rate_limited_until = 0
    assert pin.update("b") == "created"  # sin límite y con el mensaje borrado sí se recrea


def _panel(tmp_path, states, entries=(), lanes=("claude-hub",)):
    calls = SimpleNamespace(rebase=[], refresh=0, reissue=0)
    lane_objs = {n: SimpleNamespace(name=n, base="main", board="default", kind="implement") for n in lanes}
    i = SimpleNamespace(
        state_dir=tmp_path, _now=lambda: NOW, lanes=lane_objs,
        _seen=[(lane_objs[lanes[0]], None, t) for t in states],
        _state=lambda t: states.get(t, {}), pr_watch=SimpleNamespace(last_entries=list(entries)),
        rebase_card=lambda lane, tid, n: calls.rebase.append((lane.name, tid, n)) or True,
        refresh_summary=lambda: setattr(calls, "refresh", calls.refresh + 1),
        reissue_panel_markup=lambda: setattr(calls, "reissue", calls.reissue + 1))
    return LivePanel(i, CFG), i, calls


FAILED = {"status": "failed", "pr": 97, "title": "Panel", "conflict": True, "pr_url": "https://gh/pr/97", "since": NOW}


def test_conflict_line_disappears_when_resolved(tmp_path):
    states = {"t_a": dict(FAILED), "t_b": {"status": "offered", "pr": 5}}
    panel, i, _ = _panel(tmp_path, states)
    assert [c["tid"] for c in panel.conflict_entries()] == ["t_a"]
    states["t_a"] = {"status": "offered", "pr": 97}  # lo resolvió por otra vía
    assert panel.conflict_entries() == [] and panel.conflicts == []
    states["t_c"] = {"status": "pr_problem", "pr": 8, "pr_problem": "el PR está MERGED"}
    i._seen.append((i.lanes["claude-hub"], None, "t_c"))
    assert panel.conflict_entries() == []  # fusionado a mano: tampoco es un bloqueo


def test_pr_watch_conflicts_join_the_panel_once(tmp_path):
    e = {"tid": "t_w", "lane": "claude-hub", "number": 12, "title": "X", "url": "u", "state": "CONFLICTING"}
    ok = dict(e, tid="t_ok", state="MERGEABLE")
    panel, _, _ = _panel(tmp_path, {"t_a": dict(FAILED)}, entries=[e, ok, dict(e, tid="t_a")])
    assert sorted(c["tid"] for c in panel.conflict_entries()) == ["t_a", "t_w"]  # t_a no se duplica


def test_buttons_only_offer_resolver_for_rebase_conflicts(tmp_path):
    states = {"t_a": dict(FAILED), "t_g": {"status": "failed", "pr": 9, "fail_reasons": ["tests rojos"]}}
    panel, _, _ = _panel(tmp_path, states)
    panel.conflict_entries()
    issued = []

    def issue(rec, spec):
        issued.append((rec, [b["action"] for b in spec[0]]))
        return "tok", {"inline_keyboard": [[{"text": b["text"], "callback_data": "tok"} for b in spec[0]]]}
    rows = panel.markup_rows(issue)
    assert sorted(len(r) for r in rows) == [2, 3]
    by_tid = {rec["task_id"]: acts for rec, acts in issued}
    assert by_tid["t_a"] == [PV_RESOLVER, PV_VER, PV_APARCAR] and by_tid["t_g"] == [PV_VER, PV_APARCAR]


def _desk(tg):
    return SimpleNamespace(notifier=tg)


def test_buttons_do_their_thing_without_touching_the_pinned_message(tmp_path):
    tg = Tg()
    panel, i, calls = _panel(tmp_path, {"t_a": dict(FAILED)})
    where = {"chat_id": INTEG[0], "thread_id": INTEG[1], "message_id": 800}
    rec = {"task_id": "t_a", "lane": "claude-hub", "pr": 97, "url": "https://gh/pr/97"}
    assert panel.on_button(PV_VER, rec, where, _desk(tg)) and "/tarea t_a" in tg.sent[-1]["text"]
    assert panel.on_button(PV_RESOLVER, rec, where, _desk(tg))
    assert calls.rebase == [("claude-hub", "t_a", 97)] and calls.reissue == 2  # el token gastado se repone
    assert panel.on_button(PV_APARCAR, rec, where, _desk(tg)) and calls.refresh == 1
    assert not tg.edits  # nunca se pisa el fijado con texto suelto
    assert panel.conflict_entries() == []  # aparcado: no aparece
    del i._seen[:]  # el conflicto desaparece del todo: el aparcado se olvida
    panel.conflict_entries()
    assert panel.parked() == {}


def test_gather_reads_running_queue_and_recent_done(tmp_path):
    class H:
        def list_status(self, lane, status):
            return {"running": [{"id": "t_1", "title": "A", "started_at": NOW - 600}],
                    "ready": [{"id": "t_2", "priority": 1}, {"id": "t_3", "priority": 9}]}[status]

        def list_status_all(self, status):
            return {"review": [{"id": "t_r", "title": "R", "assignee": "reviewer"}],
                    "done": [{"id": "t_old", "completed_at": NOW - 90000}, {"id": "t_new", "completed_at": NOW - 60}]}[status]
    panel, i, _ = _panel(tmp_path, {})
    i.hermes_for = lambda board: H()
    data = panel.gather()
    assert [t["id"] for t in data["queue"]] == ["t_3", "t_2"]  # por prioridad
    assert [t["id"] for t in data["done"]] == ["t_new"]  # solo las últimas 24 h
    assert {(t["id"], t["status"]) for t in data["running"]} == {("t_1", "running"), ("t_r", "review")}


def test_rebase_card_title_follows_the_title_format():
    title = titulos.rebase_title("claude-hub", 97, "main")
    assert title == "[Hub] Rebasar #97 con main · implementar" and titulos.problems(title) == []
    assert titulos.rebase_title("claude-oscarhq", 1, "master").startswith("[OHQ] ")
