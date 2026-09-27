"""Panel del kanban en kanban.pildoradigital.com: public_url fijo + password_hash + secret (27-09).

Pide la contraseña nueva (no se muestra ni se guarda en claro). Ejecutar en una terminal NORMAL
(PowerShell o Git Bash), no con '!' en Claude Code, porque tiene que preguntarte la contraseña:
    ~/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe apply_dashboard_config.py
Nunca imprime valores secretos. Backup: config.yaml.bak-20260927-dashboard.
"""
import getpass
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

import yaml

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / "AppData" / "Local" / "hermes")
sys.path.insert(0, str(HOME / "hermes-agent"))
from plugins.dashboard_auth.basic import hash_password  # noqa: E402

CFG = HOME / "config.yaml"
BAK = HOME / "config.yaml.bak-20260927-dashboard"
URL = "https://kanban.pildoradigital.com"


def main():
    text = CFG.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    dash = data.get("dashboard") or {}
    if (dash.get("basic_auth") or {}).get("password_hash") and dash.get("public_url") == URL:
        print("OK  : ya aplicado, nada que hacer.")
        return 0
    pub = re.findall(r"^  public_url: .*\n", text, flags=re.M)
    pwd = re.findall(r"^    password: .*\n", text, flags=re.M)
    if len(pub) != 1 or len(pwd) != 1:
        print("FAIL: no encuentro exactamente 1 public_url y 1 basic_auth.password. No se toca nada.")
        return 1
    p1 = getpass.getpass("Contraseña nueva para el panel (mín. 12 caracteres): ")
    p2 = getpass.getpass("Repítela: ")
    if p1 != p2 or len(p1) < 12:
        print("FAIL: no coinciden o tiene menos de 12 caracteres. No se toca nada.")
        return 1
    new = text.replace(pub[0], f"  public_url: {URL}\n", 1)
    new = new.replace(pwd[0], f"    password_hash: '{hash_password(p1)}'\n    secret: '{secrets.token_urlsafe(48)}'\n", 1)
    nd = yaml.safe_load(new)
    ba = nd["dashboard"]["basic_auth"]
    checks = {
        "public_url fijo": nd["dashboard"]["public_url"] == URL,
        "password en claro eliminada": "password" not in ba,
        "password_hash presente": bool(ba.get("password_hash")),
        "secret presente": bool(ba.get("secret")),
        "resto del config intacto": {k: v for k, v in nd.items() if k != "dashboard"}
        == {k: v for k, v in data.items() if k != "dashboard"},
    }
    for k, ok in checks.items():
        print(("OK  : " if ok else "FAIL: ") + k)
    if not all(checks.values()):
        print("ABORTADO: no se ha tocado nada.")
        return 1
    if not BAK.exists():
        shutil.copy2(CFG, BAK)
    tmp = CFG.with_suffix(".yaml.tmp-dash")
    tmp.write_text(new, encoding="utf-8", newline="\n")
    os.replace(tmp, CFG)
    print("HECHO. Usuario: oscar. Ahora ejecuta instalar-panel-y-tunel.ps1 como administrador.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
