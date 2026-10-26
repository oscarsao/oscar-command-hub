"""F2-02: backend supabase del kanban contra una BD Postgres de PRUEBA (HUB_TEST_DSN; si no, se salta).

Reproduce lo que el hub lee de Hermes: forma del JSON de show/list, ciclo ready->running->review->done, bloqueos con
`BLOCKED:` y bucle a triage, respuestas de Oscar que desbloquean, padres que retienen a las hijas, idempotencia."""
import threading

import pytest

from agent_lanes import hermes
from agent_lanes.hermes import HermesError, OSCAR_AUTHOR, REVIEW_AUTHOR
from agent_lanes.kanban_api import RUN_FIELDS, TASK_FIELDS, SupabaseKanban
from tests.hubpg import hub_dsn  # noqa: F401  (fixture)

pytestmark = pytest.mark.usefixtures("hub_dsn")


@pytest.fixture
def clock():
    class Clock:
        t = 1_760_000_000

        def __call__(self):
            return self.t
    return Clock()


@pytest.fixture
def kb(hub_dsn, clock):  # noqa: F811
    return SupabaseKanban("oscarhq", author="agent-lanes", dsn=hub_dsn, clock=clock, worker="pc-oscar")


def other(kb_, board="oscarhq", **kw):
    return SupabaseKanban(board, author=kw.pop("author", "agent-lanes"), dsn=kb_._dsn, clock=kb_._clock,
                          worker=kw.pop("worker", "pc-oscar"), **kw)


def test_create_list_show_forma_de_hermes(kb):
    tid = kb.create("[Hub] Prueba · paso", "cuerpo", "Claude-Hub", priority=5, created_by="auditor",
                    alcance=["tools/agent-lanes/**"], rama="feat/x")
    assert tid.startswith("t_") and len(tid) == 10
    [row] = kb.list_status("claude-hub", "ready")
    assert set(TASK_FIELDS) <= set(row) and row["id"] == tid and row["assignee"] == "claude-hub"
    assert row["created_by"] == "auditor" and row["skills"] == [] and isinstance(row["created_at"], int)
    assert row["alcance"] == ["tools/agent-lanes/**"] and row["rama"] == "feat/x"
    show = kb.show(tid)
    assert set(show) == {"task", "latest_summary", "parents", "children", "comments", "events", "runs"}
    assert show["events"][0]["kind"] == "created" and isinstance(show["events"][0]["payload"], dict)
    assert isinstance(show["events"][0]["created_at"], int)
    assert other(kb, board="migrateam").list_status("claude-hub", "ready") == []  # los tableros no se mezclan
    with pytest.raises(HermesError):
        other(kb, board="migrateam").show(tid)


def test_orden_por_prioridad_y_list_all(kb, clock):
    a = kb.create("[Hub] a", "b", "claude-hub", priority=1)
    clock.t += 1
    b = kb.create("[Hub] b", "b", "claude-hub", priority=9)
    assert [t["id"] for t in kb.list_status("claude-hub", "ready")] == [b, a]
    assert kb.archive(a)
    assert {t["id"] for t in kb.list_all()} == {b} and {t["id"] for t in kb.list_all(archived=True)} == {a, b}
    assert kb.list_status_all("archived")[0]["id"] == a


def test_idempotencia(kb):
    a = kb.create("[Hub] a", "b", "claude-hub", key="k-1")
    assert kb.create("[Hub] a otra vez", "b", "claude-hub", key="k-1") == a
    kb.archive(a)
    assert kb.create("[Hub] a", "b", "claude-hub", key="k-1") != a  # archivada ya no cuenta (como Hermes)


def test_ciclo_completo_ready_running_review_done(kb, clock):
    tid = kb.create("[Hub] ciclo", "b", "claude-hub")
    assert kb.claim(tid, 600) and not kb.claim(tid, 600)
    assert kb.show(tid)["task"]["status"] == "running"
    assert not kb.assign(tid, "otro") and not kb.reassign(tid, "otro", "x")  # corriendo: no se reasigna
    clock.t += 100
    assert kb.heartbeat(tid, note="vivo")
    assert kb.comment(tid, "nota del worker")
    ok, err = kb.request_review(tid, "hecho: PR #1", {"pr": 1})
    assert ok and not err
    assert kb.reassign(tid, "lane-review", "revisión")
    assert kb.comment(tid, "cambia X", author=REVIEW_AUTHOR)
    assert kb.reopen_review(tid)
    show = kb.show(tid)
    assert show["task"]["status"] == "ready" and show["task"]["assignee"] == "claude-hub"  # vuelve al implementador
    assert kb.review_feedback(tid) == ["cambia X"]
    assert kb.claim(tid, 600)
    ok, _ = kb.complete(tid, "PR #1 fusionable", {"pr": 1})
    assert ok
    show = kb.show(tid)
    assert show["task"]["status"] == "done" and show["task"]["result"] == "PR #1 fusionable"
    assert show["latest_summary"] == "PR #1 fusionable"
    assert all(set(RUN_FIELDS) == set(r) for r in show["runs"]) and len(show["runs"]) == 2
    kinds = [e["kind"] for e in show["events"]]
    assert kinds[:2] == ["created", "claimed"] and kinds[-1] == "completed" and "review_reopened" in kinds
    assert not kb.complete(tid, "otra vez", {})[0]


def test_dos_claims_concurrentes_solo_gana_uno(kb):
    tid = kb.create("[Hub] carrera", "b", "claude-hub")
    results = []
    barrier = threading.Barrier(8)

    def go(i):
        k = other(kb, worker=f"w{i}")
        barrier.wait()
        results.append(k.claim(tid, 60))

    ts = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count(True) == 1


def test_bloqueo_respuesta_y_bucle_a_triage(kb, clock):
    tid = kb.create("[Hub] pregunta", "b", "claude-hub")
    kb.claim(tid, 600)
    assert kb.block(tid, "needs_input", "¿A o B?")
    show = kb.show(tid)
    assert show["task"]["status"] == "blocked"
    assert show["comments"][-1] == {"author": "agent-lanes", "body": "BLOCKED: ¿A o B?",
                                    "created_at": clock.t}
    clock.t += 5
    kb.comment(tid, "Respuesta de Oscar: ¿A o B? → A", author=OSCAR_AUTHOR)
    show = kb.show(tid)
    answers, nxt = hermes.answer_progress(show, [{"question": "¿A o B?"}])
    assert answers == {0: "A"} and nxt is None  # las respuestas posteriores al bloqueo cuentan (fechas epoch)
    assert hermes.resume(kb, tid) == "ready"
    kb.claim(tid, 600)
    assert kb.block(tid, "needs_input", "¿A o B? otra vez")
    show = kb.show(tid)
    assert show["task"]["status"] == "triage" and show["events"][-1]["kind"] == "block_loop_detected"
    assert not kb.unblock(tid)
    assert hermes.resume(kb, tid) == "ready"  # unblock falla -> requeue_triage la saca sin reescribirla
    assert kb.show(tid)["task"]["title"] == "[Hub] pregunta"


def test_padres_retienen_y_promueven(kb):
    padre = kb.create("[Hub] padre", "b", "claude-hub")
    hija = kb.create("[Hub] hija", "b", "claude-hub", parents=[padre])
    assert kb.show(hija)["task"]["status"] == "todo" and kb.show(padre)["children"] == [hija]
    assert kb.show(hija)["parents"] == [padre]
    assert not kb.link(hija, padre)  # ciclo
    otra = kb.create("[Hub] otra", "b", "claude-hub")
    assert kb.link(padre, otra) and kb.show(otra)["task"]["status"] == "todo"
    kb.claim(padre, 60)
    assert kb.complete(padre, "hecho", {})[0]
    assert kb.show(hija)["task"]["status"] == "ready" and kb.show(otra)["task"]["status"] == "ready"
    with pytest.raises(HermesError, match="padres desconocidos"):
        kb.create("[Hub] x", "b", "claude-hub", parents=["t_ffffffff"])


def test_claim_con_padre_abierto_vuelve_a_todo(kb, hub_dsn):  # noqa: F811
    padre = kb.create("[Hub] padre", "b", "claude-hub")
    hija = kb.create("[Hub] hija", "b", "claude-hub", parents=[padre])
    import psycopg2
    conn = psycopg2.connect(hub_dsn)
    with conn, conn.cursor() as cur:  # alguien la puso en ready a mano
        cur.execute("UPDATE hub.tasks SET status = 'ready' WHERE id = %s", (hija,))
    conn.close()
    assert not kb.claim(hija, 60) and kb.show(hija)["task"]["status"] == "todo"


def test_dependency_sin_padre_abierto_es_needs_input(kb):
    tid = kb.create("[Hub] dep", "b", "claude-hub")
    kb.claim(tid, 60)
    assert kb.block(tid, "dependency", "espera")
    t = kb.show(tid)
    assert t["task"]["status"] == "blocked" and t["events"][-1]["payload"]["rekind_reason"] == "no_open_parent"
    assert not kb.block(tid, "inventado", "x")


def test_prioridad_suscripciones_y_errores(kb):
    tid = kb.create("[Hub] subs", "b", "claude-hub")
    assert kb.set_priority(tid, 30) and kb.show(tid)["task"]["priority"] == 30
    assert kb.subscribe_telegram(tid, "-42", "77")
    assert kb.drop_telegram_subs(tid) == []  # las de modo notify se conservan
    assert len(kb.notify_list(tid)) == 1
    assert not kb.comment("t_00000000", "x") and not kb.unblock(tid) and not kb.reopen_review(tid)
    assert kb.requeue_triage(tid) is None and kb.last_error


def test_check_contract(kb, hub_dsn):  # noqa: F811
    assert kb.check_contract() == []
    import psycopg2
    conn = psycopg2.connect(hub_dsn)
    with conn, conn.cursor() as cur:
        cur.execute("ALTER TABLE hub.tasks DROP COLUMN rama")
    conn.close()
    assert kb.check_contract() == ["hub.tasks sin columna rama"]


def test_eventos_append_only(kb, hub_dsn):  # noqa: F811
    tid = kb.create("[Hub] e", "b", "claude-hub")
    import psycopg2
    conn = psycopg2.connect(hub_dsn)
    with pytest.raises(psycopg2.Error, match="append-only"):
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM hub.task_events WHERE task_id = %s", (tid,))
    conn.close()
