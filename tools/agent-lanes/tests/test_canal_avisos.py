"""Tema «🎯 Decisiones» (decisión de Oscar 25-10): destino configurable, clasificación en un solo sitio, enrutado con y
sin tema, edición al decidir y contador fijado. Nada sale a Telegram: notificador falso."""
from __future__ import annotations

from datetime import datetime

import pytest

from agent_lanes import canal_avisos
from agent_lanes.canal_avisos import DECISION, INFO, URGENTE, CanalAvisos, clase_de
from agent_lanes.config import load_telegram_settings
from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.notices import MessageStore, TaskNotices
from agent_lanes.runner import LaneRunner
from agent_lanes.telegram import TelegramNotifier
from tests.test_runner_state import (
    GOOD,
    LANE,
    FakeGit,
    FakeHermes,
    FakeVerifier,
    FakeWorker,
    ok_outcome,
)

OWNER = "6744452215"
GESTION = "-1003530490339"
TEMA = (GESTION, "777")          # 🎯 Decisiones (id de prueba)
TEMA_CARRIL = (GESTION, "231")   # tema del carril
KB = {"inline_keyboard": [[{"text": "✅ Aprobar", "callback_data": "tok123456:0"},
                           {"text": "🗄 Aparcar", "callback_data": "tok123456:1"}]]}


class TG(TelegramNotifier):
    """TelegramNotifier real con la Bot API sustituida: nada sale a Internet."""

    def __init__(self):
        super().__init__("123:fake", OWNER, None, allowed_chats={GESTION},
                         generic_origins={(OWNER, "*"), (GESTION, "5")})
        self.calls: list[tuple[str, dict]] = []
        self._mid = 100

    def _api(self, method, payload, timeout=None):
        self.calls.append((method, dict(payload)))
        if method == "sendMessage":
            self._mid += 1
            return {"message_id": self._mid}
        return True

    def sends(self, chat=None, thread=None, fijado=False):
        """Mensajes enviados; sin `fijado`, sin el contador fijado del tema (se prueba aparte)."""
        out = [p for m, p in self.calls if m == "sendMessage" and (fijado or not p["text"].startswith("🎯 <b>"))]
        if chat is not None:
            out = [p for p in out if str(p["chat_id"]) == str(chat)]
        if thread is not None:
            out = [p for p in out if str(p.get("message_thread_id") or "0") == str(thread)]
        return out

    def edits(self, mid=None):
        return [p for m, p in self.calls if m == "editMessageText" and (mid is None or p["message_id"] == mid)]


def canal(tmp_path, tg, destino=TEMA, messages=None, vivos=None):
    vivos = vivos if vivos is not None else set()
    return CanalAvisos(tg, OWNER, destino, tmp_path, vivo=lambda t: t in vivos, messages=messages)


def _yaml(tmp_path, text: str):
    p = tmp_path / "lanes.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# --- paso 1: destino configurable ---------------------------------------------------------------------------------

def test_destino_decisiones_se_lee_de_lanes_yaml(tmp_path):
    yml = _yaml(tmp_path, "telegram:\n  destinos:\n    decisiones: {chat: -1003530490339, thread: 777}\n")
    assert load_telegram_settings(yml)["decisiones"] == ("-1003530490339", "777")


def test_sin_destino_o_incompleto_queda_apagado(tmp_path):
    assert load_telegram_settings(_yaml(tmp_path, "telegram: {}\n"))["decisiones"] is None
    # sin thread (o 0) caería en el General del grupo: no hay valor por defecto
    for cfg in ("{chat: -1003530490339}", "{chat: -1003530490339, thread: 0}", "{thread: 5}"):
        yml = _yaml(tmp_path, f"telegram:\n  destinos:\n    decisiones: {cfg}\n")
        assert load_telegram_settings(yml)["decisiones"] is None, cfg


def test_el_lanes_yaml_real_ya_lo_activa_con_chat_y_tema():
    # 25-10 (e02ac31): el integrador creó el tema 🎯 Decisiones y lo puso en lanes.yaml (antes este test pedía None)
    dest = load_telegram_settings()["decisiones"]
    assert dest and dest[0].startswith("-100") and dest[1] not in ("", "0")


# --- paso 2: clasificación en un solo sitio ------------------------------------------------------------------------

@pytest.mark.parametrize("tipo,clase", [
    ("choque", DECISION), ("solape", DECISION), ("needs_input", DECISION), ("blocked", DECISION),
    ("aprobar", DECISION), ("integrar", DECISION), ("equipo", DECISION), ("workflow", DECISION), ("hazlo", DECISION),
    ("bloqueo_urgente", URGENTE), ("monitor", URGENTE), ("prodwatch", URGENTE),
    ("revision", INFO), ("pr_conflicto", INFO), ("paradas", INFO), ("limpieza", INFO), ("reconcile", INFO),
    ("board_guard", INFO), ("al_dia", INFO), ("recordatorio", INFO), ("resumen", INFO), ("progreso", INFO),
    ("team_notice", INFO), ("fijado", INFO), ("tipo-que-no-existe", INFO),
])
def test_cada_tipo_tiene_su_clase(tipo, clase):
    assert clase_de(tipo) == clase


def test_la_tabla_cubre_los_24_tipos_del_borrador():
    # 24 del borrador + «empezando» (el «▶️ empezando» separado de progreso, nivel de avisos 30-10)
    assert len(canal_avisos.CLASES) == 25 and "empezando" in canal_avisos.CLASES


def test_decision_exige_tema_y_botones(tmp_path):
    tg = TG()
    assert canal(tmp_path, tg).es_decision("choque", KB)
    assert not canal(tmp_path, tg).es_decision("choque", None)  # sin botones no hay nada que decidir
    assert not canal(tmp_path, tg).es_decision("choque", {"inline_keyboard": [[{"text": "Ver", "url": "https://x"}]]})
    assert not canal(tmp_path, tg).es_decision("limpieza", KB)  # informativo: como hoy
    assert not canal(tmp_path, tg, destino=None).es_decision("choque", KB)  # sin tema: como hoy
    assert canal_avisos.tipo_de_estado("needs_input") == "needs_input"
    assert canal_avisos.tipo_de_estado("running") == "empezando"
    assert canal_avisos.tipo_de_estado("review") == "progreso"


# --- paso 2: avisos sueltos (choques, solapes, limpieza…) ------------------------------------------------------------

def test_sin_tema_el_choque_va_al_dm_como_hoy(tmp_path):
    tg = TG()
    for c in (None, canal(tmp_path, tg, destino=None)):
        tg.calls.clear()
        canal_avisos.emisor(c, tg, "choque", OWNER)("⛔ Choque entre t_a y t_b", KB)
        (msg,) = tg.sends()
        assert msg["chat_id"] == OWNER and msg["reply_markup"] == KB and "message_thread_id" not in msg


def test_con_tema_el_choque_va_al_tema_con_botones_y_al_dm_una_linea(tmp_path):
    tg = TG()
    canal_avisos.emisor(canal(tmp_path, tg), tg, "choque", OWNER)("⛔ Choque entre t_a y t_b\ndetalle", KB)
    (tema,) = tg.sends(GESTION, "777")
    assert tema["reply_markup"] == KB and tema["text"].startswith("⛔ Choque")
    (dm,) = tg.sends(OWNER)
    assert "reply_markup" not in dm and dm["text"] == (
        f"🎯 Decisión nueva: ⛔ Choque entre t_a y t_b → https://t.me/c/{GESTION[4:]}/777/{tema_mid(tg, tema)}")


def tema_mid(tg, payload):
    """message_id que el falso Telegram devolvió para ese envío."""
    sends = [p for m, p in tg.calls if m == "sendMessage"]
    return 101 + sends.index(payload)


def test_lo_informativo_sigue_al_dm_aunque_haya_tema(tmp_path):
    tg = TG()
    canal_avisos.emisor(canal(tmp_path, tg), tg, "limpieza", OWNER)("🧹 t_1 resuelta", KB)
    canal_avisos.emisor(canal(tmp_path, tg), tg, "board_guard", OWNER)("tablero")
    assert [p["chat_id"] for p in tg.sends()] == [OWNER, OWNER] and not tg.sends(GESTION)


# --- paso 2: avisos por tarea (needs_input, ficha Aprobar, integrador) ----------------------------------------------

def test_tarea_sin_tema_lleva_la_copia_completa_al_dm_como_hoy(tmp_path):
    tg, store = TG(), MessageStore()
    canal_avisos.publicar(canal(tmp_path, tg, destino=None, messages=store), TaskNotices(tg, store), "needs_input",
                          "t_1", "❓ t_1 · Pregunta", None, TEMA_CARRIL, alert=True, reply_markup=KB, mirror=OWNER)
    assert [(p["chat_id"], p.get("message_thread_id"), p.get("reply_markup")) for p in tg.sends()] == [
        (GESTION, 231, KB), (OWNER, None, KB)]


def test_tarea_con_tema_copia_al_tema_y_linea_al_dm(tmp_path):
    tg, store = TG(), MessageStore()
    c = canal(tmp_path, tg, messages=store)
    canal_avisos.publicar(c, TaskNotices(tg, store), "needs_input", "t_1", "❓ t_1 · ¿Supabase o SQLite?", None,
                          TEMA_CARRIL, alert=True, reply_markup=KB, mirror=OWNER)
    (carril,) = tg.sends(GESTION, "231")
    (tema,) = tg.sends(GESTION, "777")
    (dm,) = tg.sends(OWNER)
    assert carril["reply_markup"] == KB and tema["reply_markup"] == KB and tema["text"] == carril["text"]
    assert "reply_markup" not in dm and dm["text"].startswith("🎯 Decisión nueva: ❓ t_1 · ¿Supabase o SQLite? → ")
    mirror = store.get("t_1")["mirrors"][0]
    assert mirror["decision"] is True and mirror["thread_id"] == "777" and mirror["token"] == "tok123456"


def test_aviso_de_progreso_no_va_al_tema(tmp_path):
    tg, store = TG(), MessageStore()
    canal_avisos.publicar(canal(tmp_path, tg, messages=store), TaskNotices(tg, store), "progreso", "t_1",
                          "▶️ t_1 · en marcha", None, TEMA_CARRIL, alert=False)
    assert not tg.sends(GESTION, "777") and not tg.sends(OWNER)


def test_si_el_principal_iba_al_dm_pasa_al_tema_y_el_dm_solo_recibe_la_linea(tmp_path):
    """Grupo «solo resultado final» (final_only): el needs_input iba al DM de Oscar con botones; ahora al tema."""
    tg, store = TG(), MessageStore()
    canal_avisos.publicar(canal(tmp_path, tg, messages=store), TaskNotices(tg, store), "needs_input", "t_1",
                          "❓ t_1 · Pregunta", None, (OWNER, "0"), alert=True, reply_markup=KB, mirror=OWNER)
    (tema,) = tg.sends(GESTION, "777")
    (dm,) = tg.sends(OWNER)
    assert tema["reply_markup"] == KB and "reply_markup" not in dm and dm["text"].startswith("🎯 Decisión nueva")
    assert store.get("t_1")["chat_id"] == GESTION and "mirrors" not in store.get("t_1")  # sin copia duplicada


def _desk(tmp_path, tg, store, destino=TEMA):
    desk = DecisionDesk(tg, CallbackStore(tmp_path / "cb"), lanes={LANE.name: LANE}, hermes_for=lambda b: None,
                        owner_id=OWNER, messages=store, spawn=lambda fn: fn(), now=lambda: datetime(2026, 10, 25, 12))
    desk.canal = canal_avisos.para_desk(desk, destino)
    return desk


def test_runner_needs_input_con_tema(tmp_path):
    tg, store = TG(), MessageStore()
    desk = _desk(tmp_path, tg, store)
    q = {"question": "¿A o B?", "options": ["A", "B"], "recommended": 1}
    body = f"origen-telegram: chat={GESTION} thread=231\n\nCuerpo"
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": body}])
    LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome({**GOOD, "status": "needs_input",
                                                                               "questions": [q]})]),
               verifier=FakeVerifier(), notify=tg, decisions=desk, messages=store).run_once()
    tema = [p for p in tg.sends(GESTION, "777") if p.get("reply_markup")]
    assert len(tema) == 1 and "¿A o B?" in tema[0]["text"]
    dm = tg.sends(OWNER)
    assert len(dm) == 1 and dm[0]["text"].startswith("🎯 Decisión nueva") and "reply_markup" not in dm[0]


def test_runner_needs_input_sin_tema_igual_que_antes(tmp_path):
    tg, store = TG(), MessageStore()
    desk = _desk(tmp_path, tg, store, destino=None)
    q = {"question": "¿A o B?", "options": ["A", "B"], "recommended": 1}
    body = f"origen-telegram: chat={GESTION} thread=231\n\nCuerpo"
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": body}])
    LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome({**GOOD, "status": "needs_input",
                                                                               "questions": [q]})]),
               verifier=FakeVerifier(), notify=tg, decisions=desk, messages=store).run_once()
    assert not tg.sends(GESTION, "777")
    dm = [p for p in tg.sends(OWNER) if p.get("reply_markup")]
    assert len(dm) == 1 and "¿A o B?" in dm[0]["text"]  # la copia espejo completa de siempre


# --- paso 3: al decidir, ✅ por <persona> y contador fijado ---------------------------------------------------------

MARIA, INTRUSO = "5000000001", "5000000002"
PEOPLE = """people:
  maria:
    name: María
    user_id: "5000000001"
    role: coo
    brands: ["*"]
    operador: true
  ingerber:
    name: Ingerber
    user_id: "5000000002"
    role: cmo
"""


def _desk_people(tmp_path, tg, store, h):
    from agent_lanes.people import load_people
    desk = _desk(tmp_path, tg, store)
    desk.hermes_for = lambda b: h
    p = tmp_path / "people.yaml"
    p.write_text(PEOPLE, encoding="utf-8")
    desk.people = load_people(OWNER, p)
    return desk


def _pulsar(desk, markup, n, *, user, mid, thread="777", cid="cq"):
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row][n]
    desk.handle_update({"update_id": 1, "callback_query": {
        "id": cid, "from": {"id": int(user)}, "data": data,
        "message": {"message_id": mid, "chat": {"id": int(GESTION)}, "message_thread_id": int(thread)}}})


def _pregunta(desk, tg, store, questions):
    markup = desk.markup("needs_input", task={"id": "t_1", "title": "T"}, lane=LANE, questions=questions)
    canal_avisos.publicar(desk.canal, TaskNotices(tg, store), "needs_input", "t_1", "❓ t_1 · ¿Qué BD?", None,
                          TEMA_CARRIL, alert=True, reply_markup=markup, mirror=OWNER)
    (tema,) = [p for p in tg.sends(GESTION, "777") if p.get("reply_markup")]
    return markup, tema_mid(tg, tema)


def fijado(tg):
    """Texto vigente del mensaje fijado del tema (creado una vez y luego editado)."""
    (sent,) = [p for p in tg.sends(GESTION, "777", fijado=True) if p["text"].startswith("🎯 <b>")]
    mid = tema_mid(tg, sent)
    edits = tg.edits(mid)
    return edits[-1]["text"] if edits else sent["text"]


def test_maria_decide_desde_el_tema_y_el_mensaje_queda_con_su_nombre(tmp_path):
    from tests.test_decisions import DeskHermes
    tg, store, h = TG(), MessageStore(), DeskHermes()
    desk = _desk_people(tmp_path, tg, store, h)
    markup, mid = _pregunta(desk, tg, store, [{"question": "¿Qué BD?", "options": ["Supabase", "SQLite"]}])
    assert fijado(tg).startswith("🎯 <b>1 decisión pendiente</b>")
    _pulsar(desk, markup, 1, user=MARIA, mid=mid)
    assert ("unblock", "t_1") in h.calls
    assert any("DECIDIDO-POR: María" in c[2] for c in h.calls if c[0] == "comment")  # su nombre en la tarjeta
    final = tg.edits(mid)[-1]
    assert final["text"].startswith("✅ B por María · ") and "reply_markup" not in final
    assert fijado(tg) == "🎯 <b>Sin decisiones pendientes</b>" and desk.canal.pendientes() == []
    # un aviso de progreso posterior de la tarea ya no pisa el «✅ … por María»
    n = len(tg.edits(mid))
    TaskNotices(tg, store).publish("t_1", "▶️ t_1 · en marcha", None, TEMA_CARRIL)
    assert len(tg.edits(mid)) == n


def test_quien_no_es_operador_no_decide_y_sigue_pendiente(tmp_path):
    from tests.test_decisions import DeskHermes
    tg, store, h = TG(), MessageStore(), DeskHermes()
    desk = _desk_people(tmp_path, tg, store, h)
    markup, mid = _pregunta(desk, tg, store, [{"question": "¿Qué BD?", "options": ["Supabase", "SQLite"]}])
    _pulsar(desk, markup, 0, user=INTRUSO, mid=mid)
    assert h.calls == [] and not tg.edits(mid)
    assert len(desk.canal.pendientes()) == 1


def test_pulsar_en_el_tema_del_carril_tambien_cierra_la_copia_del_tema(tmp_path):
    from tests.test_decisions import DeskHermes
    tg, store, h = TG(), MessageStore(), DeskHermes()
    desk = _desk_people(tmp_path, tg, store, h)
    markup, mid = _pregunta(desk, tg, store, [{"question": "¿Qué BD?", "options": ["Supabase", "SQLite"]}])
    carril_mid = store.get("t_1")["message_id"]
    _pulsar(desk, markup, 0, user=OWNER, mid=carril_mid, thread="231")
    assert tg.edits(mid)[-1]["text"].startswith("✅ A por Oscar · ")
    assert desk.canal.pendientes() == []


def test_varias_preguntas_sigue_abierta_hasta_la_ultima(tmp_path):
    from tests.test_decisions import DeskHermes
    tg, store, h = TG(), MessageStore(), DeskHermes()
    desk = _desk_people(tmp_path, tg, store, h)
    markup, mid = _pregunta(desk, tg, store, [{"question": "¿Qué BD?", "options": ["Supabase", "SQLite"]},
                                              {"question": "¿Cola?", "options": ["Redis", "Ninguna"]}])
    _pulsar(desk, markup, 0, user=OWNER, mid=mid)
    step = tg.edits(mid)[-1]
    assert "Pregunta 2/2" in step["text"] and step.get("reply_markup")  # la misma tarjeta, con la 2ª
    assert len(desk.canal.pendientes()) == 1 and fijado(tg).startswith("🎯 <b>1 decisión pendiente</b>")
    _pulsar(desk, step["reply_markup"], 1, user=MARIA, mid=mid, cid="cq2")
    assert tg.edits(mid)[-1]["text"].startswith("✅ B por María · ")
    assert desk.canal.pendientes() == [] and ("unblock", "t_1") in h.calls


def _kb(token):
    return {"inline_keyboard": [[{"text": "🙈 Ignorar este par", "callback_data": f"{token}:0"}]]}


def test_contador_sube_baja_y_se_edita_el_mismo_fijado(tmp_path):
    tg, vivos = TG(), {"tokA00001", "tokB00001"}
    c = canal(tmp_path, tg, vivos=vivos)
    c.enviar("choque", "⛔ Choque t_a / t_b", _kb("tokA00001"))
    c.enviar("solape", "🔀 Solape t_c / t_d", _kb("tokB00001"))
    texto = fijado(tg)
    assert texto.startswith("🎯 <b>2 decisiones pendientes</b>")
    assert "• 1 choque entre tarjetas" in texto and "• 1 solape de tarjetas" in texto
    assert c.resuelta("tokA00001", {"text": "🙈 Ignorar este par"}, "Oscar")
    assert fijado(tg).startswith("🎯 <b>1 decisión pendiente</b>")
    choque_mid = tema_mid(tg, tg.sends(GESTION, "777")[0])
    assert tg.edits(choque_mid)[-1]["text"].startswith("✅ Ignorar este par por Oscar · ")
    assert len([p for m, p in tg.calls if m == "pinChatMessage"]) == 1  # un solo fijado, editado
    assert len([p for p in tg.sends(GESTION, "777", fijado=True) if p["text"].startswith("🎯 <b>")]) == 1
    assert not c.resuelta("tokZ00009", {"text": "x"}, "Oscar")  # teclado ajeno al tema: no toca nada


def test_reinicio_rehace_el_mismo_contador_y_lo_resuelto_fuera_se_cierra_tras_la_gracia(tmp_path):
    tg, vivos, now = TG(), {"tokA00001", "tokB00001"}, [1_000_000.0]
    c = CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: t in vivos, clock=lambda: now[0])
    c.enviar("choque", "⛔ Choque t_a / t_b", _kb("tokA00001"))
    c.enviar("choque", "⛔ Choque t_e / t_f", _kb("tokB00001"))
    c2 = CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: t in vivos, clock=lambda: now[0])  # runner nuevo
    assert len(c2.pendientes()) == 2
    vivos.discard("tokA00001")  # gastado por otra vía (Hermes, texto libre) o mientras se aplica la acción
    c2.refrescar()
    assert len(c2.pendientes()) == 2  # dentro de la gracia: sigue abierta (si la acción falla vuelven los botones)
    now[0] += canal_avisos.GRACIA + 1
    c2.refrescar()
    assert len(c2.pendientes()) == 1 and fijado(tg).startswith("🎯 <b>1 decisión pendiente</b>")
    mid = tema_mid(tg, tg.sends(GESTION, "777")[0])
    assert tg.edits(mid)[-1]["text"].startswith("✅ Ya no está pendiente")


def test_aviso_nuevo_de_la_misma_tarea_sustituye_a_la_decision_anterior(tmp_path):
    tg, store = TG(), MessageStore()
    c = canal(tmp_path, tg, messages=store, vivos={"tok123456", "tok999999"})
    notices = TaskNotices(tg, store)
    canal_avisos.publicar(c, notices, "needs_input", "t_1", "❓ t_1 · 1ª", None, TEMA_CARRIL, alert=True,
                          reply_markup=KB, mirror=OWNER)
    kb2 = {"inline_keyboard": [[{"text": "🔁 Reintentar", "callback_data": "tok999999:0"}]]}
    canal_avisos.publicar(c, notices, "blocked", "t_1", "⛔ t_1 · bloqueada", None, TEMA_CARRIL, alert=True,
                          reply_markup=kb2, mirror=OWNER)
    (pend,) = c.pendientes()
    assert pend["tipo"] == "blocked" and pend["token"] == "tok999999"
    primero = tema_mid(tg, [p for p in tg.sends(GESTION, "777") if p["text"].startswith("❓")][0])
    assert tg.edits(primero)[-1]["text"].startswith("↪️ Sustituida")


def test_sin_tema_no_hay_contador_ni_registro(tmp_path):
    tg = TG()
    c = canal(tmp_path, tg, destino=None, vivos={"tokA00001"})
    c.enviar("choque", "⛔ Choque", _kb("tokA00001"))
    c.refrescar()
    assert not c.resuelta("tokA00001", {"text": "x"}, "Oscar")
    assert not (tmp_path / "decisiones_tema.json").exists() and not (tmp_path / "decisiones_fijado.json").exists()
    assert [p["chat_id"] for p in tg.sends(fijado=True)] == [OWNER]


def _integrador_con_tema(tmp_path):
    """Integrador de test_consola (ficha «Listo para integrar» con [🔀 Integrar], dos toques) + tema Decisiones."""
    from tests.test_consola import setup as consola_setup
    integ, w, h, tg, d = consola_setup(tmp_path)
    mids, edits = iter(range(5000, 6000)), []

    def send_to(chat, thread, text, **k):
        return {"chat_id": str(chat), "thread_id": str(thread or 0), "message_id": next(mids)}

    def edit(chat_id, message_id, text, reply_markup=None, **kw):
        edits.append({"mid": message_id, "text": text, "markup": reply_markup})
        return True

    tg.send_to, tg.edit = send_to, edit
    now = [1_000_000.0]
    d.canal = canal_avisos.para_desk(d, TEMA)
    d.canal.clock = lambda: now[0]
    integ.run_pass()
    return integ, w, tg, d, now, edits


def test_doble_toque_sigue_abierto_mientras_espera_el_si_y_cierra_con_la_accion_real(tmp_path):
    from tests.test_consola import first, merges
    from tests.test_integrator import buttons, tap
    _integ, w, tg, d, now, edits = _integrador_con_tema(tmp_path)
    (pend,) = d.canal.pendientes()
    assert pend["tipo"] == "integrar" and pend["chat_id"] == GESTION and pend["thread_id"] == "777"
    tap(d, first(tg.sent[-1]["markup"]))  # 1.er toque: [✅ Sí, integrar] [✖ No]
    assert buttons(tg.markups[-1])[0].startswith("✅ Sí, integrar")
    now[0] += canal_avisos.GRACIA + 60  # tarda en confirmar: el teclado [✅ Sí] sigue vivo
    d.canal.refrescar()
    assert len(d.canal.pendientes()) == 1 and not [e for e in edits if e["mid"] == pend["message_id"]]
    tap(d, first(tg.markups[-1]))  # ✅ Sí
    assert len(merges(w)) == 1 and d.canal.pendientes() == []
    final = [e for e in edits if e["mid"] == pend["message_id"]][-1]
    assert final["text"].startswith("✅ Integrar por Oscar · ") and final["markup"] is None


def test_doble_toque_con_no_vuelve_el_teclado_y_sigue_pendiente(tmp_path):
    from tests.test_consola import first, merges
    from tests.test_integrator import tap
    _integ, w, tg, d, _now, edits = _integrador_con_tema(tmp_path)
    (pend,) = d.canal.pendientes()
    tap(d, first(tg.sent[-1]["markup"]))
    tap(d, first(tg.markups[-1], 1))  # ✖ No
    assert not merges(w) and len(d.canal.pendientes()) == 1
    assert not [e for e in edits if e["mid"] == pend["message_id"] and e["text"].startswith("✅")]


# --- avisos obsoletos: tarjetas ya hechas (30-10) -----------------------------------------------------------------

def _abierto(c, mid, tipo, tid):
    """Aviso ya publicado en el tema (registro directo: no hace falta enviarlo)."""
    data = c._load()
    data["abiertas"][f"{TEMA[0]}:{mid}"] = {"tipo": tipo, "rol": "main", "tid": tid, "token": None, "head": f"aviso {tid}",
                                           "chat_id": TEMA[0], "message_id": mid, "thread_id": TEMA[1], "at": 1.0}
    c._save(data)


def _obsoletas(tmp_path, tarjetas, avisos, **kw):
    """Canal con tarjetas falsas `tarjetas` {tid: info o Exception} y avisos [(tipo, tid)]; sin pausa real."""
    tg = TG()

    def lee(tid):
        v = tarjetas.get(tid)
        if isinstance(v, Exception):
            raise v
        return v

    c = CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: False, tarjeta=lee, sleep=lambda s: None, **kw)
    for n, (tipo, tid) in enumerate(avisos):
        _abierto(c, 900 + n, tipo, tid)
    return c, tg


def _restantes(c):
    return sorted(e["tid"] for e in c.pendientes())


def test_blocked_y_needs_input_de_tarjeta_hecha_se_cierran_y_la_viva_no(tmp_path):
    tarjetas = {"t_a": {"status": "done", "comentarios": []}, "t_b": {"status": "archived", "comentarios": []},
                "t_c": {"status": "running", "comentarios": []}}
    c, tg = _obsoletas(tmp_path, tarjetas, [("blocked", "t_a"), ("needs_input", "t_b"), ("blocked", "t_c")])
    assert c.cerrar_obsoletas() == 2
    assert _restantes(c) == ["t_c"]
    assert [p["text"] for p in tg.edits()] == ["✅ Resuelta: tarjeta done", "✅ Resuelta: tarjeta archived"]


def test_aprobar_e_integrar_solo_con_constancia_de_integracion(tmp_path, caplog):
    tarjetas = {
        "t_a": {"status": "done", "comentarios": ["INTEGRADO 3f2a1b · PR #9"]},
        "t_b": {"status": "done", "comentarios": ["INTEGRADOR: PR #9 descartado"]},
        "t_c": {"status": "archived", "comentarios": [], "rama_integrada": True},
        "t_d": {"status": "done", "comentarios": [], "rama_integrada": None},
        "t_e": {"status": "done", "comentarios": ["Integrada en main (rebase)"]},
    }
    c, _tg = _obsoletas(tmp_path, tarjetas, [("aprobar", "t_a"), ("integrar", "t_b"), ("aprobar", "t_c"),
                                             ("aprobar", "t_d"), ("integrar", "t_e")])
    with caplog.at_level("INFO"):
        assert c.cerrar_obsoletas() == 3
    assert _restantes(c) == ["t_b", "t_d"]
    assert "t_b (aprobar)" not in caplog.text and "t_b (integrar) sigue abierto" in caplog.text
    assert "t_d (aprobar) sigue abierto" in caplog.text


def test_tipos_ajenos_no_se_tocan_aunque_la_tarjeta_este_hecha(tmp_path):
    tarjetas = {"t_a": {"status": "done", "comentarios": ["INTEGRADO x"], "rama_integrada": True}}
    tipos = ["tuyo", "solape", "choque", "cola", "progreso"]
    c, tg = _obsoletas(tmp_path, tarjetas, [(t, "t_a") for t in tipos])
    assert c.cerrar_obsoletas() == 0
    assert len(c.pendientes()) == len(tipos) and not tg.edits()


def test_tope_por_pasada_y_es_idempotente(tmp_path):
    tarjetas = {f"t_{n:02d}": {"status": "done", "comentarios": []} for n in range(30)}
    c, tg = _obsoletas(tmp_path, tarjetas, [("blocked", f"t_{n:02d}") for n in range(30)])
    assert c.cerrar_obsoletas() == canal_avisos.CERRAR_MAX
    assert len(c.pendientes()) == 5
    assert c.cerrar_obsoletas() == 5
    assert c.cerrar_obsoletas() == 0 and c.pendientes() == [] and len(tg.edits()) == 30


def test_fallo_de_edicion_o_de_lectura_no_rompe_la_pasada(tmp_path):
    tarjetas = {"t_a": RuntimeError("hermes caído"), "t_b": {"status": "done", "comentarios": []},
                "t_c": {"status": "done", "comentarios": []}}
    c, tg = _obsoletas(tmp_path, tarjetas, [("blocked", "t_a"), ("blocked", "t_b"), ("needs_input", "t_c")])

    def edit_roto(chat_id, message_id, text, **kw):
        raise RuntimeError("429 Too Many Requests")

    tg.edit = edit_roto
    assert c.cerrar_obsoletas() == 2  # _cerrar traga el fallo de edición: el aviso se cierra igual
    assert _restantes(c) == ["t_a"]


def test_sin_lector_de_tarjetas_no_hace_nada(tmp_path):
    c = CanalAvisos(TG(), OWNER, TEMA, tmp_path, vivo=lambda t: False)
    _abierto(c, 901, "blocked", "t_a")
    assert c.cerrar_obsoletas() == 0 and len(c.pendientes()) == 1


def test_la_revision_periodica_cierra_y_el_contador_queda_bien(tmp_path):
    tarjetas = {"t_a": {"status": "done", "comentarios": []}, "t_b": {"status": "running", "comentarios": []}}
    c, _tg = _obsoletas(tmp_path, tarjetas, [("needs_input", "t_a"), ("needs_input", "t_b")], sembrador=lambda: [])
    c.run_async = False
    c._sembrar_si_toca()  # «inicio»: pasada completa
    assert _restantes(c) == ["t_b"]
    assert json_abiertas(tmp_path) == ["t_b"]


def json_abiertas(tmp_path):
    import json
    data = json.loads((tmp_path / "decisiones_tema.json").read_text(encoding="utf-8"))
    return sorted(e["tid"] for e in data["abiertas"].values())


def test_rama_integrada_usa_refs_sin_fetch_y_dice_no_o_no_sabe(tmp_path):
    from types import SimpleNamespace as NS
    from agent_lanes import canal_avisos as ca
    lanes = [NS(repo="/r", remote="origin", base="main"), NS(repo="/r", remote="origin", base="main")]
    llamadas = []

    def git_ok(repo, *args):
        llamadas.append(args)
        return 0 if args[2] == "lane/t_a" else 1  # solo la rama t_a es ancestro

    assert ca._rama_integrada(lanes, "t_a", git_ok) is True
    assert all(a[:2] == ("merge-base", "--is-ancestor") and a[3] == "origin/main" for a in llamadas)
    assert ca._rama_integrada(lanes, "t_b", lambda repo, *a: 1) is False
    assert ca._rama_integrada(lanes, "t_c", lambda repo, *a: 128) is None


def test_estado_tarjeta_de_lee_el_tablero_y_la_rama(tmp_path):
    from types import SimpleNamespace as NS
    from agent_lanes import canal_avisos as ca

    class H:
        def __init__(self, show):
            self._show = show

        def show(self, tid):
            if isinstance(self._show, Exception):
                raise self._show
            return self._show.get(tid, {})

    tableros = {"alfa": H({"t_a": {"task": {"status": "done"}, "comments": [{"body": "INTEGRADO 1"}]}}),
                "beta": H(RuntimeError("tablero caído"))}
    desk = NS(lanes={"l": NS(board="alfa", repo="/r", remote="origin", base="main")},
              hermes_for=lambda b: tableros[b])
    tarjeta = ca.estado_tarjeta_de(desk, git=lambda *a: 0)
    assert tarjeta("t_a") == {"status": "done", "comentarios": ["INTEGRADO 1"], "rama_integrada": True}
    assert tarjeta("t_x") is None
