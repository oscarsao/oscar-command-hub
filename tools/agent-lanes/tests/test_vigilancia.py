"""Vigilante del equipo (t_04fe996e): solicitud frente a charla, deduplicación, tope de urgentes, partes de las 9:00 y
17:00, ciclo de estados de la ficha, tema 👥 Equipo, enrutado de botones y borradores que solo Oscar envía."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from agent_lanes import peticiones as pt
from agent_lanes import vigilancia as vg
from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.people import Person, People
from agent_lanes.vigilancia_botones import EquipoBotones
from tests.test_decisions import LANE, FakeTG

OSCAR, ANDREA, MARIA = "6744452215", "5000000003", "5000000001"
MADRID = vg.MADRID


def ts(h, m=0, d=23):
    return datetime(2026, 10, d, h, m, tzinfo=MADRID).timestamp()


def people():
    return People(OSCAR, [Person("andrea", "Andrea", ANDREA, "marketing", ("Píldora",)),
                          Person("maria", "María", MARIA, "coo", ("Píldora", "MigraTeam", "*"))])


def sol_ohq(sid="ohq:c1", titulo="[IDEA] Página de gracias en la web", tg=int(ANDREA), estado="nueva", creado=None):
    return {"id": sid, "persona": {"telegram_id": tg, "nombre": "Andrea"}, "titulo": titulo,
            "cita": "Necesitamos una página de gracias en la web", "marca": "pildora_digital_inbound", "bot": "avisos",
            "tipo": "idea", "prioridad": "medium", "creado": creado, "estado": estado}


def el(eid, tipo="hecho_sin_hacer", grav="urgente", nombre="Andrea", tg=int(ANDREA)):
    return {"id": eid, "tipo": tipo, "gravedad": grav, "persona": {"telegram_id": tg, "nombre": nombre},
            "resumen": f"resumen {eid}", "detalle": "", "desde": None, "enlace": {"tipo": "mensaje", "id": eid}}


class Mundo:
    """Vigilancia con todo de pega: OHQ, kanban, envíos y ediciones."""

    def __init__(self, tmp_path, now):
        self.now, self.ohq, self.tasks, self.shows = now, {}, [], {}
        self.sent, self.edits, self.topics = [], [], []
        self.topic_result = None
        self.reg = pt.Peticiones(tmp_path / "peticiones.json", now=lambda: self.now)
        self.v = vg.Vigilancia(
            estado=vg.Estado(tmp_path / "vigilancia.json"), registro=self.reg, people=people(),
            fetch_ohq=lambda: self.ohq, hermes_tasks=lambda: self.tasks, show_card=lambda t: self.shows.get(t),
            send=self._send, edit=self._edit, create_topic=self._topic, cfg=vg.Config(crear_tema=True),
            now=lambda: self.now, triaje_cfg={"duplicado_umbral": 0.5})

    def _send(self, text, spec, thread):
        self.sent.append({"text": text, "spec": spec, "thread": thread})
        return {"chat_id": vg.GESTION, "message_id": 1000 + len(self.sent)}

    def _edit(self, chat, mid, text, spec):
        self.edits.append({"mid": mid, "text": text, "spec": spec})
        return True

    def _topic(self, chat, name):
        self.topics.append(name)
        return self.topic_result

    def tick(self, now=None):
        if now is not None:
            self.now = now
        return self.v.tick()

    def acciones(self, i=-1):
        return [b["action"] for row in self.sent[i]["spec"] for b in row]


def arrancado(tmp_path, now=None):
    m = Mundo(tmp_path, now or ts(11))
    m.tick()  # primera pasada: siembra, no avisa de golpe de lo que ya había
    m.sent.clear()
    return m


# --- solicitudes ----------------------------------------------------------------------------------------------------

def test_solicitud_nueva_sale_al_momento_con_la_ficha_y_sus_botones(tmp_path):
    m = arrancado(tmp_path)
    m.ohq = {"solicitudes": [sol_ohq()]}
    m.tick(ts(11, 5))
    (msg,) = m.sent
    assert msg["text"].startswith("🙋 Solicitud req_1 · Andrea · ")
    assert "Pide: [IDEA] Página de gracias en la web" in msg["text"]
    assert "Proyecto: Pildora · Marca: pildora_digital_inbound · Desde: Avisos" in msg["text"]
    assert "Prioridad estimada: media" in msg["text"] and "Estado: 🆕 nueva" in msg["text"]
    assert m.acciones() == [vg.APROBAR, vg.ASIGNAR, vg.RESPONDER, vg.DETALLE, vg.RECHAZAR, vg.LUEGO]
    assert all(b["index"] == "req_1" for row in msg["spec"] for b in row)
    assert [r["id"] for r in m.reg.listar()] == ["req_1"]   # tabla única: el registro req_<n> de peticiones.py
    assert msg["thread"] == vg.TEMA_POR_DEFECTO


def test_charla_o_tarjeta_ajena_no_es_solicitud(tmp_path):
    m = arrancado(tmp_path)
    m.ohq = {"solicitudes": [], "elementos": []}
    m.tasks = [{"id": "t_0000aaaa", "title": "[Hub] Arreglar runner", "body": "algo del integrador",
                "created_by": "agent-lanes", "board": "default"},
               {"id": "t_0000bbbb", "title": "[Hub] Del grupo", "body": "Origen-Telegram: chat=-1003530490339 thread=5",
                "board": "default"}]
    m.tick(ts(11, 5))
    assert m.sent == [] and m.reg.listar() == []


def test_idea_por_dm_del_bot_de_carriles_es_solicitud_de_esa_persona(tmp_path):
    m = arrancado(tmp_path)
    m.tasks = [{"id": "t_1234abcd", "title": "[IDEA de Andrea] Banner nuevo en la home", "board": "oscarhq",
                "body": f"Origen-Telegram: chat={ANDREA} thread=0\n\nPara Oscar: idea...\n\n> Banner nuevo en la home\n",
                "created_by": "tg-dm-andrea"}]
    m.tick(ts(11, 5))
    assert "Andrea" in m.sent[0]["text"] and "Desde: Carriles" in m.sent[0]["text"]
    assert m.reg.get("req_1")["tarjetas"] == ["t_1234abcd"]


def test_misma_peticion_en_dos_bots_va_en_una_sola_ficha(tmp_path):
    m = arrancado(tmp_path)
    m.ohq = {"solicitudes": [sol_ohq()]}
    m.tick(ts(11, 5))
    m.tasks = [{"id": "t_1234abcd", "title": "[IDEA de Andrea] Página de gracias en la web", "board": "oscarhq",
                "body": f"Origen-Telegram: chat={ANDREA} thread=0\n\n> Necesitamos una página de gracias en la web\n",
                "created_by": "tg-dm-andrea"}]
    m.tick(ts(11, 10))
    assert len(m.sent) == 1 and len(m.reg.listar()) == 1
    assert "Desde: Avisos + Carriles" in m.edits[-1]["text"] and "t_1234(abcd)" in m.edits[-1]["text"]
    m.tick(ts(11, 15))
    assert len(m.sent) == 1   # la misma fuente no vuelve a crear nada


def test_misma_frase_de_otra_persona_no_se_junta(tmp_path):
    m = arrancado(tmp_path)
    m.ohq = {"solicitudes": [sol_ohq(), sol_ohq("ohq:c2", tg=int(MARIA))]}
    m.tick(ts(11, 5))
    assert len(m.sent) == 2


def test_tope_de_fichas_por_hora_agrupa_el_resto(tmp_path):
    m = arrancado(tmp_path)
    temas = ["banner", "logo", "precios", "blog", "correo", "formulario", "menú", "footer", "vídeo", "faq", "mapa",
             "chat", "tienda"]
    m.ohq = {"solicitudes": [dict(sol_ohq(f"ohq:c{i}", titulo=f"Cambiar {t}"), cita=f"Cambiar {t}")
                             for i, t in enumerate(temas)]}
    m.tick(ts(11, 5))
    assert len(m.sent) == 11 and m.sent[-1]["text"].startswith("🙋 3 solicitudes más")


# --- ciclo de estados -----------------------------------------------------------------------------------------------

def test_ficha_se_edita_por_aprobada_en_curso_hecha_y_entregada(tmp_path):
    m = arrancado(tmp_path)
    m.ohq = {"solicitudes": [sol_ohq()]}
    m.tick(ts(11, 5))
    assert m.v.vincular_tarjeta("req_1", "t_00001111", "oscarhq", suscrito=True)["estado"] == "aprobada"
    m.shows["t_00001111"] = {"task": {"status": "ready"}, "comments": []}
    m.tick(m.now + 60)
    assert m.edits == []                     # sigue aprobada: nada que editar
    estados = ["✅ aprobada"]
    for status, comments in (("running", []), ("done", []), ("done", [{"body": "INTEGRADO abc1234"}])):
        m.shows["t_00001111"] = {"task": {"status": status}, "comments": comments}
        m.tick(m.now + 60)
        estados.append(m.edits[-1]["text"].split("Estado: ", 1)[1].split("\n")[0])
    assert estados[0].startswith("✅ aprobada") and estados[1].startswith("🔧 en curso")
    assert estados[2].startswith("🏁 hecha") and estados[3].startswith("📦 entregada")
    assert "le llega por su suscripción" in m.edits[-1]["text"] and m.edits[-1]["spec"] == []
    n = len(m.edits)
    m.tick(m.now + 60)
    assert len(m.edits) == n   # cerrada: no se vuelve a editar


def test_cerrada_sin_suscripcion_ofrece_avisar_sin_enviar_nada(tmp_path):
    m = arrancado(tmp_path)
    m.ohq = {"solicitudes": [sol_ohq()]}
    m.tick(ts(11, 5))
    m.ohq = {"solicitudes": [sol_ohq(estado="hecha")]}
    m.tick(ts(11, 10))
    assert "📦 entregada" in m.edits[-1]["text"]
    assert [b["action"] for row in m.edits[-1]["spec"] for b in row] == [vg.AVISAR]


def test_rechazada_es_final(tmp_path):
    m = arrancado(tmp_path)
    m.ohq = {"solicitudes": [sol_ohq()]}
    m.tick(ts(11, 5))
    f = m.v.actualizar_ficha("req_1", decision="rechazada", motivo="no encaja", estado="rechazada")
    assert f["estado"] == "rechazada" and f["cerrada"]


# --- fallos urgentes ---------------------------------------------------------------------------------------------

def test_urgentes_al_momento_con_tope_y_el_resto_agrupado(tmp_path):
    m = arrancado(tmp_path)
    m.ohq = {"elementos": [el(f"hecho:m{i}") for i in range(7)]}
    m.tick(ts(11, 5))
    assert len(m.sent) == 6
    assert m.sent[0]["text"].startswith("🚨 Dijo hecho sin hacerlo · Andrea")
    assert m.acciones(0) == [vg.TAREA, vg.RESPONDER, vg.RESUELTO, vg.IGNORAR]
    assert m.sent[-1]["text"].startswith("🚨 2 avisos urgentes más")
    m.tick(ts(11, 20))
    assert len(m.sent) == 6   # nada se repite


def test_tarjeta_de_hermes_inexistente_es_urgente(tmp_path):
    m = arrancado(tmp_path)
    m.shows["t_aaaa0000"] = {"task": {"status": "ready"}}
    m.ohq = {"refs_hermes": [
        {"id": "ref:m1:t_aaaa0000", "tarjeta": "t_aaaa0000", "persona": {"nombre": "Andrea", "telegram_id": 1}},
        {"id": "ref:m2:t_bbbb1111", "tarjeta": "t_bbbb1111", "persona": {"nombre": "Andrea", "telegram_id": 1}}]}
    m.tick(ts(11, 5))
    assert len(m.sent) == 1 and "t_bbbb(1111), que no existe" in m.sent[0]["text"]


def test_primera_pasada_no_dispara_urgentes_viejos(tmp_path):
    m = Mundo(tmp_path, ts(11))
    m.ohq = {"elementos": [el("hecho:viejo")], "solicitudes": [sol_ohq()]}
    m.tick()
    assert m.sent == []


# --- partes de las 9:00 y 17:00 ---------------------------------------------------------------------------------

def test_resumen_de_las_9_agrupado_por_persona_con_solicitudes_abiertas(tmp_path):
    m = arrancado(tmp_path, ts(8))
    m.ohq = {"solicitudes": [sol_ohq(creado=ts(7))],
             "elementos": [el("sintocar:c1", "sin_tocar", "normal"), el("sinfecha:c2", "sin_fecha", "normal", "María",
                                                                        int(MARIA))]}
    m.tick(ts(8, 30))
    m.sent.clear()
    m.tick(ts(9, 5))
    head = m.sent[0]["text"]
    assert head.startswith("👥 Resumen del equipo de las 9:00") and "• Andrea (1): req_1" in head
    textos = [s["text"] for s in m.sent[1:]]
    assert textos[0].startswith("Andrea · 1 aviso(s):") and textos[1].startswith("María · 1 aviso(s):")
    assert [b["action"] for row in m.sent[1]["spec"] for b in row] == [vg.TAREA, vg.RESUELTO, vg.IGNORAR, vg.RESPONDER]
    m.sent.clear()
    m.tick(ts(9, 40))
    assert m.sent == []                       # una vez por franja
    m.tick(ts(17, 5))
    assert m.sent == []                       # lo ya avisado no se repite y sin nada nuevo no hay mensaje


def test_resumen_de_las_17_con_lo_nuevo_y_sin_mensaje_vacio(tmp_path):
    m = arrancado(tmp_path, ts(16))
    m.tick(ts(17, 1))
    assert m.sent == []
    m2 = arrancado(tmp_path / "b", ts(16))
    m2.ohq = {"elementos": [el("promesa:m1", "promesa_sin_suscripcion", "normal")]}
    m2.tick(ts(16, 30))
    assert m2.sent == []                      # lo normal espera al parte
    m2.tick(ts(17, 1))
    assert m2.sent[0]["text"].startswith("👥 Resumen del equipo de las 17:00")
    assert "Prometió avisar" in m2.sent[1]["text"]


def test_ignorar_7_dias_y_vuelve_despues(tmp_path):
    m = arrancado(tmp_path, ts(8))
    item = el("sintocar:c1", "sin_tocar", "normal")
    m.ohq = {"elementos": [item]}
    m.v.marcar_elemento("sintocar:c1", "ignorado")
    m.tick(ts(9, 5))
    assert m.sent == []
    m.tick(ts(9, 5, d=31))
    assert any("sin tocar" in s["text"].lower() for s in m.sent)


def test_permisos_que_no_cuadran_en_el_primer_parte(tmp_path):
    m = arrancado(tmp_path, ts(8))
    m.ohq = {"personas": [{"telegram_id": int(ANDREA), "nombre": "Andrea", "activo": False},
                          {"telegram_id": 5000000004, "nombre": "Angel", "activo": True},
                          {"telegram_id": int(OSCAR), "nombre": "Oscar", "activo": True}]}
    m.tick(ts(9, 1))
    head = m.sent[0]["text"]
    assert "🔐 Permisos que no cuadran:" in head
    assert "Andrea: desactivada en ohq_people" in head and "María: en people.yaml" in head
    assert "Angel: en ohq_people pero sin acceso" in head and "Oscar:" not in head


# --- tema ------------------------------------------------------------------------------------------------------

def test_tema_se_crea_una_vez_y_si_no_se_puede_va_a_t5(tmp_path):
    m = arrancado(tmp_path)
    m.ohq = {"solicitudes": [sol_ohq()]}
    m.tick(ts(11, 5))
    assert m.topics == ["👥 Equipo"] and m.sent[-1]["thread"] == 5
    m.ohq = {"solicitudes": [dict(sol_ohq("ohq:c9", titulo="Otra cosa completamente diferente"), cita="otra")]}
    m.tick(ts(11, 10))
    assert m.topics == ["👥 Equipo"] and m.sent[-1]["thread"] == 5    # no lo reintenta en cada pasada
    m2 = Mundo(tmp_path / "b", ts(11))
    m2.topic_result = 777
    m2.tick()
    m2.ohq = {"solicitudes": [sol_ohq()]}
    m2.tick(ts(11, 5))
    assert m2.sent[-1]["thread"] == 777


def test_config_desde_lanes_yaml(tmp_path):
    p = tmp_path / "lanes.yaml"
    p.write_text("equipo_telegram: {chat: -100123, thread: 42, crear_tema: false, carriles: {Pildora: claude-hub}}\n",
                 encoding="utf-8")
    cfg = vg.load_config(p)
    assert (cfg.chat, cfg.tema, cfg.crear_tema, cfg.carriles["Pildora"]) == ("-100123", 42, False, "claude-hub")
    assert vg.load_config(tmp_path / "no.yaml").tema == 5


def test_id_corto_y_prioridad():
    assert vg.corto("t_0a1b2c3d") == "t_0a1b(2c3d)" and vg.corto("req_3") == "req_3"
    assert vg.prioridad_estimada("la web está caída") == "alta"
    assert vg.prioridad_estimada("cambiar un color", "low") == "baja"


# --- botones ---------------------------------------------------------------------------------------------------

class H:
    def __init__(self):
        self.created, self.comments = [], []

    def create(self, title, body, assignee, *, priority=None, key=None, created_by=None, parents=None):
        self.created.append({"title": title, "body": body, "assignee": assignee, "created_by": created_by})
        return "t_9999eeee"

    def comment(self, tid, text, author=None):
        self.comments.append((tid, text))
        return True

    def show(self, tid):
        return {"task": {"status": "ready"}, "comments": []}


def _botones(tmp_path):
    m = arrancado(tmp_path)
    m.ohq = {"solicitudes": [sol_ohq()]}
    m.tick(ts(11, 5))
    tg, h = FakeTG(), H()
    desk = DecisionDesk(tg, CallbackStore(tmp_path / "cb"), lanes={LANE.name: LANE}, hermes_for=lambda b: h,
                        links=None, spawn=lambda fn: fn(), now=lambda: datetime(2026, 10, 23, 11, 0))
    desk.people = people()
    cc = SimpleNamespace(desk=desk, notifier=tg, hermes_for=lambda b: h, lanes={LANE.name: LANE}, owner_id=OSCAR,
                         _now=lambda: m.now)
    return m, EquipoBotones(cc, m.v), tg, h


WHERE = {"chat_id": vg.GESTION, "thread_id": "5", "message_id": 1001}


def test_aprobar_crea_la_tarjeta_con_origen_de_quien_lo_pidio(tmp_path):
    m, b, tg, h = _botones(tmp_path)
    assert b.on_button(vg.APROBAR, {}, {"action": vg.APROBAR, "index": "req_1"}, WHERE)
    (card,) = h.created
    assert card["body"].startswith(f"Origen-Telegram: chat={ANDREA} thread=0\nPidió: Andrea\n")
    assert "req: req_1" in card["body"] and card["created_by"] == "andrea"
    assert m.reg.get("req_1")["tarjetas"] == ["t_9999eeee"]
    assert "Estado: ✅ aprobada · t_9999(eeee)" in tg.edits[-1]["text"]
    assert not any(s["chat"] == ANDREA for s in tg.sent)          # a Andrea no le llega nada


def test_pedir_detalle_es_borrador_y_solo_enviar_lo_manda(tmp_path):
    m, b, tg, h = _botones(tmp_path)
    b.on_button(vg.DETALLE, {}, {"action": vg.DETALLE, "index": "req_1"}, WHERE)
    draft = tg.sent[-1]
    assert draft["text"].startswith("✉️ Borrador para Andrea (aún NO se ha enviado)")
    assert not any(s["chat"] == ANDREA for s in tg.sent)
    token = draft["reply_markup"]["inline_keyboard"][0][0]["callback_data"].split(":")[0]
    rec = b.cc.desk.store.get(token)
    assert b.on_button(vg.ENVIAR, rec, {"action": vg.ENVIAR, "index": "req_1"}, WHERE)
    assert tg.sent[-1]["chat"] == ANDREA and "¿me cuentas un poco más?" in tg.sent[-1]["text"]


def test_rechazar_con_motivo_cierra_y_prepara_borrador(tmp_path):
    m, b, tg, h = _botones(tmp_path)
    assert b.on_reply({}, {"action": vg.RECHAZAR, "index": "req_1"}, WHERE, "no entra este trimestre")
    assert m.v.ficha("req_1")["estado"] == "rechazada"
    assert "Motivo: no entra este trimestre" in tg.sent[-1]["text"] and tg.sent[-1]["chat"] == vg.GESTION


def test_resuelto_e_ignorar_de_un_elemento(tmp_path):
    m, b, tg, h = _botones(tmp_path)
    m.v.ultimo["elementos"] = [el("hecho:m1")]
    b.on_button(vg.RESUELTO, {"spec": []}, {"action": vg.RESUELTO, "index": "hecho:m1"}, WHERE)
    assert m.v.estado.load()["items"]["hecho:m1"]["estado"] == "resuelto"
    b.on_button(vg.TAREA, {"spec": []}, {"action": vg.TAREA, "index": "hecho:m1"}, WHERE)
    assert h.created[-1]["title"].startswith("[Telegram] 🚨 Dijo hecho sin hacerlo · Andrea")


def test_decisions_enruta_los_botones_y_las_respuestas_del_tema_equipo(tmp_path):
    from tests import test_free_text as ft
    desk, tg, _h, _messages = ft._setup(tmp_path)
    calls = []
    desk.commands = SimpleNamespace(
        equipo_choice=lambda a, rec, b, w: calls.append(("choice", a, b["index"])) or True,
        equipo_reply=lambda rec, b, w, text: calls.append(("reply", b["action"], text)) or True,
        equipo_prompt=lambda rec, b: "Tu respuesta para Andrea:")
    spec = [[{"text": "✅", "action": vg.APROBAR, "index": "req_1"},
             {"text": "💬", "action": vg.RESPONDER, "index": "req_1"}]]
    markup = desk.store.issue({"kind": vg.KIND, "task_id": "equipo", "board": "", "lane": "", "spec": spec}, spec)[1]
    ft._press(desk, markup, 0, mid=1001)
    assert calls == [("choice", vg.APROBAR, "req_1")]
    markup = desk.store.issue({"kind": vg.KIND, "task_id": "equipo", "board": "", "lane": "", "spec": spec}, spec)[1]
    ft._press(desk, markup, 1, mid=1002)
    ask = tg.sent[-1]
    assert ask["text"].startswith("Tu respuesta para Andrea:") and "Tarjeta:" not in ask["text"]
    ft._say(desk, "Ya lo miro mañana", mid=950, reply_to_message={"message_id": tg._next})
    assert calls[-1] == ("reply", vg.RESPONDER, "Ya lo miro mañana")


def test_idea_esperando_a_oscar_conserva_los_botones_de_decision(tmp_path):
    m = arrancado(tmp_path)
    m.shows["t_1234abcd"] = {"task": {"status": "ready", "assignee": "oscar"}, "comments": []}
    m.tasks = [{"id": "t_1234abcd", "title": "[IDEA de Andrea] Banner nuevo en la home", "board": "oscarhq",
                "body": f"Origen-Telegram: chat={ANDREA} thread=0\n\n> Banner nuevo en la home\n",
                "created_by": "tg-dm-andrea"}]
    m.tick(ts(11, 5))
    assert len(m.acciones()) == 6 and m.edits == []
    assert m.v.ficha("req_1")["estado"] == "nueva"
    m.shows["t_1234abcd"] = {"task": {"status": "archived", "assignee": "oscar"}, "comments": []}
    m.tick(ts(11, 10))
    assert "🗄 archivada" in m.edits[-1]["text"]


def test_operador_acotado_por_marca_no_puede_con_los_botones_mezclados(tmp_path):
    from tests import test_free_text as ft
    desk, tg, _h, _messages = ft._setup(tmp_path)
    angel = "5000000004"
    desk.people = People(OSCAR, [Person("angel", "Angel", angel, "dev", ("Píldora",), operador=True)])
    calls = []
    desk.commands = SimpleNamespace(equipo_choice=lambda *a: calls.append(a) or True)
    m = Mundo(tmp_path / "m", ts(11))
    cc = SimpleNamespace(desk=desk, notifier=tg, hermes_for=None, lanes={}, owner_id=OSCAR, _now=lambda: m.now)
    b = EquipoBotones(cc, m.v)
    spec = [[{"text": "✅ Resuelto 1", "action": vg.RESUELTO, "index": "hecho:m1"}]]
    for markup, esperado in ((b.markup(spec), 0),
                             (b.markup(m.v._spec({"req": "req_1", "proyecto": "Pildora"}, spec)), 1),
                             (b.markup(m.v._spec({"req": "req_1", "proyecto": "MigraTeam"}, spec)), 1)):
        data = markup["inline_keyboard"][0][0]["callback_data"]
        desk.handle_update({"update_id": 4, "callback_query": {
            "id": "cq", "from": {"id": int(angel)}, "data": data,
            "message": {"message_id": 1, "chat": {"id": ft.GROUP}, "message_thread_id": ft.TOPIC}}})
        assert len(calls) == esperado
