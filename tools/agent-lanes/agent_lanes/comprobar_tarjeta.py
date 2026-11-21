"""`lanes.py tarjeta comprobar`: pasa un cuerpo de tarjeta por el clasificador ANTES de crearla (solo lectura).

Evita crear una tarjeta que el runner mandaría a la cola del integrador por una palabra gatillo (21-11: «variable de
entorno» en una tarjeta de documentación). No crea ni toca nada en el kanban.

Uso: lanes.py tarjeta comprobar --carril claude-mcp-remoto --titulo "[X] T" fichero.markdown
Salida 0 = el carril puede con ella; 1 = iría al integrador; 2 = uso incorrecto.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_lanes import clasificador


def comprobar(lane, titulo: str, cuerpo: str, lanes: dict | None = None, cfg: dict | None = None) -> tuple[int, str]:
    """(código, texto para Oscar). Usa el mismo clasificador que el runner, así el resultado coincide."""
    v = clasificador.evaluar(lane, {"id": "t_comprobar", "title": titulo, "body": cuerpo}, lanes=lanes, cfg=cfg)
    lineas = []
    if v.va_al_integrador:
        lineas.append("BLOQUEARÍA: iría a la cola del integrador:")
        lineas += [f"  - [{rid}] {motivo}" for rid, motivo in v.integrador]
        lineas.append("Reformula esas frases (o quita la mención) y vuelve a comprobar.")
    else:
        lineas.append("OK: el carril puede con ella.")
    for rid, motivo in v.dudas:
        lineas.append(f"  aviso (menciona el tema sin pedir actuar) [{rid}]: {motivo}")
    for rid, aviso in v.avisos:
        lineas.append(f"  nota para el worker [{rid}]: {aviso}")
    return (1 if v.va_al_integrador else 0), "\n".join(lineas)


def main(argv: list[str], *, lanes: dict | None = None, out=print) -> int:
    ap = argparse.ArgumentParser(prog="lanes.py tarjeta comprobar", description=__doc__.splitlines()[0])
    ap.add_argument("fichero", help="cuerpo de la tarjeta (texto)")
    ap.add_argument("--carril", required=True)
    ap.add_argument("--titulo", default="")
    args = ap.parse_args(argv)
    if lanes is None:
        from agent_lanes.config import load_lanes
        lanes = load_lanes()
    lane = lanes.get(args.carril)
    if lane is None:
        out(f"carril desconocido: {args.carril} (válidos: {', '.join(sorted(lanes))})")
        return 2
    try:
        cuerpo = Path(args.fichero).read_text(encoding="utf-8")
    except OSError as exc:
        out(f"no se puede leer {args.fichero}: {exc}")
        return 2
    code, texto = comprobar(lane, args.titulo, cuerpo, lanes)
    out(texto)
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
