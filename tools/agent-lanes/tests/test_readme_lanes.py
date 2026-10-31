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


# Documentos que NO pueden citar un número de workers: el valor vive solo en lanes.yaml (`runner.max_workers`).
# El README sí lo cita (test de arriba); el resto de la documentación lo remite al yaml.
_NUMERO_DE_WORKERS = re.compile(
    r"max_workers\W{0,6}\d"  # max_workers = 6, max_workers: 1, `max_workers`=2
    r"|(?<![\d-])\d+\s+workers?\b"  # 2 workers (pero no «15-09 worker»)
    r"|workers?\W{0,4}=\s*\d",  # workers=2
    re.IGNORECASE,
)


def _docs_sin_numero_de_workers() -> list[Path]:
    repo = ROOT.parents[1]
    return [repo / "CONTEXT.md", *sorted((repo / "docs").rglob("*.md"))]


def test_documentacion_no_cita_un_numero_de_workers():
    # Autocomprobación: el patrón sí detecta las formas que queremos vigilar.
    assert _NUMERO_DE_WORKERS.search("`runner.max_workers` = 4")
    assert _NUMERO_DE_WORKERS.search("corre con 2 workers")
    assert not _NUMERO_DE_WORKERS.search("Documento (15-09 worker, lanes.yaml)")
    culpables = []
    for path in _docs_sin_numero_de_workers():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _NUMERO_DE_WORKERS.search(line):
                culpables.append(f"{path.relative_to(ROOT.parents[1])}:{n}: {line.strip()}")
    assert not culpables, "Citan un número de workers; remitir a lanes.yaml:\n" + "\n".join(culpables)


def test_max_parallel_citado_en_el_readme():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    lanes, defecto = _yaml()["lanes"], _yaml()["defaults"].get("max_parallel", 1)
    linea = text.split("`max_parallel` por carril:", 1)[1].split("\n\n", 1)[0]
    assert f"{defecto} por defecto" in linea
    for name, cfg in lanes.items():
        valor = cfg.get("max_parallel")
        if valor and valor != defecto:
            assert re.search(rf"{valor} en[^.;]*`{re.escape(name)}`", linea), name
