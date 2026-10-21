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
    cc = SimpleNamespace(registro_peticiones=lambda: store, _now=lambda: NOW, desk=None,
                         _show_card=lambda tid: {"task": {"status": "done", "title": "T"},
                                                 "comments": [{"body": "INTEGRADO abc1234"}]},
                         _send=lambda where, text, **kw: enviados.append((text, kw)))
    cc.peticion_items = lambda proyecto=None: CommandCenter.peticion_items(cc, proyecto)
    cc.estado_peticiones = lambda where, proyecto: CommandCenter.estado_peticiones(cc, where, proyecto)
    assert {r["id"]: e.estado for r, e in cc.peticion_items()} == {rid: "hecha", "req_2": "atascada"}
    CommandCenter.cmd_estado(cc, ("1", "0"), "hub", None)  # Hub no es marca de la vista antigua: solo peticiones
    assert "Estado · 1 petición de Hub" in enviados[0][0] and "hecha" in enviados[0][0] and len(enviados) == 1


# --- ronda 1 de revisión: formato único aplicado de verdad + texto libre en temas de foro ----------------------

def test_render_de_aviso_lleva_proyecto_estado_paso_proximo_quien_y_pie():
    txt = notices.render("needs_input", "t_1", "Algo", "claude-migrateam", "esperando", body="req: req_9\nHazlo",
                         next_step=notices.next_step("needs_input"))
    lineas = txt.splitlines()
    assert lineas[1].startswith("MigraTeam") and "req_9" in lineas[1]            # proyecto + petición
    assert "esperando · paso 3/6" in txt                                          # estado y paso
    assert "➡️ Próximo paso: responder con un botón · Lo da: Oscar" in txt         # próximo paso y quién
    assert lineas[-1] == fa.PIE_TEXTO_LIBRE                                       # texto libre además de botones
    ajeno = notices.render("running", "t_1", "Algo", "claude-hub", "en marcha", next_step=notices.next_step("running"))
    assert fa.PIE_TEXTO_LIBRE not in ajeno and "Lo da: el carril" in ajeno        # no le toca a Oscar: sin pie


def test_render_resume_el_texto_largo_y_apunta_a_explicame_mas():
    largo = "Explicación " + "muy larga " * 60
    txt = notices.render("done", "t_1", "Algo", "claude-hub", "ok", for_oscar=largo)
    para = next(l for l in txt.splitlines() if l.startswith("Para ti:"))
    assert para.endswith("(resto: 💬 Explícame más)") and len(para) < 400
    assert "Explícame" not in notices.render("done", "t_1", "Algo", "claude-hub", "ok", for_oscar="corto")


def test_aviso_stuck_y_su_recordatorio_usan_el_formato_nuevo():
    from agent_lanes.renotify import Renotifier
    enviados = []
    desk = SimpleNamespace(owner_id=1, markup=lambda *a, **k: {"inline_keyboard": []})
    notifier = SimpleNamespace(send_to=lambda chat, thread, text, **kw: enviados.append(text) or True)
    yo = SimpleNamespace(desk=desk, notifier=notifier)
    p = SimpleNamespace(tid="t_5", task={"title": "Atascada"}, lane=SimpleNamespace(name="claude-hub"),
                        status="🧊 atascada  @oscar", bullets=["Falta: tu respuesta"], block_kind="needs_input")
    assert Renotifier.remind_dm(yo, p, 24)
    texto = enviados[0]
    assert "paso 3/6" in texto and "➡️ Próximo paso: sacar de triage o archivar · Lo da: Oscar" in texto
    assert texto.splitlines()[-1] == fa.PIE_TEXTO_LIBRE
    # consola: no lo puede resolver Oscar desde Telegram -> lo da el integrador y no hay pie de texto libre
    assert fa.PIE_TEXTO_LIBRE not in notices.render("stuck", "t_5", "x", "claude-hub", "s",
                                                     next_step=notices.next_step("blocked", "console"))


def test_avisos_van_al_tema_del_proyecto_de_su_peticion(tmp_path):
    store = pt.Peticiones(tmp_path / "p.json", now=lambda: NOW)
    store.crear("Hub", "Avisos", "telegram", tarjetas=["t_1"])
    lanes = {"claude-hub": SimpleNamespace(telegram=("-100", "5"))}
    enviados = []
    tg = SimpleNamespace(send=lambda text, target, lane_target, **kw: enviados.append((target, lane_target)) or None)
    avisos = notices.TaskNotices(tg, tema_de=fa.tema_por_texto(store, lanes))
    avisos.publish("t_1", "✅ t_1 · x\nHub · req_1", None, ("-1", "9"))
    avisos.publish("t_2", "✅ t_2 · sin petición", None, ("-1", "9"))
    avisos.publish("t_3", "✅ t_3 · con origen\nreq_1", ("-7", "7"), ("-1", "9"))
    assert enviados == [(("-100", "5"), ("-1", "9")), (None, ("-1", "9")), (("-7", "7"), ("-1", "9"))]


def test_botones_de_aviso_de_peticion_son_solo_ver_mas_en_la_entrega_a():
    assert [b["action"] for row in fa.botones_aviso("req_1") for b in row] == [fa.REQ_MAS]


# Texto libre en un TEMA DE FORO = igual que en DM (absorbe t_154c3748)

def _libre(tmp_path, *, forum, extra_notice=False, reply_to=None):
    from tests import test_free_text as ft
    desk, tg, h, messages = ft._setup(tmp_path, ft.FH(t_1="blocked", t_2="blocked"))
    chat, thread = (ft.GROUP, ft.TOPIC) if forum else (ft.OSCAR, 0)
    ft._notice(desk, messages, "t_1", mid=77, thread=thread, chat=chat)
    if extra_notice:
        ft._notice(desk, messages, "t_2", mid=78, thread=thread, chat=chat)
    extra = {"reply_to_message": {"message_id": reply_to}} if reply_to else {}
    ft._say(desk, "la B, pero con tests", chat=chat, thread=thread, private=not forum, **extra)
    return h.calls, tg.sent


def test_clarify_en_tema_de_foro_con_texto_libre_se_comporta_como_en_dm(tmp_path):
    dm_calls, dm_sent = _libre(tmp_path / "dm", forum=False)
    foro_calls, foro_sent = _libre(tmp_path / "foro", forum=True)
    assert foro_calls == dm_calls and [c[0] for c in foro_calls] == ["comment", "unblock"]  # anotada ANTES de confirmar
    assert foro_calls[0][2] == "Respuesta de Oscar: ¿A o B? → la B, pero con tests"
    assert foro_sent[-1]["text"] == dm_sent[-1]["text"] == "Entendido: «la B, pero con tests» para t_1"
    assert foro_sent[-1]["thread"] == "231" and dm_sent[-1]["thread"] == "0"       # el eco vuelve al mismo hilo


def test_en_un_tema_con_dos_avisos_el_reply_decide_a_cual_responde(tmp_path):
    sin_reply, _ = _libre(tmp_path / "a", forum=True, extra_notice=True)
    assert sin_reply == []                                                          # sin reply no adivina
    con_reply, _ = _libre(tmp_path / "b", forum=True, extra_notice=True, reply_to=78)
    assert [c[:2] for c in con_reply] == [("comment", "t_2"), ("unblock", "t_2")]
