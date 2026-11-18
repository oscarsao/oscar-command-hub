"""Tope de memoria (t_24f3dfb7): con la RAM simulada sobre el tope no se reclama; histéresis; aviso 1/hora."""
from agent_lanes.service import Service
from agent_lanes.tope_memoria import TopeMemoria, leer_memoria_pct, umbrales


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _tope(pct, alertas, reloj, tope=85, reanudar=80):
    return TopeMemoria(lector=lambda: pct["v"], umbrales_fn=lambda: (tope, reanudar), alert=alertas.append, now=reloj)


def test_histeresis_y_aviso_una_vez_por_hora():
    pct, alertas, reloj = {"v": 70.0}, [], Reloj()
    t = _tope(pct, alertas, reloj)
    assert t.pausada() is False
    pct["v"] = 90.0
    assert t.pausada() is True and len(alertas) == 1
    reloj.t += 600
    assert t.pausada() is True and len(alertas) == 1  # sigue sin repetir
    pct["v"] = 82.0  # bajó del tope pero no de 80: sigue pausado
    assert t.pausada() is True
    reloj.t += 3000
    assert t.pausada() is True and len(alertas) == 2  # pasada 1 h
    pct["v"] = 79.0
    assert t.pausada() is False
    pct["v"] = 84.0  # por debajo del tope: no vuelve a pausar
    assert t.pausada() is False


def test_tope_cero_apaga_y_sin_medida_no_pausa():
    pct, alertas = {"v": 99.0}, []
    assert _tope(pct, alertas, Reloj(), tope=0).pausada() is False
    pct["v"] = None
    assert _tope(pct, alertas, Reloj()).pausada() is False and alertas == []


def test_lector_sin_psutil_o_roto_da_none():
    class Roto:
        def virtual_memory(self):
            raise RuntimeError("x")
    assert leer_memoria_pct(Roto()) is None

    class Ok:
        def virtual_memory(self):
            return type("M", (), {"percent": 81.5})()
    assert leer_memoria_pct(Ok()) == 81.5


def test_umbrales_por_defecto_y_editables(tmp_path, monkeypatch):
    from agent_lanes import ajustes_hub
    monkeypatch.setattr(ajustes_hub, "valor", lambda k: None)
    assert umbrales() == (85.0, 80.0)
    vals = {"carriles.memoria_tope_pct": 70, "carriles.memoria_reanudar_pct": 75}
    monkeypatch.setattr(ajustes_hub, "valor", lambda k: vals[k])
    assert umbrales() == (70.0, 69.0)  # reanudar nunca por encima del tope


class _Lane:
    def __init__(self):
        self.llamadas = 0

        class L:
            name, max_parallel, reserved_slots, max_big = "x", 1, 0, 0
        self.lane = L()

    def jobs(self):
        self.llamadas += 1
        return [("t_1", lambda: "ok")]


def test_service_no_reclama_sobre_el_tope_y_reanuda():
    pct, alertas = {"v": 95.0}, []
    lane = _Lane()
    svc = Service([lane], max_workers=1, memoria_pausada=_tope(pct, alertas, Reloj()).pausada)
    assert svc.tick() == [] and lane.llamadas == 0  # ni siquiera lista tarjetas
    assert svc.run_pass() == {}
    pct["v"] = 70.0
    assert svc.tick() == ["t_1"]
    svc.wait_all(5)
