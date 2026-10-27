"""F1-08 (CF-11): el README de agent-lanes no se desfasa de lanes.yaml.

La tabla «Qué va a cada carril» nombra los mismos carriles que `lanes.yaml` (los alias marcados como alias) y el
`max_workers` que cita el README es el del yaml.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
NO_CARRIL = {"oscar", "claude-<repo>"}  # assignee humano y marcador de la fila genérica
SIN_FILA = {"review"}  # carril interno: lo lanza el runner, no se asigna a mano


def _yaml() -> dict:
    return yaml.safe_load((ROOT / "lanes.yaml").read_text(encoding="utf-8"))


def _tabla() -> list[str]:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    seccion = text.split("## Qué va a cada carril", 1)[1].split("\n## ", 1)[0]
    return [line for line in seccion.splitlines() if line.startswith("| `")]


def _primera_columna(row: str) -> str:
    return row.split("|")[1]


def test_la_tabla_del_readme_nombra_los_carriles_de_lanes_yaml():
    carriles, alias = set(), set()
    for row in _tabla():
        col = _primera_columna(row)
        alias |= set(re.findall(r"alias `([^`]+)`", col))
        carriles |= set(re.findall(r"`([^`]+)`", re.sub(r"alias `[^`]+`", "", col)))
    lanes = _yaml()["lanes"]
    assert carriles - NO_CARRIL == set(lanes) - SIN_FILA
    assert alias == {a for cfg in lanes.values() for a in (cfg.get("aliases") or [])}
    assert not alias & set(lanes)  # un alias nunca es un carril propio


def test_max_workers_del_readme_es_el_de_lanes_yaml():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    citados = {int(n) for n in re.findall(r"`runner\.max_workers` = (\d+)", text)}
    assert citados == {_yaml()["runner"]["max_workers"]}


def test_max_parallel_citado_en_el_readme():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    lanes, defecto = _yaml()["lanes"], _yaml()["defaults"].get("max_parallel", 1)
    linea = text.split("`max_parallel` por carril:", 1)[1].split("\n\n", 1)[0]
    assert f"{defecto} por defecto" in linea
    for name, cfg in lanes.items():
        valor = cfg.get("max_parallel")
        if valor and valor != defecto:
            assert re.search(rf"{valor} en[^.;]*`{re.escape(name)}`", linea), name
