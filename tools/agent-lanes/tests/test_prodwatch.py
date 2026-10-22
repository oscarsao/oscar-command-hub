"""Avisos de producción del monitor (22-10): Vercel producción fallida y salud del servidor, sin repetir (sin red)."""
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("psutil")
pytest.importorskip("rich")

MON = Path(__file__).resolve().parents[2] / "monitor" / "monitor.py"
NOW = 1_800_000_000.0


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("monitor_prod_under_test", MON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", tmp_path)
    monkeypatch.setattr(mod, "ALERTS_LOG", tmp_path / "alerts.log")
    return mod


def dep(uid, *, target="production", state="ERROR", age=600, project="recursos"):
    return {"uid": uid, "name": project, "state": state, "target": target, "created": (NOW - age) * 1000,
            "meta": {"githubCommitSha": "abcdef1234567", "githubCommitMessage": "feat: x\n\ncuerpo"}}


class Api:
    def __init__(self, deployments=(), health=200, dokploy="running"):
        self.deployments, self.health, self.dokploy, self.calls = list(deployments), health, dokploy, []

    def __call__(self, url, headers, timeout=15):
        self.calls.append((url, headers))
        if "api.vercel.com" in url:
            return 200, {"deployments": self.deployments}
        if "application.one" in url:
            return 200, {"applicationStatus": self.dokploy}
        return self.health, None


def watch(monitor, tmp_path, api, monkeypatch, **kw):
    monkeypatch.setenv("T_VERCEL", "vtok")
    monkeypatch.setenv("T_DOK", "dtok")
    policies = {"claude-oscarhq": {"health_url": "https://api.example/api/health", "dokploy_url": "http://localhost:3300",
                                   "dokploy_application_id": "app1", "dokploy_token_env": "T_DOK"}}
    return monitor.prodwatch.ProdWatch({"token_env": "T_VERCEL", "projects": ["recursos"]}, policies, http=api,
                                       state_path=tmp_path / "prodwatch.json", now=lambda: NOW, **kw)


def make(monitor, prod, pushed):
    m = monitor.Monitor(write_log=True, push=pushed.append, prod=prod)
    m.now = lambda: datetime(2026, 10, 22, 12, 0)
    return m


def test_failed_production_deploy_alerts_once_and_only_production(monitor, tmp_path, monkeypatch):
    api = Api([dep("dpl_prod"), dep("dpl_preview", target=None), dep("dpl_ok", state="READY")])
    pushed = []
    m = make(monitor, watch(monitor, tmp_path, api, monkeypatch), pushed)
    m.refresh_prod(force=True)
    assert len(pushed) == 1 and "PRODUCCIÓN de recursos" in pushed[0] and "abcdef1" in pushed[0]
    assert "feat: x" in pushed[0] and "cuerpo" not in pushed[0]
    assert "vtok" not in pushed[0] and "vtok" not in (tmp_path / "alerts.log").read_text(encoding="utf-8")
    assert "target=production" in api.calls[0][0] and "state=ERROR" in api.calls[0][0]
    m.refresh_prod(force=True)  # mismo despliegue: no se repite
    assert len(pushed) == 1


def test_seen_deploys_survive_restart_and_old_failures_are_ignored(monitor, tmp_path, monkeypatch):
    api = Api([dep("dpl_prod"), dep("dpl_viejo", age=3 * 24 * 3600)])
    w1 = watch(monitor, tmp_path, api, monkeypatch)
    assert [f["id"] for f in w1.vercel_failures()] == ["dpl_prod"]
    assert json.loads((tmp_path / "prodwatch.json").read_text())["seen"] == ["dpl_prod"]
    assert watch(monitor, tmp_path, api, monkeypatch).vercel_failures() == []  # otro proceso, mismo fichero


def test_vercel_needs_token_and_projects(monitor, tmp_path, monkeypatch):
    api = Api([dep("dpl_prod")])
    w = watch(monitor, tmp_path, api, monkeypatch)
    monkeypatch.delenv("T_VERCEL")
    assert w.vercel_failures() == [] and not api.calls


def test_server_health_down_alerts_after_two_reads_without_repeating_then_resolves(monitor, tmp_path, monkeypatch):
    api = Api(health=502)
    pushed = []
    m = make(monitor, watch(monitor, tmp_path, api, monkeypatch), pushed)
    m.refresh_prod(force=True)
    assert not pushed  # una lectura no avisa
    m.refresh_prod(force=True)
    m.refresh_prod(force=True)
    assert len(pushed) == 1 and "oscarhq" in pushed[0] and "502" in pushed[0]
    api.health = 200
    m.refresh_prod(force=True)
    assert len(pushed) == 2 and "Resuelto" in pushed[1]


def test_dokploy_error_status_alerts(monitor, tmp_path, monkeypatch):
    api = Api(dokploy="error")
    pushed = []
    m = make(monitor, watch(monitor, tmp_path, api, monkeypatch), pushed)
    for _ in range(2):
        m.refresh_prod(force=True)
    assert len(pushed) == 1 and "Dokploy" in pushed[0] and "error" in pushed[0]
    assert any(h.get("x-api-key") == "dtok" for _, h in api.calls)  # el token va en la cabecera, no en el texto
    assert "dtok" not in pushed[0]


def test_skip_health_and_known_urls_are_not_checked(monitor, tmp_path, monkeypatch):
    api = Api(health=500)
    w = watch(monitor, tmp_path, api, monkeypatch, skip_health=["claude-oscarhq"])
    assert not [k for k, *_ in w.server_checks() if k.startswith("health-srv")]
    w = watch(monitor, tmp_path, api, monkeypatch, skip_urls=["https://api.example/api/health"])
    assert not [k for k, *_ in w.server_checks() if k.startswith("health-srv")]


def test_from_config_reads_lanes_yaml(monitor, tmp_path):
    y = tmp_path / "lanes.yaml"
    y.write_text("monitor_alerts:\n  vercel: {token_env: T, projects: [a]}\n  skip_health: [claude-x]\n"
                 "integrator:\n  lanes:\n    claude-x: {health_url: 'https://x/h'}\n", encoding="utf-8")
    w = monitor.prodwatch.from_config(y, [], tmp_path / "s.json")
    assert w.vercel["projects"] == ["a"] and w.skip_health == {"claude-x"}
    y.write_text("lanes: {}\n", encoding="utf-8")
    assert monitor.prodwatch.from_config(y) is None


def test_real_lanes_yaml_configures_alerts(monitor):
    w = monitor.prodwatch.from_config(Path(__file__).resolve().parents[1] / "lanes.yaml")
    assert w and w.vercel["token_env"] == "VERCEL_TOKEN" and w.vercel["projects"]


def test_production_deploy_alert_passes_quiet_hours(monitor, tmp_path, monkeypatch):
    pushed = []
    m = make(monitor, watch(monitor, tmp_path, Api([dep("dpl_prod")]), monkeypatch), pushed)
    m.now = lambda: datetime(2026, 10, 22, 3, 0)
    m.refresh_prod(force=True)
    assert len(pushed) == 1
