"""Telegram sin ruido (28-09): no mostrar tool calls de Hermes y borrar burbujas de progreso al terminar.

Añade tool_progress: off y cleanup_progress: true bajo display.platforms.telegram (donde ya está
show_reasoning: false). Ensayo por defecto; --apply escribe. No imprime valores de config.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

import yaml

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / "AppData" / "Local" / "hermes")
CFG = HOME / "config.yaml"
BAK = HOME / "config.yaml.bak-20260928-quiet"
OLD = "    telegram:\n      show_reasoning: false\n"
NEW = "    telegram:\n      show_reasoning: false\n      tool_progress: 'off'\n      cleanup_progress: true\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    text = CFG.read_text(encoding="utf-8")
    if NEW in text:
        print("OK  : ya aplicado.")
        return 0
    if text.count(OLD) != 1:
        print("FAIL: no encuentro exactamente 1 bloque display.platforms.telegram con show_reasoning. No toco nada.")
        return 1
    old, new = yaml.safe_load(text), yaml.safe_load(text.replace(OLD, NEW, 1))
    tg = new["display"]["platforms"]["telegram"]
    ok = tg.get("tool_progress") == "off" and tg.get("cleanup_progress") is True and tg.get("show_reasoning") is False
    old["display"]["platforms"]["telegram"].update(tool_progress="off", cleanup_progress=True)
    if not ok or old != new:
        print("FAIL: el cambio no es el esperado. No toco nada.")
        return 1
    print("OK  : diff = solo display.platforms.telegram.{tool_progress, cleanup_progress}")
    if not a.apply:
        print("DRY-RUN OK. Repite con --apply.")
        return 0
    if not BAK.exists():
        shutil.copy2(CFG, BAK)
    tmp = CFG.with_suffix(".yaml.tmp-quiet")
    tmp.write_text(text.replace(OLD, NEW, 1), encoding="utf-8", newline="\n")
    os.replace(tmp, CFG)
    print("HECHO. Reinicia el gateway.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
