"""Bucles del integrador (0a): un PR `failed` se reevalúa si cambió el gate o con reset explícito, y un PR en
batch_pending sin salida avisa UNA vez. Mocks de git/gh/Hermes/Telegram de test_integrator: nada real."""
from __future__ import annotations

import dataclasses

from agent_lanes import integrator as integ_mod
from agent_lanes.integrator import gate_fingerprint, reset_cli
from tests.test_integrator import World, make


def failed_world():
    w = World()
    w.test_rc = 1
    return w


def test_failed_same_head_not_reevaluated_while_gate_unchanged(tmp_path):
    integ, w, h, tg, _, _ = make(tmp_path, world=failed_world())
    assert integ.run_pass() == {"t_1": "gates_failed"}
    assert integ._state("t_1")["gate_fp"] == gate_fingerprint()
    n = len(tg.sent)
    assert integ.run_pass(force=True) == {}  # misma cabeza, mismo gate: no se repite ni se avisa
    assert len(tg.sent) == n


def test_failed_is_reevaluated_when_gate_fingerprint_changes(tmp_path, monkeypatch):
    integ, w, h, tg, _, _ = make(tmp_path, world=failed_world())
    assert integ.run_pass() == {"t_1": "gates_failed"}
    w.test_rc = 0  # el falso positivo ya está parcheado
    monkeypatch.setattr(integ_mod, "gate_fingerprint", lambda: "otra-huella")
    assert integ.run_pass(force=True) == {"t_1": "offered"}
    assert integ._state("t_1")["status"] == "offered"


def test_legacy_failed_state_without_fingerprint_is_reevaluated_once(tmp_path):
    integ, w, h, tg, _, _ = make(tmp_path)
    integ._save("t_1", status="failed", head_sha=w.pr["headRefOid"])  # estado de antes de la huella
    assert integ.run_pass(force=True) == {"t_1": "offered"}


def test_reset_forces_reevaluation(tmp_path):
    integ, w, h, tg, _, _ = make(tmp_path, world=failed_world())
    integ.run_pass()
    w.test_rc = 0
    out = []
    assert reset_cli("t_1", integrator=integ, out=out.append) == 0
    assert integ._state("t_1")["status"] == "reset"
    assert integ.run_pass(force=True) == {"t_1": "offered"}


def test_reset_refuses_other_states_and_bad_ids(tmp_path):
    integ, *_ = make(tmp_path)
    integ._save("t_1", status="offered")
    assert integ.reset_pr("t_1")[0] is False
    assert integ.reset_pr("../x")[0] is False
    assert reset_cli("t_1", integrator=integ, out=lambda s: None) == 1


def _batch(tmp_path, **kw):
    integ, w, h, tg, d, clock = make(tmp_path, **kw)
    # Sin unión automática ni aviso diario (atascos.py, test_atascos.py): aquí se prueba el aviso de lote atascado.
    pol = dataclasses.replace(integ.settings.policies["claude-oscarhq"], batch="daily", append_only_paths=())
    integ.batches.notice.tick = lambda: []
    integ.settings = dataclasses.replace(integ.settings, policies={"claude-oscarhq": pol}, batch_pending_days=3)
    t = [1_000_000.0]
    integ._now = lambda: t[0]
    return integ, w, h, tg, t


def test_batch_pending_fresh_does_not_alert(tmp_path):
    integ, w, h, tg, t = _batch(tmp_path)
    assert integ.run_pass(force=True) == {"t_1": "batch_pending"}
    t[0] += 2 * 86400
    integ.run_pass(force=True)
    assert tg.sent == [] and h.comments == []


def test_batch_pending_stale_alerts_once(tmp_path):
    integ, w, h, tg, t = _batch(tmp_path)
    integ.run_pass(force=True)
    t[0] += 4 * 86400
    integ.run_pass(force=True)
    integ.run_pass(force=True)
    integ.run_pass(force=True)
    assert len(tg.sent) == 1 and "4 días" in tg.sent[0]["text"]
    assert len([c for c in h.comments if "sin salida" in c[1]]) == 1
    assert integ._state("t_1")["status"] == "batch_pending"


def test_batch_pending_with_conflicts_alerts_once_immediately(tmp_path):
    integ, w, h, tg, t = _batch(tmp_path)
    w.pr["mergeable"] = "CONFLICTING"
    integ.run_pass(force=True)
    integ.run_pass(force=True)
    assert len(tg.sent) == 1 and "conflictos" in tg.sent[0]["text"]


def test_batch_pending_alert_does_not_block_batch_state(tmp_path):
    integ, w, h, tg, t = _batch(tmp_path)
    w.pr["mergeable"] = "CONFLICTING"
    integ.run_pass(force=True)
    assert integ._state("t_1")["pending_alert"] == "conflict"
    assert integ._state("t_1")["pending_since"] == t[0]
