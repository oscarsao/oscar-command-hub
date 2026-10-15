"""Chequeo previo de permisos del runner (dobles: sin red, sin hermes, sin claude)."""
from __future__ import annotations

from agent_lanes.config import Lane
from agent_lanes.preflight import preflight_warnings
from agent_lanes.runner import LaneRunner

from .test_runner_state import FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, ok_outcome

LANE = Lane(name="claude-x", board="x", repo="C:/r", base="develop", worktree_root="C:/wt",
            allowed_tools=("Bash(git status:*)", "Bash(git push:*)"),
            forbidden_paths=(".github/workflows/", "backend/alembic/versions/"))


def task(body, title="T"):
    return {"id": "t_1", "title": title, "body": body}


def test_migracion_con_ruta_vetada_pide_integrador():
    w = preflight_warnings(LANE, task("Añadir columna con una migración alembic"))
    assert len(w) == 1 and "integrador" in w[0] and "backend/alembic/versions/" in w[0]


def test_npm_install_y_gh_sin_permiso():
    w = preflight_warnings(LANE, task("Ejecuta npm install zod y abre el PR con gh pr create"))
    assert len(w) == 2
    assert any("npm" in x for x in w) and any("gh" in x for x in w)


def test_con_permiso_en_el_carril_no_avisa():
    lane = Lane(**{**LANE.__dict__, "allowed_tools": LANE.allowed_tools + ("Bash(npm install:*)", "Bash(gh pr:*)")})
    assert preflight_warnings(lane, task("npm install y gh pr create")) == []


def test_ruta_vetada_mencionada_avisa_y_sin_mencion_no():
    assert len(preflight_warnings(LANE, task("Edita .github/workflows/ci.yml"))) == 1
    assert preflight_warnings(LANE, task("Corrige un typo en el README")) == []


def test_sin_forbidden_paths_la_migracion_no_avisa():
    lane = Lane(**{**LANE.__dict__, "forbidden_paths": ()})
    assert preflight_warnings(lane, task("migración alembic")) == []


def test_carril_no_implement_no_avisa():
    lane = Lane(**{**LANE.__dict__, "kind": "review"})
    assert preflight_warnings(lane, task("npm install")) == []


def make(body):
    h = FakeHermes(tasks=[task(body)])
    n = FakeNotifier()
    w = FakeWorker([ok_outcome(), ok_outcome()])
    return LaneRunner(LANE, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(), notify=n), h, w, n


def test_el_runner_avisa_antes_del_worker_y_no_bloquea():
    r, h, w, n = make("Crear migración alembic")
    assert r.run_once() == {"t_1": "review"}
    assert w.runs == ["run"]  # el aviso no impide trabajar
    assert any(c[0] == "comment" and "aviso previo" in c[2] for c in h.calls)
    assert "⚠️" in n.msgs[0] and n.silent[0] is False


def test_sin_avisos_el_arranque_es_silencioso():
    r, h, w, n = make("Corrige un typo")
    r.run_once()
    assert not any(c[0] == "comment" and "aviso previo" in c[2] for c in h.calls)
    assert n.silent[0] is True


def test_el_aviso_sale_una_sola_vez_por_tarea():
    r, h, w, n = make("Crear migración alembic")
    r.process(task("Crear migración alembic"))
    h.claim_ok = True
    r.process(task("Crear migración alembic"))
    assert sum(1 for c in h.calls if c[0] == "comment" and "aviso previo" in c[2]) == 1
