"""Carril claude-hub (plan D): config real de lanes.yaml, ficha ⚙️ SISTEMA y botón [🔁 Aplicar (reinicio ordenado)]."""
from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path

import pytest

from agent_lanes.config import ROOT, load_lanes
from agent_lanes.integration import render_ficha, risk_of, summary_state
from agent_lanes.integrator import (APPLY_ARGV, APPLY_LABEL, INTEGRATOR_AUTHOR, Policy, apply_available,
                                    load_integrator_settings)
from agent_lanes.verify import render_test_cmd

from .test_integrator import MERGE, buttons, make, press

HUB = "claude-hub"
HUB_POLICY = Policy(lane="claude-oscarhq", deploy="none", apply="restart_drain", risk="system",
                    risk_label="SISTEMA", sensitive_paths=("tools/agent-lanes/contract/",))


# --- configuración real -----------------------------------------------------------------------------------

def test_hub_lane_config():
    lane = load_lanes()[HUB]
    assert lane.kind == "implement"
    assert Path(lane.repo) == Path("C:/Users/oscar/oscar-command-hub")
    assert (lane.base, lane.board, lane.remote) == ("main", "default", "origin")
    assert lane.telegram == ("-1003530490339", "5")  # Gestión · Operaciones General
    assert lane.model == "sonnet" and lane.max_budget_usd == 3.0
    assert lane.role == "roles/hub.md" and lane.role_path.is_file()
    # la lista del carril sustituye a la de defaults: .github/workflows/ tiene que seguir ahí
    assert set(lane.forbidden_paths) == {".github/workflows/", "tools/agent-lanes/.env", "tools/agent-lanes/.state/",
                                         "tools/monitor/.state/", "tools/telegram/.state/"}
    # mismos permisos que el resto de carriles de código (heredados): nada de Bash extra
    assert lane.allowed_tools == load_lanes()["claude-oscarhq"].allowed_tools


def test_hub_test_cmd_runs_gate_and_suite_of_the_worktree():
    cmd = render_test_cmd(load_lanes()[HUB])
    assert cmd.startswith(f'py -3.12 "{ROOT.as_posix()}/agent_lanes/checks.py" origin/main && ')
    # ruta relativa al worktree (cwd del test_cmd): la suite del worker, nunca la del checkout vivo
    assert cmd.endswith("py -3.12 -m pytest tools/agent-lanes/tests -q")
    assert "{" not in cmd


def test_hub_is_reviewed_by_the_review_lane():
    assert HUB in load_lanes()["review"].reviews


def test_hub_integrator_policy_from_lanes_yaml():
    policy = load_integrator_settings(env={}, lane_filter=False).policies[HUB]
    assert policy.deploy == "none" and policy.apply == "restart_drain"
    assert risk_of(policy).emoji == "⚙️" and risk_of(policy).label == "SISTEMA"
    assert "tools/agent-lanes/contract/" in policy.sensitive_paths


def test_hub_not_active_unless_listed_in_integrator_lanes(monkeypatch):
    monkeypatch.setenv("INTEGRATOR_LANES", "claude-oscarhq,claude-migrateam")
    assert HUB not in load_integrator_settings(env={}).policies
    monkeypatch.setenv("INTEGRATOR_LANES", "claude-oscarhq,claude-migrateam,claude-hub")
    assert HUB in load_integrator_settings(env={}).policies


@pytest.mark.parametrize("policy", ["{apply: reboot}", "{apply: restart_drain, deploy: railway_up}"])
def test_apply_only_known_and_with_deploy_none(tmp_path, policy):
    cfg = tmp_path / "lanes.yaml"
    cfg.write_text(f"integrator:\n  lanes:\n    x: {policy}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="apply"):
        load_integrator_settings(cfg, env={})


def test_ficha_header_is_system():
    text = render_ficha(risk=risk_of(HUB_POLICY), phase="LISTO PARA INTEGRAR", tid="t_1", title="Arreglar guard",
                        repo="oscar-command-hub", base="main", status="🚦 Listo", pr=3)
    assert text.startswith("<b>⚙️ SISTEMA · LISTO PARA INTEGRAR · PR #3</b>")


def test_summary_states_for_apply():
    assert summary_state("merged", apply="restart_drain") == "🔁 fusionado, falta aplicar"
    assert summary_state("applied", apply="restart_drain") is None
    assert summary_state("merged") == "🚀 fusionado, deploy sin confirmar"  # sin apply: como siempre


# --- botón Aplicar ------------------------------------------------------------------------------------------

def hub(tmp_path, *, probe=True):
    integ, w, h, tg, d, clock = make(tmp_path)
    integ.settings = dataclasses.replace(integ.settings, policies={"claude-oscarhq": HUB_POLICY})
    launched = []

    def launch(argv, cwd):
        # el reinicio puede parar este proceso al arrancar: todo registrado ANTES de lanzar
        assert integ._state("t_1")["status"] == "applied"
        assert any(t.startswith("APLICADO ") for _, t, _a in h.comments)
        assert "reinicio ordenado lanzado" in tg.edits[-1]["text"]
        launched.append((argv, cwd))
    integ._launch = launch
    integ._apply_probe = lambda kind: probe
    return integ, w, h, tg, d, launched


def test_offer_says_merge_does_not_deploy(tmp_path):
    integ, w, h, tg, d, launched = hub(tmp_path)
    assert integ.run_pass() == {"t_1": "offered"}
    assert buttons(tg.sent[-1]["markup"]) == ["🔀 Fusionar"]
    assert "🔁 Aplicar hace el reinicio ordenado" in tg.sent[-1]["text"]
    assert launched == []


def test_merge_offers_apply_and_apply_launches_restart_drain(tmp_path):
    integ, w, h, tg, d, launched = hub(tmp_path)
    integ.run_pass()
    press(d, tg)
    assert "falta aplicar" in tg.edits[-1]["text"] and buttons(tg.edits[-1]["markup"]) == [APPLY_LABEL]
    assert launched == []  # fusionar no aplica nada
    assert integ._state("t_1")["status"] == "merged"
    press(d, tg)
    assert launched == [(["py", "-3.12", "lanes.py", "restart", "--drain"], str(ROOT))]
    assert APPLY_ARGV["restart_drain"] == launched[0][0]
    assert integ._state("t_1")["status"] == "applied"
    assert any(a == INTEGRATOR_AUTHOR and t.startswith(f"APLICADO {MERGE}") for _, t, a in h.comments)
    assert "reinicio ordenado lanzado" in tg.edits[-1]["text"]
    # nunca toca git en el checkout vivo ni empuja nada
    assert not [a for a, _ in w.calls if a[0] == "git" and ("push" in a or "pull" in a or "checkout" in a)]


def test_apply_pressed_twice_launches_once(tmp_path):
    integ, w, h, tg, d, launched = hub(tmp_path)
    integ.run_pass()
    press(d, tg)
    markup = tg.edits[-1]["markup"]
    press(d, tg, markup=markup)
    press(d, tg, markup=markup)
    assert len(launched) == 1


def test_without_restart_subcommand_there_is_no_apply_button(tmp_path):
    integ, w, h, tg, d, launched = hub(tmp_path, probe=False)
    integ.run_pass()
    press(d, tg)
    last = tg.edits[-1]
    assert "✅ fusionado en master" in last["text"] and "lanes.py restart --drain" in last["text"]
    assert not buttons(last["markup"])
    assert launched == []


def test_apply_launch_failure_keeps_it_pending(tmp_path):
    integ, w, h, tg, d, launched = hub(tmp_path)

    def boom(argv, cwd):
        raise OSError("sin py")
    integ._launch = boom
    integ.run_pass()
    press(d, tg)
    press(d, tg)
    assert integ._state("t_1")["status"] == "merged"  # vuelve a pendiente: el fijado sigue diciendo "falta aplicar"
    assert "no se pudo aplicar" in tg.edits[-1]["text"]
    assert any("FALLÓ" in t for _, t, _a in h.comments)


def test_apply_button_ignored_for_other_policies(tmp_path):
    integ, w, h, tg, d, launched = hub(tmp_path)
    integ.run_pass()
    press(d, tg)
    integ.settings = dataclasses.replace(integ.settings, policies={
        "claude-oscarhq": dataclasses.replace(HUB_POLICY, apply="")})
    press(d, tg)
    assert launched == []


def test_apply_available_reads_lanes_py_statically(tmp_path):
    f = tmp_path / "lanes.py"
    f.write_text('if argv[:1] == ["status"]:\n    pass\n', encoding="utf-8")
    assert apply_available("restart_drain", f) is False
    f.write_text('if argv[:1] == ["restart"] and "--drain" in argv:\n    pass\n', encoding="utf-8")
    assert apply_available("restart_drain", f) is True
    assert apply_available("otra", f) is False
    assert apply_available("restart_drain", tmp_path / "no.py") is False


def test_launch_detached_does_not_wait(monkeypatch):
    from agent_lanes import integrator
    seen = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            seen.update(argv=argv, **kw)

        def wait(self, *a, **kw):  # pragma: no cover - no debe llamarse
            raise AssertionError("no debe esperar")
    monkeypatch.setattr(integrator.subprocess, "Popen", FakePopen)
    integrator.launch_detached(["py", "-3.12", "lanes.py", "restart", "--drain"], "C:/x")
    assert seen["argv"][-2:] == ["restart", "--drain"] and seen["cwd"] == "C:/x"
    assert seen["stdin"] is subprocess.DEVNULL


@pytest.mark.skipif(sys.platform != "win32", reason="flags de Windows")
def test_launch_detached_breaks_away_from_job_or_falls_back(monkeypatch):
    from agent_lanes import integrator
    calls = []

    def fake_popen(argv, **kw):
        calls.append(kw["creationflags"])
        if kw["creationflags"] & integrator.CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError(5, "Acceso denegado")
    monkeypatch.setattr(integrator.subprocess, "Popen", fake_popen)
    integrator.launch_detached(["py"], "C:/x")
    assert len(calls) == 2 and calls[0] & integrator.CREATE_BREAKAWAY_FROM_JOB
    assert not calls[1] & integrator.CREATE_BREAKAWAY_FROM_JOB and calls[1] & 0x00000008  # DETACHED_PROCESS
