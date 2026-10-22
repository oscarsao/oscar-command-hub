"""Tareas del equipo por Telegram: parseo de /asignar, permisos de /hecha, menciones, DM, comentarios, recordatorio y que
ningún carril lea el tablero «equipo». Dobles, sin red ni hermes real."""
from __future__ import annotations

from datetime import datetime

import yaml

from agent_lanes.commands import COMMANDS
from agent_lanes.config import ROOT, load_lanes
from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.equipo import Config, Equipo, Miembro, load_config, load_equipo_config, mencion
from agent_lanes.people import load_people
from agent_lanes.resumen_carriles import MADRID

OSCAR, MARIA, INGERBER, ANDREA, ANGEL, EXTRANO = "6744452215", "5516823836", "8847860660", "8313932936", "8770041085", 4242
GRUPOS = {"marketing": ("-1004277259008", "275"), "desarrollo": ("-1003731090094", "9")}


class TG:
    bot_id = "999"

    def __init__(self, fail_dm=False):
        self.sent, self.answers, self.markups, self.fail_dm = [], [], [], fail_dm

    def send_to(self, chat, thread, text, **kw):
        if self.fail_dm and not str(chat).startswith("-"):
            raise RuntimeError("chat not found")
        self.sent.append({"chat": str(chat), "thread": str(thread), "text": text, **kw})
        return {"chat_id": str(chat), "thread_id": str(thread), "message_id": 100 + len(self.sent)}

    def answer_callback(self, cid, text=None, *, alert=False):
        self.answers.append((text, alert))
        return True

    def edit_markup(self, chat, mid, markup):
        self.markups.append((chat, mid, markup))
        return True


class Hermes:
    def __init__(self):
        self.cards, self.comments, self.done, self.assigned, self.n = {}, [], [], [], 0

    def create(self, title, body, assignee, *, priority=None, created_by=None, **kw):
        self.n += 1
        tid = f"t_{self.n:08x}"
        self.cards[tid] = {"id": tid, "title": title, "body": body, "assignee": assignee, "status": "ready",
                           "priority": priority or 0, "created_by": created_by}
        return tid

    def list_board(self):
        return list(self.cards.values())

    def show(self, tid):
        if tid not in self.cards:
            raise RuntimeError("no existe")
        return {"task": self.cards[tid], "comments": []}

    def complete(self, tid, result, metadata):
        self.cards[tid]["status"] = "done"
        self.done.append((tid, result))
        return True, ""

    def comment(self, tid, text, author=None):
        self.comments.append((tid, author, text))
        return True

    def assign(self, tid, key):
        self.cards[tid]["assignee"] = key
        self.assigned.append((tid, key))
        return True


def people(tmp_path):
    p = tmp_path / "people.yaml"
    p.write_text(yaml.safe_dump({"people": {
        "maria": {"name": "María", "user_id": MARIA, "role": "coo", "operador": True, "tareas_equipo": False},
        "ingerber": {"name": "Ingerber", "user_id": INGERBER, "role": "cmo", "grupo_tareas": "marketing"},
        "andrea": {"name": "Andrea", "user_id": ANDREA, "role": "marketing", "username": "valentina272711",
                   "grupo_tareas": "marketing"},
        "angel": {"name": "Angel", "user_id": ANGEL, "role": "dev", "grupo_tareas": "desarrollo"}}}), encoding="utf-8")
    return load_people(OSCAR, p)


def make(tmp_path, tg=None, now=None):
    tg, h = tg or TG(), Hermes()
    now = now or datetime(2026, 10, 22, 8, 0, tzinfo=MADRID).timestamp()
    eq = Equipo(tg, h, people(tmp_path), Config(True, "equipo", GRUPOS, True, 9), owner_id=OSCAR,
                state_path=tmp_path / "eq.json", now=lambda: now, bot_username="carriles_bot")
    eq.clock = [now]
    eq._now = lambda: eq.clock[0]
    return eq, tg, h


def msg(uid, text, chat="-1003530490339", thread=None, reply=None, **extra):
    m = {"from": {"id": int(uid)}, "chat": {"id": int(chat)}, "text": text, "message_id": 7, **extra}
    if thread:
        m.update(message_thread_id=thread, is_topic_message=True)
    if reply:
        m["reply_to_message"] = {"message_id": reply}
    return m


# --- parseo -------------------------------------------------------------------------------------------

def test_parseo_con_y_sin_arroba_prioridad_y_fecha(tmp_path):
    eq, _, _ = make(tmp_path)
    p = eq.parse_asignar("@ingerber Crear app LinkedIn | acceso a Postiz !alta para:28-10")
    assert p["persona"].key == "ingerber" and p["titulo"] == "Crear app LinkedIn"
    assert p["detalle"] == "acceso a Postiz" and p["alta"] is True and p["para"] == "28-10"
    q = eq.parse_asignar("Andrea Revisar textos")  # nombre sin @ y con mayúscula
    assert q["persona"].key == "andrea" and q["alta"] is False and q["para"] is None and q["detalle"] == ""
    assert eq.parse_asignar("@valentina272711 algo")["persona"].key == "andrea"  # por username
    assert eq.parse_asignar("@oscar Llamar al banco")["persona"].key == "oscar"  # Oscar es asignable


def test_parseo_errores(tmp_path):
    eq, _, _ = make(tmp_path)
    assert "No conozco a «pepe»" in eq.parse_asignar("@pepe hacer algo")
    assert "No conozco" in eq.parse_asignar("@maria revisar")  # María no participa
    assert "Uso:" in eq.parse_asignar("")
    assert "Falta el título" in eq.parse_asignar("@angel | solo detalle")
    assert "no es válida" in eq.parse_asignar("@angel algo para:31-02")


def test_text_mention_resuelve_a_quien_no_tiene_username(tmp_path):
    eq, _, _ = make(tmp_path)
    m2 = msg(OSCAR, "/asignar Pepito x", entities=[{"type": "text_mention", "offset": 9, "length": 6,
                                                     "user": {"id": int(INGERBER)}}])
    assert eq.resolver("Pepito", m2).key == "ingerber"


# --- menciones ----------------------------------------------------------------------------------------

def test_mencion_con_y_sin_username():
    assert mencion(Miembro("andrea", "Andrea", "1", "valentina272711")) == "@valentina272711"
    assert mencion(Miembro("ingerber", "Ingerber", "8847860660")) == '<a href="tg://user?id=8847860660">Ingerber</a>'
    assert "&lt;" in mencion(Miembro("x", "<b>", "5"))


# --- /asignar -----------------------------------------------------------------------------------------

def test_asignar_publica_en_grupo_del_asignado_con_botones_y_confirma_donde_se_pidio(tmp_path):
    eq, tg, h = make(tmp_path)
    m = msg(OSCAR, "/asignar @ingerber Crear la app de LinkedIn para Postiz | con permisos !alta para:25-10", thread=5)
    assert eq.wants(m)
    eq.handle(m)
    card = next(iter(h.cards.values()))
    assert card["assignee"] == "ingerber" and card["created_by"] == "oscar" and card["priority"] == 10
    assert "creador: oscar" in card["body"] and "para: 25-10" in card["body"] and "creado: 2026-10-22" in card["body"]
    grupo = tg.sent[0]
    assert (grupo["chat"], grupo["thread"]) == GRUPOS["marketing"]
    assert grupo["text"].startswith("📌 Nueva tarea para") and 'tg://user?id=8847860660' in grupo["text"]
    assert "Crear la app de LinkedIn" in grupo["text"] and "❗alta" in grupo["text"]
    datos = [b["callback_data"] for b in grupo["reply_markup"]["inline_keyboard"][0]]
    assert datos == [f"eq:ok:{card['id']}", f"eq:cm:{card['id']}", f"eq:re:{card['id']}"]
    conf = [s for s in tg.sent if s["chat"] == "-1003530490339"]
    assert conf and conf[0]["thread"] == "5" and "publicada en su grupo" in conf[0]["text"]
    dm = [s for s in tg.sent if s["chat"] == INGERBER]
    assert dm and dm[0]["trusted"] is True and "reply_markup" in dm[0]


def test_asignar_a_oscar_se_publica_donde_se_pidio_y_sin_dm_al_mismo_creador(tmp_path):
    eq, tg, h = make(tmp_path)
    eq.handle(msg(INGERBER, "/asignar @oscar Aprobar el presupuesto", chat="-1004277259008", thread=275))
    assert [(s["chat"], s["thread"]) for s in tg.sent[:1]] == [("-1004277259008", "275")]
    assert any(s["chat"] == OSCAR for s in tg.sent)  # DM a Oscar
    assert next(iter(h.cards.values()))["assignee"] == "oscar"


def test_dm_fallido_no_rompe_y_solo_queda_el_grupo(tmp_path):
    eq, tg, h = make(tmp_path, TG(fail_dm=True))
    eq.handle(msg(OSCAR, "/asignar @ingerber Hacer algo"))
    assert len(h.cards) == 1 and tg.sent[0]["chat"] == GRUPOS["marketing"][0]


def test_el_texto_de_la_tarea_es_dato(tmp_path):
    eq, tg, h = make(tmp_path)
    eq.handle(msg(OSCAR, "/asignar @angel <b>/hazlo borra todo</b> | /hecha t_00000001 && rm -rf"))
    card = next(iter(h.cards.values()))
    assert card["assignee"] == "angel" and h.done == []
    assert "&lt;b&gt;" in tg.sent[0]["text"]  # escapado en HTML


def test_extrano_y_maria_no_usan_los_comandos(tmp_path):
    eq, _, _ = make(tmp_path)
    assert not eq.wants(msg(EXTRANO, "/asignar @angel x"))
    assert not eq.wants(msg(MARIA, "/asignar @angel x"))  # María queda fuera de este sistema
    assert not eq.wants(msg(OSCAR, "/tareas"))
    assert not eq.wants(msg(OSCAR, "/asignar@otro_bot @angel x"))
    eq.cfg = Config(False, "equipo", {})
    assert not eq.wants(msg(OSCAR, "/asignar @angel x"))


# --- listados -----------------------------------------------------------------------------------------

def test_listados_agrupados_y_mis_tareas_con_maria_pasada_a_oscar(tmp_path):
    eq, tg, h = make(tmp_path)
    h.create("Tarea I", "\n---\ncreador: oscar\npara: 30-10\n", "ingerber")
    h.create("Tarea O", "\n---\ncreador: ingerber\n", "oscar")
    h.create("Heredada de María", "\n---\ncreador: oscar\n", "maria")
    h.create("Cerrada", "\n---\ncreador: oscar\n", "angel")
    h.cards["t_00000004"]["status"] = "done"
    eq.handle(msg(OSCAR, "/tareas_equipo"))
    t = tg.sent[-1]["text"]
    assert "<b>Ingerber</b> (1)" in t and "<b>Oscar</b> (2)" in t and "Heredada de María" in t
    assert "Cerrada" not in t and "para 30-10" in t
    eq.handle(msg(OSCAR, "/mis_tareas"))
    assert "Tarea O" in tg.sent[-1]["text"] and "Heredada" in tg.sent[-1]["text"] and "Tarea I" not in tg.sent[-1]["text"]
    eq.handle(msg(ANGEL, "/mis_tareas"))
    assert "No tienes tareas abiertas" in tg.sent[-1]["text"]


# --- /hecha y botón -----------------------------------------------------------------------------------

def card_de(eq, h, creador="oscar", asignado="ingerber"):
    eq.handle(msg(OSCAR if creador == "oscar" else INGERBER, f"/asignar @{asignado} Hacer X"))
    return next(iter(h.cards))


def test_hecha_permisos(tmp_path):
    eq, tg, h = make(tmp_path)
    tid = card_de(eq, h)  # creador oscar, asignado ingerber
    eq.handle(msg(ANDREA, f"/hecha {tid}"))
    assert h.cards[tid]["status"] == "ready" and "Solo el asignado" in tg.sent[-1]["text"]
    eq.handle(msg(ANGEL, f"/hecha {tid}"))
    assert h.cards[tid]["status"] == "ready"
    eq.handle(msg(INGERBER, f"/hecha {tid}"))  # el asignado
    assert h.cards[tid]["status"] == "done"
    assert "hecha por Ingerber" in tg.sent[-1]["text"] and "ya está" in tg.sent[-1]["text"]  # avisa al creador (Oscar)
    assert "tg://user?id=" + OSCAR in tg.sent[-1]["text"]
    eq.handle(msg(INGERBER, f"/hecha {tid}"))
    assert "ya estaba cerrada" in tg.sent[-1]["text"]


def test_hecha_por_creador_y_por_oscar_y_maria_no(tmp_path):
    eq, tg, h = make(tmp_path)
    eq.handle(msg(INGERBER, "/asignar @andrea Preparar post"))
    tid = next(iter(h.cards))
    assert not eq.wants(msg(MARIA, f"/hecha {tid}"))  # María: ni siquiera entra al sistema
    assert h.cards[tid]["status"] == "ready"
    eq.handle(msg(INGERBER, f"/hecha {tid}"))  # el creador
    assert h.cards[tid]["status"] == "done"
    eq.handle(msg(INGERBER, "/asignar @angel Otra"))
    tid2 = [t for t in h.cards if t != tid][0]
    eq.handle(msg(OSCAR, f"/hecha {tid2}"))  # Oscar siempre
    assert h.cards[tid2]["status"] == "done"
    eq.handle(msg(OSCAR, "/hecha nada"))
    assert "/hecha t_" in tg.sent[-1]["text"]


def test_boton_hecha_y_boton_ajeno(tmp_path):
    eq, tg, h = make(tmp_path)
    tid = card_de(eq, h)
    cq = lambda uid, data: {"id": "c1", "from": {"id": int(uid)}, "data": data,
                            "message": {"message_id": 5, "chat": {"id": -1004277259008}, "message_thread_id": 275,
                                        "is_topic_message": True}}
    eq.handle_callback(cq(EXTRANO, f"eq:ok:{tid}"))
    assert tg.answers[-1][1] is True and h.cards[tid]["status"] == "ready"
    eq.handle_callback(cq(ANGEL, f"eq:ok:{tid}"))
    assert tg.answers[-1][1] is True and h.cards[tid]["status"] == "ready"
    eq.handle_callback(cq(INGERBER, f"eq:ok:{tid}"))
    assert h.cards[tid]["status"] == "done" and tg.markups == [("-1004277259008", 5, None)]
    eq.handle_callback(cq(INGERBER, "eq:zz:t_00000001"))
    assert tg.answers[-1] == ("Este botón ya no vale", False)


# --- comentar y reasignar -----------------------------------------------------------------------------

def test_comentar_respondiendo_al_mensaje_y_con_boton(tmp_path):
    eq, tg, h = make(tmp_path)
    tid = card_de(eq, h)
    grupo = tg.sent[0]
    mid = 101  # message_id que devuelve el doble para el primer envío
    eq.handle(msg(INGERBER, "Ya tengo el acceso", chat=grupo["chat"], thread=275, reply=mid))
    assert h.comments[-1] == (tid, "ingerber", "Ingerber: Ya tengo el acceso")
    assert "Comentario de Ingerber guardado" in tg.sent[-1]["text"]
    cq = {"id": "c", "from": {"id": int(ANDREA)}, "data": f"eq:cm:{tid}",
          "message": {"message_id": mid, "chat": {"id": int(grupo["chat"])}, "message_thread_id": 275, "is_topic_message": True}}
    eq.handle_callback(cq)
    ask = tg.sent[-1]
    assert ask["reply_markup"]["force_reply"] and "responde a ESTE mensaje" in ask["text"]
    pregunta = 100 + len(tg.sent)
    reply = msg(ANGEL, "no es mío", chat=grupo["chat"], reply=pregunta)
    assert not eq.wants(reply)  # la pregunta era de Andrea
    reply = msg(ANDREA, "Lo miro mañana", chat=grupo["chat"], reply=pregunta)
    assert eq.wants(reply)
    eq.handle(reply)
    assert h.comments[-1][1:] == ("andrea", "Andrea: Lo miro mañana")


def test_reasignar_con_boton_y_permisos(tmp_path):
    eq, tg, h = make(tmp_path)
    tid = card_de(eq, h)
    base = {"message": {"message_id": 101, "chat": {"id": -1004277259008}, "message_thread_id": 275,
                        "is_topic_message": True}}
    eq.handle_callback({"id": "1", "from": {"id": int(INGERBER)}, "data": f"eq:re:{tid}", **base})
    pregunta = 100 + len(tg.sent)
    eq.handle(msg(INGERBER, "angel", chat="-1004277259008", reply=pregunta))
    assert h.cards[tid]["assignee"] == "angel" and h.assigned == [(tid, "angel")]
    publicada = [s for s in tg.sent if s["text"].startswith("↩️ Tarea reasignada a")]
    assert publicada and (publicada[0]["chat"], publicada[0]["thread"]) == GRUPOS["desarrollo"]
    eq.handle_callback({"id": "2", "from": {"id": int(ANDREA)}, "data": f"eq:re:{tid}", **base})
    eq.handle(msg(ANDREA, "oscar", chat="-1004277259008", reply=100 + len(tg.sent)))
    assert h.cards[tid]["assignee"] == "angel" and "Solo el asignado" in tg.sent[-1]["text"]


# --- recordatorio -------------------------------------------------------------------------------------

def test_recordatorio_diario_una_vez_a_las_9_y_se_puede_apagar(tmp_path):
    eq, tg, h = make(tmp_path)
    h.create("Para Ingerber", "\n---\ncreador: oscar\n", "ingerber")
    h.create("Para Oscar", "\n---\ncreador: oscar\n", "oscar")
    assert eq.tick() is False  # son las 8:00
    eq.clock[0] = datetime(2026, 10, 22, 9, 5, tzinfo=MADRID).timestamp()
    assert eq.tick() is True
    destinos = {(s["chat"], s["thread"]) for s in tg.sent}
    assert destinos == {GRUPOS["marketing"], (OSCAR, "0")}
    assert any("Para Ingerber" in s["text"] and "tg://user?id=" + INGERBER in s["text"] for s in tg.sent)
    n = len(tg.sent)
    assert eq.tick() is False and len(tg.sent) == n  # ya enviado hoy
    eq.clock[0] += 86400
    eq.cfg.recordatorio = False
    assert eq.tick() is False


# --- configuración y carriles -------------------------------------------------------------------------

def test_config_apagada_sin_bloque_o_con_tablero_de_carril():
    assert load_config({}).activo is False
    assert load_config({"tareas_equipo": {"activo": False}}).activo is False
    assert load_config({"tareas_equipo": {"tablero": "oscarhq"}}, ["oscarhq"]).activo is False
    assert load_config({"tareas_equipo": {"tablero": "default"}}).activo is False
    cfg = load_config({"tareas_equipo": {"grupos": {"Marketing": {"chat": -100, "thread": 3}}, "recordatorio": {"hora": 25}}})
    assert cfg.activo and cfg.tablero == "equipo" and cfg.grupos == {"marketing": ("-100", "3")} and cfg.hora == 23


def test_ningun_carril_lee_el_tablero_equipo():
    lanes = load_lanes()
    assert "equipo" not in {l.board for l in lanes.values()}
    cfg = load_equipo_config(lane_boards=[l.board for l in lanes.values()])
    assert cfg.activo and cfg.tablero == "equipo"
    assert set(cfg.grupos) == {"marketing", "desarrollo"}


def test_people_yaml_real_maria_fuera_y_grupos_validos():
    real = load_people(OSCAR)
    por = {p.key: p for p in real.personas()}
    assert por["maria"].tareas_equipo is False and por["ingerber"].tareas_equipo is True
    assert por["andrea"].username == "valentina272711" and por["ingerber"].username == ""
    cfg = load_equipo_config()
    for p in real.personas():
        assert not p.grupo_tareas or p.grupo_tareas in cfg.grupos


def test_comandos_en_ayuda_y_enrutado_en_decisiones(tmp_path):
    assert {"asignar", "tareas_equipo", "mis_tareas", "hecha"} <= {c for c, _ in COMMANDS}
    eq, tg, h = make(tmp_path)
    desk = DecisionDesk(tg, CallbackStore(tmp_path / "cb"), lanes={}, hermes_for=lambda b: h, links=None,
                        owner_id=OSCAR, spawn=lambda fn: fn())
    desk.equipo = eq
    desk.handle_update({"update_id": 1, "message": msg(INGERBER, "/asignar @andrea Probar el bot", chat="-1004277259008")})
    assert len(h.cards) == 1 and tg.sent[0]["text"].startswith("📌 Nueva tarea para @valentina272711")
    desk.handle_update({"update_id": 2, "callback_query": {
        "id": "x", "from": {"id": int(ANDREA)}, "data": "eq:ok:t_00000001",
        "message": {"message_id": 1, "chat": {"id": -1004277259008}}}})
    assert h.cards["t_00000001"]["status"] == "done"  # el botón (no es de operador) lo atiende Equipo; Andrea es la asignada
