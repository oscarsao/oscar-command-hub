"""Integración automática de lo pequeño (t_f5422078): clasificación, interruptor, ventana de veto y fusión.
Dobles de test_integrator (git/gh/Hermes/Telegram simulados): nada real."""
from __future__ import annotations

import json

import pytest

from agent_lanes import ajustes_hub, cola_integrador
from agent_lanes.cola_integrador import clasificar_integrable, es_solo_docs, proyecto_permitido
from tests.test_integrator import (GH, HEAD, MERGE, MGT_SLUG, OHQ_SLUG, INTEGRATOR_AUTHOR, World, cp, make, press)

PROYECTOS = ("oscar-hq", "oscar-command-hub")
OK = dict(ids=("oscar-hq", "claude-oscarhq"), proyectos=PROYECTOS, tamano="S", zona=["docs/"], ficheros=["docs/a.md"],
          revisado=True, suite_ok=True)
CUERPO = "## Objetivo\nUna nota\nZona: docs/\nTamaño: M"


def clasifica(**cambios):
    return clasificar_integrable(**{**OK, **cambios})


# --- clasificación: cada condición por separado ----------------------------------------------------------

def test_el_caso_base_es_integrable():
    assert clasifica() == []


def test_tamano_s_basta_aunque_no_sea_documentacion():
    assert clasifica(tamano="S", zona=["src/"], ficheros=["src/app.py"]) == []


def test_ni_s_ni_documentacion_no_entra():
    assert clasifica(tamano="M", zona=["src/"], ficheros=["src/app.py"]) == ["no es de tamaño S ni solo documentación"]


def test_solo_documentacion_aunque_el_tamano_sea_m():
    assert clasifica(tamano="M", zona=["pitches/", "x.md"], ficheros=["pitches/p.md", "x.md"]) == []
    assert es_solo_docs(["docs/a.txt", "specs/b.md", "decisions/c.md", "README.md"])
    assert not es_solo_docs(["docs/a.md", "src/app.py"]) and not es_solo_docs([])


def test_el_diff_debe_caer_en_la_zona_declarada():
    assert "Zona" in clasifica(ficheros=["docs/a.md", "docs2/b.md"])[0]
    assert "Zona" in clasifica(zona=[])[0]
    assert clasifica(zona=["docs"], ficheros=["docs/sub/a.md"]) == []   # carpeta sin barra final
    assert "Zona" in clasifica(zona=["docs"], ficheros=["docsx/a.md"])[0]  # no es prefijo de texto


def test_migraciones_no_entran():
    assert clasifica(migracion="alembic") == ["lleva migraciones"]
    assert "lleva migraciones" in clasifica(zona=["supabase/"], ficheros=["supabase/migrations/1.sql"], tamano="S")


@pytest.mark.parametrize("ruta", [".env", "dashboard/.env.local", "tools/agent-lanes/contract/guard.py",
                                  ".github/workflows/ci.yml", "tools/agent-lanes/lanes.yaml", "app/secrets.py"])
def test_ficheros_sensibles_no_entran(ruta):
    assert "toca ficheros sensibles" in clasifica(tamano="S", zona=[ruta], ficheros=[ruta])


def test_sensibles_detectados_por_la_politica_no_entran():
    assert "toca ficheros sensibles" in clasifica(sensibles=["railway.json"])


def test_suite_en_rojo_no_entra_y_sin_ejecutar_no_cuenta_como_fallo():
    assert clasifica(suite_ok=False) == ["la suite no está en verde"]
    assert clasifica(suite_ok=None) == []


def test_sin_conformidad_del_revisor_no_entra():
    assert clasifica(revisado=False) == ["el revisor no ha dado conformidad a esta cabeza"]


def test_proyecto_fuera_de_la_lista_no_entra():
    assert "lista" in clasifica(ids=("cartera", "claude-cartera"))[0]


@pytest.mark.parametrize("ids", [("OCR-PDF-and-images", "claude-migrateam"), ("otro", "claude-migrateam"),
                                 ("migrateam", "x")])
def test_migrateam_nunca_entra_ni_poniendolo_en_la_lista(ids):
    assert clasifica(ids=ids, proyectos=(*PROYECTOS, "ocr-pdf-and-images", "migrateam")) == [
        "MigraTeam queda fuera de la integración automática"]
    assert not proyecto_permitido(ids, (*ids, "migrateam"))


def test_mayusculas_en_la_lista_de_proyectos():
    assert proyecto_permitido(("Oscar-HQ", "x"), ("oscar-hq",))


def test_la_cola_sigue_pidiendo_ok_para_fusionar_main():
    assert not cola_integrador.es_automatica("fusionar_main") and "fusionar_main" in cola_integrador.PIDE_OK


# --- ajustes ---------------------------------------------------------------------------------------------

def test_ajustes_por_defecto_y_registro_de_quien_cambia():
    assert ajustes_hub.valor("integracion_auto.activa") == 0           # apagado en el código
    assert ajustes_hub.valor("integracion_auto.veto_minutos") == 30
    assert ajustes_hub.valor("integracion_auto.proyectos") == PROYECTOS
    ajustes_hub.almacen().set("integracion_auto.activa", 1, "oscar")
    assert ajustes_hub.valor("integracion_auto.activa") == 1
    ultimo = ajustes_hub.almacen().historial("integracion_auto.activa")[-1]
    assert ultimo["quien"] == "oscar" and ultimo["antes"] == 0 and ultimo["despues"] == 1
    with pytest.raises(ajustes_hub.AjusteInvalido):
        ajustes_hub.almacen().set("integracion_auto.activa", 2, "oscar")


# --- flujo en el integrador ------------------------------------------------------------------------------

class MundoAuto(World):
    """World con la lista de ficheros del PR (`gh pr view --json files`)."""
    pr_files = ["docs/a.md"]

    def _gh(self, args):
        if args[1:3] == ["pr", "view"] and "files" in args:
            return cp(0, json.dumps({"files": [{"path": p} for p in self.pr_files]}))
        return super()._gh(args)


def montar(tmp_path, *, activo=True, name="claude-oscarhq", slug=OHQ_SLUG, cuerpo=CUERPO, ficheros=("docs/a.md",)):
    if activo:
        ajustes_hub.almacen().set("integracion_auto.activa", 1, "oscar")
    w = MundoAuto(slug)
    w.pr_files, w.changed = list(ficheros), list(ficheros)
    integ, w, h, tg, d, clock = make(tmp_path, name=name, comments=[], approve=False, world=w)
    h.tasks[0]["body"] = cuerpo
    t = [1_000_000.0]
    integ._now = lambda: t[0]
    return integ, w, h, tg, d, t


def otras_llamadas(w):
    """Todo lo que no sea el repaso de PR abiertos (pr_watch), que corre siempre."""
    return [a for a, _ in w.calls if a[1:3] != ["pr", "list"]]


def fusiones(w):
    return [a for a in w.argv(GH) if a[1:3] == ["pr", "merge"]]


def test_apagado_no_cambia_nada(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path, activo=False)
    assert integ.run_pass() == {}
    assert not tg.sent and not h.comments and not fusiones(w) and not otras_llamadas(w)


def test_documentacion_con_suite_verde_se_fusiona_tras_la_ventana_de_veto(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path)
    assert integ.run_pass(force=True) == {"t_1": "auto_veto"}
    assert "se fusiona sola en 30 min" in tg.sent[-1]["text"]
    assert tg.sent[-1]["markup"]["inline_keyboard"][0][0]["text"] == "⛔ Vetar"
    assert not fusiones(w)
    t[0] += 29 * 60
    assert integ.run_pass(force=True) == {}                         # dentro de la ventana: nada
    assert not fusiones(w)
    t[0] += 2 * 60
    assert integ.run_pass(force=True) == {"t_1": "auto_merged"}
    merge = fusiones(w)[0]
    assert "--squash" in merge and merge[merge.index("--match-head-commit") + 1] == HEAD
    assert not {"--force", "--admin", "--auto"} & set(merge)
    assert ("t_1", f"INTEGRADO {MERGE} · PR https://github.com/{OHQ_SLUG}/pull/7", INTEGRATOR_AUTHOR) in h.comments
    assert not [a for a in w.argv("railway.exe") if "up" in a]       # el despliegue sigue siendo un botón
    assert "🚀 Desplegar" in [b["text"] for r in tg.edits[-1]["markup"]["inline_keyboard"] for b in r]


def test_ventana_configurable(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path)
    ajustes_hub.almacen().set("integracion_auto.veto_minutos", 5, "oscar")
    integ.run_pass(force=True)
    t[0] += 6 * 60
    assert integ.run_pass(force=True) == {"t_1": "auto_merged"}


def test_veto_impide_la_fusion_y_queda_para_el_camino_normal(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path)
    integ.run_pass(force=True)
    press(d, tg)
    assert integ._state("t_1")["status"] == "auto_vetado"
    assert any("vetada por Oscar" in x for _, x, _ in h.comments)
    t[0] += 3600
    assert integ.run_pass(force=True) == {}
    assert not fusiones(w)


def test_veto_de_otro_usuario_no_hace_nada(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path)
    integ.run_pass(force=True)
    press(d, tg, user=999)
    assert integ._state("t_1")["status"] == "auto_veto"


def test_apagar_durante_la_ventana_cancela(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path)
    integ.run_pass(force=True)
    ajustes_hub.almacen().set("integracion_auto.activa", 0, "oscar")
    t[0] += 3600
    assert integ.run_pass(force=True) == {}
    assert not fusiones(w) and integ._state("t_1")["status"] == "auto_apagado"


def test_commit_nuevo_en_la_ventana_cancela(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path)
    integ.run_pass(force=True)
    w.pr = {**w.pr, "headRefOid": "e" * 40}
    t[0] += 3600
    integ.run_pass(force=True)
    assert not fusiones(w) and integ._state("t_1")["status"] == "auto_no"


def test_tamano_m_con_codigo_no_se_fusiona_ni_avisa(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path, cuerpo="Zona: src/\nTamaño: M", ficheros=("src/app.py",))
    assert integ.run_pass(force=True) == {}
    assert not tg.sent and not fusiones(w)


def test_con_migracion_no_se_fusiona(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path, cuerpo="Zona: supabase/\nTamaño: S",
                                   ficheros=("supabase/migrations/001.sql",))
    assert integ.run_pass(force=True) == {}
    assert not tg.sent and not fusiones(w)


def test_suite_en_rojo_no_se_fusiona(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path)
    w.test_rc = 1
    assert integ.run_pass(force=True) == {}
    assert not tg.sent and not fusiones(w)
    assert any("descartada" in x for _, x, _ in h.comments)


def test_diff_fuera_de_la_zona_no_se_fusiona(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path, ficheros=("docs/a.md", "src/b.py"))
    assert integ.run_pass(force=True) == {}
    assert not tg.sent and not fusiones(w)


def test_sin_revision_conforme_no_se_fusiona(tmp_path):
    integ, w, h, tg, d, t = montar(tmp_path)
    h.reviewed = "f" * 40
    assert integ.run_pass(force=True) == {}
    assert not tg.sent and not fusiones(w)


def test_migrateam_nunca_entra(tmp_path):
    ajustes_hub.almacen().set("integracion_auto.proyectos", "OCR-PDF-and-images,migrateam", "oscar")
    integ, w, h, tg, d, t = montar(tmp_path, name="claude-migrateam", slug=MGT_SLUG)
    assert integ.run_pass(force=True) == {}
    assert not tg.sent and not fusiones(w) and not otras_llamadas(w)


def test_una_tarjeta_aprobada_por_oscar_sigue_el_camino_de_siempre(tmp_path):
    ajustes_hub.almacen().set("integracion_auto.activa", 1, "oscar")
    integ, w, h, tg, d, _ = make(tmp_path)
    assert integ.run_pass() == {"t_1": "offered"}
