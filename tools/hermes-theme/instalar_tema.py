"""Compone el tema de Hermes «Píldora · Liquid Glass V4» y lo copia a ~/.hermes/dashboard-themes/.

Une `pildora-liquid-glass.theme.yaml` (paleta, tipografía) con `pildora-liquid-glass.css` (campo `customCSS`)
y escribe un único YAML de tema de usuario. NO toca `config.yaml` de Hermes: el tema queda disponible en el
selector de temas del panel, pero no se activa solo (activarlo escribe `dashboard.theme` en config.yaml y eso
lo decide Oscar).

Uso (en el PC donde corre Hermes):
    py -3.12 tools/hermes-theme/instalar_tema.py            # instala/actualiza el tema
    py -3.12 tools/hermes-theme/instalar_tema.py --dry-run  # solo comprueba y dice qué haría
    py -3.12 tools/hermes-theme/instalar_tema.py --dest RUTA  # otra carpeta de destino
Nunca imprime secretos (no maneja ninguno).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
NOMBRE = "pildora-liquid-glass"
CSS = AQUI / f"{NOMBRE}.css"
BASE = AQUI / f"{NOMBRE}.theme.yaml"
# Límite de Hermes (_THEME_CUSTOM_CSS_MAX): lo que pase de aquí se recorta en silencio.
CSS_MAX = 32 * 1024
DEST_DEFECTO = Path.home() / ".hermes" / "dashboard-themes"


def componer(css_path: Path = CSS, base_path: Path = BASE) -> dict:
    """Devuelve el tema completo (dict) con el CSS incrustado en `customCSS`."""
    tema = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    css = css_path.read_text(encoding="utf-8")
    if len(css) > CSS_MAX:
        raise ValueError(f"El CSS ({len(css)} caracteres) supera el límite de Hermes ({CSS_MAX}).")
    tema["customCSS"] = css
    return tema


def volcar(tema: dict) -> str:
    # Bloque literal para el CSS: legible y diff-able si alguien abre el YAML instalado.
    class _Literal(str):
        pass

    def _repr(dumper, data):
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")

    yaml.add_representer(_Literal, _repr, Dumper=yaml.SafeDumper)
    copia = dict(tema)
    copia["customCSS"] = _Literal(tema["customCSS"])
    return yaml.dump(copia, Dumper=yaml.SafeDumper, allow_unicode=True, sort_keys=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", type=Path, default=DEST_DEFECTO, help="carpeta dashboard-themes de Hermes")
    ap.add_argument("--dry-run", action="store_true", help="no escribe nada")
    args = ap.parse_args(argv)

    tema = componer()
    destino = args.dest / f"{NOMBRE}.yaml"
    texto = volcar(tema)
    if args.dry_run:
        print(f"[dry-run] escribiría {destino} ({len(texto)} bytes, CSS {len(tema['customCSS'])} caracteres)")
        return 0
    args.dest.mkdir(parents=True, exist_ok=True)
    destino.write_text(texto, encoding="utf-8")
    print(f"Tema instalado en {destino}.")
    print("Para verlo: panel de Hermes → selector de temas → «Píldora · Liquid Glass V4» "
          "(o dashboard.theme: pildora-liquid-glass en config.yaml, con el OK de Oscar).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
