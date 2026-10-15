"""Vigilante de runner ciego (agent_lanes/ciego.py): umbral, aviso único, reinicio 1/hora, recuperación, /salud."""
from __future__ import annotations

import json

from agent_lanes import ciego, health
from agent_lanes.service import Service

E4551 = "list failed: [WinError 4551] Una directiva de Control de aplicaciones bloqueó este archivo"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def minutes(self, n):
        self.t += n * 60


def make(tmp_path, clock, minutes=5):
    alerts, restarts = [], []
    v = ciego.Vigilante(path=tmp_path / "ciego.json", minutes=minutes, alert=alerts.append,
                        restart=lambda: restarts.append(1), now=clock)
    return v, alerts, restarts


def episodes(tmp_path):
    return json.loads((tmp_path / "ciego.json").read_text(encoding="utf-8"))["episodios"]


def test_bajo_el_umbral_no_alerta(tmp_path):
    c = Clock()
    v, alerts, restarts = make(tmp_path, c)
    for _ in range(4):
        v.observe([E4551])
        c.minutes(1)
    assert alerts == [] and restarts == []
    assert not (tmp_path / "ciego.json").exists()


def test_supera_el_umbral_alerta_una_vez_y_reinicia(tmp_path):
    c = Clock()
    v, alerts, restarts = make(tmp_path, c)
    for _ in range(8):
        v.observe([E4551])
        c.minutes(1)
    assert len(alerts) == 1 and "ALTA" in alerts[0] and "WinError 4551" in alerts[0]
    assert "restart" in alerts[0]
    assert restarts == [1]
    ep = episodes(tmp_path)[-1]
    assert ep["fin"] is None and "WinError 4551" in ep["error"] and "reinicio" in ep["accion"]


def test_recuperacion_manda_segundo_aviso(tmp_path):
    c = Clock()
    v, alerts, _ = make(tmp_path, c)
    for _ in range(7):
        v.observe([E4551])
        c.minutes(1)
    v.observe([])
    assert len(alerts) == 2 and "recuperado tras" in alerts[1]
    assert episodes(tmp_path)[-1]["fin"] is not None
    v.observe([])
    assert len(alerts) == 2  # ya cerrado: nada más


def test_racha_cortada_por_una_pasada_buena_reinicia_la_cuenta(tmp_path):
    c = Clock()
    v, alerts, _ = make(tmp_path, c)
    for _ in range(4):
        v.observe([E4551])
        c.minutes(1)
    v.observe([])
    for _ in range(4):
        c.minutes(1)
        v.observe([E4551])
    assert alerts == []


def test_segundo_episodio_dentro_de_la_hora_no_reinicia(tmp_path):
    c = Clock()
    v, alerts, restarts = make(tmp_path, c)
    for _ in range(6):
        v.observe([E4551])
        c.minutes(1)
    v.observe([])
    c.minutes(10)
    for _ in range(6):
        v.observe([E4551])
        c.minutes(1)
    assert restarts == [1]  # solo el primero
    assert "sin reinicio automático" in alerts[-1] and "requiere a Oscar" in alerts[-1]


def test_pasada_la_hora_vuelve_a_reiniciar(tmp_path):
    c = Clock()
    v, _, restarts = make(tmp_path, c)
    for _ in range(6):
        v.observe([E4551])
        c.minutes(1)
    v.observe([])
    c.minutes(61)
    for _ in range(6):
        v.observe([E4551])
        c.minutes(1)
    assert restarts == [1, 1]


def test_si_el_reinicio_no_arregla_avisa_una_vez_y_no_reinicia_mas(tmp_path):
    c = Clock()
    v, alerts, restarts = make(tmp_path, c)
    for _ in range(6):
        v.observe([E4551])
        c.minutes(1)
    # runner nuevo (proceso nuevo, mismo ciego.json) que sigue ciego
    v2, alerts2, restarts2 = make(tmp_path, c)
    for _ in range(20):
        v2.observe([E4551])
        c.minutes(1)
    assert restarts == [1] and restarts2 == []
    assert len(alerts2) == 1 and "requiere a Oscar" in alerts2[0] and "WinError 4551" in alerts2[0]


def test_fallo_al_lanzar_el_reinicio_queda_dicho(tmp_path):
    c = Clock()
    alerts = []

    def boom():
        raise OSError("sin permiso")

    v = ciego.Vigilante(path=tmp_path / "ciego.json", alert=alerts.append, restart=boom, now=c)
    for _ in range(6):
        v.observe([E4551])
        c.minutes(1)
    assert len(alerts) == 2 and "sin permiso" in alerts[1]


def test_alerta_que_falla_no_tumba_al_runner(tmp_path):
    c = Clock()

    def boom(_):
        raise RuntimeError("sin red")

    v = ciego.Vigilante(path=tmp_path / "ciego.json", alert=boom, restart=lambda: None, now=c)
    for _ in range(7):
        v.observe([E4551])
        c.minutes(1)
    assert episodes(tmp_path)


def test_service_registra_solo_fallos_de_listado():
    class Lane:
        def __init__(self, name, exc=None):
            self.name, self._exc = name, exc

        def jobs(self):
            if self._exc:
                raise self._exc
            return []

    s = Service([Lane("a", OSError("[WinError 4551] x")), Lane("b")])
    s.run_pass()
    assert s.list_errors == ["[WinError 4551] x"]
    s.lanes = [Lane("b")]
    s.run_pass()
    assert s.list_errors == []


def test_error_key_agrupa_por_codigo_winerror():
    a = ciego.error_key("list failed: [WinError 4551] bloqueo de X")
    b = ciego.error_key("[WinError 4551] otro texto")
    assert a == b == "[WinError 4551]"


def test_salud_muestra_episodio_activo_y_ultimo(tmp_path):
    c = Clock()
    v, _, _ = make(tmp_path, c)
    assert ciego.resumen(tmp_path / "ciego.json", c()) == ""
    for _ in range(7):
        v.observe([E4551])
        c.minutes(1)
    activo = ciego.resumen(tmp_path / "ciego.json", c())
    assert "runner ciego desde" in activo and "WinError 4551" in activo
    v.observe([])
    ultimo = ciego.resumen(tmp_path / "ciego.json", c())
    assert ultimo.startswith("último episodio ciego") and "WinError 4551" in ultimo
    html = health.build(runner=lambda: "r", rows=lambda: [], gateway=lambda: "g", monitor=lambda: ("m", []),
                        drift=lambda: "d", machine=lambda: "x", pending=lambda: (0, 0, 0), now=c(),
                        blind=lambda: ultimo)
    assert "Runner ciego" in html and "WinError 4551" in html
    plain = health.build(runner=lambda: "r", rows=lambda: [], gateway=lambda: "g", monitor=lambda: ("m", []),
                         drift=lambda: "d", machine=lambda: "x", pending=lambda: (0, 0, 0), now=c())
    assert "Runner ciego" not in plain
