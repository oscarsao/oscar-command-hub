"""Varios ordenadores sobre el mismo kanban, lado RUNNER (auditoría del segundo worker, 19-11): H1, H5, H6, H9, H10.

Solo dobles: un kanban compartido en memoria con DOS «workers» (cada uno su vista `SharedKanban`), reloj del servidor
propio (los runners llevan relojes distintos) y un `claude` falso. El SQL real está en test_kanban_multiworker_pg.py."""
from __future__ import annotations

import dataclasses
import json
import os
import threading

import pytest

from agent_lanes import multiworker, status
from agent_lanes.config import Lane
from agent_lanes.kanban_api import SupabaseKanban
from agent_lanes.multiworker import LeaseReaper, reaper_settings, worker_overrides
from agent_lanes.runner import LaneRunner
from tests.test_runner_state import (LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, ReconHermes,
                                     ok_outcome)


class Store:
    """El Postgres compartido: tarjetas y la hora del SERVIDOR (`now`), que no es la de ningún runner."""

    def __init__(self, tasks):
        self.now = 1_000_000
        self.tasks = {t["id"]: {"status": "ready", "lease_owner": None, "lease_until": None, "title": "T",
                                "body": "", **t} for t in tasks}
        self.lock = threading.Lock()
        self.log: list[tuple] = []


class SharedKanban:
    """Vista de UN worker sobre el Store (la interfaz que usa LaneRunner)."""

    def __init__(self, store, worker, *, admit=True):
        self.s, self.worker, self.admit = store, worker, admit
        self.last_error = None
        self.lost: set[str] = set()
        self.calls: list[tuple] = []

    def _view(self, t):
        owner, until = t["lease_owner"], t["lease_until"]
        return {**t, "server_now": self.s.now, "lease_expired": bool(owner and until is not None and until < self.s.now)}

    # lecturas
    def list_status(self, assignee, status_, sort="priority"):
        with self.s.lock:
            return [self._view(t) for t in self.s.tasks.values() if t["status"] == status_]

    def list_ready(self, assignee):
        return self.list_status(assignee, "ready")

    def show(self, tid):
        return {"task": self.s.tasks[tid], "events": [], "comments": []}

    def drop_telegram_subs(self, tid):
        return []

    def review_feedback(self, tid):
        return []

    def oscar_answers(self, tid):
        return []

    def coordinator_notes(self, tid):
        return []

    # reserva
    def claim(self, tid, ttl):
        return self._claim(tid, ttl)

    def claim_serial(self, tid, ttl, paths):
        self.calls.append(("claim_serial", tid, tuple(paths)))
        return self._claim(tid, ttl)

    def _claim(self, tid, ttl):
        self.calls.append(("claim", tid))
        with self.s.lock:
            if not self.admit:
                self.last_error = "claim rechazado: worker sin alta en hub.workers"
                return False
            t = self.s.tasks[tid]
            if t["status"] != "ready" or t["lease_owner"] is not None:
                return False
            t.update(status="running", lease_owner=self.worker, lease_until=self.s.now + ttl)
            self.lost.discard(tid)
            return True

    def heartbeat(self, tid, note=None):
        with self.s.lock:
            t = self.s.tasks[tid]
            if t["status"] == "running" and t["lease_owner"] == self.worker:
                t["lease_until"] += 60
                return True
            self.lost.add(tid)
            self.last_error = f"latido rechazado: es de {t['lease_owner']}"
            return False

    def lease_lost(self, tid):
        return tid in self.lost

    def comment(self, tid, text, author=None):
        self.calls.append(("comment", tid, text))
        return True

    def block(self, tid, kind, reason):
        self.calls.append(("block", tid, kind, reason))
        with self.s.lock:
            t = self.s.tasks[tid]
            if t["lease_owner"] not in (None, self.worker):
                self.lost.add(tid)
                return False
            t.update(status="blocked", lease_owner=None, lease_until=None)
            return True

    def request_review(self, tid, summary, metadata):
        self.calls.append(("review", tid))
        with self.s.lock:
            t = self.s.tasks[tid]
            if t["lease_owner"] not in (None, self.worker):
                self.lost.add(tid)
                return False, f"la reserva es de {t['lease_owner']}"
            t.update(status="review", lease_owner=None, lease_until=None)
            return True, ""

    def release_stale(self, max_attempts=3):
        with self.s.lock:
            out = []
            for tid, t in self.s.tasks.items():
                if t["status"] == "running" and t["lease_until"] is not None and t["lease_until"] < self.s.now:
                    out.append({"task_id": tid, "status": "blocked", "attempts": 1, "worker_id": t["lease_owner"]})
                    t.update(status="blocked", lease_owner=None, lease_until=None)
            return out

    def kinds(self):
        return [c[0] for c in self.calls]


def runner_for(kb, tmp_path, *, lane=LANE, alive=(), killed=None, worker=None, clock=None, notifier=None, **kw):
    killed = killed if killed is not None else []
    r = LaneRunner(lane, hermes=kb, git=FakeGit(), worker=worker or FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                   notify=notifier or FakeNotifier(), state_dir=tmp_path, pid_alive=lambda pid: pid in set(alive) | {os.getpid()},
                   kill=killed.append, created_at=lambda pid: None, **({"clock": clock} if clock else {}), **kw)
    return r, killed


# --- H1: reconcile solo toca reservas propias --------------------------------------------------------------------------
def test_reconcile_no_toca_las_tarjetas_running_del_otro_equipo(tmp_path):
    st = Store([{"id": "t_mia", "status": "running", "lease_owner": "pc-a", "lease_until": 2_000_000},
                {"id": "t_suya", "status": "running", "lease_owner": "pc-b", "lease_until": 2_000_000}])
    # en ESTE equipo hay un .state con un pid «vivo» de otro proceso para la tarjeta de B: aun así no se mata
    (tmp_path / "t_suya.json").write_text(json.dumps({"pid": 999999, "worker_pid": 777}))
    kb = SharedKanban(st, "pc-a")
    r, killed = runner_for(kb, tmp_path, alive={777})
    assert r.reconcile() == ["t_mia"]  # solo la propia (sin pid vivo) se da por huérfana
    assert killed == [] and st.tasks["t_suya"]["status"] == "running" and st.tasks["t_suya"]["lease_owner"] == "pc-b"
    assert kb.kinds().count("block") == 1 and (tmp_path / "t_suya.json").exists()


def test_reconcile_no_toca_la_ajena_aunque_su_reserva_haya_caducado(tmp_path):
    """Caducada = trabajo del segador (hora del servidor), no del arranque de otro runner."""
    st = Store([{"id": "t_suya", "status": "running", "lease_owner": "pc-b", "lease_until": 10}])
    kb = SharedKanban(st, "pc-a")
    r, killed = runner_for(kb, tmp_path)
    assert r.reconcile() == [] and "block" not in kb.kinds() and killed == []


def test_reconcile_con_dos_runners_reiniciando_a_la_vez_cada_uno_su_parte(tmp_path):
    st = Store([{"id": "t_a", "status": "running", "lease_owner": "pc-a", "lease_until": 2_000_000},
                {"id": "t_b", "status": "running", "lease_owner": "pc-b", "lease_until": 2_000_000}])
    ra, _ = runner_for(SharedKanban(st, "pc-a"), tmp_path / "a")
    rb, _ = runner_for(SharedKanban(st, "pc-b"), tmp_path / "b")
    assert ra.reconcile() == ["t_a"] and rb.reconcile() == ["t_b"]


def test_reconcile_con_hermes_es_identico(tmp_path):
    """Sin `worker` en el cliente (backend hermes) el dueño de la reserva no existe: todo cuenta como propio."""
    (tmp_path / "t_1.json").write_text(json.dumps({"pid": 999999, "worker_pid": 777}))
    h = ReconHermes(running=[{"id": "t_1"}, {"id": "t_2", "lease_owner": "otro"}])  # una clave suelta no cambia nada
    r, killed = runner_for(h, tmp_path, alive={777})
    assert sorted(r.reconcile()) == ["t_1", "t_2"] and killed == [777] and r.worker_id() is None


def test_reconcile_tarjeta_running_sin_dueno_cuenta_como_propia(tmp_path):
    st = Store([{"id": "t_x", "status": "running"}])  # importada de Hermes: sin lease_owner
    r, _ = runner_for(SharedKanban(st, "pc-a"), tmp_path)
    assert r.reconcile() == ["t_x"]


def test_skew_de_reloj_el_runner_no_decide_caducidades(tmp_path):
    """Dos runners con relojes locales muy distintos ven lo mismo: la caducidad viene del servidor en cada tarjeta."""
    st = Store([{"id": "t_v", "status": "running", "lease_owner": "pc-b", "lease_until": 1_000_600},
                {"id": "t_m", "status": "running", "lease_owner": "pc-b", "lease_until": 999_000}])
    adelantado = runner_for(SharedKanban(st, "pc-a"), tmp_path / "a", clock=lambda: 10 ** 9)[0]
    atrasado = runner_for(SharedKanban(st, "pc-c"), tmp_path / "c", clock=lambda: 0)[0]
    for r in (adelantado, atrasado):
        rows = {t["id"]: t["lease_expired"] for t in r.hermes.list_status("claude-oscarhq", "running")}
        assert rows == {"t_v": False, "t_m": True}
        assert r.reconcile() == []  # ninguna es suya


def test_task_dict_calcula_la_caducidad_con_la_hora_del_servidor():
    base = ("t_1", "T", "b", "claude-hub", "running", 0, None, "x", 1, 1, None, None, {}, [], None, "oscarhq", None)
    vivo = SupabaseKanban._task_dict((*base, "pc-b", 2000, 1000))
    muerto = SupabaseKanban._task_dict((*base, "pc-b", 900, 1000))
    libre = SupabaseKanban._task_dict((*base, None, None, 1000))
    assert (vivo["lease_expired"], muerto["lease_expired"], libre["lease_expired"]) == (False, True, False)
    assert vivo["lease_owner"] == "pc-b" and vivo["server_now"] == 1000 and libre["lease_owner"] is None


# --- dos workers reclamando a la vez ----------------------------------------------------------------------------------------
def test_dos_runners_a_la_vez_cada_tarjeta_va_a_uno_solo(tmp_path):
    tareas = [{"id": f"t_{i}", "title": "A", "body": ""} for i in range(4)]
    st = Store(tareas)
    runs = {}
    barrier = threading.Barrier(2)

    def go(wid):
        kb = SharedKanban(st, wid)
        r, _ = runner_for(kb, tmp_path / wid, worker=FakeWorker([ok_outcome() for _ in range(4)]))
        barrier.wait()
        runs[wid] = {t["id"]: r.process(t) for t in tareas}

    ts = [threading.Thread(target=go, args=(w,)) for w in ("pc-a", "pc-b")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    for tid in (t["id"] for t in tareas):
        resultados = sorted([runs["pc-a"][tid], runs["pc-b"][tid]])
        assert resultados == ["claim_lost", "review"], (tid, resultados)  # ni dos workers ni ninguno
    assert all(t["status"] == "review" for t in st.tasks.values())


def test_process_anota_el_equipo_en_el_estado_y_en_el_comentario(tmp_path):
    st = Store([{"id": "t_1", "title": "A", "body": ""}])
    seen = {}

    class Peek(FakeWorker):
        def run(self, lane, task, cwd, session_id, timeout):
            seen["state"] = json.loads((tmp_path / "t_1.json").read_text())
            return ok_outcome()

    kb = SharedKanban(st, "pc-b")
    r, _ = runner_for(kb, tmp_path, worker=Peek([]))
    assert r.process({"id": "t_1", "title": "A", "body": ""}) == "review"
    assert seen["state"]["worker"] == "pc-b"
    assert any(c[0] == "comment" and "equipo=pc-b" in c[2] for c in kb.calls)


# --- H5/H9: reserva perdida -----------------------------------------------------------------------------------------------------
class PierdeLaReserva(FakeWorker):
    """El claude trabaja; a mitad, la reserva caduca y B se queda la tarjeta; el latido de A lo descubre."""

    def __init__(self, runner_box, store, tmp_path, *, finaliza_ok=True):
        super().__init__([ok_outcome()])
        self.box, self.store, self.tmp, self.finaliza_ok = runner_box, store, tmp_path, finaliza_ok

    def run(self, lane, task, cwd, session_id, timeout):
        state_file = self.tmp / f"{task['id']}.json"
        state = json.loads(state_file.read_text())
        state["worker_pid"] = 4242  # el claude que lanzó el runner
        state_file.write_text(json.dumps(state))
        self.store.now += 10_000  # el tiempo del servidor pasa y B la reclama
        self.store.tasks[task["id"]].update(lease_owner="pc-b", lease_until=self.store.now + 600)
        assert self.box[0]._beat(task["id"]) is False  # el latido de A (hilo aparte en producción)
        return ok_outcome() if self.finaliza_ok else self.outcomes.pop(0)


def test_latido_rechazado_corta_el_claude_y_abandona_sin_tocar_la_tarjeta(tmp_path):
    st = Store([{"id": "t_1", "title": "A", "body": ""}])
    kb, box = SharedKanban(st, "pc-a"), []
    notifier = FakeNotifier()
    r, killed = runner_for(kb, tmp_path, alive={4242}, worker=PierdeLaReserva(box, st, tmp_path), notifier=notifier)
    box.append(r)
    assert r.process({"id": "t_1", "title": "A", "body": ""}) == "lease_perdida"
    assert not any("bloquead" in m.lower() or "review" in m.lower() for m in notifier.msgs)  # ni un aviso por la ajena
    assert killed == [4242]  # cortó SU claude
    assert "block" not in kb.kinds() and "review" not in kb.kinds()  # ni bloquea ni pasa a review
    assert st.tasks["t_1"]["status"] == "running" and st.tasks["t_1"]["lease_owner"] == "pc-b"  # la de B intacta
    assert r._block_pendiente() == {}  # y no queda un bloqueo pendiente que reintentar cada pasada
    assert not (tmp_path / "t_1.json").exists()  # estado local limpio


def test_latido_rechazado_sin_claude_vivo_no_mata_a_nadie(tmp_path):
    st = Store([{"id": "t_1", "title": "A", "body": ""}])
    kb, box = SharedKanban(st, "pc-a"), []
    r, killed = runner_for(kb, tmp_path, alive=set(), worker=PierdeLaReserva(box, st, tmp_path))
    box.append(r)
    assert r.process({"id": "t_1", "title": "A", "body": ""}) == "lease_perdida" and killed == []


def test_fallo_de_base_en_el_latido_no_corta_el_trabajo(tmp_path):
    """Latido False por un fallo de red (no por cambio de dueño): sigue trabajando, el siguiente latido reintenta."""
    st = Store([{"id": "t_1", "title": "A", "body": ""}])
    kb = SharedKanban(st, "pc-a")
    kb.heartbeat = lambda tid, note=None: False  # red caída: False sin `lease_lost`
    r, killed = runner_for(kb, tmp_path, alive={4242})
    assert r._beat("t_1") is False and killed == []


def test_request_review_rechazado_por_reserva_ajena_no_bloquea_ni_avisa(tmp_path):
    st = Store([{"id": "t_1", "title": "A", "body": ""}])
    kb = SharedKanban(st, "pc-a")

    class RobaAntesDeCerrar(FakeVerifier):
        def __call__(self, lane, task_id, cwd, result):  # B se queda la tarjeta justo antes del request-review
            st.tasks[task_id].update(lease_owner="pc-b", lease_until=st.now + 600)
            return super().__call__(lane, task_id, cwd, result)

    notifier = FakeNotifier()
    r, _ = runner_for(kb, tmp_path, notifier=notifier)
    r.verifier = RobaAntesDeCerrar()
    assert r.process({"id": "t_1", "title": "A", "body": ""}) == "lease_perdida"
    assert "block" not in kb.kinds() and st.tasks["t_1"]["lease_owner"] == "pc-b"
    assert r._block_pendiente() == {}
    assert not any("bloquead" in m.lower() or "review" in m.lower() for m in notifier.msgs)


# --- H10 / H3: admisión y rutas serializadas ----------------------------------------------------------------------------------
def test_claim_rechazado_por_admision_pausa_el_carril_y_no_martillea(tmp_path):
    st = Store([{"id": "t_1", "title": "A", "body": ""}])
    kb, now = SharedKanban(st, "pc-fantasma", admit=False), [0.0]
    r, _ = runner_for(kb, tmp_path, clock=lambda: now[0])
    assert r.process({"id": "t_1", "title": "A", "body": ""}) == "claim_rechazado"
    claims = kb.kinds().count("claim")
    assert r.candidates() == [] and kb.kinds().count("claim") == claims  # pausado: ni mira la cola
    now[0] += 301
    kb.admit = True
    assert [c.tid for c in r.candidates()] == ["t_1"]  # pasado el plazo vuelve a intentarlo


def test_claim_normal_perdido_no_pausa(tmp_path):
    h = FakeHermes(tasks=[{"id": "t_1", "title": "A", "body": ""}], claim_ok=False)
    r, _ = runner_for(h, tmp_path)
    assert r.process({"id": "t_1", "title": "A", "body": ""}) == "claim_lost" and r.candidates()


def test_tarjeta_de_migraciones_usa_el_claim_con_cerrojo_y_el_resto_el_normal(tmp_path):
    lane = dataclasses.replace(LANE, serialize_paths=("supabase/migrations",))
    st = Store([{"id": "t_m", "title": "M", "body": "toca supabase/migrations/x.sql"},
                {"id": "t_o", "title": "O", "body": "docs"}])
    kb = SharedKanban(st, "pc-a")
    r, _ = runner_for(kb, tmp_path, lane=lane, worker=FakeWorker([ok_outcome(), ok_outcome()]))
    r.process({"id": "t_m", "title": "M", "body": "toca supabase/migrations/x.sql"})
    r.process({"id": "t_o", "title": "O", "body": "docs"})
    assert ("claim_serial", "t_m", ("supabase/migrations",)) in kb.calls
    assert not any(c[0] == "claim_serial" and c[1] == "t_o" for c in kb.calls)


def test_con_hermes_el_claim_de_migraciones_no_cambia(tmp_path):
    """HermesCLI no tiene claim_serial: la tarjeta de migraciones usa claim(tid, ttl) como siempre."""
    lane = dataclasses.replace(LANE, serialize_paths=("supabase/migrations",))
    h = FakeHermes(tasks=[{"id": "t_m", "title": "M", "body": "supabase/migrations/x"}])
    r, _ = runner_for(h, tmp_path, lane=lane)
    assert r.process({"id": "t_m", "title": "M", "body": "supabase/migrations/x"}) == "review"
    assert [c[0] for c in h.calls if c[0] == "claim"] == ["claim"] and not hasattr(h, "claim_serial")


# --- H6: el segador -----------------------------------------------------------------------------------------------------------------
def test_segador_apagado_con_hermes_y_encendido_con_supabase():
    assert reaper_settings("hermes", {})["enabled"] is False
    assert reaper_settings("hermes", {"release_stale_max_attempts": 5})["enabled"] is False
    s = reaper_settings("supabase", {})
    assert s == {"enabled": True, "max_attempts": 3, "seconds": 60.0}
    assert reaper_settings("supabase", {"release_stale_max_attempts": 0})["enabled"] is False  # 0 = apagado
    assert reaper_settings("supabase", {"release_stale_max_attempts": 5, "release_stale_seconds": 120}) == {
        "enabled": True, "max_attempts": 5, "seconds": 120.0}
    assert reaper_settings("supabase", {"release_stale_max_attempts": 5}, {"RELEASE_STALE_MAX_ATTEMPTS": "2"})[
        "max_attempts"] == 2  # el .env manda sobre lanes.yaml
    assert reaper_settings("supabase", {"release_stale_seconds": 1})["seconds"] == 5.0  # suelo: no martillear la base


def test_segador_cada_n_segundos_con_el_tope_configurado():
    st = Store([{"id": "t_m", "status": "running", "lease_owner": "pc-b", "lease_until": 10}])
    kb, t = SharedKanban(st, "pc-a"), [0.0]
    seen = []
    orig = kb.release_stale
    kb.release_stale = lambda n=3: (seen.append(n), orig(n))[1]
    reaper = LeaseReaper(kb, max_attempts=4, seconds=60, clock=lambda: t[0])
    assert [r["task_id"] for r in reaper.tick()] == ["t_m"] and seen == [4]
    assert reaper.tick() == [] and len(seen) == 1  # aún no toca
    t[0] += 61
    reaper.tick()
    assert len(seen) == 2
    assert st.tasks["t_m"]["status"] == "blocked"


def test_segador_avisa_de_las_que_llegan_a_triage_y_nunca_lanza():
    avisos = []

    class K:
        last_error = None

        def release_stale(self, n):
            return [{"task_id": "t_1", "status": "triage", "attempts": 3, "worker_id": "pc-b"},
                    {"task_id": "t_2", "status": "blocked", "attempts": 1, "worker_id": "pc-b"}]

    rows = LeaseReaper(K(), max_attempts=3, seconds=5, alert=avisos.append).tick()
    assert len(rows) == 2 and len(avisos) == 1 and "t_1" in avisos[0] and "t_2" not in avisos[0]

    class Roto:
        last_error = "red caída"

        def release_stale(self, n):
            raise RuntimeError("base caída")

    assert LeaseReaper(Roto(), alert=avisos.append).tick() == []  # el bucle del runner sigue


# --- H10: ajustes por equipo ----------------------------------------------------------------------------------------------------------
def test_ajustes_por_equipo_en_lanes_yaml():
    s = {"max_workers": 6, "workers": {"pc-b": {"max_workers": 2, "lanes": ["claude-hub", "claude-scraper"]},
                                       "pc-c": {"lanes": []}, "pc-d": "mal"}}
    assert worker_overrides(s, "pc-b") == {"max_workers": 2, "lanes": {"claude-hub", "claude-scraper"}}
    assert worker_overrides(s, "pc-c") == {"max_workers": None, "lanes": None}
    assert worker_overrides(s, "pc-d") == worker_overrides(s, "desconocido") == worker_overrides(s, None) == {
        "max_workers": None, "lanes": None}
    import runner as main  # el script de arranque (pytest.ini: pythonpath = .)
    assert main.effective_max_workers(0, s, "pc-b") == 2  # el del equipo
    assert main.effective_max_workers(0, s, "pc-x") == 6 and main.effective_max_workers(0, s) == 6  # el global
    assert main.effective_max_workers(1, s, "pc-b") == 1  # --max-workers manda sobre todo


def test_estado_de_carriles_distingue_lo_remoto_de_un_huerfano(tmp_path):
    st = Store([{"id": "t_b", "status": "running", "lease_owner": "pc-b", "lease_until": 2_000_000, "assignee": "x"}])
    lane = dataclasses.replace(LANE, name="claude-oscarhq")
    kb = SharedKanban(st, "pc-a")
    kb.list_status = lambda assignee, s, sort="priority": [kb._view(t) for t in st.tasks.values() if t["status"] == s]
    rows = status.lane_rows({"claude-oscarhq": lane}, hermes_for=lambda b: kb, state_dir=tmp_path,
                            pid_alive=lambda p: False, runner_is_alive=False)
    assert rows[0]["state"] == "remoto" and rows[0]["running"] == ["t_b@pc-b"]  # no «huérfano»
    # con hermes (sin worker) la misma tarjeta sí es huérfana: comportamiento de siempre
    h = ReconHermes(running=[{"id": "t_b", "lease_owner": "pc-b"}])
    h.list_status = lambda assignee, s, sort="priority": [{"id": "t_b", "lease_owner": "pc-b"}] if s == "running" else []
    rows = status.lane_rows({"claude-oscarhq": lane}, hermes_for=lambda b: h, state_dir=tmp_path,
                            pid_alive=lambda p: False, runner_is_alive=False)
    assert rows[0]["state"] == "huérfano"
