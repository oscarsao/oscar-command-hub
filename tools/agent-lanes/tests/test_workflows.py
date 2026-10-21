import pytest

from agent_lanes import cola_integrador as ci
from agent_lanes import workflows as wf


@pytest.fixture
def store(tmp_path):
    return wf.Store(tmp_path / "wf.json")


def plan(store, n=2, **kw):
    tarjetas = [{"titulo": f"T{i}", "carril": "claude-hub", "depende": [f"T{i-1}"] if i and kw.pop("cadena", False) else []}
                for i in range(n)]
    return wf.propose(store, wid="wf_1", titulo="Login", req="req_7", preguntas=["¿A?"], tarjetas=tarjetas,
                      riesgos=["migración"], prueba="abrir /login", coste_estimado_eur=5, ajustes=wf.Ajustes())


def crea(log):
    def f(item, req):
        log.append((item["titulo"], req))
        return f"t_{len(log):08d}"
    return f


def test_sin_aprobar_no_se_crea_ninguna_tarjeta(store):
    plan(store)
    log = []
    assert wf.launch(store, "wf_1", crea(log), wf.Ajustes()) == []
    assert log == []


def test_aprobado_crea_y_enlaza_req(store):
    plan(store)
    wf.approve(store, "wf_1")
    log = []
    assert len(wf.launch(store, "wf_1", crea(log), wf.Ajustes())) == 2
    assert {r for _, r in log} == {"req_7"}
    assert store.get("wf_1")["estado"] == "en curso"


def test_render_plan_muestra_tope_y_botones(store):
    texto, botones = wf.render_plan(plan(store))
    assert "tope 8 €" in texto and "No se crea nada" in texto
    assert [b[0] for b in botones[0]] == ["✅ Aprobar plan y gasto", "✍️ Cambiar", "🗄 Aparcar"]


def test_maximo_preguntas_y_plan_vacio(store):
    with pytest.raises(ValueError):
        wf.propose(store, wid="w", titulo="x", req="req_1", preguntas=list("abcdef"), tarjetas=[{"titulo": "a", "carril": "c"}],
                   riesgos=[], prueba="", coste_estimado_eur=1, ajustes=wf.Ajustes())
    with pytest.raises(ValueError):
        wf.propose(store, wid="w", titulo="x", req="req_1", preguntas=[], tarjetas=[], riesgos=[], prueba="",
                   coste_estimado_eur=1, ajustes=wf.Ajustes())


def test_tope_de_tarjetas_activas_y_de_codigo(store):
    wf.propose(store, wid="wf_1", titulo="G", req="req_1", preguntas=[], riesgos=[], prueba="p", coste_estimado_eur=1,
               tarjetas=[{"titulo": f"T{i}", "carril": "c"} for i in range(7)], ajustes=wf.Ajustes())
    wf.approve(store, "wf_1")
    log = []
    nuevas = wf.launch(store, "wf_1", crea(log), wf.Ajustes(max_workstreams_codigo=10))
    assert len(nuevas) == 5
    log2 = []
    wf.propose(store, wid="wf_2", titulo="H", req="req_2", preguntas=[], riesgos=[], prueba="p", coste_estimado_eur=1,
               tarjetas=[{"titulo": f"U{i}", "carril": "c"} for i in range(3)], ajustes=wf.Ajustes())
    wf.approve(store, "wf_2")
    assert wf.launch(store, "wf_2", crea(log2), wf.Ajustes(max_workstreams_codigo=6)) == ["t_00000001"]


def test_dependencias_esperan(store):
    plan(store, cadena=True)
    wf.approve(store, "wf_1")
    log = []
    assert len(wf.launch(store, "wf_1", crea(log), wf.Ajustes())) == 1
    wf.card_done(store, "wf_1", "t_00000001")
    assert len(wf.launch(store, "wf_1", crea(log), wf.Ajustes())) == 1
    assert wf.progreso(store.get("wf_1")) == "1/2 tarjetas"


def test_gasto_avisa_una_vez_pausa_y_amplia(store):
    plan(store)
    wf.approve(store, "wf_1")
    wf.launch(store, "wf_1", crea([]), wf.Ajustes())
    assert wf.record_spend(store, "wf_1", 6.0)[1] is None
    assert wf.record_spend(store, "wf_1", 0.5)[1] == "aviso"
    assert wf.record_spend(store, "wf_1", 0.5)[1] is None
    w, ev = wf.record_spend(store, "wf_1", 1.0)
    assert ev == "pausa" and w["estado"] == "pausado"
    texto, botones = wf.render_gasto(w, "pausa")
    assert [b[0] for b in botones[0]] == ["➕ Ampliar", "⛔ Parar"]
    w = wf.ampliar(store, "wf_1", wf.Ajustes())
    assert w["tope_eur"] == 16 and w["estado"] == "en curso"
    assert wf.parar(store, "wf_1")["estado"] == "parado"


def test_ajustes_editables_y_yaml_roto(tmp_path):
    p = tmp_path / "w.yaml"
    assert wf.load_ajustes(p).tope_eur == 8
    assert wf.set_ajuste("tope_eur", 12, p).tope_eur == 12
    assert wf.set_ajuste("aviso_pct", 50, p).aviso_pct == 50 and wf.load_ajustes(p).tope_eur == 12
    with pytest.raises(ValueError):
        wf.set_ajuste("aviso_pct", 150, p)
    with pytest.raises(ValueError):
        wf.set_ajuste("inventado", 1, p)
    p.write_text("tope_eur: [1\n", encoding="utf-8")
    assert wf.load_ajustes(p) == wf.Ajustes()
    p.write_text("tope_eur: -3\nmax_preguntas: 2\n", encoding="utf-8")
    assert wf.load_ajustes(p).tope_eur == 8 and wf.load_ajustes(p).max_preguntas == 2


def test_entrega_unica(store):
    w = plan(store)
    t = wf.render_entrega(w, "Login hecho", ["PR #9"], "entra con Google")
    assert "Qué se hizo" in t and "PR #9" in t and "Cómo probarlo" in t
    assert wf.deliver(store, "wf_1")["estado"] == "entregado"


def test_cola_nunca_migra_ni_resuelve_conflictos():
    ejec = {a: (lambda it, a=a: hechas.append(a)) for a in list(ci.AUTOMATICAS) + list(ci.PIDE_OK) + ["raro"]}
    hechas = []
    cola = [{"accion": a} for a in ("aplicar_migracion", "resolver_conflicto", "fusionar_main", "desplegar_prod",
                                    "borrar", "tocar_hooks", "raro")]
    cola.append({"accion": "montar_lote"})
    out = ci.procesar(cola, ejec)
    assert hechas == ["montar_lote"] and out["hechas"] == ["montar_lote"]
    assert len(out["pide_ok"]) == 7 and all(p["botones"] for p in out["pide_ok"])


def test_cola_fallo_aislado_y_sin_ejecutor():
    def mal(it):
        raise RuntimeError("gate roto")
    out = ci.procesar([{"accion": "pasar_gates"}, {"accion": "montar_lote"}, {"accion": "reabrir_lote"}],
                      {"pasar_gates": mal, "montar_lote": lambda it: None})
    assert out["hechas"] == ["montar_lote"]
    assert [f["accion"] for f in out["fallidas"]] == ["pasar_gates", "reabrir_lote"]


def test_escalado_con_umbrales_configurables():
    assert ci.escalado(1, 2, 24) is None
    assert ci.escalado(2, 2, 24) == "escalar_2h"
    assert ci.escalado(30, 2, 24) == "recordar_24h"
    assert ci.escalado(3, 4, 24) is None
