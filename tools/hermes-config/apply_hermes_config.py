"""Compara la config real de Hermes con la plantilla versionada del hub (CF-08, D53-A; F1-07). SOLO AVISA.

    py -3.12 apply_hermes_config.py --check            # exit 0 = sin deriva · 1 = deriva · 2 = no se pudo leer
    py -3.12 apply_hermes_config.py --check --json     # lo mismo, en JSON (lo usa el monitor una vez al día)

- Lee %LOCALAPPDATA%/hermes/config.yaml (o HERMES_HOME, o --config) y `plantilla_config.yaml` (o --plantilla).
- Nunca escribe nada ni imprime valores de la config: solo nombres de clave, de herramienta y booleanos esperados.
- Aplicar la plantilla NO está implementado a propósito: cambiar la config real de Hermes es del integrador, con
  .bak-<fecha> y OK de Oscar (F1-10 / F2-09). Sin --check el script se niega.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PLANTILLA = Path(__file__).resolve().parent / "plantilla_config.yaml"


def hermes_config_path() -> Path:
    home = os.environ.get("HERMES_HOME") or str(Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
                                                / "hermes")
    return Path(home) / "config.yaml"


def _get(data: dict, dotted: str):
    cur = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return KeyError
        cur = cur[part]
    return cur


def deriva(config: dict, plantilla: dict) -> list[str]:
    """Líneas de deriva en lenguaje llano (vacía = la config cumple la plantilla). Sin valores secretos."""
    out: list[str] = []
    toolsets = config.get("platform_toolsets") if isinstance(config.get("platform_toolsets"), dict) else {}
    for plataforma, prohibidos in (plantilla.get("toolsets_prohibidos") or {}).items():
        tiene = {str(x) for x in (toolsets.get(plataforma) or []) if isinstance(x, (str, int))}
        de_mas = [t for t in prohibidos or [] if t in tiene]
        if de_mas:
            out.append(f"{plataforma}: {', '.join(de_mas)} de más")
    for clave, esperado in (plantilla.get("valores") or {}).items():
        real = _get(config, clave)
        if real is KeyError:
            out.append(f"{clave}: falta (se espera {json.dumps(esperado)})")
        elif real != esperado:
            out.append(f"{clave}: distinto de {json.dumps(esperado)}")
    servers = config.get("mcp_servers") if isinstance(config.get("mcp_servers"), dict) else {}
    for nombre, obligatorias in (plantilla.get("mcp_excluidas") or {}).items():
        srv = servers.get(nombre)
        if not isinstance(srv, dict) or srv.get("enabled") is False:
            continue
        tools = srv.get("tools") if isinstance(srv.get("tools"), dict) else {}
        excl = {str(x) for x in tools.get("exclude") or []}
        incl = tools.get("include")
        faltan = [t for t in obligatorias or [] if t not in excl and not (isinstance(incl, list) and t not in incl)]
        if faltan:
            out.append(f"MCP {nombre}: sin excluir {', '.join(faltan)}")
    return out


def check(config_path: Path | None = None, plantilla_path: Path | None = None) -> tuple[int, list[str]]:
    import yaml
    try:
        plantilla = yaml.safe_load(Path(plantilla_path or PLANTILLA).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return 2, [f"plantilla ilegible ({type(exc).__name__})"]
    try:
        config = yaml.safe_load(Path(config_path or hermes_config_path()).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return 2, [f"config de Hermes ilegible ({type(exc).__name__})"]
    if not isinstance(config, dict) or not isinstance(plantilla, dict):
        return 2, ["formato inesperado"]
    lines = deriva(config, plantilla)
    return (1 if lines else 0), lines


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="solo comprobar (lo único que hace este script)")
    ap.add_argument("--config", type=Path)
    ap.add_argument("--plantilla", type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not args.check:
        print("Solo existe --check: aplicar la plantilla es del integrador con .bak y OK de Oscar (F1-10 / F2-09).")
        return 2
    code, lines = check(args.config, args.plantilla)
    if args.json:
        print(json.dumps({"code": code, "deriva": lines}, ensure_ascii=False))
    elif code == 0:
        print("Config de Hermes: cumple la plantilla.")
    else:
        print("Config de Hermes: " + ("deriva frente a la plantilla (informativo, no se cambia nada):" if code == 1
                                      else "no se pudo comprobar:"))
        for line in lines:
            print(f"- {line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
