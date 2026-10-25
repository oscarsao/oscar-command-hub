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
from tests.test_runner_state import GOOD, LANE, FakeGit, FakeHermes, FakeVerifier, FakeWorker, ok_outcome

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

    def sends(self, chat=None, thread=None):
        out = [p for m, p in self.calls if m == "sendMessage"]
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


def test_el_lanes_yaml_real_no_lo_activa_todavia():
    assert load_telegram_settings()["decisiones"] is None  # el integrador pone el id del tema al crearlo


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
    assert len(canal_avisos.CLASES) == 24


def test_decision_exige_tema_y_botones(tmp_path):
    tg = TG()
    assert canal(tmp_path, tg).es_decision("choque", KB)
    assert not canal(tmp_path, tg).es_decision("choque", None)  # sin botones no hay nada que decidir
    assert not canal(tmp_path, tg).es_decision("choque", {"inline_keyboard": [[{"text": "Ver", "url": "https://x"}]]})
    assert not canal(tmp_path, tg).es_decision("limpieza", KB)  # informativo: como hoy
    assert not canal(tmp_path, tg, destino=None).es_decision("choque", KB)  # sin tema: como hoy
    assert canal_avisos.tipo_de_estado("needs_input") == "needs_input"
    assert canal_avisos.tipo_de_estado("running") == "progreso"


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
