"""Consumo de la suscripción de Claude: lectura (ausente / viejo / válido), coste por tarjeta, /consumo, aviso una vez por
ventana y freno con reloj inyectado. Ficheros en tmp_path; ajustes en un almacén temporal; sin red ni Telegram."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from agent_lanes import ajustes_hub, consumo, cuota, nivel_avisos
from agent_lanes.ajustes_hub import Ajustes
from agent_lanes.commands import COMMANDS, CommandCenter
from agent_lanes.runner import LaneRunner
from agent_lanes.service import Service
from tests.test_cuota_runner import FakeHermesCuota
from tests.test_runner_state import LANE, FakeGit, FakeNotifier, FakeVerifier, FakeWorker, ok_outcome

AHORA = datetime(2026, 10, 31, 14, 0).timestamp()
R5H = AHORA + 3 * 3600
RSEM = AHORA + 4 * 86400


def estado(tmp_path, *, edad=60.0, p5=40, ps=10, r5=R5H, rs=RSEM, nombre="estado.json"):
    f = {"used_percentage": p5, "resets_at": r5} if p5 is not None else {}
    s = {"used_percentage": ps, "resets_at": rs} if ps is not None else {}
    p = tmp_path / nombre
    p.write_text(json.dumps({"actualizado": AHORA - edad, "five_hour": f, "seven_day": s}), encoding="utf-8")
    return p


@pytest.fixture
def aj(tmp_path):
    store = Ajustes(tmp_path / "aj.json", tmp_path / "aud.jsonl")
    previo = ajustes_hub.usar(store)
    yield store
    ajustes_hub.usar(previo)


# --- lectura ------------------------------------------------------------------------------------------------------------

def test_lectura_valida(tmp_path):
    lec = consumo.leer(estado(tmp_path, edad=120), AHORA)
    assert lec.fiable and lec.edad_s == 120
    assert lec.ventana("five_hour").porcentaje == 40 and lec.ventana("five_hour").reinicio == R5H
    assert lec.ventana("seven_day").porcentaje == 10


def test_fichero_ausente_o_roto_no_falla(tmp_path):
    for p in (tmp_path / "no-existe.json", tmp_path / "roto.json"):
        if p.name == "roto.json":
            p.write_text("{no es json", encoding="utf-8")
        lec = consumo.leer(p, AHORA)
        assert not lec.fiable and lec.edad_s is None and all(v.porcentaje is None for v in lec.ventanas)
    (tmp_path / "raro.json").write_text(json.dumps({"actualizado": "ayer", "five_hour": 3}), encoding="utf-8")
    assert not consumo.leer(tmp_path / "raro.json", AHORA).fiable


def test_dato_de_mas_de_60_min_es_sin_dato_fiable(tmp_path):
    p = estado(tmp_path, edad=61 * 60)
    lec = consumo.leer(p, AHORA)
    assert not lec.fiable and lec.edad_s == 61 * 60 and lec.ventana("five_hour").porcentaje is None
    assert consumo.leer(estado(tmp_path, edad=59 * 60), AHORA).fiable
    assert "Sin dato fiable" in consumo.texto(lec, consumo.resumen(tmp_path / "nada.jsonl", AHORA), AHORA)


def test_ventana_ya_reiniciada_no_tiene_porcentaje_y_ventana_vacia_tampoco(tmp_path):
    lec = consumo.leer(estado(tmp_path, r5=AHORA - 10, ps=None), AHORA)
    assert lec.ventana("five_hour").porcentaje is None and lec.ventana("seven_day").porcentaje is None
    assert not lec.fiable


# --- coste por tarjeta --------------------------------------------------------------------------------------------------

def test_registro_de_coste_sin_contenido_ni_secretos(tmp_path):
    p = tmp_path / "c.jsonl"
    assert consumo.registrar_coste("t_1", "claude-hub", "sonnet", 0.5, AHORA, p)
    assert not consumo.registrar_coste("t_2", "claude-hub", "sonnet", None, AHORA, p)  # sin coste: nada
    reg = json.loads(p.read_text(encoding="utf-8"))
    assert reg == {"ts": AHORA, "tarjeta": "t_1", "carril": "claude-hub", "modelo": "sonnet", "coste": 0.5}
    assert not consumo.registrar_coste("t_3", "x", "m", 1.0, AHORA, tmp_path / "dir" / ".." / "no\0valido")


def test_resumen_hoy_semana_carril_modelo_y_mas_caras(tmp_path):
    p = tmp_path / "c.jsonl"
    for t, carril, modelo, coste, hace in [("t_a", "hub", "sonnet", 1.0, 3600), ("t_a", "hub", "sonnet", 0.5, 7200),
                                           ("t_b", "scraper", "haiku", 0.25, 2 * 86400),
                                           ("t_c", "hub", "opus", 9.0, 9 * 86400)]:  # fuera de los 7 días
        consumo.registrar_coste(t, carril, modelo, coste, AHORA - hace, p)
    p.write_text(p.read_text(encoding="utf-8") + "basura\n", encoding="utf-8")
    r = consumo.resumen(p, AHORA)
    assert r["hoy"] == pytest.approx(1.5) and r["semana"] == pytest.approx(1.75)
    assert dict(r["carril"]) == {"hub": 1.5, "scraper": 0.25} and dict(r["modelo"]) == {"sonnet": 1.5, "haiku": 0.25}
    assert r["top"] == [("t_a", "hub", 1.5), ("t_b", "scraper", 0.25)]


def test_el_runner_registra_el_coste_de_la_ejecucion(tmp_path):
    h = FakeHermesCuota([{"id": "t_1", "title": "T", "body": "B"}])
    w = FakeWorker([ok_outcome()])
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=w, verifier=FakeVerifier(), notify=FakeNotifier(),
                   state_dir=tmp_path / "tasks", wall_clock=lambda: AHORA)
    r.run_once()
    filas = [json.loads(ln) for ln in (tmp_path / "consumo_tarjetas.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(filas) == 1 and filas[0]["tarjeta"] == "t_1" and filas[0]["carril"] == LANE.name
    assert set(filas[0]) == {"ts", "tarjeta", "carril", "modelo", "coste"}


# --- /consumo -----------------------------------------------------------------------------------------------------------

def _enviar_consumo(monkeypatch, tmp_path, p):
    monkeypatch.setattr(consumo, "ESTADO_PATH", p)
    monkeypatch.setattr(consumo, "TARJETAS_PATH", tmp_path / "c.jsonl")
    consumo.registrar_coste("t_a", "hub", "sonnet", 1.2, AHORA - 600, tmp_path / "c.jsonl")
    enviados = []
    stub = SimpleNamespace(_now=lambda: AHORA, _send=lambda where, text, **kw: enviados.append(text))
    CommandCenter.cmd_consumo(stub, ("1", "0"), "", None)
    return enviados[0]


def test_comando_consumo_muestra_porcentajes_edad_y_costes(monkeypatch, tmp_path):
    texto = _enviar_consumo(monkeypatch, tmp_path, estado(tmp_path, edad=300))
    assert "5 h: 40 %" in texto and "semanal: 10 %" in texto and "17:00" in texto
    assert "hace 5 min" in texto and "NO es el % del plan" in texto
    assert "Hoy: $1.20" in texto and "t_a (hub) $1.20" in texto and "Por carril" in texto and "Por modelo" in texto


def test_comando_consumo_con_dato_viejo_dice_sin_dato_fiable(monkeypatch, tmp_path):
    texto = _enviar_consumo(monkeypatch, tmp_path, estado(tmp_path, edad=2 * 3600))
    assert "Sin dato fiable" in texto and "hace 2.0 h" in texto and "40 %" not in texto


def test_el_comando_esta_registrado_y_solo_para_todas_las_marcas():
    assert "consumo" in dict(COMMANDS)
    from agent_lanes.commands import SOLO_TODAS_LAS_MARCAS
    assert "consumo" in SOLO_TODAS_LAS_MARCAS


# --- aviso --------------------------------------------------------------------------------------------------------------

def avisador(tmp_path, p, enviados, reloj, ok=True):
    return consumo.Avisador(lambda t: enviados.append(t) or ok, tmp_path / "aviso.json", p, reloj)


def test_aviso_una_sola_vez_por_ventana_y_de_nuevo_tras_el_reinicio(aj, tmp_path):
    enviados, t = [], [AHORA]
    p = estado(tmp_path, p5=85, ps=10)
    av = avisador(tmp_path, p, enviados, lambda: t[0])
    assert av.tick() and len(enviados) == 1
    assert "5 h: 85 %" in enviados[0] and "17:00" in enviados[0] and "semanal" not in enviados[0]
    t[0] += 600
    assert not av.tick() and len(enviados) == 1                       # no se repite en la misma ventana
    p.write_text(json.dumps({"actualizado": t[0], "five_hour": {"used_percentage": 92, "resets_at": R5H + 10},
                             "seven_day": {"used_percentage": 82, "resets_at": RSEM}}), encoding="utf-8")
    assert av.tick()                                                   # la semanal cruza ahora; la de 5 h (jitter) no
    assert len(enviados) == 2 and "5 h" not in enviados[1] and "semanal: 82 %" in enviados[1]
    t[0] = R5H + 3600                                                  # nueva ventana de 5 h
    p.write_text(json.dumps({"actualizado": t[0], "five_hour": {"used_percentage": 81, "resets_at": R5H + 5 * 3600},
                             "seven_day": {"used_percentage": 82, "resets_at": RSEM}}), encoding="utf-8")
    assert av.tick() and len(enviados) == 3 and "5 h: 81 %" in enviados[2]


def test_aviso_apagado_bajo_umbral_sin_dato_o_envio_fallido(aj, tmp_path):
    enviados, p = [], estado(tmp_path, p5=79, ps=79)
    assert not avisador(tmp_path, p, enviados, lambda: AHORA).tick()           # bajo el 80 % por defecto
    aj.set("consumo.avisar_al", 0, "test")
    assert not avisador(tmp_path, estado(tmp_path, p5=99, nombre="alto.json"), enviados, lambda: AHORA).tick()
    aj.set("consumo.avisar_al", 80, "test")
    assert not avisador(tmp_path, estado(tmp_path, p5=99, edad=7200, nombre="viejo.json"), enviados, lambda: AHORA).tick()
    assert not avisador(tmp_path, tmp_path / "no.json", enviados, lambda: AHORA).tick() and enviados == []
    alto = estado(tmp_path, p5=99, nombre="alto.json")
    assert not avisador(tmp_path, alto, enviados, lambda: AHORA, ok=False).tick()  # envío fallido: no se marca
    assert avisador(tmp_path, alto, enviados, lambda: AHORA).tick()               # y se reintenta


def test_aviso_dos_ventanas_a_la_vez_es_un_solo_mensaje(aj, tmp_path):
    enviados = []
    assert avisador(tmp_path, estado(tmp_path, p5=90, ps=95), enviados, lambda: AHORA).tick()
    assert len(enviados) == 1 and "5 h: 90 %" in enviados[0] and "semanal: 95 %" in enviados[0]


def test_el_aviso_cuenta_como_esencial_y_el_umbral_es_ajuste(aj):
    assert nivel_avisos.activo("consumo", "esencial") and nivel_avisos.activo("consumo", "normal")
    assert ajustes_hub.valor("consumo.avisar_al") == 80 and ajustes_hub.valor("consumo.frenar_al") == 95
    assert ajustes_hub.valor("consumo.frenar_activo") == 0


def test_ticker_comprueba_como_mucho_cada_n_minutos(aj, tmp_path):
    enviados, t = [], [AHORA]
    av = avisador(tmp_path, estado(tmp_path, p5=99), enviados, lambda: t[0])
    tk = consumo.AvisadorTicker(av, minutes=5, clock=lambda: t[0])
    assert tk.tick() and not tk.tick()
    t[0] += 301
    assert not tk.tick() and len(enviados) == 1


# --- freno --------------------------------------------------------------------------------------------------------------

class LaneFalsa:
    name = "falsa"

    def __init__(self):
        self.lanzadas = []

    def jobs(self):
        return [("t_9", lambda: self.lanzadas.append("t_9") or "ok")]


def test_freno_apagado_por_defecto_aunque_el_consumo_sea_alto(aj, tmp_path):
    assert not consumo.frenar(AHORA, estado(tmp_path, p5=99))


def test_freno_activo_no_lanza_tarjetas_nuevas_y_se_levanta_con_el_dato(aj, tmp_path, monkeypatch):
    aj.set("consumo.frenar_activo", 1, "test")
    p = estado(tmp_path, p5=96, ps=10)
    monkeypatch.setattr(consumo, "ESTADO_PATH", p)
    t = [AHORA]
    lane = LaneFalsa()
    svc = Service([lane], max_workers=2, cuota_pausada=lambda: cuota.pausada(tmp_path / "cuota.json", t[0]))
    assert consumo.frenar(t[0]) and svc.tick() == [] and svc.run_pass() == {}
    svc.wait_all(5)
    assert lane.lanzadas == []
    t[0] += 61 * 60                                  # el dato se hace viejo: sin dato fiable no se frena
    assert not consumo.frenar(t[0]) and svc.tick() == ["t_9"]
    svc.wait_all(5)
    assert lane.lanzadas == ["t_9"]


def test_freno_respeta_el_umbral_y_la_semana(aj, tmp_path):
    aj.set("consumo.frenar_activo", 1, "test")
    assert not consumo.frenar(AHORA, estado(tmp_path, p5=94, ps=94))
    assert consumo.frenar(AHORA, estado(tmp_path, p5=10, ps=95, nombre="sem.json"))
    aj.set("consumo.frenar_al", 90, "test")
    assert consumo.frenar(AHORA, estado(tmp_path, p5=91, ps=0, nombre="otro.json"))


def test_pausa_por_cuota_sigue_frenando(aj, tmp_path):
    cuota.escribir(tmp_path / "cuota.json", AHORA + 3600, "usage limit", AHORA)
    assert cuota.pausada(tmp_path / "cuota.json", AHORA + 60) and not cuota.pausada(tmp_path / "cuota.json", AHORA + 3601)
