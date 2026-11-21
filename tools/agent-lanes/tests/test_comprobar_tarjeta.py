"""`lanes.py tarjeta comprobar`: reproduce el bloqueo del 21-11 y comprueba que el aviso sale antes de crear."""
from __future__ import annotations

from agent_lanes import clasificador, comprobar_tarjeta
from agent_lanes.config import Lane

MCP = Lane(name="claude-mcp-remoto", board="oscarhq", repo="C:/mcp", base="main", github="oscarsao/pildora-mcp")
LANES = {"claude-mcp-remoto": MCP}
CFG = clasificador.load_config("no-existe.yaml")

MALO = "Crea una guía con una cabecera Authorization con la variable de entorno y conéctalo."
BUENO = "Crea una guía con una cabecera Authorization que lea la clave de una variable del shell."


def test_cuerpo_con_palabra_gatillo_bloquearia():
    code, texto = comprobar_tarjeta.comprobar(MCP, "[MCP] Guía", MALO, LANES, CFG)
    assert code == 1
    assert "BLOQUEARÍA" in texto and "produccion_env" in texto


def test_cuerpo_reformulado_pasa():
    code, texto = comprobar_tarjeta.comprobar(MCP, "[MCP] Guía", BUENO, LANES, CFG)
    assert code == 0 and texto.startswith("OK")


def test_cli_fichero_y_carril(tmp_path):
    f = tmp_path / "c.markdown"
    f.write_text(BUENO, encoding="utf-8")
    salida = []
    assert comprobar_tarjeta.main([str(f), "--carril", "claude-mcp-remoto"], lanes=LANES, out=salida.append) == 0
    assert comprobar_tarjeta.main([str(f), "--carril", "no-existe"], lanes=LANES, out=salida.append) == 2
    assert comprobar_tarjeta.main([str(tmp_path / "no.md"), "--carril", "claude-mcp-remoto"], lanes=LANES,
                                  out=salida.append) == 2
