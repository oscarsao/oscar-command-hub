"""Escalada de review sobre una tarea en `review` (h.block solo mueve running/ready): una sola alerta, con botones."""
from __future__ import annotations

from agent_lanes.decisions import keyboard_spec
from agent_lanes.review import RECHEQUEO_ESCALADA, ReviewRunner
from tests.test_phase2 import LANE, REVIEW, FakeReviewGit, FakeVerifier, ReviewHermes, META, verdict
from tests.test_integrator import FakeTg


class FailingBlockHermes(ReviewHermes):
    def block(self, tid, kind, reason):
        self.calls.append(("block", tid, kind, reason))
        return False


class SpyDecisions:
    def __init__(self):
        self.markups = []

    def markup(self, state, **kw):
        self.markups.append((state, kw.get("block_kind")))
        return {"inline_keyboard": [[{"text": "x", "callback_data": "t:0"}]]}

    owner_id = "1"


def build(tmp_path, state_dir=True):
    h = FailingBlockHermes([{"id": "t_1", "title": "T", "body": "B", "assignee": "claude-oscarhq", "status": "review"}])
    h.show_events = [{"kind": "review_reopened", "created_at": 1}, {"kind": "review_reopened", "created_at": 2}]
    n, d = FakeTg(), SpyDecisions()

    class Rv:
        def run(self, *a, **k):
            return verdict("request_changes", ["c"])

        resume = run

    r = ReviewRunner(REVIEW, {"claude-oscarhq": LANE}, hermes_for=lambda b: h, git=FakeReviewGit(), reviewer=Rv(),
                     verifier=FakeVerifier(True), notify=n, decisions=d,
                     state_dir=(tmp_path / "rb") if state_dir else None)
    return r, h, n, d


def test_escalation_with_failed_block_alerts_once_with_buttons(tmp_path):
    r, h, n, d = build(tmp_path)
    assert r.run_once() == {"t_1": "blocked:needs_input"}
    assert d.markups == [("blocked", "review_stuck")]
    assert len(n.sent) == 1
    for _ in range(5):  # t_cce5da81: la escalada ya no se relanza (antes: blocked:already_escalated en cada vuelta)
        assert r.run_once() == {}
    assert len(n.sent) == 1 and len([c for c in h.calls if c[0] == "block"]) == 1


def test_state_survives_restart_and_new_head_reprocesses(tmp_path):
    r, h, n, d = build(tmp_path)
    r.run_once()
    r2, h2, n2, _ = build(tmp_path)  # runner nuevo (reinicio) con el mismo .state
    assert r2.run_once() == {} and n2.sent == []  # escalada: no se relanza
    h2.meta = {**META, "head_sha": "b" * 40}  # el implementador empujó otra cabeza: se revisa de nuevo
    now = r2.clock()
    r2.clock = lambda: now + RECHEQUEO_ESCALADA + 1  # en la siguiente relectura de la escalada
    assert r2.run_once() == {"t_1": "blocked:needs_input"}


def test_review_stuck_keyboard_offers_approve_changes_park():
    spec = keyboard_spec("blocked", block_kind="review_stuck")
    assert [b["action"] for b in spec[0]] == ["approve", "changes", "park"]


# --- t_cce5da81: la escalada no ocupa la plaza del carril review en bucle --------------------------------------------

def _second_task(h):
    h.tasks.append({"id": "t_2", "title": "T2", "body": "B", "assignee": "claude-oscarhq", "status": "review"})


def test_escalada_no_ocupa_la_plaza_y_pasa_la_siguiente(tmp_path):
    """max_parallel=1: con la escalada fuera de la selección, la siguiente tarjeta del carril sí se revisa."""
    r, h, n, d = build(tmp_path)
    r.run_once()  # t_1 escalada
    _second_task(h)
    assert [tid for tid, _ in r.jobs()] == ["t_2"]


def test_escalada_no_relanza_ni_se_relee_en_cada_vuelta(tmp_path):
    r, h, n, d = build(tmp_path)
    r.run_once()
    shows = []
    orig = h.show
    h.show = lambda tid: shows.append(tid) or orig(tid)
    for _ in range(10):
        assert r.jobs() == []
    assert len(shows) <= 1  # relectura como mucho cada RECHEQUEO_ESCALADA s


def test_aprobado_oscar_tras_escalar_no_se_reprocesa_aunque_cambie_la_cabeza(tmp_path):
    from agent_lanes.hermes import OSCAR_AUTHOR
    r, h, n, d = build(tmp_path)
    h.show_events = h.show_events + [{"kind": "review_requested", "created_at": 3}]
    r.run_once()
    h.comments = [{"author": OSCAR_AUTHOR, "body": "APROBADO-OSCAR 2026-10-23 16:44 · PR https://x/pull/310",
                   "created_at": 5}]
    h.meta = {**h.meta, "head_sha": "c" * 40}
    r2, h2, *_ = build(tmp_path)  # sin caché: relee y ve la aprobación
    h2.comments, h2.meta, h2.show_events = h.comments, h.meta, h.show_events
    from agent_lanes import aprobaciones
    aprobaciones.registrar(h2.tasks[0]["id"], 310)  # P08: el bot dejó el registro local al pulsar ✅ Aprobar
    assert r2.jobs() == []
    task = h2.tasks[0]
    from tests.test_phase2 import LANE as L
    assert r2.process(task, L, h2) == "skip:aprobada"  # y si llegara a procesarla, no la toca


def test_aprobacion_anterior_a_una_nueva_peticion_de_review_no_cuenta(tmp_path):
    from agent_lanes.hermes import OSCAR_AUTHOR
    from agent_lanes.review import aprobada_por_oscar
    from agent_lanes import aprobaciones
    show = {"task": {"id": "t_aaaaaaaa"},
            "comments": [{"author": OSCAR_AUTHOR, "body": "APROBADO-OSCAR x · PR https://x/pull/4", "created_at": 5}],
            "events": [{"kind": "review_requested", "created_at": 9}]}
    aprobaciones.registrar("t_aaaaaaaa", 4)
    assert not aprobada_por_oscar(show)
    show["events"] = [{"kind": "review_requested", "created_at": 1}]
    assert aprobada_por_oscar(show)
    (aprobaciones.APPROVALS_DIR / "t_aaaaaaaa.json").unlink()
    assert not aprobada_por_oscar(show)  # P08: sin registro local, el comentario solo no aprueba
    aprobaciones.registrar("t_aaaaaaaa", 4)
    show["comments"][0]["author"] = "otro"
    assert not aprobada_por_oscar(show)
