"""Backends del kanban del hub (F2-02 del plan del hub, 25-10).

El hub habla con el kanban SOLO a través de un objeto con la interfaz de `hermes.HermesCLI` (su `CONTRACT` de 14
operaciones más create/link/archive/edit de prioridad/triage/suscripciones). Este módulo elige el backend:

- `hermes` (por defecto): la CLI de Hermes 0.21.4 sobre su SQLite, exactamente como hasta ahora.
- `supabase`: tablas propias `hub.*` en Postgres (Supabase de Oscar HQ), conexión directa con el rol `hub_core`.

Selector: variable de entorno `KANBAN_BACKEND` o `runner.kanban_backend` de lanes.yaml (ver config.kanban_backend).
Conexión del backend supabase: `HUB_DB_DSN` (entorno o .env del hub; nunca en lanes.yaml ni en el repo).

El backend supabase reproduce la semántica de Hermes que el hub lee (ver tests/test_kanban_api*.py):
fechas en segundos epoch, `BLOCKED: <motivo>` como comentario al bloquear, 2º bloqueo igual -> `triage`
(`block_loop_detected`), `review_requested` con `implementer` y `reopen-review` que lo restaura, padres que retienen
a las hijas en `todo` y las promueven al terminar, `idempotency_key` que devuelve la tarjeta existente.
Esquema esperado: tests/fixtures/hub_schema_f2.sql (la migración real es F2-01 en oscar-hq).

Varios ordenadores (19-11, auditoría del segundo worker): el kanban es común y cada runner es un `worker` con id propio
(`HUB_WORKER_ID`). Reglas de este backend para que N equipos convivan sin pisarse:
- TODO tiempo que se guarda es del SERVIDOR (`now()`): un PC con el reloj mal puesto no acorta ni alarga reservas
  (H4). `clock` solo queda para lo que no se guarda en la base.
- Una reserva tiene dueño (`lease_owner`, que ahora salen en cada tarjeta junto con `lease_until` y `server_now`): el
  latido solo renueva la propia y un worker no cierra (`block`/`complete`/`request_review`) una tarjeta cuya reserva
  VIVA es de otro (H5). La caducada de otro puede tomarla cualquiera.
- `claim` comprueba que el worker esté dado de alta y activo en `hub.workers`, el interruptor de workers externos y
  el sello del guard (H3, H11); `claim_serial` añade un cerrojo transaccional para rutas serializadas (migraciones, H10).
- `release_stale` libera las reservas caducadas (H6); las llama el runner con un ticker (solo backend supabase).
Con el backend hermes nada de esto existe: los diccionarios de tarjeta no llevan `lease_owner` y el runner sigue
exactamente igual.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import socket
import threading
import time
from contextlib import contextmanager

from . import proc as _proc
from .hermes import HermesCLI, HermesError, KanbanComun, OSCAR_AUTHOR, preparar_tarjeta

log = logging.getLogger("agent_lanes")

BACKENDS = ("hermes", "supabase")
DEFAULT_BACKEND = "hermes"
ENV_BACKEND = "KANBAN_BACKEND"
ENV_DSN = "HUB_DB_DSN"
ENV_WORKER = "HUB_WORKER_ID"
WORKER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")  # = CHECK de hub.workers.id
KEY_EXTERNOS = "workers_externos"  # hub.ajustes: interruptor de los PC de otras personas (María, Ángel)
KEY_GUARD = "guard_hash_aprobado"  # hub.ajustes: sello(s) del guard que un worker debe llevar para reclamar (H11)

STATUSES = frozenset({"triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done", "archived"})
BLOCK_KINDS = frozenset({"dependency", "needs_input", "capability", "transient"})
BLOCK_RECURRENCE_LIMIT = 2  # = hermes_cli.kanban_db.BLOCK_RECURRENCE_LIMIT
TERMINAL = ("done", "archived")
SORTS = {  # = hermes_cli.kanban_db.VALID_SORT_ORDERS (+ id para desempatar: SQLite desempata por rowid)
    "created": "created_at ASC, id ASC",
    "created-desc": "created_at DESC, id DESC",
    "priority": "priority DESC, created_at ASC, id ASC",
    "priority-desc": "priority ASC, created_at ASC, id ASC",
    "status": "status ASC, created_at ASC, id ASC",
    "assignee": "assignee ASC, created_at ASC, id ASC",
    "title": "title ASC, id ASC",
    "updated": "started_at DESC NULLS LAST, created_at DESC, id DESC",
    "completed-desc": "completed_at DESC NULLS LAST, id DESC",
}
# Campos de `kanban show/list --json` de Hermes (hermes_cli.kanban_output._TASK_DICT_FIELDS). Los que el esquema
# `hub` no tiene como columna salen de `extra` (importados de Hermes) o valen None.
TASK_FIELDS = (
    "id", "title", "body", "assignee", "status", "priority", "tenant",
    "workspace_kind", "workspace_path", "branch_name", "project_id",
    "created_by", "created_at", "started_at", "completed_at", "result",
    "skills", "max_runtime_seconds", "max_retries", "model_override", "provider_override",
    "session_id", "workflow_template_id", "current_step_key", "completion_contract", "last_failure_error",
)
RUN_FIELDS = ("id", "profile", "step_key", "status", "outcome", "summary", "error", "metadata", "worker_pid",
              "started_at", "ended_at")
NOTIFY_KV = "notify:"  # hub.kv: notify:<task_id> -> [suscripciones como las de `kanban notify-list --json`]

_TASK_SQL = ("SELECT id, title, body, assignee, status, priority, created_by, created_by_worker, "
             "extract(epoch from created_at)::bigint, extract(epoch from started_at)::bigint, "
             "extract(epoch from completed_at)::bigint, result, extra, alcance, rama, board, block_kind, "
             "lease_owner, extract(epoch from lease_until)::bigint, extract(epoch from now())::bigint "
             "FROM hub.tasks")


def normalize_worker(raw: str | None) -> str:
    """Id de worker tal como lo guarda la base: minúsculas y sin espacios (el hostname de Windows va en mayúsculas)."""
    return (raw or "").strip().lower()


def worker_id_problem(worker: str) -> str | None:
    """Por qué un id de worker no vale (None = válido). Lo mira `runner.py --check`."""
    if not WORKER_ID_RE.match(worker or ""):
        return (f"HUB_WORKER_ID={worker!r} no es válido: minúsculas, números, - o _ (2 a 63 caracteres, como "
                "hub.workers.id)")
    return None


_resolved: str | None = None
_resolved_lock = threading.Lock()


def backend_name() -> str:
    """Backend de ESTE proceso: `KANBAN_BACKEND` del entorno > .env del hub > `runner.kanban_backend` de lanes.yaml >
    `hermes`. Se resuelve UNA vez por proceso (editar lanes.yaml con el runner vivo no parte el hub en dos kanbans;
    el cambio rige al reiniciar). Un valor desconocido es un error: mejor no arrancar que hablar con otro kanban."""
    global _resolved
    with _resolved_lock:
        if _resolved is None:
            from .config import kanban_backend
            name = kanban_backend()
            if name not in BACKENDS:
                raise ValueError(f"KANBAN_BACKEND={name!r} no es válido (opciones: {', '.join(BACKENDS)})")
            _resolved = name
            log.info("kanban: backend=%s", name)
        return _resolved


def reset_backend() -> None:
    """Olvida el backend resuelto (solo pruebas)."""
    global _resolved
    with _resolved_lock:
        _resolved = None


def backend_for(board: str, author: str = "agent-lanes", *, backend: str | None = None, **kw):
    """Cliente del kanban para `board` con la interfaz de HermesCLI. Punto único: todos los llamadores del hub pasan
    por aquí (con `hermes` devuelve exactamente el HermesCLI de siempre)."""
    name = backend or backend_name()
    if name == "supabase":
        return SupabaseKanban(board, author=author, **kw)
    if name == "hermes":
        return HermesCLI(board, author=author, **kw)
    raise ValueError(f"backend del kanban desconocido: {name!r}")


def _dsn_from_env() -> str:
    dsn = (os.environ.get(ENV_DSN) or "").strip()
    if dsn:
        return dsn
    from .config import load_env
    return (load_env().get(ENV_DSN) or "").strip()


def _default_connect(dsn: str):
    import psycopg2  # perezoso: el backend hermes no necesita el driver
    return psycopg2.connect(dsn, connect_timeout=10, application_name="agent-lanes")


def _db_errors() -> tuple[type[BaseException], ...]:
    try:
        import psycopg2
        return (psycopg2.Error, OSError)
    except ImportError:
        return (OSError,)


def _js(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _obj(value):
    """jsonb -> python (psycopg2 ya devuelve dict/list; un doble de test puede devolver texto)."""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


class _DBFailure(Exception):
    pass


class SupabaseKanban(KanbanComun):
    """Backend `supabase`: misma interfaz y misma forma de JSON que HermesCLI, sobre las tablas `hub.*`."""

    def __init__(self, board: str, author: str = "agent-lanes", *, dsn: str | None = None, connect=None,
                 clock=time.time, worker: str | None = None, guard_digest: str | None = None):
        self.board = board
        self.author = author
        self.last_error: str | None = None
        self._dsn = dsn
        self._connect = connect or _default_connect
        self._clock = clock  # nada de lo que se guarda en la base usa el reloj del cliente (H4)
        self._local = threading.local()  # una conexión por hilo: el hub lee tarjetas en paralelo (ThreadPoolExecutor)
        # Dueño de la reserva: como Hermes (claimer = host), cualquier proceso del mismo PC/worker puede renovarla.
        self.worker = normalize_worker(worker or os.environ.get(ENV_WORKER) or socket.gethostname())
        self.guard_digest = guard_digest  # sello del guard de ESTE checkout (guard_hash.py); None = no se declara
        self._lost: set[str] = set()  # tarjetas cuya reserva se perdió (latido rechazado por cambio de dueño)
        self._lost_lock = threading.Lock()

    # --- conexión ------------------------------------------------------------------------------------------------
    def _connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is None or getattr(conn, "closed", 0):
            dsn = self._dsn or _dsn_from_env()
            if not dsn:
                raise _DBFailure(f"falta {ENV_DSN} (cadena de conexión del rol hub_core)")
            try:
                conn = self._connect(dsn)
            except ImportError as exc:
                raise _DBFailure("falta el driver psycopg2 (pip install psycopg2-binary)") from exc
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self):
        """Una transacción; cualquier error del driver se convierte en _DBFailure y la conexión se descarta."""
        errors = _db_errors()
        try:
            conn = self._connection()
        except errors as exc:
            raise _DBFailure(f"sin conexión con el kanban: {type(exc).__name__}") from exc
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except errors as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
            raise _DBFailure(f"{type(exc).__name__}: {str(exc).strip()[:200]}") from exc
        except BaseException:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

    def _read(self, what: str, fn):
        try:
            with self._tx() as cur:
                return fn(cur)
        except _DBFailure as exc:
            _proc.mark_transient(f"kanban {what}: {exc}")
            raise HermesError(f"{what} failed: {exc}") from exc

    def _write(self, what: str, fn, default=False):
        """Escritura: fallo de base de datos -> `default` (como un returncode != 0 de la CLI), nunca una excepción."""
        self.last_error = None
        try:
            with self._tx() as cur:
                return fn(cur)
        except _DBFailure as exc:
            self.last_error = str(exc)
            _proc.mark_transient(f"kanban {what}: {exc}")
            log.warning("kanban %s (%s): %s", what, self.board, exc)
            return default

    # --- utilidades SQL ------------------------------------------------------------------------------------------
    def _now(self) -> int:
        """Reloj LOCAL: solo para lo que no se guarda en la base. Lo guardado usa now() del servidor (H4)."""
        return int(self._clock())

    @staticmethod
    def _server_now(cur) -> int:
        cur.execute("SELECT extract(epoch from now())::bigint")
        return cur.fetchone()[0]

    # created_at de eventos y comentarios se omite a propósito: lo pone el servidor (DEFAULT now()). Así un PC con el
    # reloj mal puesto no desordena la historia de la tarjeta y encaja con los GRANT de hub_core (sin created_at).
    def _event(self, cur, task_id: str, kind: str, payload=None, run_id=None) -> None:
        cur.execute("INSERT INTO hub.task_events (task_id, run_id, kind, payload) VALUES (%s, %s, %s, %s::jsonb)",
                    (task_id, run_id, kind, _js(payload)))

    def _comment(self, cur, task_id: str, author: str, body: str) -> None:
        cur.execute("INSERT INTO hub.task_comments (task_id, worker_id, body) VALUES (%s, %s, %s)",
                    (task_id, author, body))

    def _row(self, cur, task_id: str, lock: bool = False):
        cur.execute("SELECT status, assignee, lease_owner, block_kind, block_recurrences, current_run_id, parents, "
                    "(lease_until IS NOT NULL AND lease_until > now()) FROM hub.tasks WHERE id = %s AND board = %s"
                    + (" FOR UPDATE" if lock else ""), (task_id, self.board))
        r = cur.fetchone()
        if r is None:
            return None
        keys = ("status", "assignee", "lease_owner", "block_kind", "block_recurrences", "current_run_id", "parents",
                "lease_live")
        return dict(zip(keys, r))

    def _foreign_lease(self, row: dict) -> str | None:
        """Dueño de una reserva VIVA que no es de este worker (None = se puede actuar). La caducada de otro no cuenta:
        está abandonada (la libera `release_stale`) y quien actúe sobre ella no pisa a nadie (H5)."""
        owner = row.get("lease_owner")
        if owner is not None and owner != self.worker and row.get("lease_live"):
            return owner
        return None

    def _not_mine(self, row: dict, what: str) -> str | None:
        """Motivo (y last_error) si `what` choca con la reserva viva de otro worker; None si procede."""
        owner = self._foreign_lease(row)
        if owner is None:
            return None
        self.last_error = f"{what}: la reserva es de {owner} (este worker es {self.worker})"
        return self.last_error

    def _parents_done(self, cur, task_id: str) -> bool:
        cur.execute("SELECT 1 FROM hub.tasks c JOIN hub.tasks p ON p.id = ANY(c.parents) "
                    "WHERE c.id = %s AND p.status NOT IN ('done', 'archived') LIMIT 1", (task_id,))
        return cur.fetchone() is None

    def _landing(self, cur, task_id: str) -> str:
        return "ready" if self._parents_done(cur, task_id) else "todo"

    def _recompute_ready(self, cur) -> int:
        """`todo` con todos los padres terminados -> `ready` (como hermes recompute_ready; los `blocked` explícitos
        siguen bloqueados hasta un unblock)."""
        # H7: las lecturas no escriben salvo que haya algo que promover (antes un UPDATE en cada list de cada runner,
        # con contención y updated_at ruidoso).
        cur.execute(
            "SELECT 1 FROM hub.tasks c WHERE c.board = %s AND c.status = 'todo' "
            "AND NOT EXISTS (SELECT 1 FROM hub.tasks p WHERE p.id = ANY(c.parents) "
            "AND p.status NOT IN ('done', 'archived')) LIMIT 1", (self.board,))
        if cur.fetchone() is None:
            return 0
        cur.execute(
            "UPDATE hub.tasks c SET status = 'ready', updated_at = now() WHERE c.board = %s AND c.status = 'todo' "
            "AND NOT EXISTS (SELECT 1 FROM hub.tasks p WHERE p.id = ANY(c.parents) "
            "AND p.status NOT IN ('done', 'archived')) RETURNING c.id", (self.board,))
        ids = [r[0] for r in cur.fetchall()]
        for tid in ids:
            self._event(cur, tid, "promoted")
        return len(ids)

    def _end_run(self, cur, task_id: str, *, status: str, outcome: str, summary=None, metadata=None,
                 synthesize: bool = False):
        """Cierra la ejecución en curso (o crea una ya cerrada si no la hay y hay algo que guardar)."""
        cur.execute("SELECT current_run_id, assignee FROM hub.tasks WHERE id = %s", (task_id,))
        run_id, assignee = cur.fetchone()
        if run_id is not None:
            cur.execute("UPDATE hub.task_runs SET status = %s, outcome = %s, summary = COALESCE(%s, summary), "
                        "metadata = COALESCE(%s::jsonb, metadata), ended_at = now(), lease_until = NULL "
                        "WHERE id = %s", (status, outcome, summary, _js(metadata), run_id))
        elif synthesize:
            cur.execute("INSERT INTO hub.task_runs (task_id, profile, status, outcome, summary, metadata, "
                        "started_at, ended_at) VALUES (%s, %s, %s, %s, %s, %s::jsonb, now(), now()) RETURNING id",
                        (task_id, assignee, status, outcome, summary, _js(metadata)))
            run_id = cur.fetchone()[0]
        cur.execute("UPDATE hub.tasks SET current_run_id = NULL WHERE id = %s", (task_id,))
        return run_id

    @staticmethod
    def _task_dict(r) -> dict:
        (tid, title, body, assignee, status, priority, created_by, created_by_worker, created_at, started_at,
         completed_at, result, extra, alcance, rama, board, block_kind, lease_owner, lease_until, server_now) = r
        extra = _obj(extra) or {}
        d = {k: extra.get(k) for k in TASK_FIELDS}
        d.update(id=tid, title=title, body=body, assignee=assignee, status=status, priority=priority,
                 created_by=created_by_worker or (str(created_by) if created_by is not None else None),
                 created_at=created_at, started_at=started_at, completed_at=completed_at, result=result)
        d["workspace_kind"] = d.get("workspace_kind") or "scratch"
        skills = d.get("skills")
        if isinstance(skills, str):  # importada de Hermes: texto JSON tal cual
            skills = _obj(skills)
        d["skills"] = list(skills) if isinstance(skills, list) else []
        d.update(board=board, alcance=list(alcance or []), rama=rama, block_kind=block_kind)
        # Reserva (H1): dueño, caducidad y la hora del SERVIDOR con la que compararla (nunca el reloj ni el pid locales).
        # Las tarjetas del backend hermes no traen estas claves: quien las lee usa .get y sigue como siempre.
        d.update(lease_owner=lease_owner, lease_until=lease_until, server_now=server_now,
                 lease_expired=bool(lease_owner is not None and lease_until is not None and server_now is not None
                                    and lease_until < server_now))
        return d

    def _list(self, cur, where: str, params: tuple, sort: str = "priority") -> list[dict]:
        if sort not in SORTS:
            raise HermesError(f"list failed: sort {sort!r} no válido")
        self._recompute_ready(cur)
        cur.execute(f"{_TASK_SQL} WHERE board = %s {where} ORDER BY {SORTS[sort]}", (self.board, *params))
        return [self._task_dict(r) for r in cur.fetchall()]

    # --- lecturas ------------------------------------------------------------------------------------------------
    def list_status(self, assignee: str, status: str, sort: str = "priority") -> list[dict]:
        if sort not in SORTS:
            raise HermesError(f"list failed: sort {sort!r} no válido")
        return self._read("list", lambda cur: self._list(cur, "AND assignee = %s AND status = %s",
                                                         ((assignee or "").strip().lower(), status), sort))

    def list_status_all(self, status: str) -> list[dict]:
        return self._read("list", lambda cur: self._list(cur, "AND status = %s", (status,)))

    def list_ready(self, assignee: str) -> list[dict]:
        return self.list_status(assignee, "ready")

    def list_all(self, archived: bool = False) -> list[dict]:
        return self._read("list", lambda cur: self._list(cur, "" if archived else "AND status != 'archived'", ()))

    def show(self, task_id: str) -> dict:
        def run(cur):
            cur.execute(f"{_TASK_SQL} WHERE id = %s AND board = %s", (task_id, self.board))
            r = cur.fetchone()
            if r is None:
                raise HermesError(f"show {task_id} failed: no such task: {task_id}")
            cur.execute("SELECT parents FROM hub.tasks WHERE id = %s", (task_id,))
            parents = sorted(cur.fetchone()[0] or [])
            cur.execute("SELECT id FROM hub.tasks WHERE %s = ANY(parents) ORDER BY id", (task_id,))
            children = [c[0] for c in cur.fetchall()]
            cur.execute("SELECT COALESCE(worker_id, author_id::text), body, extract(epoch from created_at)::bigint "
                        "FROM hub.task_comments WHERE task_id = %s ORDER BY created_at, id", (task_id,))
            comments = [{"author": a, "body": b, "created_at": t} for a, b, t in cur.fetchall()]
            cur.execute("SELECT kind, payload, extract(epoch from created_at)::bigint, run_id "
                        "FROM hub.task_events WHERE task_id = %s ORDER BY created_at, id", (task_id,))
            events = [{"kind": k, "payload": _obj(p), "created_at": t, "run_id": rid}
                      for k, p, t, rid in cur.fetchall()]
            cur.execute("SELECT id, profile, step_key, status, outcome, summary, error, metadata, worker_pid, "
                        "extract(epoch from started_at)::bigint, extract(epoch from ended_at)::bigint "
                        "FROM hub.task_runs WHERE task_id = %s ORDER BY started_at, id", (task_id,))
            runs = [dict(zip(RUN_FIELDS, row)) for row in cur.fetchall()]
            for run_ in runs:
                run_["metadata"] = _obj(run_["metadata"])
            latest = next((x["summary"] for x in sorted(runs, key=lambda x: (x["ended_at"] or x["started_at"] or 0,
                                                                             x["id"]), reverse=True)
                           if x["summary"]), None)
            return {"task": self._task_dict(r), "latest_summary": latest, "parents": parents, "children": children,
                    "comments": comments, "events": events, "runs": runs}
        return self._read("show", run)

    # --- escrituras ----------------------------------------------------------------------------------------------
    def create(self, title: str, body: str, assignee: str, *, priority: int | None = None, key: str | None = None,
               created_by: str | None = None, parents: list[str] | None = None, alcance: list[str] | None = None,
               rama: str | None = None) -> str:
        body, assignee = preparar_tarjeta(self.board, title, body, assignee)
        author = created_by or self.author
        parents = list(dict.fromkeys(parents or ()))

        def run(cur):
            if key:
                cur.execute("SELECT id FROM hub.tasks WHERE board = %s AND idempotency_key = %s "
                            "AND status != 'archived' ORDER BY created_at DESC LIMIT 1", (self.board, key))
                found = cur.fetchone()
                if found:
                    return found[0]
            if parents:
                cur.execute("SELECT id, status FROM hub.tasks WHERE board = %s AND id = ANY(%s)",
                            (self.board, parents))
                known = dict(cur.fetchall())
                missing = [p for p in parents if p not in known]
                if missing:
                    raise HermesError(f"create failed: padres desconocidos: {', '.join(missing)}")
                status = "ready" if all(known[p] in TERMINAL for p in parents) else "todo"
            else:
                status = "ready"
            for _ in range(5):
                tid = "t_" + secrets.token_hex(4)
                cur.execute(
                    "INSERT INTO hub.tasks (id, board, title, body, assignee, status, priority, parents, "
                    "idempotency_key, created_by, created_by_worker, alcance, rama, created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::text[], %s, %s, %s, %s::text[], %s, now(), "
                    "now()) ON CONFLICT (id) DO NOTHING RETURNING id",
                    (tid, self.board, title.strip(), body, (assignee or "").strip().lower() or None, status,
                     int(priority or 0), parents, key, int(author) if str(author).isdigit() else None, author,
                     list(alcance or ()), rama))
                if cur.fetchone():
                    self._event(cur, tid, "created", {"assignee": assignee, "status": status, "parents": parents,
                                                       "created_by": author})
                    return tid
            raise HermesError("create failed: no se pudo generar un id libre")

        try:
            with self._tx() as cur:
                return run(cur)
        except _DBFailure as exc:
            _proc.mark_transient(f"kanban create: {exc}")
            raise HermesError(f"create failed: {exc}") from exc

    # --- admisión de workers (H3, H11) ---------------------------------------------------------------------------
    @staticmethod
    def _regclass(cur, name: str) -> bool:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
        return bool(cur.fetchone()[0])

    def _ajuste(self, cur, key: str):
        if not self._regclass(cur, "hub.ajustes"):
            return None
        cur.execute("SELECT valor FROM hub.ajustes WHERE clave = %s", (key,))
        r = cur.fetchone()
        return _obj(r[0]) if r else None

    def _admission(self, cur, ttl: int) -> str | None:
        """Por qué ESTE worker no puede reclamar (None = puede). Replica lo que comprueba `hub.claim` (worker activo,
        interruptor de workers externos) sin llamarla: `hub.claim` elige ella la tarjeta (se saltaría los filtros del
        runner: prevención de choques, retenciones, exclusiones), inserta columnas de run que este backend no lee y
        topa el ttl a 2 h, menos que `claim_ttl_seconds` de los carriles largos (max_runtime + 300 s).

        Falla CERRADO: un worker sin alta, desactivado, externo con el interruptor apagado o con un guard distinto del
        aprobado no reclama. Un esquema sin `hub.workers` (el de pruebas F2) no registra workers y no se comprueba."""
        if int(ttl) <= 0:
            return f"ttl inválido: {ttl}"
        if not self._regclass(cur, "hub.workers"):
            return None
        cur.execute("SELECT activo, externo FROM hub.workers WHERE id = %s", (self.worker,))
        w = cur.fetchone()
        if w is None:
            return f"worker {self.worker!r} sin alta en hub.workers (la da de alta Oscar)"
        activo, externo = w
        if not activo:
            return f"worker {self.worker!r} desactivado en hub.workers"
        if externo and self._ajuste(cur, KEY_EXTERNOS) is not True:
            return "los workers externos están apagados (ajuste workers_externos)"
        aprobado = self._ajuste(cur, KEY_GUARD)
        if aprobado:  # sin el ajuste no se exige sello (oscar-hq aún no lo define); con él, es obligatorio
            lista = [aprobado] if isinstance(aprobado, str) else list(aprobado)
            if self.guard_digest not in lista:
                return (f"el guard de {self.worker!r} ({(self.guard_digest or 'sin sello')[:12]}) no es el aprobado: "
                        "actualiza el checkout del hub (git pull) y reinicia el runner")
        return None

    def register_worker(self) -> bool:
        """Latido de alta: escribe `version` (sello del guard) y `last_seen` de este worker en hub.workers (hub_core
        solo puede tocar last_seen, version y hostname). Sin la tabla o sin alta no hace nada. True = escrito."""
        def run(cur):
            if not self._regclass(cur, "hub.workers"):
                return False
            cur.execute("UPDATE hub.workers SET version = COALESCE(%s, version), last_seen = now(), hostname = %s "
                        "WHERE id = %s", (self.guard_digest, socket.gethostname(), self.worker))
            return cur.rowcount == 1
        return self._write("register-worker", run)

    # --- admisión de workers (H3, H11) ---------------------------------------------------------------------------
    @staticmethod
    def _regclass(cur, name: str) -> bool:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
        return bool(cur.fetchone()[0])

    def _ajuste(self, cur, key: str):
        if not self._regclass(cur, "hub.ajustes"):
            return None
        cur.execute("SELECT valor FROM hub.ajustes WHERE clave = %s", (key,))
        r = cur.fetchone()
        return _obj(r[0]) if r else None

    def _admission(self, cur, ttl: int) -> str | None:
        """Por qué ESTE worker no puede reclamar (None = puede). Replica lo que comprueba `hub.claim` (worker activo,
        interruptor de workers externos) sin llamarla: `hub.claim` elige ella la tarjeta (se saltaría los filtros del
        runner: prevención de choques, retenciones, exclusiones), inserta columnas de run que este backend no lee y
        topa el ttl a 2 h, menos que `claim_ttl_seconds` de los carriles largos (max_runtime + 300 s).

        Falla CERRADO: un worker sin alta, desactivado, externo con el interruptor apagado o con un guard distinto del
        aprobado no reclama. Un esquema sin `hub.workers` (el de pruebas F2) no registra workers y no se comprueba."""
        if int(ttl) <= 0:
            return f"ttl inválido: {ttl}"
        if not self._regclass(cur, "hub.workers"):
            return None
        cur.execute("SELECT activo, externo FROM hub.workers WHERE id = %s", (self.worker,))
        w = cur.fetchone()
        if w is None:
            return f"worker {self.worker!r} sin alta en hub.workers (la da de alta Oscar)"
        activo, externo = w
        if not activo:
            return f"worker {self.worker!r} desactivado en hub.workers"
        if externo and self._ajuste(cur, KEY_EXTERNOS) is not True:
            return "los workers externos están apagados (ajuste workers_externos)"
        aprobado = self._ajuste(cur, KEY_GUARD)
        if aprobado:  # sin el ajuste no se exige sello (oscar-hq aún no lo define); con él, es obligatorio
            lista = [aprobado] if isinstance(aprobado, str) else list(aprobado)
            if self.guard_digest not in lista:
                return (f"el guard de {self.worker!r} ({(self.guard_digest or 'sin sello')[:12]}) no es el aprobado: "
                        "actualiza el checkout del hub (git pull) y reinicia el runner")
        return None

    def register_worker(self) -> bool:
        """Latido de alta: escribe `version` (sello del guard) y `last_seen` de este worker en hub.workers (hub_core
        solo puede tocar last_seen, version y hostname). Sin la tabla o sin alta no hace nada. True = escrito."""
        def run(cur):
            if not self._regclass(cur, "hub.workers"):
                return False
            cur.execute("UPDATE hub.workers SET version = COALESCE(%s, version), last_seen = now(), hostname = %s "
                        "WHERE id = %s", (self.guard_digest, socket.gethostname(), self.worker))
            return cur.rowcount == 1
        return self._write("register-worker", run)

    def claim(self, task_id: str, ttl: int) -> bool:
        return self._claim(task_id, ttl, ())

    def claim_serial(self, task_id: str, ttl: int, paths) -> bool:
        """`claim` para una tarjeta que cita rutas serializadas (migraciones): dos PC no reclaman a la vez dos tarjetas
        así del mismo carril aunque ambos las vieran libres (H10). Cerrojo transaccional por tablero y carril; si otra
        `running` del carril cita esas rutas, la reserva se rechaza."""
        return self._claim(task_id, ttl, tuple(paths or ()))

    def _claim(self, task_id: str, ttl: int, serial_paths: tuple) -> bool:
        def run(cur):
            why = self._admission(cur, ttl)
            if why:
                self.last_error = f"claim rechazado: {why}"
                log.warning("kanban claim %s (%s): %s", task_id, self.board, why)
                return False
            row = self._row(cur, task_id, lock=True)
            if row is None or row["status"] != "ready" or row["lease_owner"] is not None:
                return False
            if not self._parents_done(cur, task_id):
                cur.execute("UPDATE hub.tasks SET status = 'todo', updated_at = now() WHERE id = %s", (task_id,))
                self._event(cur, task_id, "claim_rejected", {"reason": "parents_not_done"})
                return False
            if serial_paths:
                from .grandes import touches
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"serial:{self.board}:{row['assignee']}",))
                cur.execute(f"{_TASK_SQL} WHERE board = %s AND assignee = %s AND status = 'running' AND id <> %s",
                            (self.board, row["assignee"], task_id))
                rivals = [t["id"] for t in map(self._task_dict, cur.fetchall()) if touches(t, serial_paths)]
                if rivals:
                    self._event(cur, task_id, "claim_rejected", {"reason": "serial_paths", "running": rivals})
                    return False
            ttl_s = int(ttl)
            cur.execute("INSERT INTO hub.task_runs (task_id, profile, status, lease_owner, lease_until, started_at) "
                        "VALUES (%s, %s, 'running', %s, now() + make_interval(secs => %s), now()) RETURNING id, "
                        "extract(epoch from lease_until)::bigint", (task_id, row["assignee"], self.worker, ttl_s))
            run_id, expires = cur.fetchone()
            cur.execute("UPDATE hub.tasks SET status = 'running', lease_owner = %s, "
                        "lease_until = now() + make_interval(secs => %s), lease_ttl = %s, current_run_id = %s, "
                        "started_at = COALESCE(started_at, now()), updated_at = now() "
                        "WHERE id = %s AND status = 'ready' AND lease_owner IS NULL",
                        (self.worker, ttl_s, ttl_s, run_id, task_id))
            if cur.rowcount != 1:
                raise _DBFailure("claim perdió la carrera")  # imposible con FOR UPDATE; por si acaso, rollback
            self._event(cur, task_id, "claimed", {"lock": self.worker, "expires": expires, "run_id": run_id},
                        run_id=run_id)
            with self._lost_lock:
                self._lost.discard(task_id)
            return True
        return self._write("claim", run)

    def heartbeat(self, task_id: str, note: str | None = None) -> bool:
        """Renueva la reserva SOLO si es de este worker (H5). False por dos motivos distintos: la base falló (queda
        `last_error`; se reintenta en el siguiente latido) o la reserva ya no es nuestra (`lease_lost(task_id)` pasa a
        True: otro la tiene o la segó `release_stale`; quien trabajaba debe parar)."""
        def run(cur):
            cur.execute("UPDATE hub.tasks SET lease_until = now() + make_interval(secs => COALESCE(lease_ttl, 0)), "
                        "updated_at = now() WHERE id = %s AND board = %s AND status = 'running' AND lease_owner = %s "
                        "RETURNING current_run_id, lease_until", (task_id, self.board, self.worker))
            r = cur.fetchone()
            if r is None:
                cur.execute("SELECT status, lease_owner FROM hub.tasks WHERE id = %s AND board = %s",
                            (task_id, self.board))
                now = cur.fetchone()
                if now is None or now[0] != "running" or now[1] not in (None, self.worker):
                    with self._lost_lock:
                        self._lost.add(task_id)
                    quien = f"es de {now[1]}" if now and now[1] else f"ya no está en running ({now[0] if now else 'borrada'})"
                    self.last_error = f"latido rechazado: la reserva de {task_id} ya no es de {self.worker}; {quien}"
                return False
            if r[0] is not None:
                cur.execute("UPDATE hub.task_runs SET lease_until = %s WHERE id = %s", (r[1], r[0]))
            if self._regclass(cur, "hub.workers"):
                cur.execute("UPDATE hub.workers SET last_seen = now() WHERE id = %s", (self.worker,))
            self._event(cur, task_id, "heartbeat", {"note": note} if note else None, run_id=r[0])
            return True
        return self._write("heartbeat", run)

    def lease_lost(self, task_id: str) -> bool:
        """¿Un latido de esta tarjeta fue rechazado porque la reserva dejó de ser de este worker? (el runner corta al
        `claude` y no pasa a review, bloquea ni avisa: la tarjeta ya es de otro)."""
        with self._lost_lock:
            return task_id in self._lost

    # --- segador de reservas caducadas (H6) ----------------------------------------------------------------------
    def release_stale(self, max_attempts: int = 3) -> list[dict]:
        """Libera las reservas caducadas (`lease_until < now()` del SERVIDOR): attempts+1; al llegar a `max_attempts`
        la tarjeta va a `triage`, antes a `blocked` (transient), y el run queda `released`. Cada fila liberada deja un
        evento `lease_expired` y un comentario, en la misma transacción. Usa `hub.release_stale` si existe (esquema real
        de oscar-hq); si no, la misma lógica en SQL propio. `FOR UPDATE SKIP LOCKED`: varios runners a la vez no se
        estorban. Devuelve [{task_id, status, attempts, worker_id}]; ante un fallo de base, [] y `last_error`."""
        limit = max(1, int(max_attempts))

        def run(cur):
            cur.execute("SELECT to_regprocedure('hub.release_stale(integer)') IS NOT NULL")
            if cur.fetchone()[0]:
                cur.execute("SELECT task_id, status, attempts, worker_id FROM hub.release_stale(%s)", (limit,))
                rows = [dict(zip(("task_id", "status", "attempts", "worker_id"), r)) for r in cur.fetchall()]
            else:
                cur.execute("SELECT id, lease_owner, current_run_id, attempts + 1 FROM hub.tasks "
                            "WHERE status = 'running' AND lease_until < now() ORDER BY lease_until "
                            "FOR UPDATE SKIP LOCKED")
                rows = []
                for tid, owner, run_id, nuevos in cur.fetchall():
                    status = "triage" if nuevos >= limit else "blocked"
                    cur.execute("UPDATE hub.tasks SET status = %s, block_kind = 'transient', attempts = %s, "
                                "lease_owner = NULL, lease_until = NULL, current_run_id = NULL, updated_at = now() "
                                "WHERE id = %s", (status, nuevos, tid))
                    cur.execute("UPDATE hub.task_runs SET status = 'released', outcome = 'reclaimed', "
                                "ended_at = now(), lease_until = NULL WHERE id = %s AND ended_at IS NULL", (run_id,))
                    rows.append({"task_id": tid, "status": status, "attempts": nuevos, "worker_id": owner})
            for r in rows:  # hub.release_stale no deja rastro en la tarjeta: se añade aquí, en la misma transacción
                self._event(cur, r["task_id"], "lease_expired", {"worker": r["worker_id"], "to": r["status"],
                                                                  "attempts": r["attempts"], "by": self.worker})
                self._comment(cur, r["task_id"], self.author,
                              f"BLOCKED: la reserva de {r['worker_id']} caducó sin latido (intento "
                              f"{r['attempts']}/{limit}); la tarjeta queda en {r['status']}")
            return rows
        return self._write("release-stale", run, default=[])

    def comment(self, task_id: str, text: str, author: str | None = None) -> bool:
        def run(cur):
            if self._row(cur, task_id) is None:
                return False
            self._comment(cur, task_id, author or self.author, (text or "").strip())
            return True
        return self._write("comment", run)

    def block(self, task_id: str, kind: str, reason: str) -> bool:
        if kind not in BLOCK_KINDS:
            return False

        def run(cur):
            row = self._row(cur, task_id, lock=True)
            if row is None or row["status"] not in ("running", "ready"):
                return False
            if self._not_mine(row, "block"):
                return False
            if reason:
                self._comment(cur, task_id, self.author, f"BLOCKED: {reason}")
            source = "ready"
            k = kind
            extra = {}
            if k == "dependency" and self._parents_done(cur, task_id):
                k, extra = "needs_input", {"requested_kind": "dependency", "rekind_reason": "no_open_parent"}
            if k == "dependency":
                new_status, ev, recurrences = "todo", "dependency_wait", row["block_recurrences"]
                payload = {"reason": reason, "kind": k, "source_status": source}
            else:
                recurrences = row["block_recurrences"] + 1 if row["block_kind"] == k else 1
                payload = {"reason": reason, "kind": k, "recurrences": recurrences, "source_status": source}
                if recurrences >= BLOCK_RECURRENCE_LIMIT:
                    new_status, ev = "triage", "block_loop_detected"
                    payload["limit"] = BLOCK_RECURRENCE_LIMIT
                else:
                    new_status, ev = "blocked", "blocked"
            payload.update(extra)
            run_id = self._end_run(cur, task_id, status="blocked", outcome="blocked", summary=reason or None,
                                   synthesize=bool(reason))
            cur.execute("UPDATE hub.tasks SET status = %s, block_kind = %s, block_recurrences = %s, "
                        "lease_owner = NULL, lease_until = NULL, updated_at = now() WHERE id = %s",
                        (new_status, k, recurrences, task_id))
            self._event(cur, task_id, ev, payload, run_id=run_id)
            return True
        return self._write("block", run)

    def request_review(self, task_id: str, summary: str, metadata: dict) -> tuple[bool, str]:
        def run(cur):
            row = self._row(cur, task_id, lock=True)
            if row is None:
                return False, "task not found"
            if row["status"] not in ("running", "ready"):
                return False, f"cannot request review for {task_id}: not running/ready? (status={row['status']})"
            if why := self._not_mine(row, "request-review"):
                return False, why
            if not self._parents_done(cur, task_id):
                return False, "parent dependencies are not satisfied"
            implementer = row["assignee"]
            run_id = self._end_run(cur, task_id, status="review", outcome="review_requested",
                                   summary=(summary or "").strip() or None, metadata=metadata, synthesize=True)
            cur.execute("UPDATE hub.tasks SET status = 'review', lease_owner = NULL, lease_until = NULL, "
                        "updated_at = now() WHERE id = %s", (task_id,))
            self._event(cur, task_id, "review_requested", {"summary": summary, "implementer": implementer},
                        run_id=run_id)
            return True, ""
        out = self._write("request-review", run, default=None)
        return out if out is not None else (False, self.last_error or "kanban no disponible")

    def complete(self, task_id: str, result: str, metadata: dict) -> tuple[bool, str]:
        def run(cur):
            row = self._row(cur, task_id, lock=True)
            if row is None or row["status"] not in ("running", "ready", "blocked", "review"):
                return False, f"cannot complete {task_id} (unknown id or terminal state)"
            if why := self._not_mine(row, "complete"):
                return False, why
            if not self._parents_done(cur, task_id):
                return False, f"cannot complete {task_id}: unsatisfied parent dependencies"
            if row["status"] != "review" and not (result or "").strip():
                return False, f"cannot complete {task_id}: empty completion"
            run_id = self._end_run(cur, task_id, status="done", outcome="completed", summary=result,
                                   metadata=metadata, synthesize=True)
            cur.execute("UPDATE hub.tasks SET status = 'done', result = %s, completed_at = now(), "
                        "lease_owner = NULL, lease_until = NULL, block_kind = NULL, block_recurrences = 0, "
                        "updated_at = now() WHERE id = %s", (result, task_id))
            self._event(cur, task_id, "completed", {"result": result, "summary": result}, run_id=run_id)
            self._recompute_ready(cur)
            return True, ""
        out = self._write("complete", run, default=None)
        return out if out is not None else (False, self.last_error or "kanban no disponible")

    def reopen_review(self, task_id: str) -> bool:
        def run(cur):
            row = self._row(cur, task_id, lock=True)
            if row is None or row["status"] != "review":
                return False
            if self._not_mine(row, "reopen-review"):
                return False
            new_status = self._landing(cur, task_id)
            cur.execute("SELECT payload FROM hub.task_events WHERE task_id = %s AND kind = 'review_requested' "
                        "ORDER BY created_at DESC, id DESC LIMIT 1", (task_id,))
            ev = cur.fetchone()
            implementer = ((_obj(ev[0]) or {}).get("implementer") if ev else None) or None
            cur.execute("UPDATE hub.tasks SET status = %s, assignee = COALESCE(%s, assignee), current_run_id = NULL, "
                        "lease_owner = NULL, lease_until = NULL, updated_at = now() WHERE id = %s",
                        (new_status, implementer, task_id))
            payload = {"status": new_status, **({"implementer": implementer} if implementer else {})}
            self._event(cur, task_id, "review_reopened", payload if payload != {"status": "ready"} else None)
            return True
        return self._write("reopen-review", run)

    def _assign(self, cur, task_id: str, profile: str | None, reason: str | None = None) -> bool:
        row = self._row(cur, task_id, lock=True)
        if row is None or (row["status"] == "running" and row["lease_owner"] is not None):
            return False
        profile = (profile or "").strip().lower() or None
        cur.execute("UPDATE hub.tasks SET assignee = %s, updated_at = now() WHERE id = %s", (profile, task_id))
        payload = {"assignee": profile, "from": row["assignee"], **({"reason": reason} if reason else {})}
        self._event(cur, task_id, "assigned", payload)
        return True

    def assign(self, task_id: str, profile: str) -> bool:
        return self._write("assign", lambda cur: self._assign(cur, task_id, profile))

    def reassign(self, task_id: str, profile: str, reason: str) -> bool:
        return self._write("reassign", lambda cur: self._assign(cur, task_id, profile, reason))

    def unblock(self, task_id: str) -> bool:
        def run(cur):
            row = self._row(cur, task_id, lock=True)
            if row is None or row["status"] not in ("blocked", "scheduled"):
                return False
            new_status = self._landing(cur, task_id)
            # block_kind/block_recurrences sobreviven al unblock (como en Hermes): solo complete los limpia.
            cur.execute("UPDATE hub.tasks SET status = %s, current_run_id = NULL, updated_at = now() WHERE id = %s",
                        (new_status, task_id))
            self._event(cur, task_id, "unblocked", {"status": new_status} if new_status != "ready" else None)
            return True
        return self._write("unblock", run)

    def set_priority(self, task_id: str, priority: int) -> bool:
        def run(cur):
            cur.execute("UPDATE hub.tasks SET priority = %s, updated_at = now() WHERE id = %s AND board = %s",
                        (int(priority), task_id, self.board))
            if cur.rowcount != 1:
                return False
            self._event(cur, task_id, "edited", {"fields": ["priority"]})
            return True
        return self._write("edit", run)

    def link(self, parent_id: str, child_id: str) -> bool:
        from .hermes import _TID_RE
        if not (_TID_RE.match(parent_id or "") and _TID_RE.match(child_id or "")) or parent_id == child_id:
            return False

        def run(cur):
            cur.execute("SELECT id, status FROM hub.tasks WHERE board = %s AND id = ANY(%s)",
                        (self.board, [parent_id, child_id]))
            st = dict(cur.fetchall())
            if len(st) != 2 or self._ancestor(cur, child_id, parent_id):
                return False  # desconocida o ciclo
            cur.execute("UPDATE hub.tasks SET parents = array_append(parents, %s), updated_at = now() "
                        "WHERE id = %s AND NOT (%s = ANY(parents))", (parent_id, child_id, parent_id))
            if st[parent_id] not in TERMINAL and st[child_id] == "ready":
                cur.execute("UPDATE hub.tasks SET status = 'todo' WHERE id = %s", (child_id,))
            self._event(cur, child_id, "linked", {"parent": parent_id})
            return True
        return self._write("link", run)

    def unlink(self, parent_id: str, child_id: str) -> bool:
        """Quita la dependencia padre -> hija (lo contrario de `link`); si la hija esperaba solo a ese padre, pasa a ready."""
        from .hermes import _TID_RE
        if not (_TID_RE.match(parent_id or "") and _TID_RE.match(child_id or "")):
            return False

        def run(cur):
            cur.execute("UPDATE hub.tasks SET parents = array_remove(parents, %s), updated_at = now() "
                        "WHERE id = %s AND board = %s AND %s = ANY(parents)", (parent_id, child_id, self.board, parent_id))
            if cur.rowcount != 1:
                return False
            cur.execute("UPDATE hub.tasks SET status = 'ready' WHERE id = %s AND status = 'todo' AND NOT EXISTS ("
                        "SELECT 1 FROM hub.tasks p WHERE p.id = ANY(hub.tasks.parents) "
                        "AND p.status NOT IN ('done', 'archived'))", (child_id,))
            self._event(cur, child_id, "unlinked", {"parent": parent_id})
            return True
        return self._write("unlink", run)

    @staticmethod
    def _ancestor(cur, candidate: str, of: str) -> bool:
        """¿`candidate` es `of` o uno de sus ancestros? (enlazar of -> candidate crearía un ciclo)."""
        cur.execute("WITH RECURSIVE up(id) AS (SELECT %s::text UNION SELECT unnest(t.parents) FROM hub.tasks t "
                    "JOIN up ON t.id = up.id) SELECT 1 FROM up WHERE id = %s LIMIT 1", (of, candidate))
        return cur.fetchone() is not None

    def archive(self, task_id: str) -> bool:
        def run(cur):
            row = self._row(cur, task_id, lock=True)
            if row is None or row["status"] == "archived":
                return False
            run_id = self._end_run(cur, task_id, status="reclaimed", outcome="reclaimed",
                                   summary="task archived with run still active") \
                if row["current_run_id"] is not None else None
            cur.execute("UPDATE hub.tasks SET status = 'archived', lease_owner = NULL, lease_until = NULL, "
                        "updated_at = now() WHERE id = %s", (task_id,))
            self._event(cur, task_id, "archived", None, run_id=run_id)
            self._recompute_ready(cur)
            return True
        return self._write("archive", run)

    def requeue_triage(self, task_id: str, author: str = OSCAR_AUTHOR, **_ignored) -> str | None:
        """triage -> todo (y a ready si no espera a un padre) sin tocar título ni cuerpo; estado resultante o None."""
        def run(cur):
            row = self._row(cur, task_id, lock=True)
            if row is None or row["status"] != "triage":
                self.last_error = "Hermes rechazó specify_triage_task (¿ya no está en triage?)"
                return None
            cur.execute("UPDATE hub.tasks SET status = 'todo', updated_at = now() WHERE id = %s", (task_id,))
            self._event(cur, task_id, "specified")
            self._comment(cur, task_id, author,
                          "Reintentada por Oscar desde Telegram: sale de triage (bloqueo repetido) sin cambios")
            self._recompute_ready(cur)
            cur.execute("SELECT status FROM hub.tasks WHERE id = %s", (task_id,))
            return cur.fetchone()[0]
        out = self._write("requeue-triage", run, default=None)
        return out

    # --- suscripciones de aviso (hub.kv) -------------------------------------------------------------------------
    def _subs(self, cur, task_id: str, lock: bool = False) -> list[dict]:
        cur.execute("SELECT value FROM hub.kv WHERE key = %s" + (" FOR UPDATE" if lock else ""),
                    (NOTIFY_KV + task_id,))
        r = cur.fetchone()
        return list(_obj(r[0]) or []) if r else []

    def _save_subs(self, cur, task_id: str, subs: list[dict]) -> None:
        cur.execute("INSERT INTO hub.kv (key, value, updated_at) VALUES (%s, %s::jsonb, now()) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                    (NOTIFY_KV + task_id, _js(subs)))

    def subscribe_telegram(self, task_id: str, chat: str, thread: str = "0") -> bool:
        thread = "" if str(thread) in ("", "0") else str(thread)

        def run(cur):
            if self._row(cur, task_id) is None:
                return False
            subs = [s for s in self._subs(cur, task_id, lock=True)
                    if not (s.get("platform") == "telegram" and s.get("chat_id") == str(chat)
                            and (s.get("thread_id") or "") == thread)]
            subs.append({"task_id": task_id, "platform": "telegram", "chat_id": str(chat), "thread_id": thread,
                         "delivery_mode": "notify", "created_at": self._server_now(cur), "last_event_id": 0})
            self._save_subs(cur, task_id, subs)
            return True
        return self._write("notify-subscribe", run)

    def notify_list(self, task_id: str) -> list[dict]:
        return self._read("notify-list", lambda cur: self._subs(cur, task_id))

    def drop_telegram_subs(self, task_id: str) -> list[str]:
        """Igual que HermesCLI.drop_telegram_subs: quita las de Telegram que no son `notify` y devuelve los destinos."""
        def run(cur):
            dropped, keep = [], []
            for sub in self._subs(cur, task_id, lock=True):
                if sub.get("platform") != "telegram" or sub.get("delivery_mode") == "notify":
                    keep.append(sub)
                    continue
                chat, thread = str(sub.get("chat_id") or ""), str(sub.get("thread_id") or "")
                dropped.append(f"{chat}:{thread}" if thread else chat)
            if dropped:
                self._save_subs(cur, task_id, keep)
            return dropped
        try:
            with self._tx() as cur:
                return run(cur)
        except _DBFailure as exc:
            raise HermesError(f"notify-list {task_id} failed: {exc}") from exc

    # --- salud ---------------------------------------------------------------------------------------------------
    REQUIRED = {
        "tasks": ("id", "board", "title", "body", "status", "assignee", "priority", "parents", "idempotency_key",
                  "lease_owner", "lease_until", "lease_ttl", "attempts", "created_by", "created_by_worker", "alcance",
                  "rama", "result", "block_kind", "block_recurrences", "current_run_id", "started_at",
                  "completed_at", "created_at", "updated_at", "extra"),
        "task_comments": ("id", "task_id", "author_id", "worker_id", "body", "created_at"),
        "task_events": ("id", "task_id", "run_id", "kind", "payload", "created_at"),
        "task_runs": ("id", "task_id", "profile", "step_key", "status", "outcome", "summary", "metadata", "error",
                      "worker_pid", "lease_owner", "lease_until", "started_at", "ended_at"),
        "kv": ("key", "value", "updated_at"),
        "workers": ("id", "activo", "externo", "version", "last_seen"),  # H3/H11: alta, interruptor, sello del guard
        "ajustes": ("clave", "valor"),  # workers_externos (H3) y guard_hash_aprobado (H11)
    }

    def check_contract(self) -> list[str]:
        """Problemas del esquema `hub` frente a lo que usa este backend; vacío = listo."""
        def run(cur):
            cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = 'hub'")
            have: dict[str, set] = {}
            for t, c in cur.fetchall():
                have.setdefault(t, set()).add(c)
            problems = []
            for table, cols in self.REQUIRED.items():
                if table not in have:
                    problems.append(f"falta la tabla hub.{table}")
                    continue
                problems += [f"hub.{table} sin columna {c}" for c in cols if c not in have[table]]
            return problems
        try:
            with self._tx() as cur:
                return run(cur)
        except _DBFailure as exc:
            return [f"kanban supabase no disponible: {exc}"]
