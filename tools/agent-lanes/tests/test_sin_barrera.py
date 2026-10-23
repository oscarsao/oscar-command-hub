"""Planificador sin barrera (t_c5cafff2): huecos normal / grande / reservado, max_parallel 2 de Oscar HQ, migraciones."""
import json
import os
import threading
import time
from types import SimpleNamespace

from agent_lanes import grandes, holds
from agent_lanes.config import load_lanes
from agent_lanes.runner import Cand
from agent_lanes.service import Service
from agent_lanes.status import runner_line, slots_text


class Fake:
    """Carril de pega: candidates() devuelve las tarjetas que le quedan; cada job espera a su puerta."""

    def __init__(self, name, cards, max_parallel=1, max_big=1, reserved=0):
        self.lane = SimpleNamespace(name=name, max_parallel=max_parallel, max_big=max_big, reserved_slots=reserved)
        self.cards = list(cards)  # (tid, big, serial)
        self.gates = {t: threading.Event() for t, _, _ in cards}
        self.started, self.listed = [], 0

    def candidates(self):
        self.listed += 1
        return [Cand(t, lambda t=t: self._run(t), big, serial) for t, big, serial in self.cards
                if t not in self.started]

    def _run(self, t):
        self.started.append(t)
        self.gates[t].wait(5)
        return "done"

    def finish(self, t):
        self.gates[t].set()


def started(*fakes):
    return [t for f in fakes for t in f.started]


def go(svc):
    svc.tick()
    time.sleep(0.15)


def test_una_grande_de_otro_carril_no_tapa_a_las_pequenas():
    hub = Fake("claude-hub", [("g1", True, False)])
    ohq = Fake("claude-oscarhq", [("a", False, False), ("b", False, False), ("c", False, False)], max_parallel=2)
    svc = Service([hub, ohq], max_workers=4, max_big_total=2)
    go(svc)
    assert set(started(hub, ohq)) == {"g1", "a", "b"}  # la grande corre y oscarhq usa sus 2 plazas a la vez
    for f, t in ((hub, "g1"), (ohq, "a"), (ohq, "b")):
        f.finish(t)
    svc.wait_all(5)


def test_sin_barrera_el_hueco_se_reutiliza_en_cuanto_termina_un_job():
    ohq = Fake("claude-oscarhq", [("a", False, False), ("b", False, False), ("c", False, False)], max_parallel=2)
    svc = Service([ohq], max_workers=4)
    go(svc)
    assert ohq.started == ["a", "b"]
    svc.wake.clear()
    ohq.finish("a")
    assert svc.wake.wait(2)  # el bucle principal se despierta sin esperar al intervalo
    go(svc)
    assert ohq.started == ["a", "b", "c"]  # b sigue vivo: ninguna espera a las demas
    for t in "bc":
        ohq.finish(t)
    assert svc.wait_all(5) == {"a": "done", "b": "done", "c": "done"}


def test_la_grande_va_a_su_hueco_aparte_y_solo_una_por_proyecto():
    ohq = Fake("claude-oscarhq", [("G1", True, False), ("G2", True, False), ("s1", False, False)], max_parallel=1)
    svc = Service([ohq], max_workers=4, max_big_total=2)
    go(svc)
    assert set(ohq.started) == {"G1", "s1"}  # 1 grande + 1 normal; G2 espera su hueco sin bloquear a s1
    summary = svc.slots_summary()
    assert summary["big"] == 1 and summary["normal"] == 1
    for t in ("G1", "s1"):
        ohq.finish(t)
    svc.wait_all(5)


def test_tope_global_de_grandes():
    a, b, c = Fake("a", [("A", True, False)]), Fake("b", [("B", True, False)]), Fake("c", [("C", True, False)])
    svc = Service([a, b, c], max_workers=4, max_big_total=2)
    go(svc)
    assert len(started(a, b, c)) == 2
    for f, t in ((a, "A"), (b, "B"), (c, "C")):
        f.finish(t)
    svc.wait_all(5)


def test_sin_hueco_grande_configurado_la_grande_va_por_el_carril_normal():
    a = Fake("a", [("A", True, False)])
    svc = Service([a], max_workers=2, max_big_total=0)
    go(svc)
    assert a.started == ["A"] and svc.slots_summary()["normal"] == 1
    a.finish("A")
    svc.wait_all(5)


def test_las_grandes_cuentan_contra_max_workers():
    a = Fake("a", [("A", True, False), ("a1", False, False)], max_parallel=1)
    svc = Service([a], max_workers=1, max_big_total=2)
    go(svc)
    assert a.started == ["A"]  # max_workers 1: la grande ocupa el unico worker
    a.finish("A")
    svc.wait_all(5)


def test_max_parallel_2_no_corre_dos_con_migraciones_a_la_vez():
    ohq = Fake("claude-oscarhq", [("m1", False, True), ("m2", False, True), ("x", False, False)], max_parallel=2)
    svc = Service([ohq], max_workers=4)
    go(svc)
    assert ohq.started == ["m1", "x"]  # m2 espera a m1; x (sin migraciones) ocupa la 2.a plaza
    ohq.finish("m1")
    svc.wait_all(0.5)
    go(svc)
    assert "m2" in ohq.started
    for t in ("m2", "x"):
        ohq.finish(t)
    svc.wait_all(5)


def test_una_tarjeta_bloqueada_libera_su_hueco_al_momento():
    # bloquearse = el job vuelve (la tarjeta pasa a blocked y deja de estar ready): su hueco queda libre sin esperar a otros
    a = Fake("a", [("t1", False, False), ("t2", False, False)], max_parallel=1)
    other = Fake("b", [("o1", False, False)], max_parallel=1)
    svc = Service([a, other], max_workers=4)
    go(svc)
    assert started(a, other) == ["t1", "o1"]
    svc.wake.clear()
    a.finish("t1")  # t1 se bloquea y vuelve; o1 sigue en marcha
    assert svc.wake.wait(2)
    go(svc)
    assert a.started == ["t1", "t2"]
    for f, t in ((a, "t2"), (other, "o1")):
        f.finish(t)
    svc.wait_all(5)


def test_el_reservado_no_gasta_plaza_general_y_respeta_max_parallel():
    gen = Fake("g", [("g1", False, False)])
    tg = Fake("tg", [("r1", False, False), ("r2", False, False)], max_parallel=2, reserved=1)
    svc = Service([gen, tg], max_workers=1)
    go(svc)
    assert set(started(gen, tg)) == {"g1", "r1"}  # max_workers 1 lleno, el reservado corre igualmente
    assert svc.slots_summary()["reserved"] == 1
    for f, t in ((gen, "g1"), (tg, "r1")):
        f.finish(t)
    svc.wait_all(5)


def test_no_lista_un_carril_sin_hueco():
    a = Fake("a", [("t1", False, False), ("t2", False, False)], max_parallel=1)
    svc = Service([a], max_workers=4)
    go(svc)
    listed = a.listed
    svc.tick()
    svc.tick()
    assert a.listed == listed  # lleno: no se gasta una consulta a Hermes por tick
    a.finish("t1")
    svc.wait_all(5)
    go(svc)
    a.finish("t2")
    svc.wait_all(5)


def test_un_carril_que_falla_al_listar_no_para_a_los_demas():
    class Roto:
        lane = SimpleNamespace(name="roto")

        def jobs(self):
            raise OSError("WinError 4551")

    ok = Fake("ok", [("t1", False, False)])
    svc = Service([Roto(), ok], max_workers=2)
    go(svc)
    assert ok.started == ["t1"] and svc.list_errors == ["WinError 4551"]
    ok.finish("t1")
    svc.wait_all(5)


def test_busy_json_refleja_los_huecos_y_se_borra_al_terminar(tmp_path):
    busy = tmp_path / "busy.json"
    a = Fake("a", [("G", True, False), ("s", False, False)])
    svc = Service([a], max_workers=4, busy_path=busy, max_big_total=2)
    go(svc)
    data = json.loads(busy.read_text(encoding="utf-8"))
    assert data["slots"]["big"] == 1 and data["slots"]["max_big"] == 2
    assert {j["kind"] for j in data["jobs"]} == {"big", "normal"}
    a.finish("G")
    a.finish("s")
    svc.wait_all(5)
    assert not busy.exists()


def test_salud_muestra_los_huecos(tmp_path):
    lock, busy = tmp_path / "lock", tmp_path / "busy.json"
    lock.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    a = Fake("a", [("G", True, False)])
    svc = Service([a], max_workers=4, busy_path=busy, max_big_total=2)
    go(svc)
    line = runner_line(lock, lambda p: True, None, busy)
    assert "grande)" in line and "grandes 1/2" in line and "normales 1/4" in line
    a.finish("G")
    svc.wait_all(5)
    assert slots_text(None) == ""


# --- clasificacion de grandes ------------------------------------------------------------------------------------

def test_senales_de_tarjeta_grande():
    assert grandes.is_big({"title": "[GRANDE] Migrar todo"})
    assert grandes.is_big({"title": "x", "body": "Tamaño: XL"})
    assert grandes.is_big({"title": "x", "body": "Runtime estimado: 2 h"})
    assert grandes.is_big({"title": "x", "body": "Runtime estimado: 90 min"})
    assert grandes.is_big({"title": "x", "labels": ["workflow"]})
    assert not grandes.is_big({"title": "x", "body": "Runtime estimado: 30 min\nTamaño: S"})
    assert not grandes.is_big({"title": "arreglo", "body": "toca el workflow de CI"})


def test_marcar_deja_la_senal_en_el_cuerpo_una_sola_vez():
    body = grandes.marcar("[GRANDE] Rehacer X", "## Objetivo\nx\n")
    assert body.rstrip().endswith("Tamaño: GRANDE") and grandes.marcar("[GRANDE] Rehacer X", body) == body
    assert grandes.marcar("Pequeña", "x") == "x"


def test_lanes_yaml_oscarhq_dos_plazas_y_hueco_grande():
    lanes = load_lanes()
    assert lanes["claude-oscarhq"].max_parallel == 2 and "supabase/migrations" in lanes["claude-oscarhq"].serialize_paths
    assert all(lane.max_big >= 1 for lane in lanes.values())
    assert grandes.touches({"body": "toca supabase/migrations/2026.sql"}, lanes["claude-oscarhq"].serialize_paths)


# --- toma atomica de una bloqueada -------------------------------------------------------------------------------

def test_retencion_caduca_y_se_libera(tmp_path):
    holds.hold("t_1", tmp_path)
    assert holds.held("t_1", tmp_path)
    assert not holds.held("t_1", tmp_path, now=time.time() + holds.HOLD_SECONDS + 5)
    assert not (tmp_path / "t_1").exists()
    holds.hold("t_2", tmp_path)
    holds.release("t_2", tmp_path)
    assert not holds.held("t_2", tmp_path)


def test_take_de_bloqueada_retiene_antes_del_unblock_y_suelta_tras_el_claim(monkeypatch, tmp_path):
    import lanes as cli
    order = []
    monkeypatch.setattr(holds, "HOLD_DIR", tmp_path)
    monkeypatch.setattr(holds, "hold", lambda t, d=tmp_path: (order.append("hold"), (d / t).write_text("1")))
    monkeypatch.setattr(holds, "release", lambda t, d=tmp_path: (order.append("release"), (d / t).unlink(missing_ok=True)))

    class H:
        def unblock(self, t):
            order.append("unblock")
            assert (tmp_path / t).exists()
            return True

        def claim(self, t, ttl):
            order.append("claim")
            assert (tmp_path / t).exists()
            return True

        def comment(self, *a):
            return True

    lane = SimpleNamespace(claim_ttl_seconds=60, name="l", base_ref="origin/main")
    monkeypatch.setattr(cli, "find_task", lambda t: (lane, H(), {"status": "blocked", "title": "x"}))
    monkeypatch.setattr(cli, "github_slug_problem", lambda lane_: None)
    monkeypatch.setattr(cli, "GitOps", lambda: SimpleNamespace(prepare_worktree=lambda lane_, t: "/wt"))
    assert cli.take("t_9") == 0
    assert order == ["hold", "unblock", "claim", "release"]


def test_el_runner_ignora_una_tarjeta_retenida(tmp_path, monkeypatch):
    from agent_lanes.runner import LaneRunner
    monkeypatch.setattr(holds, "HOLD_DIR", tmp_path)
    holds.hold("t_h")
    fake = SimpleNamespace(
        lane=SimpleNamespace(name="l", kind="implement", serialize_paths=(), big_minutes=45, max_parallel=1),
        _repo_guard=None, exclude=set(), _dependency_hold=lambda tid: False, _drop_hermes_subs=lambda tid: None,
        hermes=SimpleNamespace(list_ready=lambda name: [{"id": "t_h", "title": "x"}, {"id": "t_ok", "title": "y"}]),
        process=lambda t: "ok")
    assert [c.tid for c in LaneRunner.candidates(fake)] == ["t_ok"]
