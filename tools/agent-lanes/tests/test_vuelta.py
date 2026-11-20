"""Vuelta principal del runner (t_11ee04e0): cronómetro por paso, registro inicio/fin, hilos aparte y tope de la limpieza."""
import logging
import subprocess
import threading
import time

import pytest

from agent_lanes import review
from agent_lanes.review import REINTENTO_LIMPIEZA, sweep_done
from agent_lanes.vuelta import Cronometro, HiloAparte, medir

from .test_limpieza_huerfanas import lane_en


class Reloj:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_cronometro_solo_loguea_pasos_lentos_y_vueltas_largas(caplog):
    r = Reloj()
    crono = Cronometro(clock=r)
    with caplog.at_level(logging.INFO, logger="agent_lanes"):
        with crono.paso("rapido"):
            r.t += 5
        with crono.paso("integrador"):
            r.t += 40
        assert crono.cerrar() == 45
    msgs = [x.message for x in caplog.records]
    assert any("«integrador» tardó 40 s" in m for m in msgs)
    assert not any("rapido" in m for m in msgs)
    assert not any("vuelta principal" in m for m in msgs)  # 45 s < 5 min: sin línea de total
    with caplog.at_level(logging.INFO, logger="agent_lanes"):
        crono2 = Cronometro(clock=r)
        with crono2.paso("choques"):
            r.t += 400
        crono2.cerrar()
    assert any("vuelta principal de 400 s" in x.message and "choques 400s" in x.message for x in caplog.records)


def test_cronometro_no_cambia_resultado_ni_orden_ni_traga_excepciones():
    orden_con, orden_sin = [], []

    def pasos(log, envolver):
        for nombre in ("a", "b", "c"):
            with envolver(nombre):
                log.append(nombre)

    crono = Cronometro(clock=Reloj())
    pasos(orden_con, crono.paso)
    from contextlib import nullcontext
    pasos(orden_sin, lambda n: nullcontext())
    assert orden_con == orden_sin == ["a", "b", "c"]
    assert [n for n, _ in crono.pasos] == ["a", "b", "c"]
    with pytest.raises(ValueError):
        with crono.paso("falla"):
            raise ValueError("x")
    assert crono.pasos[-1][0] == "falla"  # el paso que lanza también se mide


def test_medir_registra_inicio_y_fin_tambien_con_error(caplog):
    with caplog.at_level(logging.INFO, logger="agent_lanes"):
        with medir("git worktree add t_1"):
            pass
        with pytest.raises(subprocess.TimeoutExpired):
            with medir("test_cmd"):
                raise subprocess.TimeoutExpired("x", 1)
    msgs = [x.message for x in caplog.records]
    assert "inicio: git worktree add t_1" in msgs
    assert any(m.startswith("fin: git worktree add t_1") and m.endswith("ok") for m in msgs)
    assert any(m.startswith("fin: test_cmd") and "error (TimeoutExpired)" in m for m in msgs)


def test_hilo_aparte_no_bloquea_la_vuelta_y_no_se_solapa():
    suelta, empezo, llamadas = threading.Event(), threading.Event(), []

    def colgado():
        llamadas.append(1)
        empezo.set()
        suelta.wait(10)  # integrador/limpieza «colgados»

    hilo = HiloAparte("integrador-pasada", colgado)
    t0 = time.monotonic()
    assert hilo.lanzar() is True
    assert empezo.wait(5)
    # Varias vueltas del hilo principal con el paso colgado: ninguna espera y ninguna lanza otra pasada.
    for _ in range(50):
        assert hilo.lanzar() is False
    assert time.monotonic() - t0 < 2
    assert hilo.activo() and len(llamadas) == 1
    suelta.set()
    hilo.esperar(5)
    assert not hilo.activo()
    assert hilo.lanzar() is True  # terminada la pasada, la siguiente sí arranca
    hilo.esperar(5)
    assert len(llamadas) == 2


def test_hilo_aparte_registra_el_error_sin_propagarlo(caplog):
    def roto():
        raise RuntimeError("boom")

    hilo = HiloAparte("sweep-done", roto)
    with caplog.at_level(logging.WARNING, logger="agent_lanes"):
        hilo.lanzar()
        hilo.esperar(5)
    assert any("sweep-done falló: boom" in x.message for x in caplog.records)
    assert not hilo.activo()


# --- sweep_done: TimeoutExpired y topes ----------------------------------------------------------------------

class Hermes:
    def __init__(self, ids):
        self.ids = ids

    def list_status(self, assignee, status, sort=None):
        return [{"id": i} for i in self.ids]


class GitLento:
    def __init__(self, reloj=None, coste=0.0, timeout_en=()):
        self.llamadas, self.reloj, self.coste, self.timeout_en = [], reloj, coste, set(timeout_en)

    def cleanup(self, lane, tid):
        self.llamadas.append(tid)
        if self.reloj:
            self.reloj.t += self.coste
        if tid in self.timeout_en:
            raise subprocess.TimeoutExpired("git worktree remove", 300)
        return True, "limpio"


def _carpetas(tmp_path, ids):
    root = tmp_path / "_lanes"
    for i in ids:
        (root / f"lane-{i}").mkdir(parents=True)
    return lane_en(root)


@pytest.fixture(autouse=True)
def _limpio(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "LIMPIEZA_LOG_PATH", tmp_path / "limpieza_log.json")
    review._limpieza_fallida.clear()
    yield
    review._limpieza_fallida.clear()


def test_timeout_en_el_borrado_se_registra_y_no_se_reintenta_cada_vuelta(tmp_path, caplog):
    lane, r = _carpetas(tmp_path, ["t_aaaaaaaa", "t_bbbbbbbb"]), Reloj()
    r.t = 1000.0
    g = GitLento(timeout_en={"t_aaaaaaaa"})
    with caplog.at_level(logging.WARNING, logger="agent_lanes"):
        for _ in range(5):  # cinco vueltas seguidas (una por minuto)
            cleaned = sweep_done(lane, Hermes(["t_aaaaaaaa", "t_bbbbbbbb"]), g, clock=r)
            r.t += 60
    assert g.llamadas.count("t_aaaaaaaa") == 1  # antes: un intento de 300 s por vuelta
    assert cleaned == [] or cleaned == ["t_bbbbbbbb"]  # la tarjeta sana no se ve afectada por la rota
    assert "t_bbbbbbbb" in g.llamadas
    assert any("agotó el tiempo" in x.message for x in caplog.records)
    r.t += REINTENTO_LIMPIEZA
    sweep_done(lane, Hermes(["t_aaaaaaaa"]), g, clock=r)
    assert g.llamadas.count("t_aaaaaaaa") == 2


def test_tope_de_tarjetas_y_de_segundos_por_pasada(tmp_path):
    ids = [f"t_{i:08d}" for i in range(6)]
    lane = _carpetas(tmp_path, ids)
    g = GitLento()
    assert len(sweep_done(lane, Hermes(ids), g, max_tarjetas=2)) == 2
    assert g.llamadas == ids[:2]
    # Las demás quedan para la siguiente pasada (no se marcaron como fallidas): se tocan en la vuelta siguiente.
    sweep_done(lane, Hermes(ids), g, max_tarjetas=2)
    assert g.llamadas[2:4] == ids[:2] or g.llamadas[2:4] == ids[2:4]

    r = Reloj()
    g2 = GitLento(reloj=r, coste=200.0)  # cada borrado lento cuesta 200 s
    sweep_done(lane, Hermes(ids), g2, clock=r, max_segundos=300)
    assert len(g2.llamadas) == 2  # tras 400 s se corta: nunca una pasada eterna


def test_sin_topes_el_comportamiento_es_el_de_siempre(tmp_path):
    ids = [f"t_{i:08d}" for i in range(4)]
    lane = _carpetas(tmp_path, ids)
    g = GitLento()
    assert sweep_done(lane, Hermes(ids), g) == ids
