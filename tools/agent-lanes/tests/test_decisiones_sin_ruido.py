"""Tema Decisiones sin ruido (t_b0fc4283): un aviso por conjunto, cierre de los muertos y «Decides tú» de las 9:00."""
from __future__ import annotations

from types import SimpleNamespace

from agent_lanes import canal_avisos, choques, solapes
from agent_lanes import resumen_carriles as rc
from tests.test_canal_avisos import KB, OWNER, TEMA, TG, _obsoletas, canal


def _edits(tg):
    """Textos editados, sin las ediciones del contador fijado."""
    return [p["text"] for p in tg.edits() if not p["text"].startswith("🎯 <b>")]


def _canal(tmp_path):
    tg = TG()
    return canal(tmp_path, tg, vivos={"tok123456"}), tg


def test_un_conjunto_de_tarjetas_tiene_un_solo_mensaje_y_se_edita(tmp_path):
    c, tg = _canal(tmp_path)
    send = canal_avisos.emisor(c, tg, "solape", OWNER)
    send("solape A", KB, conjunto="t_1|t_2")
    send("solape A otra vez", KB, conjunto="t_1|t_2")           # mismo conjunto: se edita
    send("solape con t_3", KB, conjunto="t_1|t_2|t_3")           # conjunto ampliado (comparte tarjetas): se edita
    assert len(tg.sends(TEMA[0], TEMA[1])) == 1
    assert _edits(tg) == ["solape A otra vez", "solape con t_3"]
    assert len(c.pendientes()) == 1 and c.pendientes()[0]["conjunto"] == "t_1|t_2|t_3"
    send("otro", KB, conjunto="t_8|t_9")                          # conjunto distinto: mensaje propio
    assert len(tg.sends(TEMA[0], TEMA[1])) == 2


def test_choque_un_mensaje_por_par_aunque_cambie_de_clase(tmp_path):
    c, tg = _canal(tmp_path)
    send = canal_avisos.emisor(c, tg, "choque", OWNER)
    send("temprano", KB, conjunto="t_1|t_2")
    send("conflicto", KB, conjunto="t_1|t_2")
    send("otro par", KB, conjunto="t_1|t_3")                      # los choques no se funden por compartir tarjeta
    assert len(tg.sends(TEMA[0], TEMA[1])) == 2 and _edits(tg) == ["conflicto"]


def test_conjunto_que_ya_no_solapa_se_cierra_a_la_segunda_pasada_sin_botones(tmp_path):
    c, tg = _canal(tmp_path)
    send = canal_avisos.emisor(c, tg, "solape", OWNER)
    send("solape", KB, conjunto="t_1|t_2")
    send("solape", KB, conjunto="t_5|t_6")
    assert send.cerrar_salvo(["t_5|t_6"]) == 0
    assert len(c.pendientes()) == 2                                # primera ausencia: solo se marca
    assert send.cerrar_salvo(["t_5|t_6"]) == 0
    assert [e["conjunto"] for e in c.pendientes()] == ["t_5|t_6"]
    assert _edits(tg)[-1].startswith("✅ Resuelto: ya no solapan")
    assert "reply_markup" not in [p for p in tg.edits() if p["text"].startswith("✅ Resuelto")][0]


def test_aviso_antiguo_sin_conjunto_se_cierra_y_avisa_al_ticker(tmp_path):
    c, tg = _canal(tmp_path)
    data = c._load()
    data["abiertas"]["g:900"] = {"tipo": "solape", "rol": "solo", "token": "x", "head": "viejo", "chat_id": TEMA[0],
                                 "message_id": 900, "thread_id": TEMA[1], "at": 1.0}
    c._save(data)
    send = canal_avisos.emisor(c, tg, "solape", OWNER)
    assert send.cerrar_salvo([]) == 1  # a la primera pasada fiable: basta una para el barrido inicial
    assert c.pendientes() == []


def test_solape_ticker_manda_el_conjunto_y_cierra_lo_resuelto(tmp_path):
    class Send:
        conjuntos = True

        def __init__(self):
            self.enviados, self.vigentes = [], []

        def __call__(self, texto, markup=None, conjunto=None):
            self.enviados.append(conjunto)

        def cerrar_salvo(self, vigentes):
            self.vigentes.append(list(vigentes))
            return 0

    card = lambda t: solapes.Card(t, "b", "l", "ready", "x")  # noqa: E731
    g = solapes.Grupo(repo="r", cards=[card("t_2"), card("t_1")], comparten=["a.py"])
    t = solapes.SolapeTicker({}, None, send=Send(), state_path=tmp_path / "s.json")
    t._enviar(g, solapes.Ajustes())
    t._cerrar_resueltos([g], [])
    assert t.send.enviados == ["t_1|t_2"] and t.send.vigentes == [["t_1|t_2"]]


def test_choques_ticker_manda_el_par_ordenado(tmp_path):
    enviados = []

    def send(texto, markup=None, conjunto=None):
        enviados.append(conjunto)

    send.conjuntos = True
    u = lambda uid: choques.Unidad(uid, uid)  # noqa: E731
    c = choques.Choque("r", "conflicto", u("t_9"), u("t_3"), ["a.py"], "")
    t = choques.ChoquesTicker({}, None, send=send, state_path=tmp_path / "c.json")
    t._enviar(c, choques.Ajustes())
    assert enviados == ["t_3|t_9"]


# --- tarjetas cerradas: sin el tope de 25 -----------------------------------------------------------------------------

def test_avisos_de_tarjetas_cerradas_se_cierran_todos_de_una_pasada(tmp_path, caplog):
    n = canal_avisos.CERRAR_MAX + 30
    tarjetas = {f"t_{i}": {"status": "done", "comentarios": []} for i in range(n)}
    c, tg = _obsoletas(tmp_path, tarjetas, [("needs_input", f"t_{i}") for i in range(n)])
    with caplog.at_level("INFO", logger="agent_lanes"):
        assert c.cerrar_obsoletas() == n
    assert c.pendientes() == [] and f"{n} avisos obsoletos cerrados" in caplog.text


def test_aprobar_conserva_el_tope_por_pasada(tmp_path):
    n = canal_avisos.CERRAR_MAX + 5
    tarjetas = {f"t_{i}": {"status": "done", "comentarios": ["INTEGRADO abc"]} for i in range(n)}
    c, _ = _obsoletas(tmp_path, tarjetas, [("aprobar", f"t_{i}") for i in range(n)])
    assert c.cerrar_obsoletas() == canal_avisos.CERRAR_MAX


# --- preguntas sueltas: solo dinero o lanzamiento ---------------------------------------------------------------------

class Notices:
    def __init__(self):
        self.publicados = []

    def publish(self, tid, text, target, lane_target, **kw):
        self.publicados.append((tid, text, target, kw))
        return None


def test_pregunta_normal_no_va_al_tema_y_la_de_dinero_o_lanzamiento_si(tmp_path):
    c, _ = _canal(tmp_path)
    n = Notices()
    c.publicar(n, "needs_input", "t_1", "¿Qué color?", None, ("g", "5"), alert=True, reply_markup=KB)
    c.publicar(n, "needs_input", "t_2", "¿Publicamos el lanzamiento?", None, ("g", "5"), alert=True, reply_markup=KB)
    c.publicar(n, "needs_input", "t_3", "Esto cuesta DINERO, ¿ok?", None, ("g", "5"), alert=True, reply_markup=KB)
    por_tid = {t: kw for t, _, _, kw in n.publicados}
    assert "decision_to" not in por_tid["t_1"]
    assert por_tid["t_2"].get("decision_to") and por_tid["t_3"].get("decision_to")


def test_dinero_solo_en_el_cuerpo_de_la_tarjeta_cuenta_como_urgente(tmp_path):
    c, _ = _canal(tmp_path)
    c.tarjeta = lambda tid: {"texto": "Contratar hosting\nImplica gasto: marca dinero"} if tid == "t_4" else None
    n = Notices()
    c.publicar(n, "needs_input", "t_4", "¿Qué plan?", None, ("g", "5"), alert=True, reply_markup=KB)
    c.publicar(n, "needs_input", "t_5", "¿Qué plan?", None, ("g", "5"), alert=True, reply_markup=KB)
    por_tid = {t: kw for t, _, _, kw in n.publicados}
    assert por_tid["t_4"].get("decision_to") and "decision_to" not in por_tid["t_5"]


# --- lectura parcial: no se cierra nada por ausencia -------------------------------------------------------------------

def _lanes():
    ln = SimpleNamespace(board="b", kind="implement", repo="C:/r", base="main", remote="origin", github="")
    return {"l": ln}


class _Roto:
    def list_status_all(self, status):
        raise RuntimeError("hermes caído")


def test_solapes_con_lectura_fallida_no_cierra_avisos_en_varias_pasadas(tmp_path):
    cierres = []

    def send(texto, markup=None, conjunto=None):
        pass

    send.conjuntos = True
    send.cerrar_salvo = lambda vigentes: cierres.append(list(vigentes)) or 0
    t = solapes.SolapeTicker(_lanes(), lambda b: _Roto(), send=send, state_path=tmp_path / "s.json",
                             clock=iter(range(0, 10**6, 10**4)).__next__)
    for _ in range(3):
        t.tick()
    assert cierres == []


def test_choques_con_lectura_fallida_no_cierra_avisos_en_varias_pasadas(tmp_path):
    cierres = []

    def send(texto, markup=None, conjunto=None):
        pass

    send.conjuntos = True
    send.cerrar_salvo = lambda vigentes: cierres.append(list(vigentes)) or 0
    t = choques.ChoquesTicker(_lanes(), lambda b: _Roto(), send=send, state_path=tmp_path / "c.json",
                              silenciadas_path=tmp_path / "si.json", ignorados_path=tmp_path / "ig.json",
                              orden_path=tmp_path / "o.json", git=SimpleNamespace(),
                              clock=iter(range(0, 10**6, 10**4)).__next__)
    for _ in range(3):
        t.tick()
    assert cierres == []


def test_solapes_con_lectura_buena_si_cierra(tmp_path):
    cierres = []

    class Ok:
        def list_status_all(self, status):
            return []

    def send(texto, markup=None, conjunto=None):
        pass

    send.conjuntos = True
    send.cerrar_salvo = lambda vigentes: cierres.append(list(vigentes)) or 0
    t = solapes.SolapeTicker(_lanes(), lambda b: Ok(), send=send, state_path=tmp_path / "s.json")
    t.tick()
    assert cierres == [[]]


# --- resumen de las 9:00: «Decides tú» --------------------------------------------------------------------------------

def test_resumen_decides_tu_con_opciones_y_tarjetas_tuyas():
    p = SimpleNamespace(tid="t_9", task={"title": "¿Publicar?"}, since=0,
                        pending_questions=lambda: [{"question": "¿?", "options": ["Sí", "No"], "recommended": 1}])
    tuyas = [{"tid": "t_7", "title": "Decidir precio", "since": 0}]
    texto = rc.render_summary([], [p], 2, 3600, "@oscar", tuyas=tuyas)
    assert "@oscar Decides tú (2):" in texto
    assert "A) Sí B) No (recomendada)" in texto and "t_7 · Decidir precio · tuya" in texto
    assert "2 en la cola del integrador" in texto and "solape" not in texto.lower() and "aprobar" not in texto.lower()


def test_resumen_vacio_dice_nada_y_solo_con_tuyas_lleva_boton(tmp_path):
    assert "Decides tú: nada" in rc.render_summary([], [], 0, 0)
    enviados = []
    s = rc.DailyLaneSummary(lambda: [], lambda: [], lambda: 0, lambda t, b: enviados.append((t, b)) or True,
                            tmp_path / "d.json", tuyas=lambda: [{"tid": "t_7", "title": "x"}], hour=9,
                            now=lambda: __import__("datetime").datetime(2026, 10, 31, 9, 5, tzinfo=rc.MADRID))
    s.tick()
    assert len(enviados) == 1 and "Decides tú" in enviados[0][0] and enviados[0][1] is True


# --- siembra: solo las preguntas urgentes llegan al tema ---------------------------------------------------------------

def test_siembra_no_publica_pregunta_sin_marcas_y_si_la_que_las_tiene():
    from agent_lanes import lo_tuyo
    from agent_lanes.commands import CommandCenter
    tareas = {"t_1": {"title": "¿Qué color?", "body": ""},
              "t_2": {"title": "Hosting", "body": "Implica gasto", "tags": ["dinero"]},
              "t_3": {"title": "Salida", "body": "Antes del lanzamiento"}}
    h = SimpleNamespace(show=lambda tid: {"task": tareas[tid]})
    items = [lo_tuyo.item("pregunta", t, tareas[t]["title"], board="b", since=None, prio=0) for t in tareas]
    fake = SimpleNamespace(desk=SimpleNamespace(hermes_for=lambda b: h, store=SimpleNamespace(issue=lambda *a: None)),
                           lo_tuyo_settings=None, lo_tuyo_items=lambda _x: items, _now=lambda: 1_000_000.0)
    claves = {s["clave"] for s in CommandCenter.semillas(fake)}
    assert claves == {"pregunta:t_2", "pregunta:t_3"}
