"""Formato único de aviso, /estado y parte de las 9:00 por petición (t_211c9902 · A2/A3/A4). Sin red ni kanban real."""
from datetime import datetime
from types import SimpleNamespace

from agent_lanes import formato_aviso as fa
from agent_lanes import notices
from agent_lanes import peticiones as pt
from agent_lanes import resumen_carriles as rc
from agent_lanes import resumen_peticiones as rp
from agent_lanes.commands import CommandCenter
from agent_lanes.resumen_carriles import MADRID

NOW = 1_800_000_000.0


def req(i, proyecto="Hub", titulo="Avisos", mov=NOW, tarjetas=("t_1",)):
    return {"id": f"req_{i}", "proyecto": proyecto, "titulo": titulo, "origen": "telegram", "tarjetas": list(tarjetas),
            "creado": mov, "ultimo_movimiento": mov, "tipo": None, "urgencia": None, "modo_oasp": None}


def test_aviso_lleva_proyecto_estado_proximo_quien_y_texto_libre():
    est = pt.Estado("atascada", "PR sin salida", 4, "resolver el PR de t_1", "el integrador")
    txt = fa.aviso_texto("Hub", "Avisos por tema", est, "req_39")
    assert txt.splitlines()[0] == "🧊 Hub · req_39 «Avisos por tema»"
    assert "Estado: atascada (PR sin salida) · paso 4/6" in txt
    assert "Próximo paso: resolver el PR de t_1 · lo da: el integrador" in txt and txt.endswith(fa.PIE_TEXTO_LIBRE)
    assert fa.botones_aviso("req_39")[0][0]["action"] == fa.REQ_MAS and fa.botones_aviso(None) == []


def test_resumir_y_paginas_nunca_pierden_texto():
    largo = "\n".join(f"línea {i} " + "x" * 80 for i in range(30))
    corto, resto = fa.resumir(largo, 300)
    assert resto and len(corto) <= 305
    assert (corto.rstrip(" …") + resto).replace("\n", "").replace(" ", "") == largo.replace("\n", "").replace(" ", "")
    assert fa.resumir("breve") == ("breve", None)
    pag = fa.paginas(largo, 500)
    assert all(len(p) <= 500 for p in pag) and "\n".join(pag) == largo
    assert "".join(fa.paginas("y" * 1200, 500)) == "y" * 1200


def test_estado_texto_ordena_filtra_y_cuenta_lo_que_no_cabe():
    items = [(req(1, "MigraTeam", "Login"), pt.Estado("esperándote", "", 3, "responder", "Oscar", NOW - 5 * 3600)),
             (req(2, "Hub", "Avisos"), pt.Estado("atascada", "PR sin salida", 4, "resolver", "el integrador")),
             (req(3, "Signal", "CSV"), pt.Estado("en curso", "", 3, "terminar", "el worker")),
             (req(4, "Pildora", "Landing"), pt.Estado("hecha", "", 6, "ninguno", "nadie"))]
    txt = fa.estado_texto(items, NOW)
    lineas = txt.splitlines()
    assert lineas[0] == "📍 Estado · 4 peticiones"
    assert [l.split(" ")[1] for l in lineas[2:]] == ["req_2", "req_1", "req_3", "req_4"]
    assert "esperándote: responder. Lo da: Oscar (hace 5 h)." in txt and "req_4 · Pildora · «Landing» — hecha." in txt
    solo = fa.estado_texto(items, NOW, "hub")
    assert "req_2" in solo and "req_1" not in solo and solo.startswith("📍 Estado · 1 petición de hub")
    muchas = [(req(i), pt.Estado("en curso", "", 3, "x", "el worker")) for i in range(1, 25)]
    assert "… y 9 más" in fa.estado_texto(muchas, NOW)
    assert "ninguna petición registrada" in fa.estado_texto([], NOW)


def test_ver_mas_muestra_la_cadena_completa():
    r = req(7, tarjetas=("t_1", "t_2"))
    r["tipo"], r["urgencia"] = "bug", "P1"
    shows = {"t_1": {"task": {"title": "Parte uno", "status": "done"}, "comments": [{"body": "INTEGRADO 3f9a1c0"}]},
             "t_2": None}
    txt = fa.cadena_texto(r, pt.derivar(r, shows, NOW), shows, NOW)
    assert "• t_1 · Parte uno · done · INTEGRADO 3f9a1c0" in txt and "• t_2 · no pude leerla" in txt
    assert "Tipo: bug · Urgencia: P1" in txt
    vacia = req(8, tarjetas=())
    assert "sin tarjeta" in fa.cadena_texto(vacia, pt.derivar(vacia, {}, NOW), {}, NOW)


def test_aviso_de_tarjeta_muestra_su_peticion():
    con = notices.render("done", "t_1", "Algo", "claude-hub", "terminada", body="req: req_9\nHazlo")
    assert "req_9" in con.splitlines()[1]
    assert "req_" not in notices.render("done", "t_1", "Algo", "claude-hub", "terminada", body="Hazlo")


def test_resumen_de_carriles_lleva_el_pie_de_texto_libre():
    d = [SimpleNamespace(tid="t_9", task={"title": "x"}, since=0)]
    assert rc.render_summary([], d, 0, 3600).endswith(fa.PIE_TEXTO_LIBRE)


def test_tema_por_proyecto():
    lanes = {"claude-hub": SimpleNamespace(telegram=("-100", "5")), "claude-migrateam": SimpleNamespace(telegram=None)}
    assert fa.tema_de("Hub", lanes) == ("-100", "5")
    assert fa.tema_de("MigraTeam", lanes) is None and fa.tema_de("Personal", lanes) is None


def test_parte_de_peticiones_novedad_sin_repetir_carriles_ni_salir_sin_novedad(tmp_path):
    reloj = [datetime(2026, 10, 22, 9, 5, tzinfo=MADRID)]
    items = [[(req(1, "Hub"), pt.Estado("atascada", "PR sin salida", 4, "resolver", "el integrador"))]]
    enviados = []
    parte = rp.DailyRequestSummary(lambda: items[0], lambda t: enviados.append(t) or True, tmp_path / "p.json",
                                   now=lambda: reloj[0])
    texto = parte.tick()
    assert texto and "▸ Hub" in texto and "req_1" in texto and "/hoy" in texto and "cola" not in texto
    assert parte.tick() is None and len(enviados) == 1  # mismo día: no repite
    reloj[0] = datetime(2026, 10, 23, 9, 0, tzinfo=MADRID)
    assert parte.tick() is None and len(enviados) == 1  # sin novedad desde ayer: no sale
    reloj[0] = datetime(2026, 10, 24, 9, 0, tzinfo=MADRID)
    items[0] = [(req(1, "Hub"), pt.Estado("hecha", "", 6, "ninguno", "nadie"))]
    assert "hecha" in parte.tick() and len(enviados) == 2  # cambió de estado
    reloj[0] = datetime(2026, 10, 25, 12, 0, tzinfo=MADRID)
    items[0] = []
    assert parte.tick() is None  # fuera de la franja 9-11


def test_parte_de_peticiones_reintenta_si_falla_el_envio(tmp_path):
    ok = [False]
    parte = rp.DailyRequestSummary(lambda: [(req(1), pt.Estado("en curso", "", 3, "x", "el worker"))],
                                   lambda t: ok[0], tmp_path / "p.json",
                                   now=lambda: datetime(2026, 10, 22, 10, 0, tzinfo=MADRID))
    assert parte.tick() is None
    ok[0] = True
    assert parte.tick() is not None


def test_comando_estado_lee_el_kanban_simulado(tmp_path):
    store = pt.Peticiones(tmp_path / "p.json", now=lambda: NOW)
    rid = store.crear("Hub", "Avisos", "telegram", tarjetas=["t_1"])["id"]
    store.crear("Signal", "Sin tarjeta", "claude-code")
    enviados = []
    cc = SimpleNamespace(peticiones=lambda: store, _now=lambda: NOW, desk=None,
                         _show_card=lambda tid: {"task": {"status": "done", "title": "T"},
                                                 "comments": [{"body": "INTEGRADO abc1234"}]},
                         _send=lambda where, text, **kw: enviados.append((text, kw)))
    cc.peticion_items = lambda proyecto=None: CommandCenter.peticion_items(cc, proyecto)
    cc.estado_peticiones = lambda where, proyecto: CommandCenter.estado_peticiones(cc, where, proyecto)
    assert {r["id"]: e.estado for r, e in cc.peticion_items()} == {rid: "hecha", "req_2": "atascada"}
    CommandCenter.cmd_estado(cc, ("1", "0"), "hub", None)  # Hub no es marca de la vista antigua: solo peticiones
    assert "Estado · 1 petición de Hub" in enviados[0][0] and "hecha" in enviados[0][0] and len(enviados) == 1
