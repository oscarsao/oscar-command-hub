"""Monitor (30-09): listado de tareas caído >10 min = ALTA, RAM como condición, silencio nocturno."""
import importlib.util
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("psutil")
pytest.importorskip("rich")

MON = Path(__file__).resolve().parents[2] / "monitor" / "monitor.py"


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("monitor_under_test", MON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", tmp_path)
    monkeypatch.setattr(mod, "ALERTS_LOG", tmp_path / "alerts.log")
    return mod


def line(ts: float, msg="lanes: no se pudieron listar tareas: [WinError 2]"):
    return f"{datetime.fromtimestamp(ts):%Y-%m-%d %H:%M:%S},123 ERROR {msg}"


def make(monitor, hour=12):
    pushed = []
    m = monitor.Monitor(write_log=True, push=pushed.append)
    m.now = lambda: datetime(2026, 9, 30, hour, 0)
    return m, pushed


def test_list_failures_over_10_min_open_alta_and_resolve(monitor):
    m, pushed = make(monitor)
    now = time.time()
    for k in range(0, 12):  # 11 min de fallos, uno por minuto, hasta ahora
        m.note_runner_line(line(now - (11 - k) * 60))
    m.check_list_failures()
    assert pushed and "sin poder listar tareas" in pushed[0]
    m._list_fail_last = now - 600  # sin fallos nuevos desde hace 10 min: recuperado
    m.check_list_failures()
    assert "Resuelto" in pushed[-1]


def test_short_list_failure_does_not_alert(monitor):
    m, pushed = make(monitor)
    now = time.time()
    for k in range(3):
        m.note_runner_line(line(now - (2 - k) * 60))
    m.check_list_failures()
    assert pushed == []


def test_pc_check_no_longer_pushes_ram_to_dm(monitor):
    m, pushed = make(monitor)
    vm = type("VM", (), {"percent": 95.0, "used": 15 * 2**30, "total": 16 * 2**30})()
    monitor.psutil.virtual_memory = lambda: vm
    for _ in range(6):
        m.pc()
    assert pushed == []  # el aviso continuo ya no existe
    assert any("RAM al 95%" in t for _, _, t in m.alerts)  # solo visible en la ventana


def test_ram_report_every_4h_and_not_at_night(monitor, tmp_path):
    monitor.ram_text = lambda top=5: "🧠 RAM 50 %"
    m, pushed = make(monitor, hour=12)
    m.ram_report()
    m.ram_report()  # recién enviado: no repite
    assert pushed == ["🧠 RAM 50 %"]
    (tmp_path / "ram_report.ts").write_text(str(time.time() - 4 * 3600 - 5))
    m.ram_report()
    assert len(pushed) == 2
    n, npushed = make(monitor, hour=2)
    (tmp_path / "ram_report.ts").write_text("0")
    n.ram_report()
    assert npushed == []  # silencio nocturno: se aplaza


def test_ram_text_lists_top_processes(monitor):
    out = monitor.ram_text(top=2)
    assert out.startswith(("🧠 RAM", "⚠️ 🧠 RAM")) and out.count("•") <= 2


def test_quiet_hours_hold_non_outage_but_let_real_outages_through(monitor):
    m, pushed = make(monitor, hour=2)
    m.alert("ALTA", "RAM al 95%", "ram")
    assert pushed == []
    m.alert("ALTA", "El runner está parado", "runner-dead")
    assert pushed == ["El runner está parado"]
    m.now = lambda: datetime(2026, 9, 30, 8, 5)
    m.flush_quiet()
    assert "RAM al 95%" in pushed[-1] and "Durante la noche" in pushed[-1]


def test_recovery_reruns_brief_and_auditor_only_if_last_run_failed(monitor):
    m, pushed = make(monitor)
    calls = []

    def fake_run(args):
        calls.append(args)
        if args[1] == "/Query":
            code = "1" if args[3] == "Resumen diario Hermes" else "0"  # el brief falló, el auditor fue bien
            return type("CP", (), {"stdout": f"TaskName: x\nLast Result:   {code}\n", "returncode": 0})()
        return type("CP", (), {"stdout": "", "returncode": 0})()

    m.run = fake_run
    now = time.time()
    for k in range(12):
        m.note_runner_line(line(now - (11 - k) * 60))
    m.check_list_failures()
    assert not any(a[1] == "/Run" for a in calls)  # durante la caída no se relanza nada
    m._list_fail_last = now - 600
    m.check_list_failures()
    assert [a[3] for a in calls if a[1] == "/Run"] == ["Resumen diario Hermes"]
    m.check_list_failures()  # ya recuperado: no se repite
    assert len([a for a in calls if a[1] == "/Run"]) == 1
