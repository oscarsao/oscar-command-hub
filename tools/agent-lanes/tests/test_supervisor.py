"""Supervisión del runner (F0): latido, reinicio condicionado y límite de RAM por worker. Reloj y procesos simulados."""
from __future__ import annotations

import json
import subprocess
import time

from agent_lanes import drain, supervisor
from agent_lanes.supervisor import GuardiaRam, Latido, evaluar
from agent_lanes.worker import ClaudeWorker
from .test_plan_d_runtime import Sched, restarter
from .test_runner_state import LANE, ok_outcome


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def latido(tmp_path, clock, pid=100):
    return Latido(tmp_path / "latido.json", clock=clock, pid=pid)


# --- latido ------------------------------------------------------------------------------------------------

def test_heartbeat_ok_then_stale_with_simulated_clock(tmp_path):
    clk = Clock()
    lat = latido(tmp_path, clk)
    lat.beat("planificador", ["t_1"])
    data = supervisor.leer(tmp_path / "latido.json")
    assert data["busy"] == ["t_1"] and data["pid"] == 100
    assert evaluar(data, clk.t + 60, 180, lambda p: True)[0] == "ok"
    estado, detalle = evaluar(data, clk.t + 181, 180, lambda p: True)
    assert estado == "colgado" and "t_1" in detalle


def test_main_loop_beat_does_not_hide_a_stuck_scheduler(tmp_path):
    clk = Clock()
    lat = latido(tmp_path, clk)
    lat.beat("planificador", [])
    clk.t += 400
    lat.beat("principal")  # el bucle de mantenimiento sigue, el planificador no
    assert evaluar(supervisor.leer(tmp_path / "latido.json"), clk.t, 180, lambda p: True)[0] == "colgado"


def test_detects_a_stopped_runner_in_under_five_minutes(tmp_path):
    clk = Clock()
    lat = latido(tmp_path, clk)
    lat.beat("planificador", [])
    sondeo = 15.0  # el monitor mira cada ~15 s
    hasta = 0.0
    while evaluar(supervisor.leer(tmp_path / "latido.json"), clk.t, 180, lambda p: True)[0] == "ok":
        clk.t += sondeo
        hasta += sondeo
    assert hasta < 300


def test_dead_pid_missing_or_corrupt_file(tmp_path):
    assert evaluar(None, 5.0)[0] == "sin_latido"
    (tmp_path / "x.json").write_text("{no json", encoding="utf-8")
    assert supervisor.leer(tmp_path / "x.json") is None
    clk = Clock()
    latido(tmp_path, clk).beat("planificador", [])
    assert evaluar(supervisor.leer(tmp_path / "latido.json"), clk.t + 999, 180, lambda p: False)[0] == "parado"


def test_beat_never_raises_on_unwritable_path(tmp_path):
    (tmp_path / "ocupado").write_text("x")
    Latido(tmp_path / "ocupado" / "latido.json", clock=Clock()).beat("planificador", [])  # no lanza


# --- reinicio condicionado -----------------------------------------------------------------------------------

def test_restart_only_when_hung_and_idle(tmp_path):
    lanzado = []
    kw = dict(now=5000.0, lanzar=lambda: lanzado.append(1), path=tmp_path / "sup.json")
    assert supervisor.reinicio_si_colgado({"busy": []}, "ok", **kw) == "" and not lanzado
    msg = supervisor.reinicio_si_colgado({"busy": ["t_1"]}, "colgado", **kw)
    assert "t_1" in msg and "NO reinicio" in msg and not lanzado  # con workers en vuelo, solo aviso
    assert "lanzado" in supervisor.reinicio_si_colgado({"busy": []}, "colgado", **kw) and lanzado == [1]


def test_restart_cooldown_one_per_hour_and_switch_off(tmp_path):
    lanzado = []
    kw = dict(lanzar=lambda: lanzado.append(1), path=tmp_path / "sup.json")
    supervisor.reinicio_si_colgado({"busy": []}, "colgado", now=5000.0, **kw)
    assert "NO reinicio" in supervisor.reinicio_si_colgado({"busy": []}, "colgado", now=5000.0 + 1800, **kw)
    assert len(lanzado) == 1
    supervisor.reinicio_si_colgado({"busy": []}, "colgado", now=5000.0 + 3601, **kw)
    assert len(lanzado) == 2
    off = supervisor.reinicio_si_colgado({"busy": []}, "colgado", now=99999.0, habilitado=False, lanzar=lanzado.append,
                                         path=tmp_path / "otro.json")
    assert "apagado" in off and len(lanzado) == 2


def test_restart_launch_failure_is_reported_not_raised(tmp_path):
    def boom():
        raise OSError("sin schtasks")
    msg = supervisor.reinicio_si_colgado({"busy": []}, "colgado", now=1e6, lanzar=boom, path=tmp_path / "s.json")
    assert "no pude" in msg and not (tmp_path / "s.json").exists()


def test_colgado_sin_workers_requires_stale_and_idle(tmp_path):
    clk = Clock()
    lat = latido(tmp_path, clk)
    f = tmp_path / "latido.json"
    lat.beat("planificador", ["t_1"])
    assert not supervisor.colgado_sin_workers(clk.t + 999, 180, f, lambda p: True)  # hay workers
    lat.beat("planificador", [])
    assert not supervisor.colgado_sin_workers(clk.t + 60, 180, f, lambda p: True)   # late
    assert supervisor.colgado_sin_workers(clk.t + 999, 180, f, lambda p: True)


def test_restarter_with_colgado_skips_the_ack_wait(tmp_path):
    s = Sched(tmp_path)
    r = drain.Restarter(pid_alive=lambda p: p in s.alive, drain_file=tmp_path / "drain.json",
                        ack_file=tmp_path / "ack.json", lock_file=tmp_path / "runner.lock", run=s.run,
                        sleep=lambda x: None, now=lambda: 1000.0, out=lambda x: None, colgado=lambda: True)
    assert r.restart(timeout=600) == 0 and s.calls == ["/End", "/Run"]  # sin acuse: el runner está colgado


def test_restarter_without_colgado_still_waits_for_ack(tmp_path):
    s = Sched(tmp_path)
    t = {"now": 0.0}

    def sleep(sec):
        t["now"] += sec
    assert restarter(tmp_path, s, sleep=sleep, clock=lambda: t["now"]).restart(timeout=30) == 1 and s.calls == []


# --- RAM por worker ------------------------------------------------------------------------------------------

def test_guardia_ram_cuts_once_above_limit_and_alerts():
    lecturas, killed, avisos = iter([1000.0, 5000.0, 6000.0]), [], []
    g = GuardiaRam(77, "t_9", 4096, medir=lambda pid: next(lecturas), kill=killed.append, alert=avisos.append)
    assert not g.revisar() and killed == []
    assert g.revisar() and killed == [77] and g.exceeded == 5000.0
    assert not g.revisar() and killed == [77] and len(avisos) == 1  # un solo corte y un solo aviso
    assert "t_9" in avisos[0] and "4.0 GB" in avisos[0]


def test_guardia_ram_ignores_unmeasurable_and_zero_limit():
    killed = []
    assert not GuardiaRam(1, "t", 4096, medir=lambda p: None, kill=killed.append).revisar()
    assert not GuardiaRam(1, "t", 0, medir=lambda p: 9e9, kill=killed.append).revisar() and killed == []


def test_guardia_ram_alert_failure_does_not_raise():
    def mal(_):
        raise RuntimeError("sin red")
    g = GuardiaRam(1, "t", 10, medir=lambda p: 99.0, kill=lambda p: None, alert=mal)
    assert g.revisar() and g.exceeded == 99.0


def _fake_claude(sleep_s):
    def run(args, on_start=None, **kw):
        if on_start:
            on_start(4242)
        time.sleep(sleep_s)
        return subprocess.CompletedProcess(args, 0, json.dumps({"subtype": "success", "is_error": False,
                                                                "structured_output": ok_outcome().structured}), "")
    return run


def test_worker_over_ram_limit_is_cut_with_alert(tmp_path, monkeypatch):
    killed, avisos = [], []
    monkeypatch.setattr(supervisor, "rss_arbol_mb", lambda pid: 9000.0)
    monkeypatch.setattr("agent_lanes.proc.kill_tree", killed.append)
    w = ClaudeWorker(runner=_fake_claude(0.4), ram_mb=4096, ram_intervalo=0.02, alert=avisos.append)
    out = w.run(LANE, {"id": "t_9", "title": "T", "body": ""}, "C:/wt", "sid", timeout=60)
    assert not out.ok and out.subtype == "worker_memoria" and "9000" in out.raw_error
    assert killed == [4242] and len(avisos) == 1


def test_worker_under_limit_or_without_limit_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "rss_arbol_mb", lambda pid: 100.0)
    for kw in ({"ram_mb": 4096, "ram_intervalo": 0.02}, {}):
        out = ClaudeWorker(runner=_fake_claude(0.1), **kw).run(LANE, {"id": "t_9", "title": "T", "body": ""},
                                                               "C:/wt", "sid", timeout=60)
        assert out.ok


def test_real_lanes_yaml_declares_the_supervision_settings():
    from agent_lanes.config import load_runner_settings
    s = load_runner_settings()
    assert s["latido_parado_segundos"] < 300 and s["worker_ram_mb"] > 0 and s["reinicio_auto_colgado"] is True
