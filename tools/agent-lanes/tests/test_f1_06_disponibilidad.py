"""F1-06 (plan del hub 25-10, 3e parcial): latido a Oscar HQ y Kuma, batería, tareas diarias perdidas, «runner parado»
solo si el PID ha muerto, choques aditivos a DEBUG y rotación diaria del log. Sin red, sin schtasks ni psutil reales."""
import importlib.util
import json
import logging
import os
import sys
import types
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import pytest

pytest.importorskip("psutil")
pytest.importorskip("rich")

MON = Path(__file__).resolve().parents[2] / "monitor" / "monitor.py"


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("monitor_f106", MON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", tmp_path)
    monkeypatch.setattr(mod, "ALERTS_LOG", tmp_path / "alerts.log")
    monkeypatch.setattr(mod, "RUNNER_LOCK", tmp_path / "runner.lock")
    return mod


def make(monitor, tmp_path, hour=12):
    pushed = []
    m = monitor.Monitor(write_log=True, push=pushed.append)
    m.now = lambda: datetime(2026, 10, 26, hour, 0)  # lunes
    m.resumen_path = tmp_path / "resumen_info.json"
    return m, pushed


def battery(pct, plugged):
    return lambda: types.SimpleNamespace(percent=pct, power_plugged=plugged)


# --- batería -----------------------------------------------------------------------------------------------

def test_bateria_avisa_a_los_10_min_alta_bajo_20_y_resuelve(monitor, tmp_path, monkeypatch):
    m, pushed = make(monitor, tmp_path)
    t = [1_000_000.0]
    monkeypatch.setattr(monitor.time, "time", lambda: t[0])
    m.read_battery = battery(32, False)
    m.check_battery()
    assert pushed == []  # recién desenchufado: aún no
    t[0] += 601
    m.check_battery()
    assert len(pushed) == 1 and "batería (32 %)" in pushed[0]
    m.read_battery = battery(15, False)
    m.check_battery()
    assert "al 15 %" in pushed[-1] and "sin corriente" in pushed[-1]
    m.read_battery = battery(16, True)
    m.check_battery()
    assert sum("Resuelto" in p for p in pushed) == 2
    data = json.loads((tmp_path / "resumen_info.json").read_text(encoding="utf-8"))
    assert [e["tipo"] for e in data["eventos"]] == ["bateria"]  # una línea por desenchufe para el resumen


def test_sobremesa_sin_bateria_no_avisa(monitor, tmp_path):
    m, pushed = make(monitor, tmp_path)
    m.read_battery = lambda: None
    assert m.check_battery() is None and pushed == []


def test_resumen_del_monitor_lo_lee_el_runner(monitor, tmp_path):
    from agent_lanes.resumen_carriles import DiarioInfo
    path = tmp_path / "resumen_info.json"
    monitor.disp.anotar_resumen(path, "relanzada", "«Auditor» relanzada", "Auditor",
                                now=lambda: datetime(2026, 10, 26, 9, 0))
    monitor.disp.anotar_resumen(path, "relanzada", "otra vez", "Auditor", now=lambda: datetime(2026, 10, 26, 9, 5))
    sent = []
    d = DiarioInfo(lambda text: sent.append(text) or True, path, now=lambda: datetime(2026, 10, 26, 10, 0).astimezone())
    assert d.tick() and "«Auditor» relanzada" in sent[0] and "otra vez" not in sent[0]


# --- tareas diarias perdidas -------------------------------------------------------------------------------

def test_laborable_930_sin_linea_relanza_una_vez(monitor, tmp_path):
    disp = monitor.disp
    log = tmp_path / "brief.log"
    log.write_text("2026-10-23 08:00:01 INFO enviado\n", encoding="utf-8")  # viernes; hoy lunes, nada
    calls = []
    run = lambda args: calls.append(args) or types.SimpleNamespace(returncode=0)  # noqa: E731
    now = [datetime(2026, 10, 26, 9, 0)]
    td = disp.TareasDiarias((disp.TareaDiaria("Resumen diario Hermes", log, (8, 0), 90),), estado=tmp_path / "td.json",
                            run=run, now=lambda: now[0])
    assert td.tick() == []  # 9:00 < 8:00 + 90 min
    now[0] = datetime(2026, 10, 26, 9, 30)
    assert td.tick() == ["Resumen diario Hermes"]
    assert calls == [["schtasks", "/Run", "/TN", "Resumen diario Hermes"]]
    now[0] = datetime(2026, 10, 26, 10, 0)
    assert td.tick() == [] and len(calls) == 1  # una vez al día
    # fin de semana, o con línea de hoy: nada
    now[0] = datetime(2026, 10, 25, 11, 0)  # domingo
    assert td.tick() == []
    log.write_text("2026-10-27 08:00:01 INFO enviado\n", encoding="utf-8")
    now[0] = datetime(2026, 10, 27, 11, 0)
    assert td.tick() == []


def test_monitor_avisa_info_de_la_relanzada(monitor, tmp_path):
    m, pushed = make(monitor, tmp_path)
    m.tareas = types.SimpleNamespace(tick=lambda: ["Auditor diario carriles"])
    assert m.check_daily_tasks() == ["Auditor diario carriles"]
    assert pushed == []  # INFO: no va al DM al momento, va al resumen
    data = json.loads((tmp_path / "resumen_info.json").read_text(encoding="utf-8"))
    assert data["eventos"][0]["tipo"] == "relanzada"


# --- runner parado solo con el PID muerto ---------------------------------------------------------------------

def test_pid_vivo_y_status_lento_no_es_runner_parado(monitor, tmp_path, monkeypatch):
    m, pushed = make(monitor, tmp_path)
    monkeypatch.setattr(monitor, "LANES", tmp_path)
    monkeypatch.setattr(monitor.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(stdout="", stderr="lanes.py status: timeout"))
    (tmp_path / "runner.lock").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    t = [1_000_000.0]
    monkeypatch.setattr(monitor.time, "time", lambda: t[0])
    for k in range(30):
        t[0] += 20
        m.lanes_at = 0
        m.refresh_lanes()
    assert pushed == []
    (tmp_path / "runner.lock").write_text(json.dumps({"pid": 0}), encoding="utf-8")  # sin PID: manda el status
    for k in range(20):
        t[0] += 20
        m.lanes_at = 0
        m.refresh_lanes()
    assert pushed and "runner de carriles" in pushed[-1]


def test_runner_pid_vivo(monitor, tmp_path):
    lock = tmp_path / "runner.lock"
    assert monitor.disp.runner_pid_vivo(lock) is False
    lock.write_text(json.dumps({"pid": 123}), encoding="utf-8")
    assert monitor.disp.runner_pid_vivo(lock, pid_exists=lambda p: p == 123) is True
    assert monitor.disp.runner_pid_vivo(lock, pid_exists=lambda p: False) is False


# --- latido ---------------------------------------------------------------------------------------------------

def test_latido_cada_5_min_a_oscar_hq_y_kuma(monitor):
    disp = monitor.disp
    posts, gets, t = [], [], [1000.0]
    lat = disp.Latido(api_url="https://api.example/", api_key="k-secreta", kuma_url="https://kuma.example/push/abc",
                      clock=lambda: t[0], post=lambda u, b, h: posts.append((u, b, h)) or 200,
                      get=lambda u: gets.append(u) or 200)
    assert lat.tick({"runner": True}) == {"ohq": True, "kuma": True}
    assert posts[0][0] == "https://api.example/api/hub/latido" and posts[0][1]["fuente"] == "pc"
    assert posts[0][2] == {"X-API-Key": "k-secreta"} and gets == ["https://kuma.example/push/abc?status=up&msg=OK"]
    t[0] += 200
    assert lat.tick() is None  # aún no toca
    t[0] += 101
    assert lat.tick() is not None and len(posts) == 2
    assert all("k-secreta" not in x and "abc" not in x for x in lat.estado())


def test_latido_sin_claves_queda_desactivado_y_fallo_no_lanza(monitor):
    disp = monitor.disp
    lat = disp.Latido.desde_env({}, api_url_defecto="https://api.example")
    assert lat.tick(force=True) == {}
    assert "desactivado (falta OHQ_API_KEY)" in lat.estado()[0] and "falta KUMA_PUSH_URL" in lat.estado()[1]

    def boom(*a):
        raise OSError("sin red")

    lat = disp.Latido(api_url="https://a", api_key="k", post=boom, get=lambda u: 503, kuma_url="https://k")
    assert lat.tick(force=True) == {"ohq": False, "kuma": False}
    assert lat.fallos == {"ohq": "OSError", "kuma": "HTTP 503"}


def test_monitor_heartbeat_avisa_tras_fallos_seguidos(monitor, tmp_path):
    m, pushed = make(monitor, tmp_path)
    m.read_battery = lambda: None
    m.latido = monitor.disp.Latido(api_url="https://a", api_key="k", post=lambda *a: 500, cada=0)
    for _ in range(6):
        m.heartbeat()
    assert any("latido del PC a Oscar HQ falla (HTTP 500)" in a[2] for a in m.alerts)


# --- logs -------------------------------------------------------------------------------------------------------

def test_runner_log_rota_a_diario_14_dias(tmp_path):
    spec = importlib.util.spec_from_file_location("runner_f106", Path(__file__).resolve().parents[1] / "runner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    h = mod.log_handler(tmp_path / "runner.log")
    try:
        assert isinstance(h, TimedRotatingFileHandler) and h.when == "MIDNIGHT" and h.backupCount == 14
    finally:
        h.close()


def test_choques_aditivos_no_salen_en_info(caplog):
    from tests.test_choques import CMD, FakeGit, run
    g = FakeGit({"lane/t_1": (1_800_000_000, "x", [CMD]), "lane/t_2": (1_800_000_000, "y", [CMD])},
                reales={frozenset(("lane/t_1", "lane/t_2")): False})
    with caplog.at_level(logging.INFO, logger="agent_lanes"):
        assert run(g) == []
    assert not [r for r in caplog.records if "aditivo" in r.getMessage() and r.levelno >= logging.INFO]


def test_auditor_lee_tambien_los_logs_rotados(tmp_path):
    import auditor
    (tmp_path / "runner.log.2026-10-25").write_text("2026-10-25 22:00:00,000 ERROR ayer\n", encoding="utf-8")
    (tmp_path / "runner.log").write_text("2026-10-26 07:00:00,000 ERROR hoy\n", encoding="utf-8")
    since = datetime(2026, 10, 25, 7, 40).timestamp()
    lines = auditor.read_rotated(tmp_path / "runner.log", since)
    assert len(lines) == 2
