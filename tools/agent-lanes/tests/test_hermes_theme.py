"""Tema de Hermes «Píldora · Liquid Glass V4» (tools/hermes-theme): se compone, cabe en el límite de Hermes y su
YAML instalado vuelve a leerse con los campos que Hermes normaliza. Sin red, sin tocar ~/.hermes."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parents[3] / "tools" / "hermes-theme"


def _instalador():
    spec = importlib.util.spec_from_file_location("instalar_tema", RAIZ / "instalar_tema.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compone_con_css_dentro_del_limite():
    tema = _instalador().componer()
    assert tema["name"] == "pildora-liquid-glass"
    assert 0 < len(tema["customCSS"]) <= 32 * 1024
    assert tema["palette"]["background"] == "#0a0613"
    assert tema["colorOverrides"]["primary"] == "#ff6a52"


def test_css_equilibrado_y_con_las_piezas_clave():
    css = (RAIZ / "pildora-liquid-glass.css").read_text(encoding="utf-8")
    assert css.count("{") == css.count("}")
    assert css.count("/*") == css.count("*/")
    for clase in (".hermes-kanban-card", ".hermes-kanban-column", ".hermes-kanban-dot-running", "backdrop-filter"):
        assert clase in css


def test_selectores_kanban_existen_en_hermes_si_esta_instalado():
    """Si Hermes está en este PC, cada `.hermes-kanban-*` del tema debe existir en su style.css (avisa si un
    update de Hermes renombra clases). En CI/otro PC sin Hermes no comprueba nada."""
    hermes = Path.home() / "AppData/Local/hermes/hermes-agent/plugins/kanban/dashboard/dist/style.css"
    if not hermes.is_file():
        return
    orig = hermes.read_text(encoding="utf-8")
    css = (RAIZ / "pildora-liquid-glass.css").read_text(encoding="utf-8")
    clases = set(re.findall(r"\.(hermes-kanban[\w-]*)", css))
    huerfanas = sorted(c for c in clases if f".{c}" not in orig)
    assert not huerfanas, f"Clases que Hermes ya no define: {huerfanas}"


def test_instala_en_carpeta_temporal(tmp_path):
    inst = _instalador()
    assert inst.main(["--dest", str(tmp_path / "temas")]) == 0
    escrito = tmp_path / "temas" / "pildora-liquid-glass.yaml"
    data = yaml.safe_load(escrito.read_text(encoding="utf-8"))
    assert data == inst.componer()


def test_dry_run_no_escribe(tmp_path):
    assert _instalador().main(["--dest", str(tmp_path / "temas"), "--dry-run"]) == 0
    assert not (tmp_path / "temas").exists()
