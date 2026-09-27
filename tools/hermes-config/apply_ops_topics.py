"""Añade los temas de operaciones por marca (Gestión t230 MigraTeam, t231 Píldora) a free_response_topics.

Ensayo por defecto; --apply escribe. Nunca imprime valores de config salvo la propia clave de temas.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

import yaml

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / "AppData" / "Local" / "hermes")
CFG = HOME / "config.yaml"
BAK = HOME / "config.yaml.bak-20260927-ops-topics"
OLD = '    free_response_topics: "-1003530490339:5"\n'
NEW = '    free_response_topics: "-1003530490339:5,-1003530490339:230,-1003530490339:231"\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    text = CFG.read_text(encoding="utf-8")
    if NEW in text:
        print("OK  : ya aplicado, nada que hacer.")
        return 0
    if text.count(OLD) != 1:
        print("FAIL: el ancla free_response_topics no aparece exactamente 1 vez. No se toca nada.")
        return 1
    old, new = yaml.safe_load(text), yaml.safe_load(text.replace(OLD, NEW, 1))
    old["platforms"]["telegram"]["free_response_topics"] = new["platforms"]["telegram"]["free_response_topics"]
    if old != new:
        print("FAIL: el cambio afectaría a otras claves. No se toca nada.")
        return 1
    print("OK  : ancla única y diff semántico = solo free_response_topics")
    if not a.apply:
        print("DRY-RUN OK. Repite con --apply.")
        return 0
    if not BAK.exists():
        shutil.copy2(CFG, BAK)
    tmp = CFG.with_suffix(".yaml.tmp-ops")
    tmp.write_text(text.replace(OLD, NEW, 1), encoding="utf-8", newline="\n")
    os.replace(tmp, CFG)
    print("HECHO: free_response_topics = t5, t230, t231. Reinicia el gateway.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
