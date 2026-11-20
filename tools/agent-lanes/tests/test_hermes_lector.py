"""t_ee0b27a3: lector de solo lectura de `show` (hermes.leer_show_ro), su interruptor y el caché de zona por tarjeta.
Sin red, sin hermes real, sin tocar la base viva: bases sintéticas en tmp_path y, si existe, una COPIA de la base."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from agent_lanes import choques, hermes, proc, solapes
from agent_lanes.hermes import HermesCLI, leer_show_ro

COPIAS = Path(r"C:\Users\oscar\integrador-docs\copias_bd_hermes")
CAMPOS_TASK = {"id", "title", "body", "assignee", "status", "priority", "tenant", "workspace_kind", "workspace_path",
               "branch_name", "project_id", "created_by", "created_at", "started_at", "completed_at", "result",
               "skills", "max_runtime_seconds", "max_retries", "model_override", "provider_override", "session_id",
               "workflow_template_id", "current_step_key", "completion_contract", "last_failure_error"}
CAMPOS_SHOW = {"task", "latest_summary", "parents", "children", "comments", "events", "runs"}

SCHEMA = """
CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, assignee TEXT, status TEXT NOT NULL,
  priority INTEGER DEFAULT 0, created_by TEXT, created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER,
  workspace_kind TEXT NOT NULL DEFAULT 'scratch', workspace_path TEXT, branch_name TEXT, project_id TEXT,
  claim_lock TEXT, claim_expires INTEGER, tenant TEXT, result TEXT, last_failure_error TEXT, max_runtime_seconds INTEGER,
  max_retries INTEGER, skills TEXT, model_override TEXT, provider_override TEXT, session_id TEXT,
  workflow_template_id TEXT, current_step_key TEXT, completion_contract TEXT);
CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL, PRIMARY KEY (parent_id, child_id));
CREATE TABLE task_comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, author TEXT NOT NULL,
  body TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, run_id INTEGER,
  kind TEXT NOT NULL, payload TEXT, created_at INTEGER NOT NULL);
CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, profile TEXT, step_key TEXT,
  status TEXT NOT NULL, worker_pid INTEGER, started_at INTEGER NOT NULL, ended_at INTEGER, outcome TEXT, summary TEXT,
  metadata TEXT, error TEXT);
"""


@pytest.fixture
def db(tmp_path) -> Path:
    path = tmp_path / "kanban.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO tasks (id, title, body, assignee, status, priority, created_at, skills, model_override,"
                 " provider_override, tenant) VALUES ('t_aaaaaaaa', 'Título', 'Zona: a/b', 'claude-hub', 'ready', 3,"
                 " 100, '[\"x\", \"\", \"y\"]', '', '', 'oscar')")
    conn.execute("INSERT INTO tasks (id, title, status, created_at) VALUES ('t_bbbbbbbb', 'Hija', 'todo', 200)")
    conn.execute("INSERT INTO task_links VALUES ('t_aaaaaaaa', 't_bbbbbbbb')")
    conn.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES ('t_aaaaaaaa', 'u', 'dos', 20)")
    conn.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES ('t_aaaaaaaa', 'u', 'uno', 10)")
    conn.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES"
                 " ('t_aaaaaaaa', 1, 'blocked', '{\"kind\": \"needs_input\"}', 30)")
    conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES ('t_aaaaaaaa', 'x', 'no json', 31)")
    conn.execute("INSERT INTO task_runs (task_id, profile, status, started_at, ended_at, summary, metadata)"
                 " VALUES ('t_aaaaaaaa', 'p', 'done', 50, 60, 'resumen viejo', '{\"a\": 1}')")
    conn.execute("INSERT INTO task_runs (task_id, profile, status, started_at, ended_at, summary)"
                 " VALUES ('t_aaaaaaaa', 'p', 'done', 70, 80, 'resumen nuevo')")
    conn.commit()
    conn.close()
    return path


def test_lector_devuelve_la_forma_de_show(db):
    s = leer_show_ro(db, "t_aaaaaaaa")
    assert set(s) == CAMPOS_SHOW and set(s["task"]) == CAMPOS_TASK
    t = s["task"]
    assert (t["id"], t["title"], t["status"], t["priority"], t["tenant"]) == ("t_aaaaaaaa", "Título", "ready", 3, "oscar")
    assert t["skills"] == ["x", "y"]  # los vacíos se descartan, como Hermes
    assert t["model_override"] is None and t["provider_override"] is None  # "" = sin definir
    assert t["last_failure_error"] is None and t["body"] == "Zona: a/b"
    assert s["children"] == ["t_bbbbbbbb"] and s["parents"] == []
    assert [c["body"] for c in s["comments"]] == ["uno", "dos"]  # por fecha
    assert set(s["comments"][0]) == {"author", "body", "created_at"}
    assert s["events"][0] == {"kind": "blocked", "payload": {"kind": "needs_input"}, "created_at": 30, "run_id": 1}
    assert s["events"][1]["payload"] is None and s["events"][1]["run_id"] is None  # payload ilegible = None
    assert [r["summary"] for r in s["runs"]] == ["resumen viejo", "resumen nuevo"]
    assert s["runs"][0]["metadata"] == {"a": 1} and s["runs"][0]["ended_at"] == 60
    assert s["latest_summary"] == "resumen nuevo"
    assert s["runs"][0].keys() == {"id", "profile", "step_key", "status", "outcome", "summary", "error", "metadata",
                                   "worker_pid", "started_at", "ended_at"}


def test_lector_tarjeta_o_base_inexistente_es_none(db, tmp_path):
    assert leer_show_ro(db, "t_cccccccc") is None
    assert leer_show_ro(tmp_path / "no-existe.db", "t_aaaaaaaa") is None


def test_lector_no_escribe_ni_deja_ficheros(db):
    antes = (db.stat().st_size, db.stat().st_mtime_ns, sorted(p.name for p in db.parent.iterdir()))
    assert leer_show_ro(db, "t_aaaaaaaa") is not None
    assert (db.stat().st_size, db.stat().st_mtime_ns, sorted(p.name for p in db.parent.iterdir())) == antes


def test_lector_reintenta_un_bloqueo_transitorio_y_luego_cae(db, monkeypatch):
    intentos, esperas = [], []

    def falla(*a, **k):
        intentos.append(1)
        raise sqlite3.OperationalError("attempt to write a readonly database")
    monkeypatch.setattr(hermes.sqlite3, "connect", falla)
    assert leer_show_ro(db, "t_aaaaaaaa", sleep=esperas.append) is None
    assert len(intentos) == 3 and esperas == [0.2, 0.5]


def test_lector_esquema_distinto_es_none(tmp_path):
    p = tmp_path / "raro.db"
    sqlite3.connect(p).executescript("CREATE TABLE tasks (id TEXT);")
    assert leer_show_ro(p, "t_aaaaaaaa", sleep=lambda s: None) is None


# --- interruptor y caída al CLI -------------------------------------------------------------------------------------

def _cli(db, salida=None, rc=0):
    llamadas = []

    def run(cmd, **kw):
        llamadas.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, json.dumps(salida or {"task": {"id": "via-cli"}}), "boom")
    return HermesCLI("default", runner=run, lector_db=db, sleep=lambda s: None), llamadas


def test_show_usa_el_lector_y_no_lanza_subproceso(db, monkeypatch):
    monkeypatch.delenv(hermes.LECTOR_ENV, raising=False)
    h, llamadas = _cli(db)
    assert h.show("t_aaaaaaaa")["task"]["id"] == "t_aaaaaaaa" and llamadas == []


def test_interruptor_entorno_vuelve_al_cli(db, monkeypatch):
    monkeypatch.setenv(hermes.LECTOR_ENV, "0")
    h, llamadas = _cli(db)
    assert h.show("t_aaaaaaaa")["task"]["id"] == "via-cli" and len(llamadas) == 1
    assert llamadas[0][-3:] == ["show", "t_aaaaaaaa", "--json"]


def test_interruptor_lanes_yaml_y_el_entorno_gana(db, monkeypatch):
    monkeypatch.delenv(hermes.LECTOR_ENV, raising=False)
    monkeypatch.setattr(proc, "_cli_cfg", {"timeout": 1, "backoff": (), "lector_ro": False})
    h, llamadas = _cli(db)
    assert h.show("t_aaaaaaaa")["task"]["id"] == "via-cli" and len(llamadas) == 1
    monkeypatch.setenv(hermes.LECTOR_ENV, "1")  # el entorno gana sobre lanes.yaml
    assert h.show("t_aaaaaaaa")["task"]["id"] == "t_aaaaaaaa" and len(llamadas) == 1


def test_show_cae_al_cli_si_el_lector_no_puede(db, monkeypatch):
    monkeypatch.delenv(hermes.LECTOR_ENV, raising=False)
    h, llamadas = _cli(db)
    assert h.show("t_cccccccc")["task"]["id"] == "via-cli" and len(llamadas) == 1  # no está en la base: el CLI decide


def test_sin_base_explicita_y_runner_doble_no_se_adivina(monkeypatch):
    monkeypatch.delenv(hermes.LECTOR_ENV, raising=False)
    h, _llamadas = _cli(None)
    assert h._lector_db is None and h.show("t_aaaaaaaa")["task"]["id"] == "via-cli"


def test_ruta_kanban_db_como_hermes(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_KANBAN_DB", raising=False)
    monkeypatch.setenv("HERMES_KANBAN_HOME", str(tmp_path))
    assert hermes.ruta_kanban_db("default") == tmp_path / "kanban.db"
    assert hermes.ruta_kanban_db("OscarHQ") == tmp_path / "kanban/boards/oscarhq/kanban.db"
    assert hermes.ruta_kanban_db("../x") is None
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "pin.db"))
    assert hermes.ruta_kanban_db("lo-que-sea") == tmp_path / "pin.db"


# --- contra la COPIA de la base (se salta si no está) ----------------------------------------------------------------

def _copias():
    return sorted(COPIAS.glob("*.db")) if COPIAS.is_dir() else []


@pytest.mark.skipif(not _copias(), reason="sin copias de la base de Hermes")
@pytest.mark.parametrize("copia", _copias(), ids=lambda p: p.name)
def test_copia_200_tarjetas_misma_forma(copia, tmp_path):
    db = tmp_path / "kanban.db"
    shutil.copy2(copia, db)
    conn = sqlite3.connect(db)
    ids = [r[0] for r in conn.execute("SELECT id FROM tasks ORDER BY created_at, id")]
    conn.close()
    muestra = ids[:: max(1, len(ids) // 200)][:200]
    assert muestra
    for tid in muestra:
        s = leer_show_ro(db, tid)
        assert s is not None and set(s) == CAMPOS_SHOW and set(s["task"]) == CAMPOS_TASK, tid
        assert s["task"]["id"] == tid and isinstance(s["task"]["skills"], list)


@pytest.mark.skipif(not (_copias() and os.environ.get("AGENT_LANES_PARIDAD_CLI") == "1" and hermes.HERMES_EXE.is_file()),
                    reason="paridad contra el hermes real: AGENT_LANES_PARIDAD_CLI=1 (o tools/agent-lanes/bench_lector_hermes.py)")
def test_copia_paridad_con_show_json_del_cli(tmp_path):  # pragma: no cover - usa el hermes real, solo a demanda
    import bench_lector_hermes as bench
    assert bench.main([str(_copias()[0]), "--n", "20"]) == 0


# --- caché de la zona declarada --------------------------------------------------------------------------------------

def test_rutas_tarjeta_cachea_por_id_y_texto(monkeypatch):
    choques._ZONAS.clear()
    llamadas = []
    real = solapes.extraer_rutas
    monkeypatch.setattr(solapes, "extraer_rutas", lambda *a, **k: (llamadas.append(1), real(*a, **k))[1])
    aj = solapes.Ajustes()
    texto = "T\nZona: tools/agent-lanes/agent_lanes/hermes.py, tools/agent-lanes/tests"
    a = choques.rutas_tarjeta("t_1", texto, aj, "C:/repo")
    b = choques.rutas_tarjeta("t_1", texto, aj, "C:/repo")
    assert a == b == real(texto, aj, "C:/repo") and len(llamadas) == 1
    a.add("mutado")  # lo devuelto es una copia: no contamina el caché
    assert "mutado" not in choques.rutas_tarjeta("t_1", texto, aj, "C:/repo")
    choques.rutas_tarjeta("t_1", texto + "\nZona: otra/", aj, "C:/repo")  # tarjeta editada: se recalcula
    choques.rutas_tarjeta("t_2", texto, aj, "C:/repo")  # otra tarjeta
    assert len(llamadas) == 3


def test_rutas_tarjeta_se_acota(monkeypatch):
    choques._ZONAS.clear()
    monkeypatch.setattr(choques, "_ZONAS_MAX", 3)
    for i in range(10):
        choques.rutas_tarjeta(f"t_{i}", "Zona: a/b/", solapes.Ajustes(), "")
    assert len(choques._ZONAS) <= 3
