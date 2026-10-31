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


# --- enrutado antes de reclamar (P7) ---------------------------------------------------------------------------------

def test_pide_lo_imposible_detecta_sql_remoto_produccion_y_env_vars():
    from agent_lanes.preflight import pide_lo_imposible
    assert "base remota" in pide_lo_imposible(task("Aplica la migración en Supabase producción"))
    assert "base remota" in pide_lo_imposible(task("Usa execute_sql para crear la tabla"))
    assert "producción" in pide_lo_imposible(task("Despliega el cambio en Dokploy"))
    assert "variables de entorno" in pide_lo_imposible(task("Cambia las variables de entorno en producción"))


def test_pide_lo_imposible_ignora_limites_y_negaciones():
    from agent_lanes.preflight import pide_lo_imposible
    for body in ("Corrige el typo\nLímites: nada de producción ni de desplegar",
                 "Sin desplegar nada, solo el código", "No apliques la migración en Supabase producción",
                 "Documenta cómo es el entorno de producción", "Aplica la migración en local"):
        assert pide_lo_imposible(task(body)) is None, body


def test_pide_lo_imposible_no_dispara_con_despliega_sin_objetivo_de_produccion():
    from agent_lanes.preflight import pide_lo_imposible
    for body in ("Despliega el menú lateral con un acordeón", "Los tests deben pasar antes de desplegar",
                 "Despliega en producción: no, eso lo hace el integrador",
                 "Corrige el typo\nLímites:\n- nada de desplegar a producción\n- sin tocar Vercel\n\nHecho: test verde"):
        assert pide_lo_imposible(task(body)) is None, body
    assert pide_lo_imposible(task("Redespliega la app en Railway")) is not None
    assert pide_lo_imposible(task("Límites:\n- solo código\n\nDespliega a producción al terminar")) is not None


def _runner_sin_clasificador(monkeypatch, body):
    from agent_lanes import clasificador
    monkeypatch.setattr(clasificador, "evaluar", lambda *a, **k: type(
        "V", (), {"va_al_integrador": False, "avisos": [], "dudas": []})())
    h = FakeHermes(tasks=[task(body)])
    w = FakeWorker([ok_outcome()])
    return LaneRunner(LANE, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(), notify=FakeNotifier()), h, w


def test_runner_no_lanza_worker_si_la_tarjeta_pide_lo_imposible(monkeypatch):
    r, h, w = _runner_sin_clasificador(monkeypatch, "Aplica la migración en Supabase producción")
    assert r.run_once() == {"t_1": "blocked:limitacion_guard"} and w.runs == []
    block = next(c for c in h.calls if c[0] == "block")
    assert block[2] == "needs_input" and "LIMITACION-GUARD:" in block[3] and "(preflight)" in block[3]
    assert "lo hace el integrador" in block[3]


def test_runner_lanza_worker_normal_con_limites_de_produccion(monkeypatch):
    r, h, w = _runner_sin_clasificador(monkeypatch, "Corrige el typo\nLímites: nada de producción")
    assert r.run_once() == {"t_1": "review"} and w.runs == ["run"]
