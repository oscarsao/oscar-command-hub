"""/pendiente es la bandeja única: filtros, atajos (/decisiones, /atencion, /estado, /falta) y tiempo máximo."""
from __future__ import annotations

import threading
import time

import pytest

from agent_lanes import lo_tuyo as lt
from agent_lanes.commands import CommandCenter


def _cc(llamadas: list, **extra) -> CommandCenter:
    cc = object.__new__(CommandCenter)
    cc._vista_decisiones = lambda *a, **kw: llamadas.append(("decisiones", a, kw))
    cc._vista_estado = lambda *a, **kw: llamadas.append(("estado", a, kw))
    cc._vista_falta = lambda *a, **kw: llamadas.append(("falta", a, kw))
    cc._now = lambda: 1000.0
    cc._send = lambda where, text, **kw: llamadas.append(("send", text, kw))
    cc._lo_tuyo_markup = lambda shown: None
    for k, v in extra.items():
        setattr(cc, k, v)
    return cc


@pytest.mark.parametrize("args,esperado", [
    ("decisiones", ("decisiones", ("w", "", "B"), {})),
    ("atencion", ("decisiones", ("w", "", "B"), {"show_all": True})),
    ("estado migrateam", ("estado", ("w", "migrateam", "B"), {})),
    ("FALTA pildora", ("falta", ("w", "pildora", "B"), {})),
])
def test_cada_filtro_va_a_su_vista(args, esperado):
    llamadas: list = []
    _cc(llamadas).cmd_pendiente("w", args, "B")
    assert llamadas == [esperado]


@pytest.mark.parametrize("atajo,args,filtro", [
    ("cmd_decisiones", "", "decisiones"), ("cmd_atencion", "", "atencion"),
    ("cmd_estado", "migrateam", "estado"), ("cmd_falta", "pildora", "falta")])
def test_los_atajos_solo_llaman_a_pendiente_con_su_filtro(atajo, args, filtro):
    llamadas: list = []
    cc = object.__new__(CommandCenter)
    cc.cmd_pendiente = lambda *a, **kw: llamadas.append((a, kw))
    getattr(cc, atajo)("w", args, "B")
    assert llamadas == [(("w", f"{filtro} {args}", "B"), {})]


def test_decisiones_show_all_es_el_filtro_atencion():
    llamadas: list = []
    cc = object.__new__(CommandCenter)
    cc.cmd_pendiente = lambda *a, **kw: llamadas.append(a)
    cc.cmd_decisiones("w", "", None, show_all=True)
    assert llamadas == [("w", "atencion ", None)]


@pytest.mark.parametrize("args", ["", "todo", "algo raro"])
def test_sin_filtro_o_todo_es_la_bandeja_de_siempre(args):
    llamadas: list = []
    cc = _cc(llamadas, lo_tuyo_items=lambda brand, timeout=None: ([], []))
    cc.cmd_pendiente("w", args, None)
    assert [l[0] for l in llamadas] == ["send"] and "Nada pendiente de ti" in llamadas[0][1]


def test_recoger_responde_parcial_si_una_fuente_va_lenta():
    parar = threading.Event()
    rapido = lt.item("pregunta", "t_1", "rápida")
    t0 = time.monotonic()
    items, lentas = lt.recoger((("preguntas", lambda: [rapido]), ("kanban", lambda: parar.wait(30) and [])), 0.3)
    parar.set()
    assert time.monotonic() - t0 < 5
    assert items == [rapido] and lentas == ["kanban"]


def test_recoger_una_fuente_que_falla_no_vacia_la_bandeja():
    def rota():
        raise RuntimeError("hermes caído")
    rapido = lt.item("pregunta", "t_1", "rápida")
    items, lentas = lt.recoger((("rota", rota), ("ok", lambda: [rapido])), 1)
    assert items == [rapido] and lentas == []


def test_pendiente_con_hermes_lento_avisa_y_no_se_cuelga(tmp_path):
    parar = threading.Event()
    ajustes = tmp_path / "lo_tuyo.yaml"
    ajustes.write_text("timeout_segundos: 0.3\n", encoding="utf-8")
    llamadas: list = []
    cc = _cc(llamadas, lo_tuyo_settings=str(ajustes))
    cc.lanes = {}
    cc.pending_decisions = lambda brand: parar.wait(30) and []   # Hermes simulado lento
    cc.stuck_tasks = lambda brand: []
    cc.ready_to_approve = lambda brand: []
    cc.board_tasks = lambda brand=None: []
    cc.oscar_cards = lambda brand: ([], [])
    cc.guard_queue = lambda: None
    cc.cola_externos = lambda: None
    cc.estado_integrador = lambda: None
    cc.hermes_for = lambda board: None
    t0 = time.monotonic()
    cc.cmd_pendiente("w", "", None)
    parar.set()
    assert time.monotonic() - t0 < 5
    texto = llamadas[-1][1]
    assert "Respuesta parcial" in texto and "preguntas" in texto


def test_render_sin_lentas_no_cambia_y_con_lentas_avisa():
    aj = lt.Ajustes()
    assert lt.render([], aj, 0.0) == ("Nada pendiente de ti 🎉", [])
    texto, _ = lt.render([lt.item("pregunta", "t_1", "x")], aj, 0.0, lentas=["kanban"])
    assert texto.endswith("Repite /pendiente en un minuto.") and "kanban" in texto


def test_timeout_configurable_y_con_default_20():
    assert lt.Ajustes().timeout_segundos == 20
    assert lt.load_ajustes("no-existe.yaml").timeout_segundos == 20
