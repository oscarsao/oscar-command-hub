"""F2-03: tools/kanban_migrar.py, ida (SQLite de Hermes -> hub.*) y vuelta (hub.* -> SQLite con el esquema de Hermes).

Datos sintéticos con el esquema real de Hermes (tests/fixtures/hermes_kanban_schema.sql). Con KANBAN_COPIA_DIR
apuntando a una COPIA local de los SQLite (nunca los originales) se repite la ida y vuelta con datos reales."""
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from agent_lanes.kanban_api import SupabaseKanban
from tests.hubpg import HERMES_SCHEMA, hub_dsn  # noqa: F401  (fixture)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # tools/
import kanban_migrar as km  # noqa: E402

T0 = 1_760_000_000


def hermes_db(path: Path, tasks: int = 3, base: int = 0) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(HERMES_SCHEMA.read_text(encoding="utf-8"))
    for i in range(tasks):
        tid = f"t_{base + i:08x}"
        conn.execute("INSERT INTO tasks (id, title, body, assignee, status, priority, created_by, created_at, "
                     "workspace_kind, branch_name, skills, tenant, consecutive_failures, block_kind, "
                     "block_recurrences, claim_lock, claim_expires) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'worktree', ?, ?, "
                     "?, ?, ?, ?, ?, ?)",
                     (tid, f"[Hub] tarea {i}", f"cuerpo {i}\n- con guion", "claude-hub",
                      ("ready", "done", "blocked", "running")[i % 4], i, "default", T0 + i, f"lane/{tid}",
                      json.dumps(["s1"]) if i == 0 else None, "t1" if i == 1 else None, i, "needs_input" if i == 2
                      else None, 1 if i == 2 else 0, "pc:1" if i == 3 else None, T0 + 999 if i == 3 else None))
        conn.execute("INSERT INTO task_runs (task_id, profile, status, started_at, ended_at, outcome, summary, "
                     "metadata) VALUES (?, 'claude-hub', 'done', ?, ?, 'completed', ?, ?)",
                     (tid, T0 + i, T0 + i + 10, f"resumen {i}", json.dumps({"pr": i})))
        run_id = conn.execute("SELECT max(id) FROM task_runs").fetchone()[0]
        conn.execute("INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?, ?, 'claimed', "
                     "?, ?)", (tid, run_id, json.dumps({"lock": "pc", "run_id": run_id}), T0 + i))
        conn.execute("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, 'raro', ?, ?)",
                     (tid, "no es json", T0 + i + 1))
        conn.execute("INSERT INTO task_events (task_id, kind, created_at) VALUES (?, 'promoted', ?)", (tid, T0 + i))
        conn.execute("INSERT INTO task_comments (task_id, author, body, created_at) VALUES (?, ?, ?, ?)",
                     (tid, "oscar-telegram", f"Respuesta de Oscar: ¿{i}? → sí", T0 + i + 2))
    if tasks >= 2:
        a, b = f"t_{base:08x}", f"t_{base + 1:08x}"
        conn.execute("INSERT INTO task_links (parent_id, child_id) VALUES (?, ?)", (a, b))
        conn.execute("UPDATE tasks SET current_run_id = (SELECT max(id) FROM task_runs WHERE task_id = ?) "
                     "WHERE id = ?", (a, a))
        conn.execute("INSERT INTO kanban_notify_subs (task_id, platform, chat_id, thread_id, delivery_mode, "
                     "created_at) VALUES (?, 'telegram', '-42', '7', 'notify', ?)", (b, T0))
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def pg(hub_dsn):  # noqa: F811
    conn = km.conectar(hub_dsn)
    yield conn
    conn.close()


def rows(path: Path, table: str, order: str) -> list[dict]:
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    out = [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY {order}")]
    c.close()
    return out


def _norm_json(rows_, col):
    for r in rows_:
        if r.get(col) is not None:
            try:
                r[col] = json.loads(r[col])
            except ValueError:
                pass
    return rows_


def test_descubrir_carpeta_de_hermes_y_de_copias(tmp_path):
    home = tmp_path / "hermes"
    (home / "kanban/boards/oscarhq").mkdir(parents=True)
    (home / "kanban/boards/default").mkdir(parents=True)
    hermes_db(home / "kanban.db")
    hermes_db(home / "kanban/boards/oscarhq/kanban.db")
    (home / "kanban/boards/default/kanban.db").write_bytes(b"")  # el de verdad es <home>/kanban.db
    assert set(km.descubrir(home)) == {"default", "oscarhq"}
    assert km.descubrir(home)["default"] == home / "kanban.db"
    copias = tmp_path / "copias"
    out = km.copiar(home, copias)
    assert out == {"default": "copiado", "oscarhq": "copiado"} and set(km.descubrir(copias)) == {"default", "oscarhq"}


def test_ida_y_vuelta_identica(tmp_path, pg, hub_dsn):  # noqa: F811
    src = tmp_path / "src"
    src.mkdir()
    hermes_db(src / "oscarhq.db", tasks=5)
    hermes_db(src / "migrateam.db", tasks=2, base=0x100)
    hermes_db(src / "marketing.db", tasks=0)  # vacío: se omite
    origen = km.descubrir(src)
    informe = km.importar(pg, origen)
    assert informe["marketing"] == {"omitido": "sin tarjetas"} and informe["oscarhq"]["tareas"] == 5
    a = {b: v for b, v in km.recuentos_sqlite(origen).items() if v["tareas"]}
    assert km.comparar(a, km.recuentos_pg(pg)) == []
    # el backend supabase lee lo importado con la forma de Hermes
    k = SupabaseKanban("oscarhq", dsn=hub_dsn)
    show = k.show("t_00000001")
    assert show["parents"] == ["t_00000000"] and show["task"]["tenant"] == "t1"
    assert k.show("t_00000000")["task"]["skills"] == ["s1"] and k.show("t_00000000")["children"] == ["t_00000001"]
    assert [e["payload"] for e in show["events"] if e["kind"] == "raro"] == [{"_texto": "no es json"}]
    assert k.oscar_answers("t_00000001") == ["Respuesta de Oscar: ¿1? → sí"]
    # vuelta
    dest = tmp_path / "vuelta"
    out = km.exportar(pg, dest, src / "oscarhq.db")
    assert set(out) == {"oscarhq", "migrateam"}
    for board in ("oscarhq", "migrateam"):
        o, d = src / f"{board}.db", dest / f"{board}.db"
        assert rows(o, "tasks", "id") == rows(d, "tasks", "id")
        assert rows(o, "task_links", "parent_id, child_id") == rows(d, "task_links", "parent_id, child_id")
        assert rows(o, "task_comments", "id") == rows(d, "task_comments", "id")
        assert _norm_json(rows(o, "task_runs", "id"), "metadata") == _norm_json(rows(d, "task_runs", "id"),
                                                                                "metadata")
        assert _norm_json(rows(o, "task_events", "id"), "payload") == _norm_json(rows(d, "task_events", "id"),
                                                                                 "payload")
        assert rows(o, "kanban_notify_subs", "task_id") == rows(d, "kanban_notify_subs", "task_id")
    assert km.comparar(a, km.recuentos_sqlite(km.descubrir(dest))) == []


def test_lo_nuevo_tras_el_corte_tambien_vuelve(tmp_path, pg, hub_dsn):  # noqa: F811
    src = tmp_path / "src"
    src.mkdir()
    hermes_db(src / "oscarhq.db", tasks=2)
    km.importar(pg, km.descubrir(src))
    k = SupabaseKanban("oscarhq", dsn=hub_dsn, worker="pc")
    nueva = k.create("[Hub] tras el corte", "b", "claude-hub")
    k.claim(nueva, 60)
    k.comment(nueva, "nota")
    km.exportar(pg, tmp_path / "v", src / "oscarhq.db")
    c = sqlite3.connect(tmp_path / "v/oscarhq.db")
    assert c.execute("SELECT status FROM tasks WHERE id = ?", (nueva,)).fetchone() == ("running",)
    ids = [r[0] for r in c.execute("SELECT id FROM task_comments ORDER BY id")]
    assert len(ids) == len(set(ids)) == 3  # ids nuevos detrás de los de Hermes, sin chocar
    seq = dict(c.execute("SELECT name, seq FROM sqlite_sequence"))
    assert seq["task_comments"] == max(ids)
    c.close()


def test_no_importa_encima_ni_deja_nada_en_seco(tmp_path, pg):
    src = tmp_path / "src"
    src.mkdir()
    hermes_db(src / "oscarhq.db", tasks=2)
    km.importar(pg, km.descubrir(src), seco=True)
    assert km.recuentos_pg(pg) == {}
    km.importar(pg, km.descubrir(src))
    with pytest.raises(km.MigrarError, match="ya tiene tarjetas"):
        km.importar(pg, km.descubrir(src))
    otro = tmp_path / "otro"
    otro.mkdir()
    hermes_db(otro / "migrateam.db", tasks=1)  # mismo id en otro board: se dice, no se pisa
    with pytest.raises(km.MigrarError, match="ya existen en otro board"):
        km.importar(pg, km.descubrir(otro))


def test_exportar_nunca_dentro_de_hermes_ni_encima(tmp_path, pg, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    hermes_db(src / "oscarhq.db", tasks=1)
    km.importar(pg, km.descubrir(src))
    monkeypatch.setattr(km, "HERMES_HOME", tmp_path / "hermes")
    with pytest.raises(km.MigrarError, match="carpeta de Hermes"):
        km.exportar(pg, tmp_path / "hermes" / "kanban_espejo", src / "oscarhq.db")
    km.exportar(pg, tmp_path / "v", src / "oscarhq.db")
    with pytest.raises(km.MigrarError, match="ya existe"):
        km.exportar(pg, tmp_path / "v", src / "oscarhq.db")


def test_espejo_retiene_14_dias_y_solo_borra_carpetas_de_espejo(tmp_path, pg):
    src = tmp_path / "src"
    src.mkdir()
    hermes_db(src / "oscarhq.db", tasks=1)
    km.importar(pg, km.descubrir(src))
    raiz = tmp_path / "espejo"
    (raiz / "2026-10-01").mkdir(parents=True)
    (raiz / "2026-10-20").mkdir()
    (raiz / "notas").mkdir()
    out = km.espejo(pg, raiz, src / "oscarhq.db", retener=14, hoy=date(2026, 10, 25))
    assert out["quitadas"] == ["2026-10-01"]
    assert sorted(p.name for p in raiz.iterdir()) == ["2026-10-20", "2026-10-25", "notas"]


def test_origen_en_solo_lectura(tmp_path):
    db = hermes_db(tmp_path / "x.db", tasks=1)
    conn = km.abrir_ro(db)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM tasks")
    conn.close()


@pytest.mark.skipif(not os.environ.get("KANBAN_COPIA_DIR"), reason="sin KANBAN_COPIA_DIR (copia local del kanban)")
def test_ida_y_vuelta_sobre_la_copia_real(tmp_path, pg):
    copia = Path(os.environ["KANBAN_COPIA_DIR"])
    assert "AppData" not in str(copia.resolve()), "usar una COPIA, nunca la carpeta de Hermes"
    origen = km.descubrir(copia)
    km.importar(pg, origen)
    a = {b: v for b, v in km.recuentos_sqlite(origen).items() if v["tareas"]}
    assert km.comparar(a, km.recuentos_pg(pg)) == []
    dest = tmp_path / "vuelta"
    km.exportar(pg, dest, next(iter(origen.values())))
    assert km.comparar(a, km.recuentos_sqlite(km.descubrir(dest))) == []
    for board in a:
        assert rows(origen[board], "tasks", "id") == rows(dest / f"{board}.db", "tasks", "id")
        assert rows(origen[board], "task_comments", "id") == rows(dest / f"{board}.db", "task_comments", "id")


@pytest.mark.skipif(not os.environ.get("KANBAN_COPIA_DIR"), reason="sin KANBAN_COPIA_DIR (copia local del kanban)")
def test_contrato_entre_backends_sobre_la_copia(tmp_path, pg, hub_dsn, monkeypatch):  # noqa: F811
    """La CLI de Hermes (sobre una copia, HERMES_KANBAN_DB) y el backend supabase (sobre lo importado) dan el mismo
    JSON en show/list. Solo cambian los ids de las ejecuciones (secuencia global en hub.task_runs)."""
    import random
    import shutil
    from agent_lanes.hermes import HERMES_EXE, HermesCLI
    if not HERMES_EXE.exists():
        pytest.skip("sin Hermes instalado")
    copia = Path(os.environ["KANBAN_COPIA_DIR"])
    board = "oscarhq" if (copia / "oscarhq.db").exists() else next(iter(km.descubrir(copia)))
    shutil.copy(copia / f"{board}.db", tmp_path / "k.db")  # la CLI puede escribir (recompute_ready): en otra copia
    monkeypatch.setenv("HERMES_KANBAN_DB", str(tmp_path / "k.db"))
    km.importar(pg, {board: copia / f"{board}.db"})
    h, s = HermesCLI(board), SupabaseKanban(board, dsn=hub_dsn)
    ids = sorted(km.recuentos_sqlite({board: copia / f"{board}.db"})[board]["ids"])
    sin_id = lambda runs: [{k: v for k, v in r.items() if k != "id"} for r in runs]  # noqa: E731
    for tid in random.Random(1).sample(ids, min(15, len(ids))):
        a, b = h.show(tid), s.show(tid)
        assert {k: b["task"].get(k) for k in a["task"]} == a["task"]
        for k in ("latest_summary", "parents", "children", "comments"):
            assert a[k] == b[k], (tid, k)
        assert [(e["kind"], e["payload"], e["created_at"]) for e in a["events"]] == \
               [(e["kind"], e["payload"], e["created_at"]) for e in b["events"]]
        assert sin_id(a["runs"]) == sin_id(b["runs"])
    for status in ("ready", "todo", "done"):
        la, lb = h.list_status_all(status), s.list_status_all(status)
        assert sorted(t["id"] for t in la) == sorted(t["id"] for t in lb)
        key = lambda t: (t["priority"], t["created_at"])  # noqa: E731  (empates: SQLite por rowid, aquí por id)
        assert [key(t) for t in la] == [key(t) for t in lb]
