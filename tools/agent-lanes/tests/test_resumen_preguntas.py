"""Resumen de preguntas de carriles a las 9, 14 y 20 h (t_a37ab14b). Dobles en memoria: sin Telegram ni kanban reales."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from agent_lanes import resumen_carriles as rc

H = 3600


def _now(hour: int, day: int = 20, minute: int = 5) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=rc.MADRID)


def _p(tid: str, horas: float, now: datetime, state: str = "needs_input", guard: bool = False, kind=None):
    return SimpleNamespace(tid=tid, since=now.timestamp() - horas * H, state=state, guard=guard, block_kind=kind)


class Mundo:
    """Estado mutable que los callables del resumen leen; `reloj` es la hora actual."""

    def __init__(self, tmp_path, hour=9, cfg=None):
        self.reloj = _now(hour)
        self.preguntas: list = []
        self.bloqueadas: list = []
        self.rows = [{"name": "claude-hub", "doing": ["a"]}, {"name": "claude-ops", "doing": []}]
        self.estado = "vivo"
        self.hechas = 3
        self.enviados: list[str] = []
        self.fichas: list[list] = []
        self.ok = True
        self.cfg = cfg or rc.load_resumen_config(tmp_path / "no_existe.yaml")
        self.tmp = tmp_path

    def resumen(self) -> rc.ResumenPreguntas:
        def send(text):
            if self.ok:
                self.enviados.append(text)
            return self.ok
        return rc.ResumenPreguntas(lambda: self.preguntas, lambda: self.bloqueadas, lambda: self.rows,
                                   lambda ts: self.hechas, lambda: self.estado, send, self.fichas.append,
                                   self.tmp / "estado.json", now=lambda: self.reloj, config=lambda: self.cfg)


def test_con_cambios_se_envia_con_fichas(tmp_path):
    m = Mundo(tmp_path)
    m.preguntas = [_p("t_1", 50, m.reloj), _p("t_2", 3, m.reloj)]
    texto = m.resumen().tick()
    assert texto and m.enviados == [texto]
    assert "2 preguntas esperan" in texto and "t_1" in texto
    assert m.fichas == [m.preguntas]  # las fichas con botones salen tras el parte


def test_sin_cambios_no_se_envia(tmp_path):
    m = Mundo(tmp_path)
    m.preguntas = [_p("t_1", 50, m.reloj)]
    r = m.resumen()
    assert r.tick()
    m.reloj = _now(14)  # siguiente hueco, mismas preguntas, mismos bloqueos y runner
    assert r.tick() is None
    assert len(m.enviados) == 1
    m.reloj = _now(14, minute=30)
    assert r.tick() is None  # el hueco consumido sin novedades no se reevalúa


def test_cambio_de_preguntas_bloqueos_o_runner_dispara_el_siguiente(tmp_path):
    m = Mundo(tmp_path)
    m.preguntas = [_p("t_1", 5, m.reloj)]
    r = m.resumen()
    assert r.tick()
    m.reloj = _now(14)
    m.preguntas.append(_p("t_2", 1, m.reloj))
    texto = r.tick()
    assert texto and "1 pregunta nueva" in texto
    m.reloj = _now(20)
    m.estado = "en pausa (drenando para reiniciar)"
    assert "en pausa" in r.tick()
    m.reloj = _now(9, day=21)
    m.bloqueadas = [_p("t_9", 1, m.reloj, state="blocked", guard=True)]
    assert "guard 1" in r.tick()


def test_primer_parte_sin_nada_no_sale(tmp_path):
    m = Mundo(tmp_path)
    assert m.resumen().tick() is None and m.enviados == []


def test_recuento_de_antiguas(tmp_path):
    ahora = _now(9)
    ps = [_p("t_1", 405, ahora), _p("t_2", 25, ahora), _p("t_3", 24, ahora), _p("t_4", 2, ahora)]
    assert rc.contar_antiguas(ps, ahora.timestamp(), 24) == 2  # «más de 24 h»: las de exactamente 24 no cuentan
    assert rc.contar_antiguas(ps, ahora.timestamp(), 1) == 4
    m = Mundo(tmp_path)
    m.preguntas = ps
    assert "4 preguntas esperan · 2 con más de 24 h · la más antigua hace 405 h (t_1)" in m.resumen().tick()


def test_maximo_12_lineas_aunque_haya_de_todo(tmp_path):
    m = Mundo(tmp_path)
    m.preguntas = [_p(f"t_{i}", i, m.reloj) for i in range(40)]
    m.bloqueadas = [_p(f"b_{i}", 1, m.reloj, state="blocked", kind=f"tipo{i}") for i in range(30)]
    m.rows = [{"name": f"carril{i}", "doing": ["x"] * 3} for i in range(15)]
    texto = m.resumen().tick()
    assert len(texto.splitlines()) <= rc.MAX_LINEAS
    assert len(m.fichas[0]) == 5  # preguntas_max por defecto


def test_interruptores_por_tipo_quitan_lineas(tmp_path):
    m = Mundo(tmp_path)
    m.preguntas = [_p("t_1", 5, m.reloj)]
    m.cfg = {**m.cfg, "tipos": {**m.cfg["tipos"], "runner": False, "en_marcha": False, "bloqueadas": False}}
    texto = m.resumen().tick()
    assert "Runner" not in texto and "En marcha" not in texto and "Bloqueadas" not in texto
    assert "esperan" in texto and "Desde el último parte" in texto


def test_apagado_y_fichas_a_cero(tmp_path):
    m = Mundo(tmp_path)
    m.preguntas = [_p("t_1", 5, m.reloj)]
    m.cfg = {**m.cfg, "activo": False}
    assert m.resumen().tick() is None
    m.cfg = {**m.cfg, "activo": True, "preguntas_max": 0}
    assert m.resumen().tick() and m.fichas == []


def test_solo_sale_en_sus_horas_y_dentro_del_margen(tmp_path):
    m = Mundo(tmp_path, hour=12)
    m.preguntas = [_p("t_1", 5, m.reloj)]
    r = m.resumen()
    assert r.tick() is None
    m.reloj = _now(14, minute=59)
    assert r.tick()
    m.cfg = {**m.cfg, "horas": [17]}
    m.reloj = _now(17, day=21)
    m.preguntas.append(_p("t_2", 1, m.reloj))
    assert "17:00" in r.tick()


def test_envio_fallido_se_reintenta_y_no_consume_el_hueco(tmp_path):
    m = Mundo(tmp_path)
    m.preguntas = [_p("t_1", 5, m.reloj)]
    r = m.resumen()
    m.ok = False
    assert r.tick() is None and not (tmp_path / "estado.json").exists()
    m.ok = True
    assert r.tick() and len(m.enviados) == 1


def test_fallo_al_pintar_fichas_no_tumba_el_parte(tmp_path):
    m = Mundo(tmp_path)
    m.preguntas = [_p("t_1", 5, m.reloj)]
    r = m.resumen()
    r.send_cards = lambda ps: (_ for _ in ()).throw(RuntimeError("telegram"))
    assert r.tick()
    assert json.loads((tmp_path / "estado.json").read_text(encoding="utf-8"))["last"]["snap"]["preguntas"] == ["t_1"]


def test_config_del_yaml_valida_y_con_defaults(tmp_path):
    f = tmp_path / "v.yaml"
    f.write_text("resumen_preguntas:\n  activo: false\n  horas: [8, 13, 99, 'x']\n  preguntas_max: 3\n"
                 "  antiguas_horas: 48\n  tipos: {runner: false, otro: false}\n", encoding="utf-8")
    cfg = rc.load_resumen_config(f)
    assert cfg["activo"] is False and cfg["horas"] == [8, 13] and cfg["preguntas_max"] == 3
    assert cfg["antiguas_horas"] == 48.0 and cfg["tipos"]["runner"] is False and cfg["tipos"]["nuevas"] is True
    f.write_text("resumen_preguntas: 7\n", encoding="utf-8")
    assert rc.load_resumen_config(f) == rc.load_resumen_config(tmp_path / "no.yaml")
    assert rc.load_resumen_config(tmp_path / "no.yaml")["horas"] == [9, 14, 20]


def test_el_yaml_del_repo_trae_la_seccion_con_los_defaults():
    cfg = rc.load_resumen_config()
    assert cfg["horas"] == [9, 14, 20] and cfg["activo"] is True and set(cfg["tipos"]) == set(rc.SECCIONES)


def test_tipos_de_bloqueo():
    ahora = _now(9)
    ps = [_p("a", 1, ahora, "blocked", guard=True), _p("b", 1, ahora, "stuck"), _p("c", 1, ahora, "needs_input"),
          _p("d", 1, ahora, "blocked", kind="falta"), _p("e", 1, ahora, "blocked")]
    assert [rc.tipo_bloqueo(p) for p in ps] == ["guard", "atascada", "pregunta", "falta", "otro"]
