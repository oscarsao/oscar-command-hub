"""22-10: autor configurable de los commits del integrador y modo de deploy `dokploy` (sin red ni Dokploy real)."""
from __future__ import annotations

import pytest
from agent_lanes.integrator import (DEFAULT_COMMIT_EMAIL, DEFAULT_COMMIT_NAME, Policy, _api_url_ok, ident_args,
                                    load_integrator_settings)

from .test_integrator import MERGE, make, press

APP = "app-123"
DOK = Policy(lane="claude-oscarhq", deploy="dokploy", dokploy_url="https://panel.example", dokploy_application_id=APP,
             dokploy_token_env="TEST_DOKPLOY_TOKEN", health_url="https://ohq/api/health")


class Dokploy:
    """API de Dokploy simulada: `one` es la cola de respuestas de application.one (la última se repite)."""

    def __init__(self, one, redeploy_code=200):
        self.one, self.redeploy_code, self.calls = list(one), redeploy_code, []

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, headers, body))
        if url.endswith("/api/application.deploy"):
            return self.redeploy_code, {}
        if "/api/application.one" in url:
            return 200, (self.one.pop(0) if len(self.one) > 1 else self.one[0])
        raise AssertionError(url)


def app(status, deps=()):
    return {"applicationStatus": status, "deployments": [{"deploymentId": i, "status": s} for i, s in deps]}


def deploy(tmp_path, monkeypatch, one, *, policy=DOK, health=None, token="tok", redeploy_code=200):
    monkeypatch.setenv("TEST_DOKPLOY_TOKEN", token) if token else monkeypatch.delenv("TEST_DOKPLOY_TOKEN", raising=False)
    integ, w, h, tg, d, clock = make(tmp_path, health=health)
    integ.settings.policies["claude-oscarhq"] = policy
    api = Dokploy(one, redeploy_code)
    integ._http_json = api
    integ.run_pass()
    press(d, tg)
    press(d, tg)  # 🚀 Desplegar
    return integ, w, h, tg, api, clock


def test_dokploy_redeploy_polls_application_and_checks_health(tmp_path, monkeypatch):
    one = [app("done", [("old", "done")]), app("running", [("old", "done")]),
           app("running", [("old", "done"), ("new", "running")]), app("done", [("old", "done"), ("new", "done")])]
    integ, w, h, tg, api, clock = deploy(tmp_path, monkeypatch, one)
    red = [c for c in api.calls if c[1].endswith("application.deploy")]
    assert len(red) == 1 and red[0][0] == "POST" and red[0][3] == {"applicationId": APP}
    assert red[0][2]["x-api-key"] == "tok"  # el token sale de la variable, nunca del yaml
    assert "🚀 desplegado · ddddddd" in tg.edits[-1]["text"] and "health OK" in tg.edits[-1]["text"]
    assert any(t.startswith(f"DESPLEGADO {MERGE} · dokploy deployment new done") for _, t, _ in h.comments)
    assert integ._state("t_1")["status"] == "deployed"
    assert clock.t > 0  # esperó al despliegue nuevo, no se quedó con el done del anterior


def test_dokploy_stale_done_is_not_the_new_deploy(tmp_path, monkeypatch):
    # el despliegue nuevo nunca aparece y el estado sigue `done` desde antes: hay que ver arrancar o asentarse 3 lecturas
    integ, w, h, tg, api, clock = deploy(tmp_path, monkeypatch, [app("done", [("old", "done")])])
    assert clock.t >= 40  # no aceptó la primera lectura (poll_seconds=20 en los tests)
    assert "🚀 desplegado" in tg.edits[-1]["text"]


@pytest.mark.parametrize("status_new", ["error"])
def test_dokploy_error_blocks_with_failure_comment(tmp_path, monkeypatch, status_new):
    one = [app("done", [("old", "done")]), app("error", [("old", "done"), ("new", status_new)])]
    integ, w, h, tg, api, _ = deploy(tmp_path, monkeypatch, one)
    assert "⛔ deploy falló · error" in tg.edits[-1]["text"]
    assert any(t.startswith("DEPLOY-FALLIDO") for _, t, _ in h.comments)
    assert integ._state("t_1")["status"] != "deployed"


def test_dokploy_never_finishing_times_out(tmp_path, monkeypatch):
    one = [app("done", [("old", "done")]), app("running", [("old", "done")])]
    integ, w, h, tg, api, clock = deploy(tmp_path, monkeypatch, one)
    assert "⛔ deploy falló · sin terminar en 15 min" in tg.edits[-1]["text"]
    assert clock.t >= 900


def test_dokploy_done_but_health_down_is_a_failure(tmp_path, monkeypatch):
    one = [app("done", [("old", "done")]), app("done", [("old", "done"), ("new", "done")])]
    integ, w, h, tg, api, _ = deploy(tmp_path, monkeypatch, one, health=lambda url: (502, ""))
    assert "⛔ deploy falló · health HTTP 502" in tg.edits[-1]["text"]
    assert any(t.startswith("DEPLOY-FALLIDO") and "health HTTP 502" in t for _, t, _ in h.comments)


def test_dokploy_redeploy_rejected(tmp_path, monkeypatch):
    integ, w, h, tg, api, _ = deploy(tmp_path, monkeypatch, [app("done", [("old", "done")])], redeploy_code=401)
    assert "⛔ deploy falló al pedirlo a Dokploy" in tg.edits[-1]["text"]
    assert any("application.deploy HTTP 401" in t for _, t, _ in h.comments)


def test_dokploy_without_token_or_application_id_does_not_call_api(tmp_path, monkeypatch):
    integ, w, h, tg, api, _ = deploy(tmp_path, monkeypatch, [app("done")], token="")
    assert "falta la variable TEST_DOKPLOY_TOKEN" in tg.edits[-1]["text"] and not api.calls
    integ, w, h, tg, api, _ = deploy(tmp_path / "otro", monkeypatch, [app("done")],
                                     policy=Policy(**{**DOK.__dict__, "dokploy_application_id": ""}))
    assert "deploy no configurado" in tg.edits[-1]["text"] and not api.calls


def test_dokploy_unreachable_api_blocks_before_redeploy(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_DOKPLOY_TOKEN", "tok")
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.settings.policies["claude-oscarhq"] = DOK

    def boom(*a, **k):
        raise OSError("sin túnel")
    integ._http_json = boom
    integ.run_pass()
    press(d, tg)
    press(d, tg)
    assert "Dokploy no responde" in tg.edits[-1]["text"]


def test_dokploy_policy_offers_deploy_button_after_merge(tmp_path, monkeypatch):
    integ, w, h, tg, d, _ = make(tmp_path)
    integ.settings.policies["claude-oscarhq"] = DOK
    integ.run_pass()
    press(d, tg)
    assert "🚀 Desplegar" in str(tg.edits[-1]["markup"]) and "sin desplegar" in tg.edits[-1]["text"]


def test_dokploy_settings_validation(tmp_path):
    y = tmp_path / "l.yaml"
    y.write_text("lanes: {}\nintegrator:\n  lanes:\n    x: {deploy: dokploy, dokploy_url: 'http://evil.example'}\n")
    with pytest.raises(ValueError, match="dokploy_url"):
        load_integrator_settings(y, env={})
    y.write_text("lanes: {}\nintegrator:\n  lanes:\n    x: {deploy: dokploy, dokploy_url: 'http://localhost:3300', "
                 "dokploy_token_env: 'abc def'}\n")
    with pytest.raises(ValueError, match="NOMBRE"):
        load_integrator_settings(y, env={})
    y.write_text("lanes: {}\nintegrator:\n  lanes:\n    x: {deploy: dokploy, dokploy_url: 'http://localhost:3300', "
                 "dokploy_token_env: T}\n")
    assert load_integrator_settings(y, env={}).policies["x"].deploy == "dokploy"
    assert _api_url_ok("http://127.0.0.1:3300") and not _api_url_ok("http://localhost.evil.com")


# --- autor de los commits ---------------------------------------------------------------------------------

def test_commit_author_default_is_oscars_github_noreply(tmp_path):
    y = tmp_path / "l.yaml"
    y.write_text("lanes: {}\nintegrator: {}\n")
    s = load_integrator_settings(y, env={})
    assert (s.commit_name, s.commit_email) == (DEFAULT_COMMIT_NAME, DEFAULT_COMMIT_EMAIL)
    assert DEFAULT_COMMIT_EMAIL.endswith("@users.noreply.github.com") and "localhost" not in DEFAULT_COMMIT_EMAIL


def test_commit_author_from_yaml_and_validation(tmp_path):
    y = tmp_path / "l.yaml"
    y.write_text("lanes: {}\nintegrator:\n  commit_author: {name: Ana Pérez, email: 1+ana@users.noreply.github.com}\n",
                 encoding="utf-8")
    s = load_integrator_settings(y, env={})
    assert ident_args(s.commit_name, s.commit_email)[:4] == ("-c", "user.name=Ana Pérez", "-c",
                                                              "user.email=1+ana@users.noreply.github.com")
    y.write_text("lanes: {}\nintegrator:\n  commit_author: {name: x, email: 'sin-arroba'}\n")
    with pytest.raises(ValueError, match="commit_author"):
        load_integrator_settings(y, env={})


def test_real_lanes_yaml_author_is_not_localhost():
    s = load_integrator_settings(env={})
    assert "localhost" not in s.commit_email and s.commit_email.endswith("@users.noreply.github.com")


def test_integrator_merge_uses_configured_author(tmp_path):
    integ, w, h, tg, d, _ = make(tmp_path)
    object.__setattr__(integ.settings, "commit_name", "Ana")
    object.__setattr__(integ.settings, "commit_email", "ana@users.noreply.github.com")
    integ.run_pass()
    merges = [a for a in w.argv("merge") if "--no-ff" in a]
    assert merges
    for a in merges:
        assert "user.email=ana@users.noreply.github.com" in a and "user.name=Ana" in a
    assert not any("agent-lanes@localhost" in " ".join(a) for a, _ in w.calls)
