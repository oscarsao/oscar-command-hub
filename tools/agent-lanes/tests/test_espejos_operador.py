"""María (operadora con todas las marcas) recibe en su DM las decisiones del DM de Oscar, con los mismos botones.

Lo decide uno y el otro lo ve («decidido por María 10:32»); no se decide dos veces; /pendientes; APROBADO-MARIA.
"""
from __future__ import annotations

from pathlib import Path

from agent_lanes.collab_intake import collab_view
from agent_lanes.commands import NOT_OWNER as NOT_OWNER_CMD
from agent_lanes.espejos import Espejos
from agent_lanes.hermes import OSCAR_AUTHOR, is_approval
from agent_lanes.integrator import Integrator
from agent_lanes.people import load_people
from agent_lanes.telegram import TelegramNotifier
from tests.test_botones_tarjetas import BODY, card, comments_of, decisiones, setup
from tests.test_commands import OSCAR, command, press

OWNER, MARIA_DM, ACOTADO = "6744452215", "5516823836", "8847860660"
KB = {"inline_keyboard": [[{"text": "Aprobar", "callback_data": "tok123456:0"}]]}

YAML = """people:
  maria:
    name: María
    user_id: "5516823836"
    role: coo
    brands: ["*"]
    operador: true
  acotado:
    name: Acotado
    user_id: "8847860660"
    role: dev
    brands: ["MigraTeam"]
    operador: true
  ingerber:
    name: Ingerber
    user_id: "8313932936"
    role: cmo
"""


class Api(TelegramNotifier):
    """Notificador real con la llamada HTTP sustituida: guarda (método, payload)."""

    def __init__(self, tmp_path, operadores=(MARIA_DM,)):
        super().__init__("123:tok", OWNER)
        self.calls, self._n = [], 100
        self.owner_id = OWNER
        self.operator_chats = set(operadores)
        self.collab_chats = {ACOTADO}
        self.collab_view = collab_view
        self.espejos = Espejos(Path(tmp_path) / "espejos.json")

    def _api(self, method, payload, timeout=None):
        self.calls.append((method, payload))
        self._n += 1
        return {"message_id": self._n}


def enviados(n):
    return [(p["chat_id"], p.get("reply_markup")) for m, p in n.calls if m == "sendMessage"]


def test_decision_del_dm_de_oscar_llega_tambien_al_dm_de_maria_con_los_mismos_botones(tmp_path):
    n = Api(tmp_path)
    sent = n.send_to(OWNER, None, "❓ decide", reply_markup=KB)
    assert sent["chat_id"] == OWNER
    assert enviados(n) == [(OWNER, KB), (MARIA_DM, KB)]  # María no pasa por collab_view: botones intactos


def test_lo_que_no_es_una_decision_o_no_va_al_dm_de_oscar_no_se_copia(tmp_path):
    n = Api(tmp_path)
    n.send_to(OWNER, None, "texto sin botones")
    n.send_to(OWNER, None, "force_reply", reply_markup={"force_reply": True})
    n.send_to("-1001", "5", "tema", reply_markup=KB)
    assert [c for c, _ in enviados(n)] == [OWNER, OWNER, "-1001"]


def test_respuesta_a_un_comando_no_se_copia(tmp_path):
    n = Api(tmp_path)
    with n.sin_espejo():
        n.send_to(OWNER, None, "bandeja", reply_markup=KB)
    assert [c for c, _ in enviados(n)] == [OWNER]


def test_sin_operadores_o_sin_registro_todo_sigue_igual(tmp_path):
    n = Api(tmp_path, operadores=())
    n.send_to(OWNER, None, "x", reply_markup=KB)
    n.espejos, n.operator_chats = None, {MARIA_DM}
    n.send_to(OWNER, None, "x", reply_markup=KB)
    assert [c for c, _ in enviados(n)] == [OWNER, OWNER]


def test_dm_sin_start_no_rompe_el_aviso_de_oscar(tmp_path):
    n = Api(tmp_path)
    api = n._api

    def falla(method, payload, timeout=None):
        if payload.get("chat_id") == MARIA_DM:
            raise RuntimeError("chat not found")
        return api(method, payload, timeout)

    n._api = falla
    assert n.send_to(OWNER, None, "x", reply_markup=KB)["chat_id"] == OWNER
    assert not n.tiene_copias(OWNER, n._n)


def test_editar_texto_botones_o_borrar_un_mensaje_lo_hace_en_las_dos_copias(tmp_path):
    n = Api(tmp_path)
    s = n.send_to(OWNER, None, "x", reply_markup=KB)
    mid_oscar, mid_maria = s["message_id"], s["message_id"] + 1
    assert n.tiene_copias(OWNER, mid_oscar) and n.tiene_copias(MARIA_DM, mid_maria)
    n.calls.clear()
    assert n.edit(OWNER, mid_oscar, "✅ decidido por María 10:32")
    assert [(m, p["chat_id"], p["message_id"]) for m, p in n.calls] == [
        ("editMessageText", OWNER, mid_oscar), ("editMessageText", MARIA_DM, mid_maria)]
    assert all("reply_markup" not in p for _, p in n.calls)  # sin botones en ambas
    n.calls.clear()
    n.edit_markup(MARIA_DM, mid_maria, None)  # al revés: desde la copia de María
    assert [p["chat_id"] for _, p in n.calls] == [MARIA_DM, OWNER]
    n.calls.clear()
    n.delete(OWNER, mid_oscar)
    assert [(m, p["chat_id"]) for m, p in n.calls] == [("deleteMessage", OWNER), ("deleteMessage", MARIA_DM)]


def test_el_registro_de_copias_sobrevive_a_un_reinicio(tmp_path):
    Espejos(Path(tmp_path) / "e.json").add([(OWNER, 1), (MARIA_DM, 2)])
    assert Espejos(Path(tmp_path) / "e.json").hermanos(MARIA_DM, 2) == [(OWNER, "1")]
    assert Espejos(Path(tmp_path) / "e.json").hermanos(OWNER, 9) == []


def test_solo_los_operadores_con_todas_las_marcas_reciben_copia(tmp_path):
    p = Path(tmp_path) / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    assert load_people(OWNER, p).operadores_todas_marcas() == {MARIA_DM}


# --- decidir desde una de las copias --------------------------------------------------------------------

def test_al_pulsar_maria_se_retiran_los_botones_de_las_copias_y_se_firma_la_decision(tmp_path):
    cc, desk, bot, h = setup(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    p = Path(tmp_path) / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    desk.people = load_people(str(OSCAR), p)
    bot.tiene_copias = lambda chat, mid: True
    decisiones(desk)
    press(desk, bot.sent[-1], 1, user=int(MARIA_DM))
    assert any(m[2] is None for m in bot.markups)  # botones fuera de todas las copias desde el primer toque
    textos = [e["text"] for e in bot.edits]
    assert textos and all("decidido por María" in t for t in textos)
    # segundo toque (Oscar desde su copia): el teclado ya se gastó
    press(desk, bot.sent[-1], 1, user=int(OWNER))
    assert bot.answers[-1] == "Esta decisión ya no está activa"
    assert len(comments_of(h, "complete")) == 1


def test_sin_copias_el_estado_no_lleva_sello(tmp_path):
    cc, desk, bot, h = setup(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    decisiones(desk)
    press(desk, bot.sent[-1], 1)
    assert not any("decidido por" in e["text"] for e in bot.edits)


# --- /pendientes --------------------------------------------------------------------------------------

def test_pendientes_lista_lo_que_espera_la_decision_de_maria(tmp_path):
    cc, desk, bot, h = setup(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    p = Path(tmp_path) / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    desk.people = load_people(str(OSCAR), p)
    command(desk, "/pendientes", user=int(MARIA_DM), chat=int(MARIA_DM))
    de_maria = [m for m in bot.sent if m["chat_id"] == MARIA_DM]
    # /pendientes ya existía: María (operadora) lo usa en su DM, la misma vista que Oscar, no el rechazo de operadores
    assert de_maria and all(m["text"] != NOT_OWNER_CMD for m in de_maria)
    cc2, desk2, bot2, _ = setup(Path(tmp_path) / "b", {"t_d0000001": card("t_d0000001", body=BODY)})
    command(desk2, "/pendientes")
    assert [m["text"] for m in de_maria] == [m["text"] for m in bot2.sent]


# --- APROBADO-MARIA -----------------------------------------------------------------------------------

def test_aprobado_maria_vale_como_aprobado_oscar_solo_con_el_autor_de_oscar():
    url = "PR https://github.com/o/r/pull/7"
    assert is_approval({"author": OSCAR_AUTHOR, "body": f"APROBADO-MARIA 2026-10-25 · {url}"})
    assert is_approval({"author": OSCAR_AUTHOR, "body": f"APROBADO-OSCAR 2026-10-25 · {url}"})
    assert not is_approval({"author": "lane-claude", "body": f"APROBADO-MARIA · {url}"})  # un worker no se aprueba
    assert not is_approval({"author": OSCAR_AUTHOR, "body": "RECHAZADO-MARIA"})


def test_el_integrador_acepta_la_aprobacion_de_maria(tmp_path):
    class Links:
        @staticmethod
        def repo_slug(lane):
            return "o/r"

    integ = Integrator.__new__(Integrator)
    integ.links = Links()
    c = {"author": OSCAR_AUTHOR, "body": "APROBADO-MARIA 2026-10-25 10:32 · PR https://github.com/o/r/pull/7 · por María"}
    assert integ._approval(object(), "t_d0000001", [c]) == (7, "https://github.com/o/r/pull/7")
