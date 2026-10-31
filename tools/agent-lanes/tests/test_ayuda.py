"""/ayuda agrupada por tarea y rol, menú «/» por ámbito y sugerencias por palabras (t_93e677e8). Sin Telegram real."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_lanes import ayuda
from agent_lanes.commands import COMMANDS, SOLO_TODAS_LAS_MARCAS, CommandCenter, register_commands
from tests.test_commands import OSCAR, Bot, center, command

DOC = Path(__file__).resolve().parents[3] / "docs" / "bots" / "comandos.md"


# --- /ayuda ------------------------------------------------------------------------------------------------

def test_ayuda_lista_cada_comando_agrupado_por_tarea():
    texto = CommandCenter.help_text()
    for titulo, _ in ayuda.GRUPOS:
        assert titulo in texto
    for cmd, desc in COMMANDS:
        if not desc.startswith("Alias de"):  # /aldia = /al_dia: no ocupa línea propia
            assert f"/{cmd} · " in texto, f"/{cmd} no sale en la ayuda"
    assert "Otros" not in texto  # todo comando está en algún grupo


def test_ayuda_explica_roles_y_enlaza_la_guia():
    texto = CommandCenter.help_text()
    assert "Quién puede usarlos" in texto and "Operador acotado" in texto
    assert "/integrador" in texto and "lista en la guía" in texto
    guia = DOC.read_text(encoding="utf-8")  # la lista completa vive en la guía
    assert all(f"`/{c}`" in guia.split("Operador acotado")[1].split("Cualquier otra")[0] for c in SOLO_TODAS_LAS_MARCAS)
    assert "docs/bots/comandos.md" in texto
    assert ayuda.load_config()["docs_url"] in texto


def test_ayuda_cabe_en_un_mensaje_de_telegram_incluso_con_el_aviso_de_desconocido():
    # _help de decisions.py antepone una frase y añade un ejemplo: 4096 es el tope de Telegram
    assert len(CommandCenter.help_text()) + 250 < 4096


def test_comando_ayuda_responde_con_la_guia(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    command(desk, "/ayuda")
    assert "Ver cómo va todo" in bot.sent[0]["text"] and "comandos.md" in bot.sent[0]["text"]


def test_los_comandos_estan_en_la_guia_del_equipo():
    guia = DOC.read_text(encoding="utf-8")
    faltan = [c for c, _ in COMMANDS if f"/{c}" not in guia]
    assert not faltan, f"faltan en docs/bots/comandos.md: {faltan}"


# --- menú por ámbito -----------------------------------------------------------------------------------------

def test_menu_por_ambito_ayuda_en_todos_y_sin_sesion_en_grupos():
    bot = Bot()
    register_commands(bot)
    privado = [c for c, _ in bot.commands["all_private_chats"]]
    grupo = [c for c, _ in bot.commands["all_group_chats"]]
    assert privado[0] == grupo[0] == "ayuda"
    assert "sesion" in privado and "sesion" not in grupo
    assert len(privado) <= 100 and all(re.fullmatch(r"[a-z0-9_]{1,32}", c) for c in privado)
    assert all(1 <= len(d) <= 256 for _, d in bot.commands["all_group_chats"])


# --- sugerencias ---------------------------------------------------------------------------------------------

CFG = {"activo": True, "enfriamiento_s": 600.0, "docs_url": "", "reglas": [
    {"comando": "falta", "palabras": ["que falta"], "ejemplo": "/falta"},
    {"comando": "ram", "palabras": ["cuanta ram"], "ejemplo": ""}]}


@pytest.mark.parametrize("texto,comando", [
    ("¿Qué FALTA para salir?", "falta"), ("oye, cuánta RAM queda", "ram"), ("qué faltante", None), ("hola", None)])
def test_sugerir_ignora_tildes_mayusculas_y_palabras_a_medias(texto, comando):
    sug = ayuda.sugerir(texto, CFG)
    assert (sug or {}).get("comando") == comando


def test_sugerir_apagado_o_comando_no_permitido():
    assert ayuda.sugerir("que falta", {**CFG, "activo": False}) is None
    assert ayuda.sugerir("que falta", CFG, permitido=lambda c: False) is None


def test_load_config_tolera_yaml_roto_o_ausente(tmp_path):
    assert ayuda.load_config(tmp_path / "no_existe.yaml")["reglas"] == []
    malo = tmp_path / "x.yaml"
    malo.write_text("reglas: [1, {comando: 3}, {comando: ram, palabras: [a]}]\nactivo: 7", encoding="utf-8")
    cfg = ayuda.load_config(malo)
    assert cfg["activo"] is True and [r["comando"] for r in cfg["reglas"]] == ["ram"]


def test_reglas_reales_apuntan_a_comandos_que_existen():
    nombres = {c for c, _ in COMMANDS}
    reglas = ayuda.load_config()["reglas"]
    assert reglas and {r["comando"] for r in reglas} <= nombres
    for r in reglas:
        assert ayuda.sugerir(r["palabras"][0], {"activo": True, "reglas": [r]})["comando"] == r["comando"]


def _dm(desk, texto, uid):
    desk.handle_update({"update_id": uid, "message": {"message_id": uid, "from": {"id": OSCAR},
                                                      "chat": {"id": OSCAR, "type": "private"}, "text": texto}})


def test_texto_libre_en_dm_sugiere_el_comando_una_vez(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    cc._asker = lambda q, ctx: "respuesta"
    _dm(desk, "¿qué falta para poder salir?", 1)
    _dm(desk, "y qué falta ahora?", 2)
    avisos = [m["text"] for m in bot.sent if m["text"].startswith("💡")]
    assert len(avisos) == 1 and "/falta" in avisos[0] and "/ayuda" in avisos[0]  # el segundo cae en el enfriamiento
    assert any(m["text"].startswith("💬") for m in bot.sent)  # la respuesta normal sigue saliendo


def test_texto_libre_sin_regla_no_sugiere_nada(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    cc._asker = lambda q, ctx: "respuesta"
    _dm(desk, "gracias, perfecto", 1)
    assert not [m for m in bot.sent if m["text"].startswith("💡")]
