"""H3 y H4 sin base de datos: un cursor que GRABA el SQL que emite `SupabaseKanban` (corre siempre, también sin Postgres).

Comprueba lo que se manda al servidor, no su resultado: un worker sin permiso no escribe nada, y ningún SQL usa la hora
del cliente. El comportamiento con Postgres de verdad está en test_kanban_multiworker_pg.py (HUB_TEST_EMBEDDED=1)."""
from agent_lanes.kanban_api import SupabaseKanban


class Cursor:
    """Responde por trozos del SQL (`reglas`: lista de (trozo, fila | None)); graba todo lo que se ejecuta."""

    def __init__(self, conn):
        self.conn, self.rowcount, self._row = conn, 1, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.conn.log.append((" ".join(sql.split()), tuple(params or ())))
        self._row = None
        for trozo, fila in self.conn.reglas:
            if trozo in sql:
                self._row = fila
                break

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [] if self._row is None else [self._row]


class Conn:
    closed = 0

    def __init__(self, reglas):
        self.reglas, self.log = reglas, []

    def cursor(self):
        return Cursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        self.closed = 1


READY = ("ready", "claude-hub", None, None, 0, None, [], False)  # status, assignee, owner, block_kind, ..., lease_live


def kanban(reglas, **kw):
    conn = Conn(reglas)
    return SupabaseKanban("oscarhq", dsn="x", connect=lambda dsn: conn, worker="pc-b", **kw), conn


def escribe(conn):
    return [q for q, _ in conn.log if q.startswith(("UPDATE hub.tasks", "INSERT INTO hub.task_runs",
                                                    "INSERT INTO hub.task_events"))]


# --- H3: sin permiso no se escribe nada --------------------------------------------------------------------------------------------------
def test_worker_inactivo_no_reclama_ni_escribe():
    k, conn = kanban([("to_regclass", (True,)), ("SELECT activo, externo", (False, False))])
    assert k.claim("t_00000001", 60) is False and "desactivado" in k.last_error
    assert escribe(conn) == [] and not any("FOR UPDATE" in q for q, _ in conn.log)  # ni siquiera bloquea la fila


def test_worker_sin_alta_no_reclama_ni_escribe():
    k, conn = kanban([("to_regclass", (True,)), ("SELECT activo, externo", None)])
    assert k.claim("t_00000001", 60) is False and "sin alta" in k.last_error and escribe(conn) == []


def test_externo_con_el_interruptor_apagado_no_escribe():
    k, conn = kanban([("to_regclass", (True,)), ("SELECT activo, externo", (True, True)),
                      ("SELECT valor FROM hub.ajustes", (False,))])
    assert k.claim("t_00000001", 60) is False and "externos" in k.last_error and escribe(conn) == []


def test_sin_tabla_de_workers_no_reclama():
    k, conn = kanban([("to_regclass", (False,))])
    assert k.claim("t_00000001", 60) is False and "hub.workers" in k.last_error and escribe(conn) == []


def test_serial_y_normal_pasan_por_la_misma_admision():
    for fn in (lambda k: k.claim("t_00000001", 60), lambda k: k.claim_serial("t_00000001", 60, ("supabase/migrations",))):
        k, conn = kanban([("to_regclass", (True,)), ("SELECT activo, externo", (False, False))])
        assert fn(k) is False and escribe(conn) == []


# --- H4: nada de lo que se guarda usa la hora del cliente ----------------------------------------------------------------------------------
def reglas_claim_admitido():
    return [("to_regclass", (True,)), ("SELECT activo, externo", (True, False)),
            ("SELECT valor FROM hub.ajustes", None),
            ("FROM hub.tasks WHERE id = %s AND board = %s FOR UPDATE", READY),
            ("SELECT 1 FROM hub.tasks c JOIN", None),  # _parents_done: sin padres pendientes
            ("INSERT INTO hub.task_runs", (7, 1_900_000_000)),
            ("INSERT INTO hub.task_events", None)]


def clock_prohibido():
    raise AssertionError("el reloj del cliente no debe usarse para nada que se guarde")


def test_claim_no_usa_la_hora_del_cliente():
    k, conn = kanban(reglas_claim_admitido(), clock=clock_prohibido)
    assert k.claim("t_00000001", 600) is True, k.last_error
    sqls = " ".join(q for q, _ in conn.log)
    assert "to_timestamp" not in sqls and "now() + make_interval" in sqls  # lease con el reloj del servidor
    run = next(p for q, p in conn.log if q.startswith("INSERT INTO hub.task_runs"))
    assert run == ("t_00000001", "claude-hub", "pc-b", 600)  # el único número es el ttl, no una hora
    upd = next(p for q, p in conn.log if q.startswith("UPDATE hub.tasks SET status = 'running'"))
    assert upd == ("pc-b", 600, 600, 7, "t_00000001")


def test_eventos_comentarios_y_cierres_no_llevan_hora_del_cliente():
    k, conn = kanban([("FROM hub.tasks WHERE id = %s AND board = %s", ("running", "claude-hub", "pc-b", None, 0, 5, [],
                                                                       True)),
                      ("SELECT 1 FROM hub.tasks c JOIN", None), ("SELECT current_run_id, assignee", (5, "claude-hub")),
                      ("INSERT INTO hub.task_runs", (9,))], clock=clock_prohibido)
    k.comment("t_00000001", "nota")
    k.heartbeat("t_00000001")
    ok, _ = k.complete("t_00000001", "hecho", {})
    assert ok
    sqls = " ".join(q for q, _ in conn.log)
    assert "to_timestamp" not in sqls
    for insercion in (q for q, _ in conn.log if q.startswith(("INSERT INTO hub.task_events", "INSERT INTO hub.task_comments"))):
        assert "created_at" not in insercion  # el DEFAULT now() del servidor, y encaja con los GRANT de hub_core
    assert "completed_at = now()" in sqls


def test_crear_una_tarjeta_usa_now_del_servidor():
    k, conn = kanban([("SELECT id FROM hub.tasks WHERE board = %s AND idempotency_key", None),
                      ("INSERT INTO hub.tasks", ("t_deadbeef",)), ("INSERT INTO hub.task_events", None)],
                     clock=clock_prohibido)
    k.create("[Hub] Prueba · paso", "cuerpo", "claude-hub")
    insercion = next(q for q, _ in conn.log if q.startswith("INSERT INTO hub.tasks"))
    assert "to_timestamp" not in insercion and insercion.count("now()") == 2
