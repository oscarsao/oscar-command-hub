"""Operadores del bot de carriles: Oscar + personas de people.yaml con `operador: true` (hoy María).

Mismo acceso que Oscar a comandos, botones y aprobaciones; el resto recibe el rechazo con salida. Las acciones de un
operador que no es Oscar quedan firmadas con su nombre en la tarjeta.
"""
from __future__ import annotations

from pathlib import Path

from agent_lanes.commands import NOT_OWNER as NOT_OWNER_CMD
from agent_lanes.decisions import NOT_OWNER
from agent_lanes.hermes import OSCAR_AUTHOR
from agent_lanes.people import load_people
from tests.test_botones_tarjetas import BODY, card, comments_of, decisiones, setup
from tests.test_commands import OSCAR, command, press

MARIA, INGERBER = 5000000001, 5000000002

YAML = """people:
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
  andrea:
    name: Andrea
    user_id: "5000000003"
    role: marketing
    operador: "true"
"""


def people(tmp_path):
    p = Path(tmp_path) / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    return load_people(str(OSCAR), p)


def with_people(tmp_path, shows):
    cc, desk, bot, h = setup(tmp_path, shows)
    desk.people = people(tmp_path)
    return cc, desk, bot, h


def test_es_operador_lee_el_yaml_y_exige_booleano_estricto(tmp_path):
    ppl = people(tmp_path)
    assert ppl.es_operador(OSCAR) and ppl.es_operador(MARIA)
    assert not ppl.es_operador(INGERBER)
    assert not ppl.es_operador(5000000003)  # "true" entre comillas no vale
    assert not ppl.es_operador(1)


def test_el_yaml_real_solo_hace_operadora_a_maria():
    real = load_people(str(OSCAR))
    assert real.es_operador(MARIA) and real.es_operador(OSCAR)
    assert not real.es_operador(INGERBER) and not real.es_operador(5000000003)


def test_maria_ejecuta_tareas_decisiones_y_aprobar(tmp_path):
    cc, desk, bot, h = with_people(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    for cmd in ("/tareas", "/decisiones", "/aprobar"):
        before = len(bot.sent)
        command(desk, cmd, user=MARIA, chat=MARIA)
        assert len(bot.sent) > before and all(m["text"] != NOT_OWNER_CMD for m in bot.sent)


def test_ingerber_recibe_el_rechazo_con_salida(tmp_path):
    cc, desk, bot, h = with_people(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    for cmd in ("/tareas", "/decisiones", "/aprobar"):
        command(desk, cmd, user=INGERBER, chat=INGERBER)
        assert bot.sent[-1]["text"] == NOT_OWNER_CMD
    assert "operadores" in NOT_OWNER_CMD and "Oscar o a María" in NOT_OWNER_CMD and "texto libre" in NOT_OWNER_CMD
    assert "Solo Oscar" not in NOT_OWNER_CMD and not comments_of(h, "comment")


def test_maria_pulsa_un_boton_y_queda_firmado_con_su_nombre(tmp_path):
    cc, desk, bot, h = with_people(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    decisiones(desk)
    press(desk, bot.sent[-1], 1, user=MARIA)  # B
    comments = comments_of(h, "comment")
    assert comments[0][2] == "RESPUESTA-OSCAR: B) 19 euros" and comments[0][3] == OSCAR_AUTHOR  # sigue valiendo
    assert any(c[2].startswith("DECIDIDO-POR: María") for c in comments)
    [done] = comments_of(h, "complete")
    assert "Decidido por María" in done[2] and "Oscar" not in done[2]
    assert bot.answers[-1] != NOT_OWNER


def test_el_dm_de_maria_no_va_a_la_bandeja_de_ideas_pero_el_de_ingerber_si(tmp_path):
    cc, desk, bot, h = with_people(tmp_path, {})
    claimed = []

    class Collab:
        claims = staticmethod(lambda msg, owner: True)

        def handle(self, msg):
            claimed.append(msg["from"]["id"])

    desk.collab = Collab()
    desk._spawn = lambda fn: fn()
    for uid in (INGERBER, MARIA):
        desk._message({"text": "hola", "from": {"id": uid}, "chat": {"id": uid, "type": "private"}, "message_id": 1})
    assert claimed == [INGERBER]


def test_oscar_sigue_igual_sin_firma_extra(tmp_path):
    cc, desk, bot, h = with_people(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    decisiones(desk)
    press(desk, bot.sent[-1], 1)
    assert [c[2] for c in comments_of(h, "comment")] == ["RESPUESTA-OSCAR: B) 19 euros"]
    assert "Decidido por Oscar" in comments_of(h, "complete")[0][2]


def test_ingerber_no_puede_pulsar_botones(tmp_path):
    cc, desk, bot, h = with_people(tmp_path, {"t_d0000001": card("t_d0000001", body=BODY)})
    decisiones(desk)
    press(desk, bot.sent[-1], 1, user=INGERBER)
    assert bot.answers[-1] == NOT_OWNER and not comments_of(h, "comment") and not comments_of(h, "complete")
