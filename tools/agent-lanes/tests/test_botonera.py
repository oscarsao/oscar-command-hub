"""Botonera estándar (F1-14): un solo catálogo, mismo orden en todos los estados, ningún estado sin salida."""
from __future__ import annotations

import pytest

from agent_lanes import botonera as bt

OBLIGATORIOS = {"responder", "integrador", "hecha", "archivar"}


def ids(filas):
    return [b["id"] for fila in filas for b in fila]


@pytest.mark.parametrize("estado", bt.cargar()["estados"])
def test_todo_estado_tiene_responder_integrador_hecha_y_archivar(estado):
    assert OBLIGATORIOS <= set(ids(bt.ficha(estado)["filas"]))


def test_estados_del_criterio_de_hecho_estan_cubiertos():
    assert set(bt.cargar()["estados"]) == {"ready", "running", "blocked", "needs_input", "triage", "review", "done"}


def test_mismo_orden_en_todas_las_fichas():
    ordenes = {tuple(ids(bt.ficha(e)["filas"])) for e in bt.cargar()["estados"]}
    assert len(ordenes) == 1
    assert set(ids(bt.ficha("done")["mas"])) >= {"otro_carril", "partir", "aplazar", "detalle"}


def test_irreversibles_piden_dos_toques_y_la_estrella_va_en_la_recomendada():
    filas = bt.ficha("blocked", recomendada="hecha")["filas"]
    por_id = {b["id"]: b for fila in filas for b in fila}
    assert por_id["archivar"]["confirmar"] and por_id["hecha"]["confirmar"] and not por_id["responder"].get("confirmar")
    assert por_id["hecha"]["text"].startswith("⭐ ") and not por_id["archivar"]["text"].startswith("⭐")


def test_reintentar_solo_si_el_diagnostico_lo_permite():
    assert "reintentar" not in ids(bt.ficha("blocked")["filas"])
    assert ids(bt.ficha("blocked", reintentable=True)["filas"])[0] == "reintentar"
    assert bt.diagnostico_reintentable(kind="transient", reason="timeout") == (True, "reintentar")
    assert bt.diagnostico_reintentable(kind="transient", repeticiones=2)[0] is False   # bloqueo repetido
    assert bt.diagnostico_reintentable(kind="estructural")[0] is False


def test_verificacion_fallida_sin_commits_recomienda_dar_por_hecha():
    ok, rec = bt.diagnostico_reintentable(kind="needs_input", reason="Verificación fallida: sin commits", commits=0)
    assert ok is False and rec == "hecha"


def test_completar_anade_solo_lo_que_falta_sin_repetir():
    antes = [[{"text": "🗄 Aparcar", "action": "park"}]]
    despues = bt.completar(antes, "blocked")
    acciones = [b["action"] for fila in despues for b in fila]
    assert len(acciones) == len(set(acciones))
    assert {b["id"] for fila in despues[1:] for b in fila} == {"responder", "integrador", "hecha"}


def test_catalogo_incompleto_se_rechaza(tmp_path):
    f = tmp_path / "b.yaml"
    f.write_text("botones: []\norden: []\nestados: []\nobligatorios: [x]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bt.cargar(f)


def test_las_acciones_del_catalogo_que_ya_existen_apuntan_a_constantes_reales():
    from agent_lanes import decisions
    for bid, d in bt.cargar()["botones"].items():
        if d["accion"] != "pendiente":
            assert hasattr(decisions, d["accion"]), bid
