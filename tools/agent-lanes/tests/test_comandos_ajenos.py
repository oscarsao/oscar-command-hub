"""25-10: aviso de /comandos que no son de Trabajos (de Hermes o inexistentes), ayuda con las reglas por grupo/tema y
«Un momento…» en los comandos lentos (/tareas, /aprobar)."""
from __future__ import annotations

from agent_lanes import commands as commands_mod
from agent_lanes.commands import CommandCenter
from tests.test_commands import GESTION, OSCAR, center

MARIA = 777


def grupo(desk, text, *, user=OSCAR, uid=1, chat=GESTION, thread=5):
    msg = {"message_id": 50 + uid, "from": {"id": user}, "chat": {"id": int(chat), "type": "supergroup"}, "text": text}
    if thread:
        msg.update({"message_thread_id": thread, "is_topic_message": True})
    desk.handle_update({"update_id": uid, "message": msg})


def test_comando_de_hermes_sin_arroba_avisa_con_el_sufijo(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    grupo(desk, "/status")
    assert [m["text"] for m in bot.sent] == ["Ese comando es de Hermes: escribe /status@oscarsaoBot"]


def test_comando_inexistente_remite_a_ayuda(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    grupo(desk, "/loquesea")
    assert [m["text"] for m in bot.sent] == ["Ese comando no existe; /ayuda"]


def test_aviso_vale_para_cualquier_persona_y_no_se_repite_en_10_min(tmp_path, monkeypatch):
    cc, desk, bot, *_ = center(tmp_path, {})
    reloj = [1000.0]
    monkeypatch.setattr("agent_lanes.decisions.time.monotonic", lambda: reloj[0])
    grupo(desk, "/status", user=MARIA, uid=1)
    grupo(desk, "/status", user=MARIA, uid=2)      # mismo comando, misma persona: callado
    grupo(desk, "/new", user=MARIA, uid=3)         # otro comando: avisa
    grupo(desk, "/status", user=OSCAR, uid=4)      # otra persona: avisa
    assert len(bot.sent) == 3
    reloj[0] += 601
    grupo(desk, "/status", user=MARIA, uid=5)      # pasados 10 min: vuelve a avisar
    assert len(bot.sent) == 4


def test_no_avisa_de_comandos_nuestros_dirigidos_a_otro_bot_ni_en_privado(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    grupo(desk, "/status@oscarsaoBot")             # ya va dirigido a Hermes
    grupo(desk, "/tareas@otro_bot", uid=2)
    assert bot.sent == []


def test_comando_nuestro_sin_arroba_no_dispara_el_aviso(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    grupo(desk, "/ayuda")
    assert all("Ese comando" not in m["text"] for m in bot.sent)


def test_lista_de_hermes_inyectable(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    desk.hermes_commands = {"zeta"}
    grupo(desk, "/zeta")
    grupo(desk, "/status", uid=2)
    assert [m["text"] for m in bot.sent] == ["Ese comando es de Hermes: escribe /zeta@oscarsaoBot",
                                              "Ese comando no existe; /ayuda"]


def test_ayuda_explica_reglas_por_grupo_y_tema():
    ayuda = CommandCenter.help_text()
    for trozo in ("Gestión (temas 5, 230, 231 y 1903)", "Marketing (tema 406)", "Desarrollo (tema 71)",
                  "respuestas (reply) a mis mensajes", "/comando@oscarsaoBot", "/hazlo <texto>"):
        assert trozo in ayuda
    assert "texto libre" not in ayuda.split("Si me escribes en texto libre")[0]  # no promete leer texto en grupo


def test_comando_lento_manda_un_momento_y_lo_edita_con_el_resultado(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    grupo(desk, "/tareas", thread=5)
    mid = bot.sent[0]["message_id"]  # el doble sustituye el texto del enviado al editarlo, como Telegram
    assert bot.edits and bot.edits[0]["mid"] == mid and bot.edits[0]["text"] != commands_mod.MOMENTO
    assert "En marcha" in bot.sent[0]["text"]
    assert len(bot.sent) == 1                      # la primera página sustituye al «Un momento…», no se duplica


def test_comando_rapido_no_manda_un_momento(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    grupo(desk, "/ayuda")
    assert all(m["text"] != commands_mod.MOMENTO for m in bot.sent)


def test_un_momento_no_se_queda_colgado_si_el_comando_falla(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    cc.cmd_tareas = lambda where, args, brand: (_ for _ in ()).throw(RuntimeError("boom"))
    grupo(desk, "/tareas")
    assert bot.edits[0]["text"].startswith("No pude completar /tareas")
