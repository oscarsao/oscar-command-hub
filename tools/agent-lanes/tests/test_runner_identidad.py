"""Incidente del 14-11-2026: Windows reutilizó el PID del runner muerto (StartMenuExperienceHost) y candado, latido y
estado externo lo dieron por vivo ~2 h 45 min. Aquí: identidad del proceso, candado retomable, latido envejecido en
el estado externo y aviso «runner colgado» sin el tope diario. Sin red, sin Telegram, sin hermes."""
import importlib.util
import json
import os
import subprocess
import sys
import time
import types
from datetime import datetime
from pathlib import Path

import pytest

from agent_lanes import procid, supervisor
from agent_lanes.service import acquire_single_instance
from agent_lanes.status import runner_alive, runner_line

MON = Path(__file__).resolve().parents[2] / "monitor" / "monitor.py"


# --- identidad del proceso ----------------------------------------------------------------------------------------

def test_es_el_runner_distingue_pid_reutilizado():
    runner = lambda pid: (1000.0, "C:/Python312/pythonw.exe C:/x/agent-lanes/runner.py --all")  # noqa: E731
    otro = lambda pid: (5000.0, "C:\\Windows\\SystemApps\\StartMenuExperienceHost.exe -Embedding")  # noqa: E731
    nacido_despues = lambda pid: (5000.0, "python runner.py")  # noqa: E731
    assert procid.es_el_runner(10976, 1001.0, runner) is True
    assert procid.es_el_runner(10976, 1001.0, otro) is False             # otra línea de comandos
    assert procid.es_el_runner(10976, 1001.0, nacido_despues) is False   # arrancó DESPUÉS de escribirse el candado
    assert procid.es_el_runner(10976, None, runner) is True              # candado antiguo sin `started`: solo cmdline
    assert procid.es_el_runner(10976, 1001.0, lambda pid: None) is None  # no se puede saber
    assert procid.runner_vivo(10976, 1001.0, lambda p: True, otro) is False
    assert procid.runner_vivo(10976, 1001.0, lambda p: True, lambda p: None) is True  # sin datos: manda pid_alive


# --- candado ------------------------------------------------------------------------------------------------------

def test_candado_con_pid_de_otro_proceso_se_retoma_con_aviso(tmp_path, caplog):
    lock = tmp_path / "runner.lock"
    lock.write_text(json.dumps({"pid": 10976, "started": 1000.0}), encoding="utf-8")
    with caplog.at_level("WARNING", logger="agent_lanes"):
        assert acquire_single_instance(lock, pid_alive=lambda p: True, identidad=lambda p, s: False)
    assert "PID reutilizado" in caplog.text
    assert json.loads(lock.read_text(encoding="utf-8"))["pid"] == os.getpid()


def test_candado_de_runner_vivo_se_respeta(tmp_path):
    lock = tmp_path / "runner.lock"
    lock.write_text(json.dumps({"pid": 10976, "started": 1000.0}), encoding="utf-8")
    assert not acquire_single_instance(lock, pid_alive=lambda p: True, identidad=lambda p, s: True)
    assert not acquire_single_instance(lock, pid_alive=lambda p: True, identidad=lambda p, s: None)


def _hijo(*extra):
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", *extra],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_candado_con_proceso_real_ajeno_se_retoma_y_con_runner_py_se_respeta(tmp_path):
    pytest.importorskip("psutil")
    lock = tmp_path / "runner.lock"
    ajeno = _hijo()
    try:
        lock.write_text(json.dumps({"pid": ajeno.pid, "started": time.time() + 1}), encoding="utf-8")
        assert acquire_single_instance(lock)  # pid_alive real: el PID vive pero no es runner.py
    finally:
        ajeno.kill()
        ajeno.wait()
    runner = _hijo("runner.py")  # su línea de comandos lleva runner.py
    try:
        lock.write_text(json.dumps({"pid": runner.pid, "started": time.time() + 1}), encoding="utf-8")
        assert not acquire_single_instance(lock)
    finally:
        runner.kill()
        runner.wait()


# --- estado del runner (lanes.py status) -------------------------------------------------------------------------

def test_status_marca_parado_un_pid_reutilizado(tmp_path):
    lock = tmp_path / "runner.lock"
    lock.write_text(json.dumps({"pid": 10976, "started": 1000.0}), encoding="utf-8")
    assert runner_line(lock, lambda p: True).startswith("runner: VIVO")
    line = runner_line(lock, lambda p: True, identidad=lambda p, s: False)
    assert line.startswith("runner: PARADO") and "PID reutilizado" in line
    assert runner_alive(lock, lambda p: True) is True
    assert runner_alive(lock, lambda p: True, lambda p, s: False) is False


# --- latido interno ----------------------------------------------------------------------------------------------

def test_evaluar_pid_reutilizado_es_parado_y_edades():
    data = {"pid": 10976, "desde": 1000.0, "planificador_at": 1100.0, "principal_at": 1150.0}
    assert supervisor.evaluar(data, 1200.0, 300, lambda p: True, lambda p, d: True)[0] == "ok"
    estado, detalle = supervisor.evaluar(data, 1200.0, 300, lambda p: True, lambda p, d: False)
    assert estado == "parado" and "PID reutilizado" in detalle
    assert supervisor.evaluar(data, 1500.0, 300, lambda p: True, lambda p, d: True)[0] == "colgado"
    assert supervisor.edades(data, 1500.0) == {"planificador_edad_s": 400, "principal_edad_s": 350}
    assert supervisor.edades(None, 1500.0) == {"planificador_edad_s": None, "principal_edad_s": None}


# --- monitor: estado externo y aviso -----------------------------------------------------------------------------

@pytest.fixture
def monitor(tmp_path, monkeypatch):
    pytest.importorskip("psutil")
    pytest.importorskip("rich")
    spec = importlib.util.spec_from_file_location("monitor_identidad", MON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", tmp_path)
    monkeypatch.setattr(mod, "ALERTS_LOG", tmp_path / "alerts.log")
    monkeypatch.setattr(mod, "RUNNER_LOCK", tmp_path / "runner.lock")
    monkeypatch.setattr(mod, "LANES", tmp_path)
    return mod


def _latido(tmp_path, **kw):
    d = {"pid": os.getpid(), "desde": time.time() - 4000, "planificador_at": time.time() - 3000,
         "principal_at": time.time() - 2900, "busy": [], **kw}
    (tmp_path / ".state").mkdir(exist_ok=True)
    (tmp_path / ".state" / "runner-latido.json").write_text(json.dumps(d), encoding="utf-8")


def _monitor(monitor, tmp_path, posts):
    pushed = []
    m = monitor.Monitor(write_log=True, push=pushed.append)
    m.now = lambda: datetime(2026, 11, 16, 12, 0)
    m.read_battery = lambda: None
    m.latido = monitor.disp.Latido(api_url="https://a", api_key="k", cada=0,
                                   post=lambda u, b, h: posts.append(b) or 200)
    return m, pushed


def test_latido_externo_marca_colgado_con_la_edad_del_planificador(monitor, tmp_path, monkeypatch):
    posts = []
    m, _ = _monitor(monitor, tmp_path, posts)
    m.lanes_text = "runner: VIVO (pid 1)"
    monkeypatch.setattr(procid, "proceso_info", lambda pid: (time.time() - 5000, "python runner.py"))
    _latido(tmp_path)  # el planificador lleva ~50 min sin latir con el PID «vivo»
    m.heartbeat()
    det = posts[0]["detalle"]
    assert det["colgado"] is True and det["runner"] is False and det["runner_vivo"] is True
    assert 2900 <= det["planificador_edad_s"] <= 3100 and 2800 <= det["principal_edad_s"] <= 3000


def test_latido_externo_pid_reutilizado_no_es_runner(monitor, tmp_path, monkeypatch):
    posts = []
    m, _ = _monitor(monitor, tmp_path, posts)
    m.lanes_text = ""
    monkeypatch.setattr(procid, "proceso_info", lambda pid: (time.time(), "StartMenuExperienceHost.exe"))
    _latido(tmp_path)
    m.heartbeat()
    det = posts[0]["detalle"]
    assert det["runner"] is False and det["estado"] == "parado"


def test_latido_externo_sano(monitor, tmp_path, monkeypatch):
    posts = []
    m, _ = _monitor(monitor, tmp_path, posts)
    m.lanes_text = "runner: VIVO (pid 1)"
    monkeypatch.setattr(procid, "proceso_info", lambda pid: (time.time() - 5000, "python runner.py"))
    _latido(tmp_path, planificador_at=time.time() - 10, principal_at=time.time() - 10)
    m.heartbeat()
    det = posts[0]["detalle"]
    assert det["runner"] is True and det["colgado"] is False and det["planificador_edad_s"] <= 12


def test_aviso_runner_colgado_llega_aunque_ya_hubiera_salido_otro_ese_dia(monitor, tmp_path, monkeypatch):
    """Causa de que el aviso del 14-11 no llegara: tope de 1 aviso/día por clave y por huella (texto sin cifras)."""
    m, pushed = _monitor(monitor, tmp_path, [])
    monkeypatch.setattr(procid, "proceso_info", lambda pid: (time.time() - 5000, "python runner.py"))
    monkeypatch.setattr(monitor, "alert_cap_seconds", lambda: 24 * 3600)
    _latido(tmp_path)
    m._runner_colgado(False)
    assert len(pushed) == 1 and "colgado" in pushed[0]
    t0 = time.time()
    monkeypatch.setattr(monitor.time, "time", lambda: t0 + 1900)  # pasada la repetición de 30 min, mismo día
    m._runner_colgado(False)
    assert len(pushed) == 2


def test_aviso_runner_colgado_se_dispara_de_madrugada(monitor, tmp_path, monkeypatch):
    m, pushed = _monitor(monitor, tmp_path, [])
    m.now = lambda: datetime(2026, 11, 14, 3, 0)
    monkeypatch.setattr(procid, "proceso_info", lambda pid: (time.time() - 5000, "python runner.py"))
    _latido(tmp_path)
    m._runner_colgado(False)
    assert len(pushed) == 1  # runner-hung es OUTAGE_KEY: sale aunque sea horario de silencio


def test_runner_pid_vivo_exige_runner_py(monitor, tmp_path):
    psutil = pytest.importorskip("psutil")
    lock = tmp_path / "runner.lock"
    ajeno = _hijo()
    try:
        lock.write_text(json.dumps({"pid": ajeno.pid, "started": time.time() + 1}), encoding="utf-8")
        assert psutil.pid_exists(ajeno.pid)
        assert monitor.disp.runner_pid_vivo(lock) is False  # PID vivo y de Python, pero no es runner.py
    finally:
        ajeno.kill()
        ajeno.wait()
    runner = _hijo("runner.py")
    try:
        lock.write_text(json.dumps({"pid": runner.pid, "started": time.time() + 1}), encoding="utf-8")
        assert monitor.disp.runner_pid_vivo(lock) is True
        lock.write_text(json.dumps({"pid": runner.pid, "started": time.time() - 3600}), encoding="utf-8")
        assert monitor.disp.runner_pid_vivo(lock) is False  # nació después del candado: PID reutilizado
    finally:
        runner.kill()
        runner.wait()
