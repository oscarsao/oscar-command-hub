"""Producción desde Telegram (01-10): Scraper con deploy on_merge verificado por GitHub Deployments y 🚀 de Oscar HQ
que nombra las migraciones pendientes. Dobles de gh/git/Telegram, sin red."""
from __future__ import annotations

import json

import pytest

from agent_lanes.integration import risk_of
from agent_lanes.integrator import Policy, load_integrator_settings
from tests.test_integrator import MERGE, MGT_SLUG, OHQ_POLICY, World, buttons, cp, make

WHERE = {"chat_id": "-100", "thread_id": "231", "message_id": 555}
SCRAPER_POLICY =Policy(lane="claude-migrateam", deploy="on_merge", deploy_check="github_deployments",
                        risk="production", risk_label="SCRAPER")


class DeployWorld(World):
    """gh api de deployments/statuses: `states` es la secuencia de estados del último deployment."""

    def __init__(self, states, deployments=True, sha=MERGE):
        super().__init__(MGT_SLUG)
        self.states, self.deployments_on, self.dep_sha = list(states), deployments, sha

    def _gh(self, args):
        if args[1] == "api" and "/deployments?sha=" in args[2]:
            return cp(0, json.dumps([{"id": 11, "sha": self.dep_sha}] if self.deployments_on else []))
        if args[1] == "api" and args[2].endswith("/deployments/11/statuses?per_page=1"):
            st = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return cp(0, json.dumps([{"state": st}]))
        return super()._gh(args)


def scraper_verify(tmp_path, world):
    integ, w, h, tg, d, clock = make(tmp_path, name="claude-migrateam", world=world)
    ln = integ.lanes["claude-migrateam"]
    rec = {"kind": "integrate", "task_id": "t_1", "board": "migrateam", "lane": "claude-migrateam", "title": "x",
           "pr_number": 7, "pr_url": f"https://github.com/{MGT_SLUG}/pull/7", "merge_sha": MERGE}
    integ._verify_github_deployments(ln, SCRAPER_POLICY, rec, WHERE, d)
    return integ, h, tg, clock


def test_scraper_policy_in_real_yaml_is_production_with_github_check():
    p = load_integrator_settings(env={}).policies["claude-scraper"]
    assert (p.deploy, p.deploy_check, p.risk, p.risk_label) == ("on_merge", "github_deployments", "production",
                                                               "SCRAPER")
    assert risk_of(p).emoji == "🔴"


def test_unknown_deploy_check_is_rejected(tmp_path):
    yml = tmp_path / "l.yaml"
    yml.write_text("integrator:\n  lanes:\n    x: {deploy: on_merge, deploy_check: magia}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_integrator_settings(yml, env={})


def test_scraper_waits_until_github_deployment_success(tmp_path):
    integ, h, tg, clock = scraper_verify(tmp_path, DeployWorld(["queued", "in_progress", "success"]))
    assert any("DESPLEGADO" in c[1] for c in h.comments) and "desplegado" in tg.all_text
    assert clock.t >= 40  # sondeó dos veces antes del success


def test_scraper_failure_state_alerts(tmp_path):
    integ, h, tg, _ = scraper_verify(tmp_path, DeployWorld(["in_progress", "failure"]))
    assert any("DEPLOY-FALLIDO" in c[1] for c in h.comments) and "deploy falló" in tg.all_text


def test_scraper_timeout_is_unconfirmed_not_success(tmp_path):
    integ, h, tg, clock = scraper_verify(tmp_path, DeployWorld(["in_progress"]))
    assert any("DEPLOY-SIN-CONFIRMAR" in c[1] for c in h.comments) and clock.t >= 900
    assert not any(c[1].startswith("DESPLEGADO") for c in h.comments)


def test_scraper_ignores_deployments_of_other_commits(tmp_path):
    integ, h, tg, _ = scraper_verify(tmp_path, DeployWorld(["success"], sha="f" * 40))
    assert any("DEPLOY-SIN-CONFIRMAR" in c[1] for c in h.comments)


def test_scraper_without_any_deployment_times_out(tmp_path):
    integ, h, tg, _ = scraper_verify(tmp_path, DeployWorld(["success"], deployments=False))
    assert any("DEPLOY-SIN-CONFIRMAR" in c[1] and "none" in c[1] for c in h.comments)


def test_oscarhq_after_merge_names_pending_migrations_one_by_one(tmp_path):
    w = World()
    w.changed = ["supabase/migrations/20261001_a.sql", "supabase/migrations/20261001_b.sql", "src/app.py"]
    integ, w, h, tg, d, _ = make(tmp_path, world=w)
    ln = integ.lanes["claude-oscarhq"]
    rec = {"kind": "integrate", "task_id": "t_1", "board": "oscarhq", "lane": "claude-oscarhq", "title": "x",
           "pr_number": 7, "pr_url": "u", "merge_sha": MERGE, "migration": "supabase"}
    integ._after_merge(ln, OHQ_POLICY, rec, WHERE, d, MERGE)
    assert "20261001_a.sql" in tg.all_text and "20261001_b.sql" in tg.all_text and "src/app.py" not in tg.all_text
    assert integ._state("t_1")["migration_files"] == ["supabase/migrations/20261001_a.sql",
                                                      "supabase/migrations/20261001_b.sql"]
    assert any("Migración aplicada" in b for m in tg.edits for b in buttons(m["markup"]))


def test_oscarhq_rejected_deploy_is_logged_on_the_card(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    ln = integ.lanes["claude-oscarhq"]
    problem = integ._tip_problem(ln, OHQ_POLICY, "t_1", MERGE, "no-es-sha")
    assert problem and any("DEPLOY-RECHAZADO" in c[1] for c in h.comments)
