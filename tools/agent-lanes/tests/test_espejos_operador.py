"""F1-09 (PR #135 recortado a D4-A): María decide como Oscar con los mismos botones, sin copias al DM.

Lo que queda de #135: los operadores con todas las marcas («*», María) salen del filtro de colaboradores, así que en su
DM y en el tema 🎯 Decisiones ven avisos y botones completos; los acotados por marca siguen filtrados. Lo que se quitó:
copias de las decisiones al DM de María (espejos), sellos «decidido por» en las copias y APROBADO-MARIA (María aprueba
con el botón; la aprobación queda en el registro local con su nombre, P08).

El nombre del fichero se conserva porque el plan del hub lo cita como prueba de F1-09. Ids de Telegram ficticios.
"""
from __future__ import annotations

from pathlib import Path

from agent_lanes import hermes
from agent_lanes.people import PEOPLE_EXAMPLE, load_people
from agent_lanes.telegram import TelegramNotifier, _protect_collaborator_dms
from tests.test_botones_tarjetas import BODY, card, comments_of, decisiones, setup
from tests.test_commands import OSCAR, press

OWNER = str(OSCAR)
MARIA, ACOTADO, COLAB = "5000000001", "5000000002", "5000000003"
KB = {"inline_keyboard": [[{"text": "Aprobar", "callback_data": "tok123456:0"}]]}

YAML = f"""people:
  maria:
    name: María
    user_id: "{MARIA}"
    role: coo
    brands: ["*"]
    operador: true
    permisos: [aprobar_produccion]
  acotado:
    name: Acotado
    user_id: "{ACOTADO}"
    role: dev
    brands: ["Signal"]
    operador: true
  colab:
    name: Colab
    user_id: "{COLAB}"
    role: cmo
"""


def people(tmp_path):
    p = Path(tmp_path) / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    return load_people(OWNER, p)


class Api(TelegramNotifier):
    """Notificador real con la llamada HTTP sustituida: guarda (método, payload)."""

    def __init__(self, tmp_path):
        super().__init__("123:tok", "-1009")
        self.calls, self._n = [], 100
        _protect_collaborator_dms(self, {"OWNER_TELEGRAM_ID": OWNER}, people=people(tmp_path))

    def _api(self, method, payload, timeout=None):
        self.calls.append((method, payload))
        self._n += 1
        return {"message_id": self._n}


def test_maria_sale_del_filtro_de_colaboradores_y_ve_los_botones(tmp_path):
    n = Api(tmp_path)
    assert n.operator_chats == {MARIA}
    assert MARIA not in n.collab_chats and {ACOTADO, COLAB} <= n.collab_chats
    n.send_to(MARIA, None, "❓ decide", reply_markup=KB)
    (_, payload), = n.calls
    assert payload["chat_id"] == MARIA and payload["reply_markup"] == KB and payload["text"] == "❓ decide"


def test_los_acotados_por_marca_y_los_colaboradores_siguen_filtrados(tmp_path):
    n = Api(tmp_path)
    assert n._is_collab_chat(ACOTADO) and n._is_collab_chat(COLAB)
    assert n._is_collab_chat("5000000099")  # DM desconocido: falla cerrado, como antes
    assert not n._is_collab_chat(MARIA) and not n._is_collab_chat(OWNER)


def test_sin_copias_al_dm_de_maria(tmp_path):
    """D4-A: María decide en 🎯 Decisiones; una decisión del DM de Oscar no se duplica en el suyo."""
    n = Api(tmp_path)
    n.send_to(OWNER, None, "❓ decide", reply_markup=KB)
    assert [p["chat_id"] for _, p in n.calls] == [OWNER]
    assert not hasattr(n, "espejos") and not hasattr(n, "sin_espejo")


def test_solo_los_operadores_con_todas_las_marcas(tmp_path):
    assert people(tmp_path).operadores_todas_marcas() == {MARIA}


def test_aprobado_maria_eliminado():
    assert not hasattr(hermes, "APPROVED_PREFIXES") and not hasattr(hermes, "is_approval")


def test_maria_puede_aprobar_con_el_people_yaml_versionado():
    """Prueba del plan «can_approve(María)==True»: en el código es `puede_aprobar_produccion` (y `es_operador`).
    El people.yaml real ya no está en el repo (t_6ea0d37c): se comprueba el ejemplo versionado, que tiene la misma forma."""
    real = load_people(OWNER, PEOPLE_EXAMPLE)
    maria = next(p for p in real._by_id.values() if p.name == "María")
    assert real.es_operador(maria.user_id) and real.puede_aprobar_produccion(maria.user_id)
    assert maria.user_id in real.operadores_todas_marcas()


def test_decision_pulsada_por_maria_queda_firmada_por_maria(tmp_path):
    cc, desk, bot, h = setup(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    desk.people = people(tmp_path)
    decisiones(desk)
    press(desk, bot.sent[-1], 1, user=int(MARIA))
    (_, _, result), = comments_of(h, "complete")
    assert "por María" in result
