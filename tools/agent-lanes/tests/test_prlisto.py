"""[PR LISTOS] (t_2795df37): recordatorio periódico de PR de carriles abiertos, separado por MERGEABLE / CONFLICTING /
UNKNOWN, para todos los repos de los carriles. Dobles de test_integrator: nada real."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

from agent_lanes.integrator import load_integrator_settings
from agent_lanes.prlisto import ReadyPRWatcher
from tests.test_integrator import cp, make

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc).timestamp()


def _iso(hours_ago: float) -> str:
    return datetime.fromtimestamp(NOW - hours_ago * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(n, state, hours, head=None, **kw):
    return {"number": n, "title": f"Cambio {n}", "url": f"https://github.com/oscarsao/oscar-hq/pull/{n}",
            "mergeable": state, "isDraft": False, "headRefName": head or f"lane/t_{n}", "createdAt": _iso(hours),
            "isCrossRepository": False, **kw}


def _env(tmp_path, prs, **settings):
    integ, w, h, tg, d, clock = make(tmp_path, approve=False)
    integ.settings = dataclasses.replace(integ.settings, **settings)
    integ._now = lambda: NOW
    lists = []

    def runner(args, **kw):
        if "list" in args and "pr" in args:
            lists.append(args)
            return cp(0, json.dumps(prs))
        return w(args, **kw)

    integ._run = runner
    return integ, tg, lists


def test_avisa_de_listos_conflictos_y_unknown_por_separado(tmp_path):
    prs = [_pr(211, "MERGEABLE", 5), _pr(210, "CONFLICTING", 9), _pr(205, "UNKNOWN", 4), _pr(1, "MERGEABLE", 0.5)]
    integ, tg, lists = _env(tmp_path, prs)
    assert integ.pr_watch.tick() is True
    text = tg.sent[-1]["text"]
    assert "Listos para fusionar (1)" in text and "pull/211" in text and "5 h" in text
    assert "Con conflictos (1)" in text and "rebase" in text and "pull/210" in text
    assert "Pendientes de GitHub (1)" in text and "pull/205" in text
    assert "pull/1\"" not in text  # menos de 2 h: todavía no
    assert text.index("Listos") < text.index("conflictos") < text.index("Pendientes")


def test_unknown_solo_no_avisa_y_se_reintenta(tmp_path):
    prs = [_pr(205, "UNKNOWN", 4)]
    integ, tg, lists = _env(tmp_path, prs)
    assert integ.pr_watch.tick() is False and tg.sent == []
    prs[0]["mergeable"] = "MERGEABLE"  # la siguiente pasada GitHub ya lo sabe
    assert integ.pr_watch.tick() is True
    assert "Listos para fusionar (1)" in tg.sent[-1]["text"]


def test_no_repite_el_mismo_conjunto_pero_si_cambia_o_pasa_el_tiempo(tmp_path):
    prs = [_pr(211, "MERGEABLE", 5)]
    integ, tg, lists = _env(tmp_path, prs)
    assert integ.pr_watch.tick() is True
    assert integ.pr_watch.tick() is False  # mismo conjunto
    prs.append(_pr(212, "MERGEABLE", 3))   # PR nuevo: avisa ya
    assert integ.pr_watch.tick() is True
    prs[1]["mergeable"] = "CONFLICTING"    # cambia de estado: avisa ya
    assert integ.pr_watch.tick() is True
    assert integ.pr_watch.tick() is False
    integ._now = lambda: NOW + 7 * 3600    # pasan más de pr_watch_repeat_hours
    assert integ.pr_watch.tick() is True
    assert len(tg.sent) == 4


def test_ignora_borradores_forks_ramas_ajenas_y_tarjetas_ya_fusionadas(tmp_path):
    prs = [_pr(1, "MERGEABLE", 9, isDraft=True), _pr(2, "MERGEABLE", 9, isCrossRepository=True),
           _pr(3, "MERGEABLE", 9, head="feature/x"), _pr(4, "MERGEABLE", 9, head="lane/t_merged"),
           _pr(5, "MERGEABLE", 9, head="lane/t_lote")]
    integ, tg, lists = _env(tmp_path, prs)
    integ._save("t_merged", status="merged")
    integ._save("t_lote", status="batch_included")
    assert integ.pr_watch.tick() is False and tg.sent == []


def test_recorre_todos_los_repos_distintos_de_los_carriles(tmp_path):
    integ, tg, lists = _env(tmp_path, [_pr(1, "MERGEABLE", 9)])
    other = dataclasses.replace(next(iter(integ.lanes.values())), name="claude-hub")
    integ.lanes = {**integ.lanes, "claude-hub": other}
    integ.links.repo_slug = lambda lane: "oscarsao/oscar-hq" if lane.name != "claude-hub" else "oscarsao/oscar-command-hub"
    integ.pr_watch.tick()
    repos = {a[a.index("--repo") + 1] for a in lists}
    assert repos == {"oscarsao/oscar-hq", "oscarsao/oscar-command-hub"}


def test_apagado_dry_run_y_fallo_de_gh_no_avisan_ni_lanzan(tmp_path):
    integ, tg, lists = _env(tmp_path, [_pr(1, "MERGEABLE", 9)], pr_watch_hours=0)
    assert integ.pr_watch.tick() is False and lists == []
    integ, tg, lists = _env(tmp_path, [_pr(1, "MERGEABLE", 9)])
    integ.dry_run = True
    assert integ.pr_watch.tick() is False and lists == []
    integ.dry_run = False
    integ._run = lambda args, **kw: cp(1, "", "boom")
    assert integ.pr_watch.tick() is False and tg.sent == []


def test_configurable_en_lanes_yaml(tmp_path):
    f = tmp_path / "lanes.yaml"
    f.write_text("integrator:\n  pr_watch_hours: 0\n  pr_watch_repeat_hours: 3\n", encoding="utf-8")
    s = load_integrator_settings(f, {})
    assert (s.pr_watch_hours, s.pr_watch_repeat_hours) == (0.0, 3.0)
    g = tmp_path / "otro.yaml"
    g.write_text("integrator:\n  interval_seconds: 300\n", encoding="utf-8")
    d = load_integrator_settings(g, {})
    assert (d.pr_watch_hours, d.pr_watch_repeat_hours) == (2.0, 6.0)


def test_render_limita_filas_por_seccion():
    rows = [{"repo": "r", "slug": "o/r", "number": n, "title": "t", "url": "", "age": 3.0, "state": "MERGEABLE"}
            for n in range(40)]
    assert "y 25 más" in ReadyPRWatcher.render(rows)
