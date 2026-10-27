"""Compara las skills versionadas en el hub con las instaladas en ~/.claude/skills (F1-08, CF-15). Solo lectura.

    py -3.12 skills_sync.py --check [--instaladas DIR] [--hub DIR]

Fuentes del hub: `skills/<nombre>/` y `tools/agent-lanes/skills/<nombre>/` (todo fichero de la carpeta, byte a byte).
Salida 0 = todas iguales; 1 = alguna difiere o falta instalada (lista cada una); 2 = argumentos o rutas inválidas.
Nunca copia nada: actualizar ~/.claude es configuración del usuario y lo hace Oscar o con su OK (botón CF-15).
Las skills instaladas que el hub no versiona (spec-lite, distill…) no se miran.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

HUB = Path(__file__).resolve().parents[2]
FUENTES = ("skills", "tools/agent-lanes/skills")
IGUAL, DIFIERE, FALTA = "igual", "difiere", "falta instalada"


@dataclass(frozen=True)
class Resultado:
    skill: str
    estado: str
    detalle: str = ""


def skills_del_hub(hub: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for rel in FUENTES:
        base = hub / rel
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            if d.is_dir() and (d / "SKILL.md").is_file():
                found.setdefault(d.name, d)
    return found


def _ficheros(d: Path) -> dict[str, Path]:
    return {p.relative_to(d).as_posix(): p for p in d.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def comparar(hub: Path, instaladas: Path) -> list[Resultado]:
    out = []
    for name, src in skills_del_hub(hub).items():
        dst = instaladas / name
        if not (dst / "SKILL.md").is_file():
            out.append(Resultado(name, FALTA))
            continue
        a, b = _ficheros(src), _ficheros(dst)
        distintos = sorted(f for f in a if f not in b or a[f].read_bytes() != b[f].read_bytes())
        out.append(Resultado(name, DIFIERE, ", ".join(distintos)) if distintos else Resultado(name, IGUAL))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="comparar y avisar (único modo; nunca copia)")
    ap.add_argument("--instaladas", type=Path, default=Path.home() / ".claude" / "skills")
    ap.add_argument("--hub", type=Path, default=HUB)
    args = ap.parse_args(argv)
    if not skills_del_hub(args.hub):
        print(f"sin skills versionadas en {args.hub}", file=sys.stderr)
        return 2
    res = comparar(args.hub, args.instaladas)
    for r in res:
        print(f"{r.estado:16} {r.skill}" + (f"  ({r.detalle})" if r.detalle else ""))
    malas = [r for r in res if r.estado != IGUAL]
    if malas:
        print(f"{len(malas)} skill(s) instaladas no coinciden con el hub en {args.instaladas}. Copiarlas requiere OK "
              "de Oscar (CF-15).")
        return 1
    print(f"{len(res)} skills iguales")
    return 0


if __name__ == "__main__":
    sys.exit(main())
