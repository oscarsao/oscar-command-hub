"""Prueba de humo de TODOS los comandos del bot de carriles (t_93e677e8): cada uno, ejecutado por Oscar con un kanban
vacío y dobles de Telegram, debe contestar algo y no caer en «No pude completar». Sin red, sin Telegram, sin hermes."""
from __future__ import annotations

import pytest

from agent_lanes import health
from agent_lanes.commands import COMMANDS, HELP_ALIASES
from tests.test_commands import GESTION, OSCAR, center, command

# Argumentos mínimos plausibles; sin ellos el comando contesta su uso, que también cuenta como respuesta.
ARGS = {"prioridad": "t_00000000 5", "tarea": "t_00000000", "encolar": "repo 1", "desplegar": "oscarhq",
        "promover": "migrateam", "lote": "oscarhq", "hazlo": "", "silenciar": "", "avisos": "", "sesion": "estado"}
FALLO = "No pude"  # «No pude completar /x» o «No pude comprobar…»: cualquier excusa cuenta como fallo


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Lo que mira el equipo real (RAM, runner, hermes) se sustituye: el humo no toca la máquina ni la red.
    monkeypatch.setattr(health, "build", lambda *a, **k: "salud de prueba", raising=False)
    monkeypatch.setattr(health, "ram_report", lambda *a, **k: "ram de prueba", raising=False)
    cc, desk, bot, h, _ = center(tmp_path, {}, brief=lambda brand: "resumen de prueba")
    cc._health = lambda: "salud de prueba"
    return desk, bot


def _lanzar(desk, cmd, **where):
    arg = ARGS.get(cmd, "")
    command(desk, f"/{cmd} {arg}".strip(), **where)


@pytest.mark.parametrize("cmd", [c for c, _ in COMMANDS] + list(HELP_ALIASES))
def test_comando_responde_en_dm(env, cmd):
    desk, bot = env
    _lanzar(desk, cmd)
    textos = [m["text"] for m in bot.sent] + [e["text"] for e in bot.edits]
    assert textos, f"/{cmd} no contesta"
    assert not any(FALLO in t for t in textos), f"/{cmd} falla: {textos}"


@pytest.mark.parametrize("cmd", [c for c, _ in COMMANDS if c not in ("sesion",)])
def test_comando_responde_en_tema_de_grupo(env, cmd):
    desk, bot = env
    _lanzar(desk, f"{cmd}@pildora_carriles_bot", chat=int(GESTION), thread=230)
    textos = [m["text"] for m in bot.sent] + [e["text"] for e in bot.edits]
    assert textos, f"/{cmd} no contesta en grupo"
    assert not any(FALLO in t for t in textos), f"/{cmd} falla en grupo: {textos}"


def test_ningun_comando_contesta_a_quien_no_es_operador(env):
    desk, bot = env
    command(desk, "/decisiones", user=OSCAR + 1)
    assert len(bot.sent) == 1 and "operadores" in bot.sent[0]["text"]
