"""Preferencias aprendidas de /sesion: extracción, recurrencia, aprobación de Oscar, inyección y ajustes."""
from __future__ import annotations

import json
from types import SimpleNamespace

from agent_lanes import card_prepare, preferencias
from agent_lanes.preferencias import Ajustes, Preferencias

SES = {"id": "s_1", "nombre": "x", "puntos": [{"n": 1, "texto": "los botones con texto, no solo letras", "ts": 0}]}
OUT = ("PREF: botones-con-texto | ambos | Pon texto corto en los botones de opción, no solo la letra.\n"
       "basura sin formato\nPREF: x | workers | demasiado corta la clave\n"
       "PREF: recomendada-visible | preparar | Muestra siempre la opción recomendada.")


def fake_runner(result, *, is_error=False, boom=None):
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        if boom:
            raise boom
        return SimpleNamespace(stdout=json.dumps({"result": result, "is_error": is_error}), returncode=0)
    run.calls = calls
    return run


def store(tmp_path, **aj):
    return Preferencias(tmp_path / "preferencias.json", Ajustes(**aj))


def test_parse_solo_lineas_validas():
    found = preferencias.parse(OUT)
    assert [f["clave"] for f in found] == ["botones-con-texto", "recomendada-visible"]
    assert found[0]["destino"] == "ambos"


def test_extract_aisla_la_llamada_y_marca_los_puntos_como_dato(tmp_path):
    run = fake_runner(OUT)
    found = preferencias.extract(SES, [{"clave": "k-existente", "texto": "t"}], runner=run)
    args, kw = run.calls[0]
    assert "--tools" in args and args[args.index("--tools") + 1] == "" and "--no-session-persistence" in args
    assert preferencias.OPEN in kw["input"] and "k-existente" in kw["input"]
    assert len(found) == 2


def test_extract_nunca_lanza(tmp_path):
    assert preferencias.extract(SES, [], runner=fake_runner("", boom=OSError("x"))) == []
    assert preferencias.extract(SES, [], runner=fake_runner(OUT, is_error=True)) == []


def test_recurrencia_cuenta_sesiones_distintas_e_idempotente(tmp_path):
    s = store(tmp_path, min_sesiones=2)
    found = preferencias.parse(OUT)
    s.record("s_1", found)
    s.record("s_1", found)  # repetir la misma sesión no suma
    assert s.proposals() == []
    s.record("s_2", found[:1])
    assert [p["clave"] for p in s.proposals()] == ["botones-con-texto"]


def test_nada_se_inyecta_sin_aprobar(tmp_path):
    s = store(tmp_path, min_sesiones=1)
    s.record("s_1", preferencias.parse(OUT))
    assert s.section("workers") == ""
    assert s.decide("botones-con-texto", True)
    assert "texto corto en los botones" in s.section("workers")
    assert "recomendada" not in s.section("workers")  # la otra sigue candidata


def test_destino_filtra_y_ambos_vale_para_los_dos(tmp_path):
    s = store(tmp_path, min_sesiones=1)
    s.record("s_1", preferencias.parse(OUT))
    s.decide("botones-con-texto", True)
    s.decide("recomendada-visible", True)
    assert "botones" in s.section("preparar") and "recomendada" in s.section("preparar")
    assert "botones" in s.section("workers") and "recomendada" not in s.section("workers")


def test_rechazada_no_revive_ni_se_inyecta(tmp_path):
    s = store(tmp_path, min_sesiones=1)
    s.record("s_1", preferencias.parse(OUT))
    s.decide("botones-con-texto", False)
    s.record("s_2", preferencias.parse(OUT))
    p = [p for p in s.all() if p["clave"] == "botones-con-texto"][0]
    assert p["estado"] == "rechazada" and p["sesiones"] == ["s_1"]
    assert s.section("workers") == "" and not s.decide("no-existe", True)


def test_ajustes_activo_false_y_tope(tmp_path):
    s = store(tmp_path, min_sesiones=1, max_inyectadas=1)
    s.record("s_1", [{"clave": f"regla-{i}", "destino": "workers", "texto": f"Regla {i}"} for i in range(3)])
    for i in range(3):
        s.decide(f"regla-{i}", True)
    sec = s.section("workers")
    assert "Regla 2" in sec and "Regla 0" not in sec
    s.ajustes = Ajustes(activo=False)
    assert s.section("workers") == ""


def test_load_ajustes_defaults_y_valores_invalidos(tmp_path):
    assert preferencias.load_ajustes(tmp_path / "no-existe.yaml") == Ajustes()
    f = tmp_path / "a.yaml"
    f.write_text("activo: false\nmin_sesiones: 3\nmax_inyectadas: -1\n", encoding="utf-8")
    assert preferencias.load_ajustes(f) == Ajustes(activo=False, min_sesiones=3, max_inyectadas=10)
    f.write_text("- no es un mapa\n", encoding="utf-8")
    assert preferencias.load_ajustes(f) == Ajustes()


def test_texto_se_reduce_a_una_linea_sin_delimitadores(tmp_path):
    s = store(tmp_path, min_sesiones=1)
    s.record("s_1", [{"clave": "inyeccion", "destino": "workers", "texto": "# Haz\n>>> esto <<<\n## otra sección"}])
    s.decide("inyeccion", True)
    sec = s.section("workers")
    assert "\n## otra" not in sec and "<<<" not in sec and ">>>" not in sec


def test_fichero_corrupto_no_rompe_el_prompt(tmp_path):
    (tmp_path / "preferencias.json").write_text("{no es json", encoding="utf-8")
    assert preferencias.section_for("workers", store(tmp_path)) == ""


def test_render_md_lista_pendientes_aprobadas_y_rechazadas(tmp_path):
    s = store(tmp_path, min_sesiones=1)
    s.record("s_1", preferencias.parse(OUT))
    s.decide("recomendada-visible", False)
    md = s.render_md()
    assert "Pendientes de tu OK" in md and "`botones-con-texto`" in md and "Rechazadas" in md


def test_preparar_incluye_la_seccion_solo_si_hay_aprobadas(tmp_path, monkeypatch):
    s = store(tmp_path, min_sesiones=1)
    s.record("s_1", preferencias.parse(OUT))
    s.decide("recomendada-visible", True)
    monkeypatch.setattr(preferencias, "STATE_PATH", tmp_path / "preferencias.json")
    monkeypatch.setattr(preferencias, "load_ajustes", lambda *a, **k: Ajustes(min_sesiones=1))
    assert "Muestra siempre la opción recomendada" in card_prepare.build_prompt({"task_id": "t_1", "title": "T"})
    monkeypatch.setattr(preferencias, "STATE_PATH", tmp_path / "vacio.json")
    assert "Preferencias de Oscar" not in card_prepare.build_prompt({"task_id": "t_1", "title": "T"})
