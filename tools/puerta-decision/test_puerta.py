"""py -3.12 -m pytest tools/puerta-decision -q"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import puerta as p  # noqa: E402

AHORA = time.time()


def fake(cards, errores=None):
    return lambda: (cards, errores or {})


def card(i, titulo, estado="ready", board="oscarhq", dias=1, asignado="claude-oscarhq"):
    return {"id": i, "title": titulo, "status": estado, "_board": board, "assignee": asignado,
            "created_at": AHORA - dias * 86400, "completed_at": AHORA - dias * 86400}


def test_tarjeta_abierta_parecida_bloquea(tmp_path):
    cards = [card("t_1", "[OHQ · Staging] La cookie SSO del staging pisa la de producción · fix"),
             card("t_2", "Algo totalmente distinto de pantallas de reservas")]
    f = p.ficha("cookie SSO del staging pisa producción", 1, fake(cards), [], [], [])
    a1 = [l for l in f["lineas"] if l["criterio"] == "A1"][0]
    assert a1["estado"] == "✗" and "t_1" in a1["detalle"] and "t_2" not in a1["detalle"]
    assert f["bloqueo"] is True


def test_sin_parecidos_pasa(tmp_path):
    cards = [card("t_9", "Reservas: política si Google falla")]
    f = p.ficha("exportar informes de coste a una hoja de cálculo", 1, fake(cards), [str(tmp_path)], [], [])
    estados = {l["criterio"]: l["estado"] for l in f["lineas"]}
    assert estados["A1"] == "✓" and estados["A2"] == "✓" and estados["A3"] == "✓"
    assert f["bloqueo"] is False


def test_tarjeta_hecha_reciente_bloquea_a3_pero_no_a1(tmp_path):
    cards = [card("t_5", "Alerta automática de leads aprobados sin actividad", estado="done", dias=3),
             card("t_6", "Alerta automática de leads aprobados sin actividad", estado="done", dias=90)]
    f = p.ficha("alerta de leads aprobados sin actividad", 1, fake(cards), [], [], [])
    e = {l["criterio"]: l for l in f["lineas"]}
    assert e["A1"]["estado"] == "✓"
    assert e["A3"]["estado"] == "✗" and "t_5" in e["A3"]["detalle"] and "t_6" not in e["A3"]["detalle"]


def test_decision_previa_en_documento_fuerte(tmp_path):
    (tmp_path / "decision.md").write_text("DECISIÓN de Oscar: NextJobs queda pausado, no lanzar con el perfil de María\n", encoding="utf-8")
    f = p.ficha("lanzar NextJobs con el perfil de María", 1, fake([]), [str(tmp_path)], [], [])
    a2 = [l for l in f["lineas"] if l["criterio"] == "A2"][0]
    assert a2["estado"] == "✗" and "decision.md" in a2["detalle"]


def test_documento_flojo_da_aviso_no_bloqueo(tmp_path):
    (tmp_path / "otro.md").write_text("Notas sobre staging y cookies de sesión\n", encoding="utf-8")
    f = p.ficha("cookie SSO del staging con dominio propio para el dashboard", 1, fake([card("t_x", "Reservas")]), [str(tmp_path)], [], [])
    a2 = [l for l in f["lineas"] if l["criterio"] == "A2"][0]
    assert a2["estado"] in ("⚠", "✓") and f["bloqueo"] is False


def test_kanban_caido_es_na_no_aprueba_ni_bloquea(tmp_path):
    f = p.ficha("idea cualquiera con texto suficiente", 1, fake([], {"oscarhq": "bloqueado", "default": "x", "migrateam": "y"}), [], [], [])
    e = {l["criterio"]: l["estado"] for l in f["lineas"]}
    assert e["A1"] == "n/a" and e["A3"] == "n/a" and f["bloqueo"] is False
    assert f["fuentes_caidas"]


def test_redacta_secretos_en_fragmentos(tmp_path):
    (tmp_path / "m.md").write_text("clave de prueba: password: SuperSecreta123 y token sk-abcdefghijklmnopqrstuvwxyz para el staging cookie\n", encoding="utf-8")
    f = p.ficha("cookie staging clave de prueba token", 1, fake([]), [str(tmp_path)], [], [])
    assert "SuperSecreta123" not in json.dumps(f) and "sk-abcdefghijkl" not in json.dumps(f)
    assert "SuperSecreta123" not in p.redacta("password: SuperSecreta123")


def test_nivel_1_y_2_muestran_grupos_distintos(tmp_path):
    f1 = p.ficha("idea cualquiera con texto suficiente", 1, fake([]), [], [], [])
    f2 = p.ficha("idea cualquiera con texto suficiente", 2, fake([]), [], [], [])
    c1 = {l["criterio"] for l in f1["lineas"]}
    c2 = {l["criterio"] for l in f2["lineas"]}
    assert {"B", "E", "F", "G"}.isdisjoint(c1) and {"C", "D"} <= c1
    assert {"B", "C", "D", "E", "F", "G"} <= c2


def test_main_codigos_de_salida(tmp_path, capsys):
    cards = [card("t_1", "Cookie SSO del staging pisa la de producción")]
    assert p.main(["cookie SSO del staging pisa producción", "--nivel", "1", "--docs", str(tmp_path)], tarjetas_fn=fake(cards)) == 2
    assert p.main(["exportar informes de coste a una hoja", "--nivel", "1", "--docs", str(tmp_path)], tarjetas_fn=fake(cards)) == 0
    assert p.main(["corto"], tarjetas_fn=fake([])) == 1
    out = capsys.readouterr().out
    assert "Ficha de decisión" in out and "RESULTADO" in out


def test_json_valido(tmp_path, capsys):
    p.main(["exportar informes de coste a una hoja", "--json", "--docs", str(tmp_path)], tarjetas_fn=fake([]))
    j = json.loads(capsys.readouterr().out)
    assert j["lineas"] and "bloqueo" in j


def test_archivadas_se_ignoran(tmp_path):
    cards = [card("t_a", "Cookie SSO del staging pisa la de producción", estado="archived")]
    f = p.ficha("cookie SSO del staging pisa producción", 1, fake(cards), [], [], [])
    assert [l for l in f["lineas"] if l["criterio"] == "A1"][0]["estado"] == "✓"


def test_palabras_lejanas_no_son_decision_previa(tmp_path):
    # las cuatro palabras de la idea aparecen, pero a 40 líneas unas de otras: no es una decisión sobre ESO
    relleno = "\n".join(f"línea de relleno número {i} sin relación" for i in range(40))
    (tmp_path / "largo.md").write_text(f"cookie de sesión\n{relleno}\nstaging del panel\n{relleno}\ndominio compartido\n{relleno}\nproducción\n", encoding="utf-8")
    f = p.ficha("cookie staging dominio producción", 1, fake([]), [str(tmp_path)], [], [])
    a2 = [l for l in f["lineas"] if l["criterio"] == "A2"][0]
    assert a2["estado"] != "✗"


def test_ramas_exigen_dos_palabras_en_ideas_largas(tmp_path, monkeypatch):
    class R:
        stdout = "feat/dashboard-nuevo\nfeat/cookie-sso-staging\n"
    monkeypatch.setattr(p.subprocess, "run", lambda *a, **k: R())
    hits = p.buscar_ramas("cookie SSO staging dashboard producción", ["x"])
    assert [h["rama"] for h in hits] == ["feat/cookie-sso-staging"]


def test_tablero_caido_parcial_no_da_visto_bueno(tmp_path):
    # 2 de 3 tableros fallan: sin parecidos en el que sí respondió -> ⚠ (no ✓) en A1 y A3, y lo dice
    f = p.ficha("exportar informes de coste a una hoja de cálculo", 1,
                fake([card("t_9", "Reservas: política si Google falla")], {"default": "x", "migrateam": "y"}), [], [], [])
    e = {l["criterio"]: l for l in f["lineas"]}
    assert e["A1"]["estado"] == "⚠" and "default" in e["A1"]["detalle"] and "migrateam" in e["A1"]["detalle"]
    assert e["A3"]["estado"] == "⚠"
    assert f["bloqueo"] is False


def test_busqueda_de_pr_caida_no_da_visto_bueno(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "buscar_prs", lambda texto, repos: ([], {"oscarsao/x": "TimeoutExpired"}))
    f = p.ficha("exportar informes de coste a una hoja de cálculo", 1, fake([card("t_9", "Reservas")]), [], ["oscarsao/x"], [])
    a1 = [l for l in f["lineas"] if l["criterio"] == "A1"][0]
    assert a1["estado"] == "⚠" and "PR oscarsao/x" in a1["detalle"]


def test_un_parecido_sigue_bloqueando_aunque_falte_un_tablero(tmp_path):
    cards = [card("t_1", "Cookie SSO del staging pisa la de producción")]
    f = p.ficha("cookie SSO del staging pisa producción", 1, fake(cards, {"default": "x"}), [], [], [])
    assert f["bloqueo"] is True


def test_texto_final_avisa_que_no_cubre_las_reglas_duras(tmp_path):
    f = p.ficha("exportar informes de coste a una hoja", 1, fake([card("t_9", "Reservas")]), [], [], [])
    t = p.a_texto(f)
    assert "NO cubre las reglas duras" in t and "NUNCA se decide por la ficha" in t
