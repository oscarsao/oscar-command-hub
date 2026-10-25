"""Tema «🎯 Decisiones» (decisión de Oscar 25-10): destino configurable, clasificación en un solo sitio, enrutado con y
sin tema, edición al decidir y contador fijado. Nada sale a Telegram: notificador falso."""
from __future__ import annotations

from agent_lanes.config import load_telegram_settings


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
