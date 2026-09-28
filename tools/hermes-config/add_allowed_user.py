"""Añade un user_id de Telegram a TELEGRAM_ALLOWED_USERS del .env de Hermes (28-09). Solo imprime la lista de IDs.

Uso:  python add_allowed_user.py 5516823836 [--apply]
Los user_id no son secretos; el resto del .env no se imprime nunca. Backup: .env.bak-<fecha>-allowed.
"""
import argparse
import os
import shutil
import sys
from datetime import date
from pathlib import Path

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / "AppData" / "Local" / "hermes")
ENV = HOME / ".env"
KEY = "TELEGRAM_ALLOWED_USERS="


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("user_id")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if not a.user_id.isdigit():
        sys.exit("FAIL: el user_id debe ser numérico.")
    lines = ENV.read_text(encoding="utf-8").splitlines()
    idx = [i for i, l in enumerate(lines) if l.startswith(KEY)]
    if len(idx) != 1:
        sys.exit(f"FAIL: esperaba exactamente 1 línea {KEY} y hay {len(idx)}. No toco nada.")
    ids = [x.strip() for x in lines[idx[0]][len(KEY):].split(",") if x.strip()]
    if a.user_id in ids:
        print("OK: ya estaba. Lista:", ",".join(ids))
        return 0
    ids.append(a.user_id)
    print("Nueva lista:", ",".join(ids))
    if not a.apply:
        print("DRY-RUN. Repite con --apply.")
        return 0
    bak = ENV.with_name(f".env.bak-{date.today():%Y%m%d}-allowed")
    if not bak.exists():
        shutil.copy2(ENV, bak)
    lines[idx[0]] = KEY + ",".join(ids)
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("HECHO. Reinicia el gateway de Hermes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
