"""W3b: Hermes solo responde libremente en Gestion t5; en el resto de temas, solo con mencion.

Cambia `platforms.telegram` en %LOCALAPPDATA%/hermes/config.yaml (perfil default) con UNA
sustitucion exacta. Anade dos claves:

    require_mention: true
    free_response_topics: "-1003530490339:5"

Diseno (ver decisions/2026-09-27-w3b-mecanismo-temas.md):
- No imprime ningun valor de config.yaml ni de .env; solo OK/FAIL por comprobacion.
- No escribe nada si alguna comprobacion falla.
- Backup config.yaml.bak-20260927-w3b antes de escribir; escritura atomica (tmp + replace).
- Idempotente: si ya esta aplicado, lo dice y sale con 0.

Uso (con el Python del venv de hermes, que trae PyYAML):
    PY=~/AppData/Local/hermes/hermes-agent/venv/Scripts/python.exe
    $PY apply_w3b_config.py            # solo comprueba (dry-run)
    $PY apply_w3b_config.py --apply    # aplica
    $PY apply_w3b_config.py --revert   # quita exactamente el bloque anadido
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

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / "AppData" / "Local" / "hermes")
CONFIG = HERMES_HOME / "config.yaml"
ENV_FILE = HERMES_HOME / ".env"
BACKUP = HERMES_HOME / "config.yaml.bak-20260927-w3b"

GESTION_T5 = "-1003530490339:5"
ANCHOR = "platforms:\n  telegram:\n    enabled: true\n"
BLOCK = (
    "    # W3b 2026-09-27: Hermes principal solo en Gestion t5; resto de temas solo con mencion.\n"
    "    require_mention: true\n"
    f"    free_response_topics: \"{GESTION_T5}\"\n"
)
NEW_KEYS = {"require_mention": True, "free_response_topics": GESTION_T5}
# Variables de entorno que pisarian el YAML (el env gana, adapter.py:7208-7221).
OVERRIDE_ENV_NAMES = ("TELEGRAM_REQUIRE_MENTION", "TELEGRAM_FREE_RESPONSE_TOPICS")

failures: list[str] = []


def check(ok: bool, label: str) -> bool:
    print(f"{'OK  ' if ok else 'FAIL'}: {label}")
    if not ok:
        failures.append(label)
    return ok


def telegram_section(data: dict) -> dict | None:
    plats = data.get("platforms") if isinstance(data, dict) else None
    tg = plats.get("telegram") if isinstance(plats, dict) else None
    return tg if isinstance(tg, dict) else None


def env_names_present() -> set[str]:
    """Solo NOMBRES de variables del .env (nunca valores)."""
    names: set[str] = set()
    if not ENV_FILE.exists():
        return names
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            names.add(s.split("=", 1)[0].removeprefix("export ").strip())
    return names


def common_checks(text: str, data: dict) -> None:
    check(isinstance(data, dict), "config.yaml es un mapa YAML valido")
    check("\r" not in text, "config.yaml usa finales de linea LF")
    check(not isinstance(data.get("telegram"), dict), "no hay bloque telegram: de nivel superior (ganaria al anidado)")
    check("require_mention" not in data, "no hay require_mention de nivel superior")
    present = env_names_present() | {n for n in OVERRIDE_ENV_NAMES if os.environ.get(n)}
    for name in OVERRIDE_ENV_NAMES:
        check(name not in present, f"{name} no esta definido en .env ni en el entorno (pisaria el YAML)")


def write_atomic(new_text: str) -> None:
    tmp = CONFIG.with_suffix(".yaml.tmp-w3b")
    tmp.write_text(new_text, encoding="utf-8", newline="\n")
    os.replace(tmp, CONFIG)


def do_apply(text: str, data: dict, apply: bool) -> int:
    tg = telegram_section(data)
    if tg is not None and all(tg.get(k) == v for k, v in NEW_KEYS.items()) and BLOCK in text:
        print("OK  : ya aplicado; no se escribe nada")
        return 0
    common_checks(text, data)
    check(tg is not None, "existe platforms.telegram")
    check(text.count(ANCHOR) == 1, "el ancla 'platforms/telegram/enabled: true' aparece exactamente 1 vez")
    for key in NEW_KEYS:
        check(tg is None or key not in tg, f"platforms.telegram no tiene ya '{key}'")
    if failures:
        print(f"ABORTADO: {len(failures)} comprobacion(es) fallidas; no se ha escrito nada")
        return 1

    new_text = text.replace(ANCHOR, ANCHOR + BLOCK, 1)
    try:
        new_data = yaml.safe_load(new_text)
    except yaml.YAMLError:
        check(False, "el YAML resultante parsea")
        print("ABORTADO: no se ha escrito nada")
        return 1
    check(True, "el YAML resultante parsea")
    new_tg = telegram_section(new_data) or {}
    for key, val in NEW_KEYS.items():
        check(new_tg.get(key) == val, f"platforms.telegram.{key} tiene el valor esperado")
    stripped = copy.deepcopy(new_data)
    for key in NEW_KEYS:
        (telegram_section(stripped) or {}).pop(key, None)
    check(stripped == data, "el resto del config queda identico (diff semantico = solo las 2 claves)")
    if failures:
        print("ABORTADO: no se ha escrito nada")
        return 1
    if not apply:
        print("DRY-RUN: todo cuadra. Ejecuta con --apply para escribir.")
        return 0
    if BACKUP.exists():
        check(False, f"no existe ya {BACKUP.name} (no se sobrescribe un backup)")
        print("ABORTADO: renombra o borra el backup previo si es de un intento anterior")
        return 1
    shutil.copy2(CONFIG, BACKUP)
    check(BACKUP.exists(), f"backup creado: {BACKUP.name}")
    write_atomic(new_text)
    final = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    check(final == new_data, "config.yaml escrito y releido correctamente")
    if failures:
        return 1
    print("HECHO. Reinicia el gateway: ~/AppData/Local/hermes/bin/hermes gateway restart")
    return 0


def do_revert(text: str, data: dict) -> int:
    check(isinstance(data, dict), "config.yaml es un mapa YAML valido")
    if text.count(ANCHOR + BLOCK) != 1:
        check(False, "el bloque W3b aparece exactamente 1 vez (tal como lo escribio este script)")
        print("ABORTADO: no se ha escrito nada. Alternativa manual: restaurar " + BACKUP.name)
        return 1
    new_text = text.replace(ANCHOR + BLOCK, ANCHOR, 1)
    new_data = yaml.safe_load(new_text)
    expected = copy.deepcopy(data)
    for key in NEW_KEYS:
        (telegram_section(expected) or {}).pop(key, None)
    if not check(new_data == expected, "el revert solo quita las 2 claves"):
        print("ABORTADO: no se ha escrito nada")
        return 1
    write_atomic(new_text)
    check(yaml.safe_load(CONFIG.read_text(encoding="utf-8")) == expected, "config.yaml revertido y releido")
    print("HECHO. Reinicia el gateway: ~/AppData/Local/hermes/bin/hermes gateway restart")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="escribe los cambios (por defecto: dry-run)")
    g.add_argument("--revert", action="store_true", help="quita el bloque W3b")
    args = ap.parse_args()
    if not check(CONFIG.exists(), "existe config.yaml de hermes"):
        return 1
    text = CONFIG.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        check(False, "config.yaml actual parsea")
        return 1
    if args.revert:
        return do_revert(text, data)
    return do_apply(text, data, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
