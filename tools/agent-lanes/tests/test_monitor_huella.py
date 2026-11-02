"""Avisos del monitor deduplicados por huella (t_88f59559): el mismo aviso no se repite en la ventana, aunque cambie la
clave o las cifras; pasada la ventana (o con ventana 0) sí sale. Dobles: sin red ni Telegram."""
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

pytest.importorskip("psutil")
pytest.importorskip("rich")

MON = Path(__file__).resolve().parents[2] / "monitor" / "monitor.py"


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("monitor_huella", MON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "STATE", tmp_path)
    monkeypatch.setattr(mod, "ALERTS_LOG", tmp_path / "alerts.log")
    monkeypatch.setattr(mod, "LANES", tmp_path / "lanes")
    return mod


def make(monitor):
    pushed = []
    m = monitor.Monitor(write_log=True, push=pushed.append)
    m.now = lambda: datetime(2026, 11, 2, 12, 0)
    return m, pushed


def test_huella_ignora_cifras_y_espacios(monitor):
    f = monitor.alert_fingerprint
    assert f("AVISO", "RAM al 86 %  alta") == f("AVISO", "RAM al 87 % alta")
    assert f("AVISO", "RAM alta") != f("ALTA", "RAM alta")
    assert f("AVISO", "RAM alta") != f("AVISO", "disco lleno")


def test_mismo_aviso_con_otra_clave_no_se_repite_en_la_ventana(monitor):
    m, pushed = make(monitor)
    m.alert("AVISO", "Limpieza pendiente: 14 ramas", "limpieza-a")
    m.alert("AVISO", "Limpieza pendiente: 15 ramas", "limpieza-b")
    assert pushed == ["Limpieza pendiente: 14 ramas"]
    m.alert("AVISO", "Otra cosa distinta", "otra")
    assert len(pushed) == 2


def test_pasada_la_ventana_vuelve_a_salir_y_sobrevive_a_reinicios(monitor):
    m, pushed = make(monitor)
    m.alert("AVISO", "algo roto 1", "k1")
    m2, pushed2 = make(monitor)  # reinicio: lee alert_daily.json (con la huella)
    m2.alert("AVISO", "algo roto 2", "k2")
    assert pushed == ["algo roto 1"] and pushed2 == []
    h = "huella:" + monitor.alert_fingerprint("AVISO", "algo roto 2")
    m2._seen[h] -= 25 * 3600
    m2.alert("AVISO", "algo roto 3", "k3")
    assert pushed2 == ["algo roto 3"]


def test_ventana_configurable_y_cero_desactiva(monitor, tmp_path):
    (tmp_path / "lanes").mkdir()
    (tmp_path / "lanes" / "ajustes_hub.json").write_text(
        json.dumps({"valores": {"avisos.huella_ventana_horas": 0}}), encoding="utf-8")
    m, pushed = make(monitor)
    m.alert("AVISO", "x 1", "k1")
    m.alert("AVISO", "x 2", "k2")
    assert len(pushed) == 2


def test_resuelto_e_info_no_se_filtran(monitor):
    m, pushed = make(monitor)
    m.alert("RESUELTO", "ok", "a-ok", every=0)
    m.alert("RESUELTO", "ok", "b-ok", every=0)
    assert pushed == ["ok", "ok"]
