"""Varios ordenadores sobre el mismo kanban supabase (auditoría del segundo worker, 19-11): H1, H3, H4, H5, H6, H10, H11.

Contra Postgres de verdad (HUB_TEST_DSN o pgserver embebido, ver tests/hubpg.py): los «workers» son clientes
`SupabaseKanban` con distinto `worker`, cada uno con SU reloj (a veces desfasado una hora)."""
import threading
import time

import pytest

from agent_lanes.kanban_api import SupabaseKanban, normalize_worker, worker_id_problem
from tests.hubpg import hub_dsn  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("hub_dsn")


def make(dsn, worker, board="oscarhq", **kw):
    return SupabaseKanban(board, author="agent-lanes", dsn=dsn, worker=worker, **kw)


def sql(kb, query, params=(), fetch=False):
    with kb._tx() as cur:
        cur.execute(query, params)
        return cur.fetchall() if fetch else None


def add_worker(kb, wid, *, activo=True, externo=False):
    sql(kb, "INSERT INTO hub.workers (id, activo, externo) VALUES (%s, %s, %s)", (wid, activo, externo))


def envejecer(kb, tid, segundos=300):
    """La reserva caducó hace `segundos` (según el reloj del SERVIDOR)."""
    sql(kb, "UPDATE hub.tasks SET lease_until = now() - make_interval(secs => %s) WHERE id = %s", (segundos, tid))


def status_owner(kb, tid):
    return sql(kb, "SELECT status, lease_owner FROM hub.tasks WHERE id = %s", (tid,), fetch=True)[0]


@pytest.fixture
def a(hub_dsn):  # noqa: F811
    k = make(hub_dsn, "pc-a")
    add_worker(k, "pc-a")
    return k


@pytest.fixture
def b(hub_dsn, a):  # noqa: F811
    k = make(hub_dsn, "pc-b")
    add_worker(k, "pc-b", externo=True)
    sql(k, "UPDATE hub.ajustes SET valor = 'true'::jsonb WHERE clave = 'workers_externos'")
    return k


def nueva(kb, titulo="[Hub] prueba", **kw):
    return kb.create(titulo, "cuerpo", "claude-hub", **kw)


# --- H1: la tarjeta dice de quién es la reserva -----------------------------------------------------------------------
def test_tarjeta_running_expone_dueno_y_caducidad_con_la_hora_del_servidor(a):
    tid = nueva(a)
    assert a.claim(tid, 600)
    [t] = a.list_status("claude-hub", "running")
    assert t["lease_owner"] == "pc-a" and t["lease_expired"] is False
    assert t["lease_until"] - t["server_now"] in range(595, 606)
    envejecer(a, tid)
    [t] = a.list_status("claude-hub", "running")
    assert t["lease_owner"] == "pc-a" and t["lease_expired"] is True and t["lease_until"] < t["server_now"]
    assert a.show(tid)["task"]["lease_owner"] == "pc-a"


def test_tarjeta_sin_reserva_no_tiene_dueno(a):
    nueva(a)
    [t] = a.list_status("claude-hub", "ready")
    assert t["lease_owner"] is None and t["lease_until"] is None and t["lease_expired"] is False


# --- H3: admisión del worker -------------------------------------------------------------------------------------------
def test_claim_exige_alta_y_activo(a, hub_dsn):  # noqa: F811
    tid = nueva(a)
    ghost = make(hub_dsn, "pc-fantasma")
    assert ghost.claim(tid, 60) is False and "sin alta" in ghost.last_error
    add_worker(a, "pc-apagado", activo=False)
    off = make(hub_dsn, "pc-apagado")
    assert off.claim(tid, 60) is False and "desactivado" in off.last_error
    assert status_owner(a, tid) == ("ready", None)  # ninguno tocó la tarjeta
    assert a.claim(tid, 60) is True


def test_workers_externos_solo_con_el_interruptor(a, hub_dsn):  # noqa: F811
    add_worker(a, "pc-maria", externo=True)
    maria = make(hub_dsn, "pc-maria")
    tid = nueva(a)
    assert maria.claim(tid, 60) is False and "externos" in maria.last_error  # ajuste en false
    sql(a, "UPDATE hub.ajustes SET valor = 'true'::jsonb WHERE clave = 'workers_externos'")
    assert maria.claim(tid, 60) is True
    sql(a, "UPDATE hub.ajustes SET valor = 'false'::jsonb WHERE clave = 'workers_externos'")
    otra = nueva(a, "[Hub] otra")
    assert maria.claim(otra, 60) is False  # el kill-switch vale también con el worker ya en marcha
    assert a.claim(otra, 60) is True  # el interno no depende del interruptor


def test_ttl_invalido_no_reclama(a):
    tid = nueva(a)
    assert a.claim(tid, 0) is False and "ttl" in a.last_error
    assert status_owner(a, tid) == ("ready", None)


def test_id_de_worker_normalizado_y_validado():
    assert normalize_worker("  DESKTOP-ABC ") == "desktop-abc"
    assert worker_id_problem("desktop-abc") is None
    assert worker_id_problem("Mi PC") and worker_id_problem("") and worker_id_problem("a")
    assert SupabaseKanban("x", dsn="d", worker="PC-Mayusculas").worker == "pc-mayusculas"


# --- dos workers a la vez ------------------------------------------------------------------------------------------------
def test_dos_workers_reclaman_cada_uno_su_tarjeta_y_ninguno_la_del_otro(a, b):
    t1, t2 = nueva(a, "[Hub] uno"), nueva(a, "[Hub] dos")
    assert a.claim(t1, 600) is True
    assert b.claim(t1, 600) is False  # ya es de A
    assert b.claim(t2, 600) is True
    assert status_owner(a, t1) == ("running", "pc-a") and status_owner(a, t2) == ("running", "pc-b")
    owners = {t["id"]: t["lease_owner"] for t in a.list_status("claude-hub", "running")}
    assert owners == {t1: "pc-a", t2: "pc-b"}


def test_ocho_workers_a_la_vez_una_sola_reserva(a, hub_dsn):  # noqa: F811
    for i in range(7):
        add_worker(a, f"w{i}")
    tid = nueva(a)
    wins, barrier = [], threading.Barrier(8)

    def go(i):
        k = make(hub_dsn, "pc-a" if i == 7 else f"w{i}")
        barrier.wait()
        wins.append(k.claim(tid, 60))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert wins.count(True) == 1


# --- H4: la hora la pone el servidor --------------------------------------------------------------------------------------
@pytest.mark.parametrize("desfase", [3600, -3600, 86400])
def test_reloj_desfasado_no_acorta_ni_alarga_la_reserva(hub_dsn, a, desfase):  # noqa: F811
    skewed = make(hub_dsn, "pc-a", clock=lambda: time.time() + desfase)
    tid = nueva(skewed)
    assert skewed.claim(tid, 600)
    [(restante,)] = sql(a, "SELECT extract(epoch from lease_until - now())::int FROM hub.tasks WHERE id = %s", (tid,),
                        fetch=True)
    assert 590 <= restante <= 600  # ni nace caducada (reloj atrasado) ni dura horas de más (adelantado)
    assert skewed.release_stale(3) == []  # el segador no la toca: está viva
    assert skewed.heartbeat(tid)
    [(restante,)] = sql(a, "SELECT extract(epoch from lease_until - now())::int FROM hub.tasks WHERE id = %s", (tid,),
                        fetch=True)
    assert 590 <= restante <= 600
    skewed.comment(tid, "nota")
    # eventos, comentarios, runs y tarjeta llevan fecha del servidor aunque el cliente vaya a otra hora
    fechas = sql(a, "SELECT min(abs(extract(epoch from created_at - now()))) FROM (SELECT created_at FROM "
                    "hub.task_events WHERE task_id = %s UNION ALL SELECT created_at FROM hub.task_comments "
                    "WHERE task_id = %s UNION ALL SELECT started_at FROM hub.task_runs WHERE task_id = %s "
                    "UNION ALL SELECT started_at FROM hub.tasks WHERE id = %s) x", (tid, tid, tid, tid), fetch=True)
    assert fechas[0][0] < 60
    for col, tabla, where in (("created_at", "task_events", "task_id"), ("created_at", "task_comments", "task_id"),
                              ("started_at", "task_runs", "task_id")):
        [(maximo,)] = sql(a, f"SELECT max(abs(extract(epoch from {col} - now()))) FROM hub.{tabla} WHERE {where} = %s",
                          (tid,), fetch=True)
        assert maximo < 60, (tabla, maximo)
    [t] = skewed.list_status("claude-hub", "running")
    assert abs(t["created_at"] - t["server_now"]) < 60 and abs(t["started_at"] - t["server_now"]) < 60


def test_complete_y_create_usan_la_hora_del_servidor(hub_dsn, a):  # noqa: F811
    skewed = make(hub_dsn, "pc-a", clock=lambda: time.time() - 7200)
    tid = nueva(skewed)
    skewed.claim(tid, 60)
    assert skewed.complete(tid, "hecho", {})[0]
    t = skewed.show(tid)["task"]
    assert abs(t["completed_at"] - int(time.time())) < 60 and abs(t["created_at"] - int(time.time())) < 60


# --- H5: el latido y los cierres respetan al dueño ----------------------------------------------------------------------
def test_heartbeat_solo_renueva_la_propia(a, b):
    tid = nueva(a)
    assert a.claim(tid, 600)
    envejecer(a, tid, 5)
    assert b.heartbeat(tid) is False and b.lease_lost(tid) is True  # B no puede alargar la reserva de A
    [(restante,)] = sql(a, "SELECT extract(epoch from lease_until - now())::int FROM hub.tasks WHERE id = %s", (tid,),
                        fetch=True)
    assert restante < 0
    assert a.heartbeat(tid) is True and a.lease_lost(tid) is False
    [(restante,)] = sql(a, "SELECT extract(epoch from lease_until - now())::int FROM hub.tasks WHERE id = %s", (tid,),
                        fetch=True)
    assert restante > 590


def test_dueno_que_perdio_la_reserva_se_entera_y_no_cierra_la_tarjeta_de_otro(a, b):
    tid = nueva(a)
    assert a.claim(tid, 600)
    envejecer(a, tid)  # A se cuelga (suspensión, red) y su reserva caduca
    assert [r["task_id"] for r in b.release_stale(3)] == [tid]
    assert b.unblock(tid) and b.claim(tid, 600)  # reintento: ahora es de B
    assert a.heartbeat(tid) is False and a.lease_lost(tid) is True
    assert "pc-b" in a.last_error
    assert a.block(tid, "transient", "tarde") is False and "pc-b" in a.last_error
    ok, why = a.request_review(tid, "resumen", {})
    assert ok is False and "pc-b" in why
    ok, why = a.complete(tid, "resultado", {})
    assert ok is False and "pc-b" in why
    assert status_owner(a, tid) == ("running", "pc-b")  # nada cambió
    assert b.heartbeat(tid) and b.request_review(tid, "resumen de B", {})[0]  # el dueño sí
    assert status_owner(a, tid) == ("review", None)


def test_fallo_de_base_no_se_confunde_con_reserva_perdida(a, hub_dsn):  # noqa: F811
    class Roto:
        closed = 0

        def cursor(self):
            raise OSError("red caída")

        def rollback(self):
            pass

        def close(self):
            self.closed = 1

    tid = nueva(a)
    a.claim(tid, 600)
    k = make(hub_dsn, "pc-a", connect=lambda dsn: Roto())
    assert k.heartbeat(tid) is False and k.lease_lost(tid) is False and "red caída" in k.last_error


def test_la_reserva_caducada_de_otro_la_puede_cerrar_cualquiera_la_viva_no(a, b):
    tid = nueva(a)
    a.claim(tid, 600)
    assert b.block(tid, "needs_input", "¿b?") is False  # viva y de A: se respeta
    envejecer(a, tid)
    assert b.block(tid, "needs_input", "¿b?") is True  # caducada: abandonada, no pisa a nadie
    assert status_owner(a, tid) == ("blocked", None)


def test_sin_reserva_nada_cambia_para_quien_cierra(a, b):
    """review/blocked/ready no tienen dueño: el integrador, Telegram y los carriles de review siguen igual."""
    tid = nueva(a)
    a.claim(tid, 60)
    assert a.request_review(tid, "r", {})[0]
    assert b.reopen_review(tid)  # review sin dueño: la reabre quien sea (revisor, Telegram)
    assert status_owner(a, tid) == ("ready", None)
    tid2 = nueva(a, "[Hub] dos")
    assert b.block(tid2, "needs_input", "q")  # ready sin dueño: se puede bloquear
    assert a.unblock(tid2)
    a.claim(tid2, 60)
    a.request_review(tid2, "r", {})
    assert b.complete(tid2, "validada", {})[0] is True  # review sin dueño: la valida quien sea


# --- H6: el segador --------------------------------------------------------------------------------------------------------
def test_release_stale_libera_solo_lo_caducado_con_rastro_en_la_tarjeta(a, b):
    vivo, muerto = nueva(a, "[Hub] vivo"), nueva(a, "[Hub] muerto")
    a.claim(vivo, 600)
    b.claim(muerto, 600)
    envejecer(a, muerto)
    rows = a.release_stale(3)
    assert rows == [{"task_id": muerto, "status": "blocked", "attempts": 1, "worker_id": "pc-b"}]
    assert status_owner(a, vivo) == ("running", "pc-a") and status_owner(a, muerto) == ("blocked", None)
    show = a.show(muerto)
    assert show["events"][-1]["kind"] == "lease_expired" and show["events"][-1]["payload"]["worker"] == "pc-b"
    assert show["comments"][-1]["body"].startswith("BLOCKED: la reserva de pc-b caducó")
    assert show["runs"][-1]["status"] == "released" and show["runs"][-1]["outcome"] == "reclaimed"
    assert a.release_stale(3) == []  # idempotente


def test_release_stale_sube_a_triage_con_el_tope_configurable(a):
    tid = nueva(a)
    for esperado in ("blocked", "blocked", "triage"):
        if status_owner(a, tid)[0] == "blocked":
            assert a.unblock(tid)
        assert a.claim(tid, 600)
        envejecer(a, tid)
        [r] = a.release_stale(3)
        assert r["status"] == esperado
    otra = nueva(a, "[Hub] otra")
    a.claim(otra, 600)
    envejecer(a, otra)
    assert a.release_stale(1)[0]["status"] == "triage"  # tope 1: a la primera


def test_dos_segadores_a_la_vez_liberan_cada_tarjeta_una_vez(a, hub_dsn):  # noqa: F811
    ids = [nueva(a, f"[Hub] t{i}") for i in range(6)]
    for tid in ids:
        a.claim(tid, 600)
        envejecer(a, tid)
    out, barrier = [], threading.Barrier(4)

    def go():
        k = make(hub_dsn, "pc-a")
        barrier.wait()
        out.append(k.release_stale(3))

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    liberadas = [r["task_id"] for rows in out for r in rows]
    assert sorted(liberadas) == sorted(ids)  # ninguna dos veces (SKIP LOCKED)
    assert all(a.show(t)["task"]["status"] == "blocked" for t in ids)
    assert len([e for t in ids for e in a.show(t)["events"] if e["kind"] == "lease_expired"]) == len(ids)


def test_release_stale_usa_la_funcion_del_esquema_real_si_existe(a):
    sql(a, "CREATE FUNCTION hub.release_stale(p_max integer DEFAULT 3) RETURNS TABLE (task_id text, status text, "
           "attempts integer, worker_id text) LANGUAGE sql AS $$ UPDATE hub.tasks SET status = 'triage', "
           "lease_owner = NULL, lease_until = NULL, current_run_id = NULL WHERE status = 'running' AND lease_until < "
           "now() RETURNING id, status, 99, 'x' $$")
    tid = nueva(a)
    a.claim(tid, 600)
    envejecer(a, tid)
    assert a.release_stale(3) == [{"task_id": tid, "status": "triage", "attempts": 99, "worker_id": "x"}]
    assert a.show(tid)["events"][-1]["kind"] == "lease_expired"  # el rastro lo añade el envoltorio


def test_release_stale_con_la_base_caida_no_lanza(hub_dsn):  # noqa: F811
    class Roto:
        closed = 0

        def cursor(self):
            raise OSError("red caída")

        def rollback(self):
            pass

        def close(self):
            pass

    k = make(hub_dsn, "pc-a", connect=lambda dsn: Roto())
    assert k.release_stale(3) == [] and "red caída" in k.last_error


# --- H10: rutas serializadas entre PC ------------------------------------------------------------------------------------------
MIGRA = ("supabase/migrations",)


def test_claim_serial_rechaza_la_segunda_tarjeta_de_migraciones(a, b):
    m1 = a.create("[Hub] migración uno", "toca supabase/migrations/x.sql", "claude-oscarhq")
    m2 = a.create("[Hub] migración dos", "toca supabase/migrations/y.sql", "claude-oscarhq")
    otra = a.create("[Hub] otra cosa", "docs", "claude-oscarhq")
    assert a.claim_serial(m1, 600, MIGRA) is True
    assert b.claim_serial(m2, 600, MIGRA) is False
    assert b.show(m2)["events"][-1]["payload"] == {"reason": "serial_paths", "running": [m1]}
    assert status_owner(a, m2) == ("ready", None)
    assert b.claim_serial(otra, 600, ()) is True  # sin rutas serializadas no se frena a nadie
    assert a.complete(m1, "hecha", {})[0]
    assert b.claim_serial(m2, 600, MIGRA) is True


def test_claim_serial_dos_pc_a_la_vez_gana_uno(a, b, hub_dsn):  # noqa: F811
    for ronda in range(5):
        ids = [a.create(f"[Hub] mig {ronda}-{i}", "toca supabase/migrations", "claude-oscarhq") for i in range(2)]
        res, barrier = {}, threading.Barrier(2)

        def go(wid, tid):
            k = make(hub_dsn, wid)
            barrier.wait()
            res[wid] = k.claim_serial(tid, 600, MIGRA)

        ts = [threading.Thread(target=go, args=(w, t)) for w, t in zip(("pc-a", "pc-b"), ids)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sorted(res.values()) == [False, True], (ronda, res)
        for t in ids:  # deja el carril libre para la ronda siguiente
            if status_owner(a, t)[0] == "running":
                a.complete(t, "ok", {}) if status_owner(a, t)[1] == "pc-a" else b.complete(t, "ok", {})
            else:
                a.archive(t)


# --- H11: sello del guard --------------------------------------------------------------------------------------------------------
def test_guard_aprobado_en_ajustes_obliga_a_llevar_el_sello(a, hub_dsn):  # noqa: F811
    tid = nueva(a)
    sin = make(hub_dsn, "pc-a")
    assert sin.claim(tid, 60) is True  # sin el ajuste no se exige sello
    sin.block(tid, "transient", "x")
    sin.unblock(tid)
    sql(a, "INSERT INTO hub.ajustes (clave, valor) VALUES ('guard_hash_aprobado', '[\"abc123\"]'::jsonb)")
    assert sin.claim(tid, 60) is False and "guard" in sin.last_error
    viejo = make(hub_dsn, "pc-a", guard_digest="zzz")
    assert viejo.claim(tid, 60) is False and "zzz" in viejo.last_error
    bueno = make(hub_dsn, "pc-a", guard_digest="abc123")
    assert bueno.claim(tid, 60) is True
    sql(a, "UPDATE hub.ajustes SET valor = '\"solo-uno\"'::jsonb WHERE clave = 'guard_hash_aprobado'")  # texto suelto
    otra = nueva(a, "[Hub] otra")
    assert make(hub_dsn, "pc-a", guard_digest="solo-uno").claim(otra, 60) is True


def test_register_worker_escribe_sello_y_latido(a, hub_dsn):  # noqa: F811
    k = make(hub_dsn, "pc-a", guard_digest="sello-1")
    assert k.register_worker() is True
    [(version, visto)] = sql(a, "SELECT version, last_seen IS NOT NULL FROM hub.workers WHERE id = 'pc-a'", fetch=True)
    assert version == "sello-1" and visto is True
    assert make(hub_dsn, "no-existe").register_worker() is False  # sin alta no inventa nada
    assert make(hub_dsn, "pc-a").register_worker() is True  # sin sello no borra el que había
    assert sql(a, "SELECT version FROM hub.workers WHERE id = 'pc-a'", fetch=True) == [("sello-1",)]


def test_check_contract_pide_las_tablas_de_workers(a):
    assert a.check_contract() == []
    sql(a, "DROP TABLE hub.ajustes")
    assert any("ajustes" in p for p in a.check_contract())
