"""Carriles libres (t_ebd2b253, PR 1): reloj inyectado, carriles y tablero falsos, sin red."""
import json
from types import SimpleNamespace

from agent_lanes import ajustes_hub as ah
from agent_lanes import ociosos


class FakeHermes:
    def __init__(self, running=None, ready=None, fail=False):
        self.running, self.ready, self.fail = running or {}, ready or {}, fail

    def list_status(self, assignee, status, sort="priority"):
        if self.fail:
            raise RuntimeError("tablero roto")
        return (self.running if status == "running" else self.ready).get(assignee, [])


def lanes(*names, kind="implement"):
    return {n: SimpleNamespace(name=n, kind=kind, board="default") for n in names}


def det(tmp_path, lns, hermes, t):
    return ociosos.Ociosos(lns, lambda b: hermes, tmp_path / "ociosos.json", clock=lambda: t[0])


def test_libre_guarda_desde_y_cuenta_minutos(tmp_path):
    t = [1000.0]
    d = det(tmp_path, lanes("claude-hub"), FakeHermes(), t)
    assert [x.supera_umbral for x in d.pasada()] == [False]
    t[0] += 31 * 60
    (libre,) = d.pasada()
    assert libre.desde == 1000.0 and round(libre.minutos) == 31 and libre.supera_umbral
    assert json.loads((tmp_path / "ociosos.json").read_text())["claude-hub"] == 1000.0


def test_con_running_o_ready_no_esta_libre_y_se_borra(tmp_path):
    t = [0.0]
    h = FakeHermes()
    d = det(tmp_path, lanes("a", "b", "c"), h, t)
    assert {x.carril for x in d.pasada()} == {"a", "b", "c"}
    h.running = {"a": [{"id": "t_1"}]}
    h.ready = {"b": [{"id": "t_2"}]}
    assert [x.carril for x in d.pasada()] == ["c"]
    assert set(json.loads((tmp_path / "ociosos.json").read_text())) == {"c"}


def test_estado_sobrevive_a_un_reinicio(tmp_path):
    t = [500.0]
    det(tmp_path, lanes("a"), FakeHermes(), t).pasada()
    t[0] = 500.0 + 3600
    (libre,) = det(tmp_path, lanes("a"), FakeHermes(), t).pasada()
    assert libre.desde == 500.0 and round(libre.minutos) == 60


def test_tablero_ilegible_no_inventa_ni_borra(tmp_path):
    t = [0.0]
    det(tmp_path, lanes("a"), FakeHermes(), t).pasada()
    t[0] = 600.0
    (libre,) = det(tmp_path, lanes("a"), FakeHermes(fail=True), t).pasada()
    assert libre.desde == 0.0  # conserva el dato previo
    assert det(tmp_path, lanes("zz"), FakeHermes(fail=True), t).pasada() == []  # sin dato previo: no es libre


def test_solo_carriles_de_codigo(tmp_path):
    assert det(tmp_path, lanes("rev", kind="review"), FakeHermes(), [0.0]).pasada() == []


def test_umbral_viene_del_ajuste(tmp_path):
    previo = ah.usar(ah.Ajustes(tmp_path / "aj.json", tmp_path / "aud.jsonl"))
    try:
        ah.almacen().set("carriles.ocioso_minutos", 5, "test")
        t = [0.0]
        d = det(tmp_path, lanes("a"), FakeHermes(), t)
        d.pasada()
        t[0] = 6 * 60
        assert d.pasada()[0].supera_umbral
    finally:
        ah.usar(previo)


def test_ticker_respeta_el_intervalo(tmp_path):
    t = [100.0]
    tk = ociosos.OciososTicker(det(tmp_path, lanes("a"), FakeHermes(), t), minutes=1, clock=lambda: t[0])
    assert len(tk.tick()) == 1
    t[0] += 30
    assert tk.tick() == []
    t[0] += 40
    assert len(tk.tick()) == 1


def test_texto_libres():
    assert "Ningún carril libre" in ociosos.texto([], 30)
    out = ociosos.texto([ociosos.Libre("claude-hub", 0, 45, True), ociosos.Libre("claude-oscarhq", 0, 5, False)], 30)
    assert "claude-hub" in out and "45 min" in out and "aviso a los 30" in out
