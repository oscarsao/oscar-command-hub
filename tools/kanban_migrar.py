"""Importador y exportador del kanban: SQLite de Hermes <-> esquema `hub` de Supabase (F2-03 del plan del hub).

    py -3.12 tools/kanban_migrar.py recuentos --origen <dir>            # board×estado del SQLite (solo lectura)
    py -3.12 tools/kanban_migrar.py copiar    --origen <hermes> --destino <dir>   # copia coherente, solo lectura
    py -3.12 tools/kanban_migrar.py importar  --origen <dir> [--seco]   # SQLite -> hub.* (HUB_DB_DSN)
    py -3.12 tools/kanban_migrar.py exportar  --destino <dir> --esquema-de <kanban.db> [--espejo --retener 14]
    py -3.12 tools/kanban_migrar.py comparar  --origen <dir>            # SQLite frente a hub.*: recuentos e ids

`--origen` acepta la carpeta de Hermes (kanban.db = board default + kanban/boards/<b>/kanban.db) o una carpeta con
copias `<board>.db`. Los SQLite de origen se abren SIEMPRE en solo lectura (`mode=ro`). `exportar` (= «volver» del
plan) escribe ficheros nuevos en otra carpeta, nunca dentro de la de Hermes: devolverlos a Hermes es un paso manual
del integrador con el runner parado. La conexión a Postgres sale de HUB_DB_DSN (nunca de la línea de órdenes).
La salida solo da recuentos agregados e ids que faltan o sobran: nunca títulos, cuerpos ni chats.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

HERMES_HOME = Path.home() / "AppData/Local/hermes"
DSN_ENV = "HUB_DB_DSN"
STATUSES = ("triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done", "archived")
_BOARD_RE = re.compile(r"^[\w-]{1,64}$")
_ESPEJO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# tasks de Hermes -> columnas propias de hub.tasks. Lo demás va a `extra` tal cual (ida y vuelta exacta).
TASK_MAP = {"id": "id", "title": "title", "body": "body", "assignee": "assignee", "status": "status",
            "priority": "priority", "created_by": "created_by_worker", "result": "result",
            "idempotency_key": "idempotency_key", "claim_lock": "lease_owner", "consecutive_failures": "attempts",
            "block_kind": "block_kind", "block_recurrences": "block_recurrences"}
TASK_TIMES = {"created_at": "created_at", "started_at": "started_at", "completed_at": "completed_at",
              "claim_expires": "lease_until"}
RUN_MAP = {"profile": "profile", "step_key": "step_key", "status": "status", "outcome": "outcome",
           "summary": "summary", "error": "error", "worker_pid": "worker_pid", "claim_lock": "lease_owner"}
RUN_TIMES = {"started_at": "started_at", "ended_at": "ended_at", "claim_expires": "lease_until"}
KV_NOTIFY = "notify:"
KV_ADJUNTOS = "adjuntos:"


class MigrarError(RuntimeError):
    pass


# --- SQLite (origen y destino) ---------------------------------------------------------------------------------

def descubrir(origen: Path) -> dict[str, Path]:
    """{board: kanban.db} de una carpeta de Hermes o de una carpeta de copias `<board>.db`."""
    origen = Path(origen)
    if origen.is_file():
        return {origen.stem: origen}
    found: dict[str, Path] = {}
    if (origen / "kanban.db").is_file():
        found["default"] = origen / "kanban.db"
    boards = origen / "kanban" / "boards"
    if boards.is_dir():
        for d in sorted(boards.iterdir()):
            if d.name != "default" and (d / "kanban.db").is_file():
                found[d.name] = d / "kanban.db"
    if not found:
        for f in sorted(origen.glob("*.db")):
            found[f.stem] = f
    bad = [b for b in found if not _BOARD_RE.match(b)]
    if bad:
        raise MigrarError(f"nombres de board no válidos: {bad}")
    return found


def abrir_ro(path: Path) -> sqlite3.Connection:
    if not path.is_file() or path.stat().st_size == 0:
        raise MigrarError(f"{path.name}: vacío o inexistente")
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def copiar(origen: Path, destino: Path) -> dict[str, str]:
    """Copia coherente (API de backup de SQLite, origen en solo lectura) de cada board a `destino/<board>.db`."""
    destino.mkdir(parents=True, exist_ok=True)
    out = {}
    for board, path in descubrir(origen).items():
        if path.stat().st_size == 0:
            out[board] = "vacío"
            continue
        target = destino / f"{board}.db"
        if target.exists():
            raise MigrarError(f"{target} ya existe")
        src = abrir_ro(path)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        out[board] = "copiado"
    return out


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def recuentos_sqlite(paths: dict[str, Path]) -> dict[str, dict]:
    """{board: {"estados": {status: n}, "tareas", "comentarios", "eventos", "ejecuciones", "ids"}}."""
    out = {}
    for board, path in paths.items():
        if path.stat().st_size == 0:
            continue
        conn = abrir_ro(path)
        try:
            tabs = _tables(conn)
            if "tasks" not in tabs:
                continue
            estados = dict(conn.execute("SELECT status, count(*) FROM tasks GROUP BY status").fetchall())
            n = lambda t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] if t in tabs else 0  # noqa: E731
            out[board] = {"estados": estados, "tareas": n("tasks"), "comentarios": n("task_comments"),
                          "eventos": n("task_events"), "ejecuciones": n("task_runs"),
                          "ids": {r[0] for r in conn.execute("SELECT id FROM tasks")}}
        finally:
            conn.close()
    return out


# --- Postgres ---------------------------------------------------------------------------------------------------

def conectar(dsn: str | None = None):
    dsn = dsn or os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        raise MigrarError(f"falta {DSN_ENV}")
    import psycopg2
    return psycopg2.connect(dsn, connect_timeout=10, application_name="kanban_migrar")


def recuentos_pg(conn, boards: list[str] | None = None) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT board, status, count(*) FROM hub.tasks GROUP BY board, status")
        out: dict[str, dict] = {}
        for b, st, n in cur.fetchall():
            out.setdefault(b, {"estados": {}})["estados"][st] = n
        for b in out:
            cur.execute("SELECT id FROM hub.tasks WHERE board = %s", (b,))
            out[b]["ids"] = {r[0] for r in cur.fetchall()}
            out[b]["tareas"] = len(out[b]["ids"])
            for key, table in (("comentarios", "task_comments"), ("eventos", "task_events"),
                               ("ejecuciones", "task_runs")):
                cur.execute(f"SELECT count(*) FROM hub.{table} x JOIN hub.tasks t ON t.id = x.task_id "
                            "WHERE t.board = %s", (b,))
                out[b][key] = cur.fetchone()[0]
    conn.rollback()
    return {b: v for b, v in out.items() if boards is None or b in boards}


def _json_value(raw):
    """Texto JSON de Hermes -> valor para jsonb. Lo que no es JSON se guarda envuelto (y se desenvuelve al volver)."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"_texto": raw}


def _json_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict) and set(value) == {"_texto"}:
        return value["_texto"]
    return json.dumps(value, ensure_ascii=False)


def _dumps(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def importar(conn, paths: dict[str, Path], *, seco: bool = False) -> dict[str, dict]:
    """SQLite de Hermes -> hub.* en UNA transacción. Boards sin tarjetas se omiten (marketing/pildora hoy). Un board
    que ya tiene tarjetas en hub.* aborta todo (no se mezcla ni se borra nada). `seco`: hace todo y deshace."""
    informe: dict[str, dict] = {}
    try:
        with conn.cursor() as cur:
            for board, path in paths.items():
                if path.stat().st_size == 0:
                    informe[board] = {"omitido": "fichero vacío"}
                    continue
                src = abrir_ro(path)
                try:
                    informe[board] = _importar_board(cur, board, src)
                finally:
                    src.close()
        if seco:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    return informe


def _importar_board(cur, board: str, src: sqlite3.Connection) -> dict:
    tabs = _tables(src)
    n_tasks = src.execute("SELECT count(*) FROM tasks").fetchone()[0] if "tasks" in tabs else 0
    if n_tasks == 0:
        return {"omitido": "sin tarjetas"}
    cur.execute("SELECT count(*) FROM hub.tasks WHERE board = %s", (board,))
    if cur.fetchone()[0]:
        raise MigrarError(f"el board {board} ya tiene tarjetas en hub.tasks: no se importa encima")
    src_ids = [r[0] for r in src.execute("SELECT id FROM tasks")]
    cur.execute("SELECT id, board FROM hub.tasks WHERE id = ANY(%s)", (src_ids,))
    repetidos = cur.fetchall()
    if repetidos:  # los ids de tarjeta son únicos en hub.tasks (los de Hermes lo son por board)
        raise MigrarError(f"{len(repetidos)} ids del board {board} ya existen en otro board "
                          f"({', '.join(sorted(r[0] for r in repetidos)[:5])})")
    parents: dict[str, list[str]] = {}
    if "task_links" in tabs:
        for r in src.execute("SELECT parent_id, child_id FROM task_links ORDER BY parent_id"):
            parents.setdefault(r["child_id"], []).append(r["parent_id"])
    ids = set()
    current_runs: dict[str, int] = {}
    for r in src.execute("SELECT * FROM tasks ORDER BY created_at, id"):
        row = dict(r)
        cols = {dst: row.get(srcc) for srcc, dst in TASK_MAP.items()}
        times = {dst: row.get(srcc) for srcc, dst in TASK_TIMES.items()}
        extra = {k: v for k, v in row.items() if k not in TASK_MAP and k not in TASK_TIMES and k != "current_run_id"}
        if row.get("current_run_id") is not None:
            current_runs[row["id"]] = row["current_run_id"]
        cols["attempts"] = cols["attempts"] or 0
        cols["block_recurrences"] = cols["block_recurrences"] or 0
        cols["priority"] = cols["priority"] or 0
        cur.execute(
            "INSERT INTO hub.tasks (id, board, title, body, assignee, status, priority, created_by_worker, result, "
            "idempotency_key, lease_owner, attempts, block_kind, block_recurrences, parents, extra, created_at, "
            "started_at, completed_at, lease_until, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s::text[], %s::jsonb, to_timestamp(%s), to_timestamp(%s), to_timestamp(%s), "
            "to_timestamp(%s), now())",
            (cols["id"], board, cols["title"], cols["body"], cols["assignee"], cols["status"], cols["priority"],
             cols["created_by_worker"], cols["result"], cols["idempotency_key"], cols["lease_owner"],
             cols["attempts"], cols["block_kind"], cols["block_recurrences"], parents.get(row["id"], []),
             _dumps(extra), times["created_at"], times["started_at"], times["completed_at"], times["lease_until"]))
        ids.add(row["id"])
    huerfanas = {"ejecuciones": 0, "eventos": 0, "comentarios": 0}
    run_map: dict[int, int] = {}
    if "task_runs" in tabs:
        for r in src.execute("SELECT * FROM task_runs ORDER BY id"):
            row = dict(r)
            if row["task_id"] not in ids:
                huerfanas["ejecuciones"] += 1
                continue
            cols = {dst: row.get(s) for s, dst in RUN_MAP.items()}
            times = {dst: row.get(s) for s, dst in RUN_TIMES.items()}
            extra = {k: v for k, v in row.items()
                     if k not in RUN_MAP and k not in RUN_TIMES and k not in ("id", "task_id", "metadata")}
            cur.execute(
                "INSERT INTO hub.task_runs (task_id, origen_id, profile, step_key, status, outcome, summary, "
                "metadata, error, worker_pid, lease_owner, extra, started_at, ended_at, lease_until) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, to_timestamp(%s), to_timestamp(%s), "
                "to_timestamp(%s)) RETURNING id",
                (row["task_id"], row["id"], cols["profile"], cols["step_key"], cols["status"], cols["outcome"],
                 cols["summary"], _dumps(_json_value(row.get("metadata"))), cols["error"], cols["worker_pid"],
                 cols["lease_owner"], _dumps(extra), times["started_at"], times["ended_at"], times["lease_until"]))
            run_map[row["id"]] = cur.fetchone()[0]
    for tid, old in current_runs.items():
        cur.execute("UPDATE hub.tasks SET current_run_id = %s WHERE id = %s", (run_map.get(old), tid))
    if "task_events" in tabs:
        for r in src.execute("SELECT * FROM task_events ORDER BY id"):
            if r["task_id"] not in ids:
                huerfanas["eventos"] += 1
                continue
            cur.execute("INSERT INTO hub.task_events (task_id, origen_id, run_id, kind, payload, created_at) VALUES "
                        "(%s, %s, %s, %s, %s::jsonb, to_timestamp(%s))",
                        (r["task_id"], r["id"], run_map.get(r["run_id"]) if r["run_id"] is not None else None,
                         r["kind"], _dumps(_json_value(r["payload"])), r["created_at"]))
    if "task_comments" in tabs:
        for r in src.execute("SELECT * FROM task_comments ORDER BY id"):
            if r["task_id"] not in ids:
                huerfanas["comentarios"] += 1
                continue
            cur.execute("INSERT INTO hub.task_comments (task_id, origen_id, worker_id, body, created_at) VALUES "
                        "(%s, %s, %s, %s, to_timestamp(%s))",
                        (r["task_id"], r["id"], r["author"], r["body"], r["created_at"]))
    for table, prefix in (("kanban_notify_subs", KV_NOTIFY), ("task_attachments", KV_ADJUNTOS)):
        if table not in tabs:
            continue
        grouped: dict[str, list[dict]] = {}
        for r in src.execute(f"SELECT * FROM {table} ORDER BY rowid"):
            if r["task_id"] in ids:
                grouped.setdefault(r["task_id"], []).append(dict(r))
        for tid, rows in grouped.items():
            cur.execute("INSERT INTO hub.kv (key, value, updated_at) VALUES (%s, %s::jsonb, now()) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                        (prefix + tid, _dumps(rows)))
    return {"tareas": len(ids), "ejecuciones": len(run_map), "huerfanas": huerfanas}


# --- exportar (espejo nocturno / volver) ------------------------------------------------------------------------

def _esquema_sql(esquema_de: Path) -> list[str]:
    """DDL de un kanban.db real de Hermes (abierto en solo lectura): el destino queda con SU esquema exacto."""
    src = abrir_ro(esquema_de)
    try:
        rows = src.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL "
                           "AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END, "
                           "rowid").fetchall()
    finally:
        src.close()
    return [r["sql"] for r in rows]


def _fuera_de_hermes(path: Path) -> None:
    p = path.resolve()
    home = HERMES_HOME.resolve()
    if p == home or home in p.parents:
        raise MigrarError("exportar nunca escribe dentro de la carpeta de Hermes (paso manual del integrador)")


def exportar(conn, destino: Path, esquema_de: Path, boards: list[str] | None = None) -> dict[str, dict]:
    """hub.* -> `destino/<board>.db` con el esquema de Hermes. Ficheros nuevos: si ya existe alguno, no escribe."""
    _fuera_de_hermes(destino)
    ddl = _esquema_sql(esquema_de)
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT board FROM hub.tasks ORDER BY board")
        todos = [r[0] for r in cur.fetchall()]
    conn.rollback()
    boards = [b for b in todos if boards is None or b in boards]
    destino.mkdir(parents=True, exist_ok=True)
    for b in boards:
        if not _BOARD_RE.match(b):
            raise MigrarError(f"board no válido: {b!r}")
        if (destino / f"{b}.db").exists():
            raise MigrarError(f"{destino / (b + '.db')} ya existe")
    informe = {}
    for b in boards:
        tmp = destino / f".{b}.db.tmp"
        if tmp.exists():
            tmp.unlink()
        dst = sqlite3.connect(tmp)
        try:
            for stmt in ddl:
                dst.execute(stmt)
            informe[b] = _exportar_board(conn, b, dst)
            dst.commit()
        finally:
            dst.close()
        tmp.replace(destino / f"{b}.db")
    conn.rollback()
    return informe


def _cols(dst: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in dst.execute(f"PRAGMA table_info({table})")]


def _insert(dst: sqlite3.Connection, table: str, row: dict) -> None:
    cols = [c for c in _cols(dst, table) if c in row]
    dst.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [row[c] for c in cols])


def _new_ids(rows: list[dict]) -> dict[int, int]:
    """Ids de Hermes: se conserva origen_id; las filas nuevas (sin origen) siguen tras el máximo, en orden."""
    used = {r["origen_id"] for r in rows if r["origen_id"] is not None}
    nxt = max(used, default=0) + 1
    out = {}
    for r in rows:
        if r["origen_id"] is not None:
            out[r["id"]] = r["origen_id"]
        else:
            out[r["id"]] = nxt
            nxt += 1
    return out


def _exportar_board(conn, board: str, dst: sqlite3.Connection) -> dict:
    with conn.cursor() as cur:
        ep = lambda c: f"extract(epoch from {c})::bigint"  # noqa: E731
        cur.execute("SELECT r.id, r.origen_id, r.task_id, r.profile, r.step_key, r.status, r.outcome, r.summary, "
                    f"r.metadata, r.error, r.worker_pid, r.lease_owner, r.extra, {ep('r.started_at')}, "
                    f"{ep('r.ended_at')}, {ep('r.lease_until')} FROM hub.task_runs r JOIN hub.tasks t "
                    "ON t.id = r.task_id WHERE t.board = %s ORDER BY r.id", (board,))
        keys = ("id", "origen_id", "task_id", "profile", "step_key", "status", "outcome", "summary", "metadata",
                "error", "worker_pid", "lease_owner", "extra", "started_at", "ended_at", "lease_until")
        runs = [dict(zip(keys, r)) for r in cur.fetchall()]
        run_ids = _new_ids(runs)
        cur.execute("SELECT id, title, body, assignee, status, priority, created_by_worker, created_by, result, "
                    "idempotency_key, lease_owner, attempts, block_kind, block_recurrences, parents, extra, "
                    f"current_run_id, {ep('created_at')}, {ep('started_at')}, {ep('completed_at')}, "
                    f"{ep('lease_until')} FROM hub.tasks WHERE board = %s ORDER BY created_at, id", (board,))
        tkeys = ("id", "title", "body", "assignee", "status", "priority", "created_by_worker", "created_by", "result",
                 "idempotency_key", "lease_owner", "attempts", "block_kind", "block_recurrences", "parents", "extra",
                 "current_run_id", "created_at", "started_at", "completed_at", "lease_until")
        tasks = [dict(zip(tkeys, r)) for r in cur.fetchall()]
        cur.execute("SELECT e.id, e.origen_id, e.task_id, e.run_id, e.kind, e.payload, "
                    f"{ep('e.created_at')} FROM hub.task_events e JOIN hub.tasks t ON t.id = e.task_id "
                    "WHERE t.board = %s ORDER BY e.id", (board,))
        events = [dict(zip(("id", "origen_id", "task_id", "run_id", "kind", "payload", "created_at"), r))
                  for r in cur.fetchall()]
        cur.execute("SELECT c.id, c.origen_id, c.task_id, COALESCE(c.worker_id, c.author_id::text), c.body, "
                    f"{ep('c.created_at')} FROM hub.task_comments c JOIN hub.tasks t ON t.id = c.task_id "
                    "WHERE t.board = %s ORDER BY c.id", (board,))
        comments = [dict(zip(("id", "origen_id", "task_id", "author", "body", "created_at"), r))
                    for r in cur.fetchall()]
        ids = [t["id"] for t in tasks]
        cur.execute("SELECT key, value FROM hub.kv WHERE key = ANY(%s)",
                    ([KV_NOTIFY + i for i in ids] + [KV_ADJUNTOS + i for i in ids],))
        kv = dict(cur.fetchall())
    for t in tasks:
        extra = t["extra"] if isinstance(t["extra"], dict) else json.loads(t["extra"] or "{}")
        row = dict(extra)
        row.update(id=t["id"], title=t["title"], body=t["body"], assignee=t["assignee"], status=t["status"],
                   priority=t["priority"], created_by=t["created_by_worker"] or (
                       str(t["created_by"]) if t["created_by"] is not None else None),
                   result=t["result"], idempotency_key=t["idempotency_key"], claim_lock=t["lease_owner"],
                   consecutive_failures=t["attempts"], block_kind=t["block_kind"],
                   block_recurrences=t["block_recurrences"],
                   current_run_id=run_ids.get(t["current_run_id"]) if t["current_run_id"] else None,
                   created_at=t["created_at"], started_at=t["started_at"], completed_at=t["completed_at"],
                   claim_expires=t["lease_until"])
        row.setdefault("workspace_kind", "scratch")
        row.setdefault("goal_mode", 0)
        _insert(dst, "tasks", row)
        for p in t["parents"] or []:
            dst.execute("INSERT OR IGNORE INTO task_links (parent_id, child_id) VALUES (?, ?)", (p, t["id"]))
    for r in runs:
        extra = r["extra"] if isinstance(r["extra"], dict) else json.loads(r["extra"] or "{}")
        row = dict(extra)
        row.update(id=run_ids[r["id"]], task_id=r["task_id"], profile=r["profile"], step_key=r["step_key"],
                   status=r["status"], outcome=r["outcome"], summary=r["summary"], metadata=_json_text(r["metadata"]),
                   error=r["error"], worker_pid=r["worker_pid"], claim_lock=r["lease_owner"],
                   started_at=r["started_at"], ended_at=r["ended_at"], claim_expires=r["lease_until"])
        _insert(dst, "task_runs", row)
    ev_ids = _new_ids(events)
    for e in events:
        _insert(dst, "task_events", {"id": ev_ids[e["id"]], "task_id": e["task_id"],
                                     "run_id": run_ids.get(e["run_id"]) if e["run_id"] is not None else None,
                                     "kind": e["kind"], "payload": _json_text(e["payload"]),
                                     "created_at": e["created_at"]})
    cm_ids = _new_ids(comments)
    for c in comments:
        _insert(dst, "task_comments", {"id": cm_ids[c["id"]], "task_id": c["task_id"], "author": c["author"],
                                       "body": c["body"], "created_at": c["created_at"]})
    for key, value in kv.items():
        rows = value if isinstance(value, list) else json.loads(value)
        table = "kanban_notify_subs" if key.startswith(KV_NOTIFY) else "task_attachments"
        for row in rows:
            _insert(dst, table, row)
    for table, mapping in (("task_runs", run_ids), ("task_events", ev_ids), ("task_comments", cm_ids)):
        top = max(mapping.values(), default=0)
        if top and "sqlite_sequence" in _tables(dst):
            dst.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
            dst.execute("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", (table, top))
    return {"tareas": len(tasks), "ejecuciones": len(runs), "eventos": len(events), "comentarios": len(comments)}


def espejo(conn, raiz: Path, esquema_de: Path, retener: int = 14, hoy: date | None = None) -> dict:
    """Espejo nocturno: `raiz/AAAA-MM-DD/<board>.db`; quita SOLO carpetas de espejo (nombre AAAA-MM-DD) de más de
    `retener` días dentro de `raiz`."""
    hoy = hoy or date.today()
    informe = exportar(conn, raiz / hoy.isoformat(), esquema_de)
    limite = hoy - timedelta(days=retener)
    quitadas = []
    for d in sorted(raiz.iterdir()):
        if d.is_dir() and _ESPEJO_RE.match(d.name) and date.fromisoformat(d.name) < limite:
            shutil.rmtree(d)
            quitadas.append(d.name)
    return {"boards": informe, "quitadas": quitadas}


# --- comparar ---------------------------------------------------------------------------------------------------

def comparar(a: dict[str, dict], b: dict[str, dict]) -> list[str]:
    """Diferencias entre dos recuentos (board×estado, totales e ids). Lista vacía = iguales."""
    diffs = []
    for board in sorted(set(a) | set(b)):
        x, y = a.get(board), b.get(board)
        if x is None or y is None:
            diffs.append(f"{board}: solo en {'origen' if y is None else 'destino'}")
            continue
        for st in sorted(set(x["estados"]) | set(y["estados"])):
            if x["estados"].get(st, 0) != y["estados"].get(st, 0):
                diffs.append(f"{board} {st}: {x['estados'].get(st, 0)} != {y['estados'].get(st, 0)}")
        for key in ("tareas", "comentarios", "eventos", "ejecuciones"):
            if x.get(key) != y.get(key):
                diffs.append(f"{board} {key}: {x.get(key)} != {y.get(key)}")
        faltan, sobran = x["ids"] - y["ids"], y["ids"] - x["ids"]
        if faltan:
            diffs.append(f"{board}: faltan {len(faltan)} ids ({', '.join(sorted(faltan)[:5])})")
        if sobran:
            diffs.append(f"{board}: sobran {len(sobran)} ids ({', '.join(sorted(sobran)[:5])})")
    return diffs


def texto_recuentos(r: dict[str, dict]) -> str:
    lines = []
    for board in sorted(r):
        est = r[board]["estados"]
        parts = " / ".join(f"{s} {est[s]}" for s in STATUSES if est.get(s))
        lines.append(f"{board}: {parts}  (tareas {r[board]['tareas']}, comentarios {r[board]['comentarios']}, "
                     f"eventos {r[board]['eventos']}, ejecuciones {r[board]['ejecuciones']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kanban: SQLite de Hermes <-> hub.* (Supabase)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("recuentos")
    p.add_argument("--origen", type=Path, help="carpeta de Hermes o de copias; sin ella, hub.* (HUB_DB_DSN)")
    p = sub.add_parser("copiar")
    p.add_argument("--origen", type=Path, default=HERMES_HOME)
    p.add_argument("--destino", type=Path, required=True)
    p = sub.add_parser("importar")
    p.add_argument("--origen", type=Path, required=True)
    p.add_argument("--seco", action="store_true", help="importa y deshace (prueba)")
    for name in ("exportar", "volver"):
        p = sub.add_parser(name)
        p.add_argument("--destino", type=Path, required=True)
        p.add_argument("--esquema-de", type=Path, required=True, help="un kanban.db de Hermes (solo lectura)")
        p.add_argument("--board", action="append")
        p.add_argument("--espejo", action="store_true", help="destino/AAAA-MM-DD/ con retención")
        p.add_argument("--retener", type=int, default=14)
    p = sub.add_parser("comparar")
    p.add_argument("--origen", type=Path, required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "recuentos":
            if args.origen:
                print(texto_recuentos(recuentos_sqlite(descubrir(args.origen))))
            else:
                print(texto_recuentos(recuentos_pg(conectar())))
            return 0
        if args.cmd == "copiar":
            _fuera_de_hermes(args.destino)
            print(json.dumps(copiar(args.origen, args.destino), ensure_ascii=False))
            return 0
        if args.cmd == "importar":
            conn = conectar()
            print(json.dumps(importar(conn, descubrir(args.origen), seco=args.seco), ensure_ascii=False))
            if not args.seco:
                diffs = comparar({b: v for b, v in recuentos_sqlite(descubrir(args.origen)).items() if v["tareas"]},
                                 recuentos_pg(conn))
                print("recuentos iguales" if not diffs else "\n".join(diffs))
                return 0 if not diffs else 1
            return 0
        if args.cmd in ("exportar", "volver"):
            conn = conectar()
            if args.espejo:
                out = espejo(conn, args.destino, args.esquema_de, args.retener)
            else:
                out = exportar(conn, args.destino, args.esquema_de, args.board)
            print(json.dumps(out, ensure_ascii=False))
            return 0
        if args.cmd == "comparar":
            conn = conectar()
            origen = {b: v for b, v in recuentos_sqlite(descubrir(args.origen)).items() if v["tareas"]}
            diffs = comparar(origen, recuentos_pg(conn, list(origen)))
            print("recuentos iguales" if not diffs else "\n".join(diffs))
            return 0 if not diffs else 1
    except MigrarError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())
