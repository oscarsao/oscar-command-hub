"""Regla «hilo principal sin latir, planificador vivo» (t_564166df): umbral, deduplicación y caso normal.
Reloj y estado simulados: sin red, sin Telegram, sin hermes."""
import importlib.util
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from agent_lanes import supervisor
from agent_lanes.supervisor import principal_sin_latir

MON = Path(__file__).resolve().parents[2] / "monitor" / "monitor.py"


def estado(now, principal_edad, planificador_edad=10, pid=100):
    return {"pid": pid, "desde": now - 5000, "principal_at": now - principal_edad,
            "planificador_at": now - planificador_edad, "busy": []}


def test_avisa_si_principal_pasa_el_umbral_con_planificador_al_dia():
    ok, detalle = principal_sin_latir(estado(1000.0, 660), 1000.0, 600, 300)
    assert ok and "hilo principal sin latir, planificador vivo" in detalle and "11 min" in detalle


def test_no_avisa_con_edad_normal_ni_justo_en_el_umbral():
    assert principal_sin_latir(estado(1000.0, 30), 1000.0, 600, 300) == (False, "")
    assert principal_sin_latir(estado(1000.0, 600), 1000.0, 600, 300) == (False, "")


def test_no_avisa_si_el_planificador_tambien_esta_parado_ni_sin_datos():
    assert principal_sin_latir(estado(1000.0, 900, planificador_edad=400), 1000.0, 600, 300)[0] is False
    assert principal_sin_latir(None, 1000.0) == (False, "")
    assert principal_sin_latir({"pid": 1, "planificador_at": 990.0, "principal_at": None}, 1000.0) == (False, "")


def test_umbral_por_defecto_y_configurable():
    assert supervisor.DEFAULT_PRINCIPAL_PARADO_SECS == 600 and supervisor.AVISO_COOLDOWN == 1800
    from agent_lanes.config import load_runner_settings
    assert load_runner_settings()["principal_parado_segundos"] == 600
    assert principal_sin_latir(estado(1000.0, 120), 1000.0, 100, 300)[0] is True  # umbral bajado


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    pytest.importorskip("psutil")
    pytest.importorskip("rich")
    spec = importlib.util.spec_from_file_location("monitor_principal", MON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", tmp_path)
    monkeypatch.setattr(mod, "ALERTS_LOG", tmp_path / "alerts.log")
    lanes = tmp_path / "lanes"
    (lanes / ".state").mkdir(parents=True)
    monkeypatch.setattr(mod, "LANES", lanes)
    return mod


def latir(monitor, principal_edad):
    (monitor.LANES / ".state" / "runner-latido.json").write_text(
        json.dumps(estado(time.time(), principal_edad, pid=os.getpid())), encoding="utf-8")


def make(monitor):
    enviados = []
    m = monitor.Monitor(write_log=True, push=enviados.append)
    m.now = lambda: datetime(2026, 11, 19, 12, 0)
    return m, enviados


def test_monitor_emite_la_alerta_una_sola_vez(monitor):
    latir(monitor, 3960)  # 66 min, como el caso real del 18-11
    m, enviados = make(monitor)
    for _ in range(4):
        m._runner_colgado(False)
    assert len(enviados) == 1 and "hilo principal sin latir, planificador vivo" in enviados[0]


def test_monitor_no_avisa_con_principal_normal(monitor):
    latir(monitor, 20)
    m, enviados = make(monitor)
    for _ in range(3):
        m._runner_colgado(False)
    assert enviados == []
