"""Anade a SOUL.md de Hermes la regla de enlaces obligatorios y de board correcto (texto: soul_enlaces.md).

Un bloque delimitado por marcadores al final de %LOCALAPPDATA%/hermes/SOUL.md:

    <!-- ENLACES-0b:inicio -->
    ...contenido de soul_enlaces.md...
    <!-- ENLACES-0b:fin -->

Diseno (patron de apply_w3b_config.py):
- Dry-run por defecto: solo comprueba y dice que haria. No imprime el contenido de SOUL.md.
- --apply: backup SOUL.md.bak-AAAAMMDD (no se sobrescribe uno previo), escritura atomica (tmp + replace).
- --revert: quita exactamente el bloque anadido (y deja el resto del fichero identico).
- Idempotente: si el bloque ya esta con el mismo texto, lo dice y sale con 0; si esta con otro texto, no lo
  actualiza solo (haz --revert y --apply).

Uso:
    python apply_soul_enlaces.py            # dry-run
    python apply_soul_enlaces.py --apply
    python apply_soul_enlaces.py --revert
Despues reinicia el gateway: ~/AppData/Local/hermes/bin/hermes gateway restart
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import date
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / "AppData" / "Local" / "hermes")
SOUL = HERMES_HOME / "SOUL.md"
SOURCE = Path(__file__).with_name("soul_enlaces.md")
START = "<!-- ENLACES-0b:inicio -->"
END = "<!-- ENLACES-0b:fin -->"

failures: list[str] = []


def check(ok: bool, label: str) -> bool:
    print(f"{'OK  ' if ok else 'FAIL'}: {label}")
    if not ok:
        failures.append(label)
    return ok


def block() -> str:
    body = SOURCE.read_text(encoding="utf-8").replace("\r\n", "\n").strip("\n")
    return f"{START}\n{body}\n{END}\n"


def write_atomic(text: str) -> None:
    tmp = SOUL.with_suffix(".md.tmp-enlaces")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, SOUL)


def do_apply(text: str, apply: bool) -> int:
    new_block = block()
    if text.count(START) == 1 and text.count(END) == 1:
        if new_block in text:
            print("OK  : ya aplicado; no se escribe nada")
            return 0
        check(False, "el bloque existente coincide con soul_enlaces.md (haz --revert y luego --apply)")
        return 1
    check(START not in text and END not in text, "SOUL.md no tiene marcadores a medias")
    check("\r" not in text, "SOUL.md usa finales de linea LF")
    if failures:
        print(f"ABORTADO: {len(failures)} comprobacion(es) fallidas; no se ha escrito nada")
        return 1
    new_text = text.rstrip("\n") + "\n\n" + new_block
    check(new_text.startswith(text.rstrip("\n")), "el contenido previo queda intacto (solo se anade al final)")
    if failures:
        return 1
    if not apply:
        print(f"DRY-RUN: se anadirian {len(new_block.splitlines())} lineas al final de SOUL.md. --apply para escribir.")
        return 0
    backup = SOUL.with_name(f"SOUL.md.bak-{date.today():%Y%m%d}")
    if backup.exists():
        check(False, f"no existe ya {backup.name} (no se sobrescribe un backup)")
        print("ABORTADO: renombra el backup previo si es de un intento anterior")
        return 1
    shutil.copy2(SOUL, backup)
    check(backup.exists(), f"backup creado: {backup.name}")
    write_atomic(new_text)
    check(SOUL.read_text(encoding="utf-8") == new_text, "SOUL.md escrito y releido correctamente")
    if failures:
        return 1
    print("HECHO. Reinicia el gateway: ~/AppData/Local/hermes/bin/hermes gateway restart")
    return 0


def do_revert(text: str) -> int:
    if not (text.count(START) == 1 and text.count(END) == 1 and text.index(START) < text.index(END)):
        check(False, "el bloque ENLACES-0b aparece exactamente 1 vez")
        print("ABORTADO: no se ha escrito nada. Alternativa manual: restaurar el SOUL.md.bak-AAAAMMDD")
        return 1
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1].lstrip("\n")
    new_text = head.rstrip("\n") + "\n" + (("\n" + tail) if tail else "")
    write_atomic(new_text)
    check(START not in SOUL.read_text(encoding="utf-8"), "bloque ENLACES-0b retirado y SOUL.md releido")
    print("HECHO. Reinicia el gateway: ~/AppData/Local/hermes/bin/hermes gateway restart")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="escribe los cambios (por defecto: dry-run)")
    g.add_argument("--revert", action="store_true", help="quita el bloque anadido")
    args = ap.parse_args()
    if not (check(SOUL.exists(), "existe SOUL.md de hermes") and check(SOURCE.exists(), "existe soul_enlaces.md")):
        return 1
    text = SOUL.read_text(encoding="utf-8")
    return do_revert(text) if args.revert else do_apply(text, args.apply)


if __name__ == "__main__":
    sys.exit(main())
