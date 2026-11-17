"""PR en borrador automático (pr_borrador.py): doble de gh, sin red."""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace

from agent_lanes import pr_borrador
from agent_lanes.config import load_lanes
from agent_lanes.runner import LaneRunner
from agent_lanes.verify import VerifyResult
from .test_runner_state import (GOOD, LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, ok_outcome)

URL = "https://github.com/oscarsao/repo/pull/42"
LANE_ON = replace(LANE, github="oscarsao/repo", base="main", pr_borrador_automatico=True)
RESULT = {**GOOD, "summary": "Hecho. Ver C:\\Users\\oscar\\dev\\x.py y /c/Users/oscar/y.py"}
CHECK = VerifyResult(ok=True, test_exit=0, remote_sha="a" * 40)


class FakeGh:
    def __init__(self, existing=None, create_rc=0, list_rc=0, boom=None):
        self.calls, self.existing, self.create_rc, self.list_rc, self.boom = [], existing or [], create_rc, list_rc, boom

    def __call__(self, cmd, **kw):
        if self.boom:
            raise self.boom
        self.calls.append(cmd)
        sub = cmd[1:3]
        if sub == ["pr", "list"]:
            return subprocess.CompletedProcess(cmd, self.list_rc, json.dumps(self.existing), "net down" if self.list_rc else "")
        return subprocess.CompletedProcess(cmd, self.create_rc, URL + "\n" if not self.create_rc else "",
                                           "HTTP 403" if self.create_rc else "")

    def creates(self):
        return [c for c in self.calls if c[1:3] == ["pr", "create"]]


def test_abre_borrador_cuando_no_hay_pr():
    gh = FakeGh()
    nota = pr_borrador.abrir(LANE_ON, "t_1", {"title": "Mi tarjeta", "body": ""}, RESULT, CHECK, gh_run=gh)
    assert URL in nota
    cmd = gh.creates()[0]
    assert "--draft" in cmd and cmd[cmd.index("--base") + 1] == "main" and cmd[cmd.index("--head") + 1] == "lane/t_1"
    assert cmd[cmd.index("--title") + 1] == "Mi tarjeta" and cmd[cmd.index("--repo") + 1] == "oscarsao/repo"


def test_cuerpo_con_datos_y_sin_rutas_ni_secretos():
    secreto = "AKIA" + "ABCDEFGHIJKLMNOP"
    res = {**RESULT, "summary": RESULT["summary"] + f"\nclave {secreto}"}
    gh = FakeGh()
    pr_borrador.abrir(LANE_ON, "t_1", {"title": "T"}, res, CHECK, gh_run=gh)
    cmd = gh.creates()[0]
    cuerpo = cmd[cmd.index("--body") + 1]
    assert "t_1" in cuerpo and "a" * 40 in cuerpo and LANE_ON.test_cmd in cuerpo and "OK (exit 0)" in cuerpo
    assert pr_borrador.NOTA in cuerpo
    assert "C:\\Users" not in cuerpo and "/c/Users" not in cuerpo and "oscar\\dev" not in cuerpo
    assert secreto not in cuerpo


def test_no_duplica_si_ya_hay_pr_abierto():
    gh = FakeGh(existing=[{"number": 7, "url": "https://github.com/oscarsao/repo/pull/7"}])
    nota = pr_borrador.abrir(LANE_ON, "t_1", {"title": "T"}, RESULT, CHECK, gh_run=gh)
    assert "pull/7" in nota and not gh.creates()


def test_fallo_de_gh_no_lanza_y_se_anota():
    for gh in (FakeGh(create_rc=1), FakeGh(list_rc=1), FakeGh(boom=OSError("sin gh"))):
        nota = pr_borrador.abrir(LANE_ON, "t_1", {"title": "T"}, RESULT, CHECK, gh_run=gh)
        assert "NO abierto" in nota


def test_interruptor_apagado_no_llama_a_gh():
    gh = FakeGh()
    assert pr_borrador.abrir(replace(LANE_ON, pr_borrador_automatico=False), "t_1", {}, RESULT, CHECK, gh_run=gh) is None
    assert not gh.calls


def test_rama_push_no_abre_pr():
    gh = FakeGh()
    task = {"title": "Rebase", "body": "Rama-origen: lane/t_0\nRama-push: lane/t_0\n"}
    assert pr_borrador.abrir(LANE_ON, "t_1", task, RESULT, CHECK, gh_run=gh) is None
    assert not gh.calls


def test_sin_slug_github_se_anota_sin_llamar_a_gh():
    gh = FakeGh()
    nota = pr_borrador.abrir(replace(LANE_ON, github=""), "t_1", {}, RESULT, CHECK, gh_run=gh)
    assert "NO abierto" in nota and not gh.calls


def test_interruptor_apagado_por_defecto_en_lanes_yaml():
    assert all(l.pr_borrador_automatico is False for l in load_lanes().values())


def _runner(tmp_path, lane, abrir):
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": ""}])
    r = LaneRunner(lane, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                   notify=FakeNotifier(), state_dir=tmp_path, pr_abrir=abrir)
    return r, h


def test_runner_comenta_la_url_en_la_tarjeta(tmp_path):
    r, h = _runner(tmp_path, LANE_ON, lambda *a: f"PR en borrador abierto por el runner: {URL}")
    assert r.process({"id": "t_1", "title": "T", "body": ""}) == "review"
    assert any(c[0] == "comment" and URL in str(c) for c in h.calls)


def test_runner_sigue_a_review_aunque_el_abridor_reviente(tmp_path):
    def boom(*a):
        raise RuntimeError("x")
    r, h = _runner(tmp_path, LANE_ON, boom)
    assert r.process({"id": "t_1", "title": "T", "body": ""}) == "review"
    assert any(c[0] == "review" for c in h.calls)
