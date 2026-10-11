"""DM de desconocidos a Hermes: responder "no tienes acceso" una vez en vez de ofrecer un codigo de emparejamiento.

Anade a `gateway:` de %LOCALAPPDATA%/hermes/config.yaml (perfil default) con UNA insercion exacta:

    unauthorized_dm_behavior: decline
    unauthorized_dm_decline_message: "..."

`decline` = Hermes contesta un unico mensaje corto y calla 24 h a ese remitente (hermes-agent/gateway/config.py).
Con el valor por defecto (`pair`) un desconocido recibiria un codigo de emparejamiento.

Diseno (mismo patron que apply_w3b_config.py):
- Dry-run por defecto. No imprime ningun valor de config.yaml ni de .env; solo OK/FAIL por comprobacion.
- No escribe nada si alguna comprobacion falla. Backup config.yaml.bak-20261011-dmpolicy antes de escribir;
  escritura atomica (tmp + replace). Idempotente. --revert quita exactamente el bloque anadido.
- HERMES_HOME (variable de entorno) cambia la carpeta de hermes (los tests usan una temporal).

Uso (con el Python del venv de hermes, que trae PyYAML):
    PY=~/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe
    $PY apply_dm_policy.py            # solo comprueba (dry-run)
    $PY apply_dm_policy.py --apply    # aplica (lo ejecuta Oscar; reiniciar el gateway despues)
    $PY apply_dm_policy.py --revert   # quita exactamente el bloque anadido
"""

from __future__ import annotations

import argparse
import copy
import os
import shutil
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: falta PyYAML; usa el python del venv de hermes (hermes-agent/venv/Scripts/python.exe)")
    sys.exit(2)

ANCHOR = "\ngateway:\n"
MESSAGE = "Este asistente es privado y no tienes acceso. Si lo necesitas, pidele acceso a Oscar."
BLOCK = (
    "  # DM-policy 2026-10-11: un desconocido recibe un unico rechazo en vez de un codigo de emparejamiento.\n"
    "  unauthorized_dm_behavior: decline\n"
    f"  unauthorized_dm_decline_message: \"{MESSAGE}\"\n"
)
NEW_KEYS = {"unauthorized_dm_behavior": "decline", "unauthorized_dm_decline_message": MESSAGE}
OVERRIDE_ENV_NAMES = ("GATEWAY_UNAUTHORIZED_DM_BEHAVIOR", "UNAUTHORIZED_DM_BEHAVIOR")

failures: list[str] = []


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / "AppData" / "Local" / "hermes")


def check(ok: bool, label: str) -> bool:
    print(f"{'OK  ' if ok else 'FAIL'}: {label}")
    if not ok:
        failures.append(label)
    return ok


def gateway_section(data) -> dict | None:
    gw = data.get("gateway") if isinstance(data, dict) else None
    return gw if isinstance(gw, dict) else None


def env_names_present(home: Path) -> set[str]:
    """Solo NOMBRES de variables del .env (nunca valores)."""
    names: set[str] = set()
    env_file = home / ".env"
    if not env_file.exists():
        return names
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            names.add(s.split("=", 1)[0].removeprefix("export ").strip())
    return names


def write_atomic(config: Path, new_text: str) -> None:
    tmp = config.with_suffix(".yaml.tmp-dmpolicy")
    tmp.write_text(new_text, encoding="utf-8", newline="\n")
    os.replace(tmp, config)


def do_apply(config: Path, backup: Path, text: str, data: dict, apply: bool) -> int:
    gw = gateway_section(data)
    if gw is not None and all(gw.get(k) == v for k, v in NEW_KEYS.items()) and BLOCK in text:
        print("OK  : ya aplicado; no se escribe nada")
        return 0
    check(isinstance(data, dict), "config.yaml es un mapa YAML valido")
    check("\r" not in text, "config.yaml usa finales de linea LF")
    present = env_names_present(config.parent) | {n for n in OVERRIDE_ENV_NAMES if os.environ.get(n)}
    for name in OVERRIDE_ENV_NAMES:
        check(name not in present, f"{name} no esta definido en .env ni en el entorno")
    check(gw is not None, "existe el bloque gateway:")
    check(("\n" + text).count(ANCHOR) == 1, "el ancla 'gateway:' aparece exactamente 1 vez (nivel superior)")
    check(not any(k in data for k in NEW_KEYS), "no hay ya claves unauthorized_dm_* de nivel superior")
    for key in NEW_KEYS:
        check(gw is None or key not in gw, f"gateway no tiene ya '{key}'")
    if failures:
        print(f"ABORTADO: {len(failures)} comprobacion(es) fallidas; no se ha escrito nada")
        return 1

    new_text = (("\n" + text).replace(ANCHOR, ANCHOR + BLOCK, 1))[1:]
    try:
        new_data = yaml.safe_load(new_text)
    except yaml.YAMLError:
        check(False, "el YAML resultante parsea")
        print("ABORTADO: no se ha escrito nada")
        return 1
    check(True, "el YAML resultante parsea")
    new_gw = gateway_section(new_data) or {}
    for key, val in NEW_KEYS.items():
        check(new_gw.get(key) == val, f"gateway.{key} tiene el valor esperado")
    stripped = copy.deepcopy(new_data)
    for key in NEW_KEYS:
        (gateway_section(stripped) or {}).pop(key, None)
    check(stripped == data, "el resto del config queda identico (diff semantico = solo las 2 claves)")
    if failures:
        print("ABORTADO: no se ha escrito nada")
        return 1
    if not apply:
        print("DRY-RUN: todo cuadra. Ejecuta con --apply para escribir.")
        return 0
    if backup.exists():
        check(False, f"no existe ya {backup.name} (no se sobrescribe un backup)")
        print("ABORTADO: renombra el backup previo si es de un intento anterior")
        return 1
    shutil.copy2(config, backup)
    check(backup.exists(), f"backup creado: {backup.name}")
    write_atomic(config, new_text)
    final = yaml.safe_load(config.read_text(encoding="utf-8"))
    check(final == new_data, "config.yaml escrito y releido correctamente")
    if failures:
        return 1
    print("HECHO. Reinicia el gateway: ~/AppData/Local/hermes/bin/hermes gateway restart")
    return 0


def do_revert(config: Path, backup: Path, text: str, data: dict) -> int:
    check(isinstance(data, dict), "config.yaml es un mapa YAML valido")
    if text.count(ANCHOR.lstrip("\n") + BLOCK) != 1:
        check(False, "el bloque dm-policy aparece exactamente 1 vez (tal como lo escribio este script)")
        print("ABORTADO: no se ha escrito nada. Alternativa manual: restaurar " + backup.name)
        return 1
    new_text = text.replace(ANCHOR.lstrip("\n") + BLOCK, ANCHOR.lstrip("\n"), 1)
    new_data = yaml.safe_load(new_text)
    expected = copy.deepcopy(data)
    for key in NEW_KEYS:
        (gateway_section(expected) or {}).pop(key, None)
    if not check(new_data == expected, "el revert solo quita las 2 claves"):
        print("ABORTADO: no se ha escrito nada")
        return 1
    write_atomic(config, new_text)
    check(yaml.safe_load(config.read_text(encoding="utf-8")) == expected, "config.yaml revertido y releido")
    print("HECHO. Reinicia el gateway: ~/AppData/Local/hermes/bin/hermes gateway restart")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    failures.clear()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="escribe los cambios (por defecto: dry-run)")
    g.add_argument("--revert", action="store_true", help="quita el bloque dm-policy")
    args = ap.parse_args(argv)
    home = hermes_home()
    config, backup = home / "config.yaml", home / "config.yaml.bak-20261011-dmpolicy"
    if not check(config.exists(), "existe config.yaml de hermes"):
        return 1
    text = config.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        check(False, "config.yaml actual parsea")
        return 1
    if args.revert:
        return do_revert(config, backup, text, data)
    return do_apply(config, backup, text, data, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
