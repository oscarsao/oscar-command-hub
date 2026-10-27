"""Una sola fuente de permisos (24-10, Oscar): people.yaml -> listas de acceso de Hermes (@oscarsaoBot).

Genera, a partir de tools/telegram/people.yaml (leído con agent_lanes.people.load_people, el mismo cargador del bot de
Trabajos):
  1. En el .env de Hermes: TELEGRAM_ALLOWED_USERS y TELEGRAM_GROUP_ALLOWED_USERS = Oscar (OWNER_TELEGRAM_ID, implícito)
     + cada persona con user_id. Una entrada con user_id null (p. ej. Mauredy) NO entra: Hermes no le contesta.
     Hermes solo responde a quien esté en estas listas, en grupos y en privado.
  2. En el SOUL.md de Hermes: la tabla «persona × marcas × qué puede hacer y aprobar», entre los marcadores
     <!-- EQUIPO-PERMISOS:inicio --> y <!-- EQUIPO-PERMISOS:fin --> (si faltan, se insertan justo después de
     <!-- PERSONAS-ACCESO:inicio -->). El resto de SOUL.md no se toca: las reglas escritas a mano siguen mandando.

Uso:  py -3.12 sincronizar_permisos.py            -> modo seco: dice qué cambiaría (nombres, nunca valores del .env)
      py -3.12 sincronizar_permisos.py --apply    -> escribe (con copia .bak-sync-<fecha> de cada fichero que cambie)
      --hermes-home <dir>                          -> otra carpeta de Hermes (por defecto %LOCALAPPDATA%/hermes)
Después de --apply: `hermes gateway restart` para que Hermes relea el .env y el SOUL.md.

Lo usará también la pantalla de permisos del panel de Oscar HQ (edita people.yaml y lanza esta sincronización).
Códigos de salida: 0 sin cambios o aplicado · 1 hay cambios pendientes (modo seco) · 2 error.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent-lanes"))

from agent_lanes.people import (  # noqa: E402
    APROBAR_PRODUCCION, TODAS_LAS_MARCAS, People, Person, load_people, people_file,
)

PEOPLE = people_file()  # el real, fuera del repo (AGENT_LANES_PEOPLE_FILE o tools/agent-lanes/.state/people.yaml)
DEFAULT_OWNER = "6744452215"  # = agent_lanes.decisions.OWNER_TELEGRAM_ID (sin importar decisions: arrastra el bot)
ENV_KEYS = ("TELEGRAM_ALLOWED_USERS", "TELEGRAM_GROUP_ALLOWED_USERS")
MARK_START, MARK_END = "<!-- EQUIPO-PERMISOS:inicio -->", "<!-- EQUIPO-PERMISOS:fin -->"
ANCHOR = "<!-- PERSONAS-ACCESO:inicio -->"


def hermes_home(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    base = os.environ.get("HERMES_HOME") or os.path.join(os.environ.get("LOCALAPPDATA") or "", "hermes")
    return Path(base)


def personas(people: People, raw_keys: list[str]) -> list[Person]:
    """Oscar primero y luego las personas con user_id, en el orden de people.yaml."""
    alta = [people.lookup(uid) for uid in people.collaborator_ids()]
    orden = {k: i for i, k in enumerate(raw_keys)}
    return [people.owner] + sorted((p for p in alta if p), key=lambda p: orden.get(p.key, len(orden)))


def ids_permitidos(lista: list[Person]) -> str:
    return ",".join(p.user_id for p in lista)


def _celda(value) -> str:
    """Texto seguro para una celda Markdown: sin `|` ni saltos de línea."""
    return " ".join(str(value or "").replace("|", "/").split())


def _marcas(p: Person) -> str:
    if p.is_owner or TODAS_LAS_MARCAS in p.brands:
        return "todas"
    return ", ".join(b for b in p.brands) or "ninguna"


def _puede(p: Person) -> str:
    if p.is_owner:
        return "todo"
    if p.operador and TODAS_LAS_MARCAS in p.brands:
        return "todo (igual que Oscar): crear, mover y decidir tarjetas en todas las marcas"
    if p.operador:
        return "crear, consultar y mover tarjetas de sus marcas; decidir avisos de sus marcas"
    return "consultar estado de sus marcas y pedir trabajo (tarjeta en sus marcas); no mueve ni decide"


def _aprueba(p: Person) -> str:
    if p.is_owner or (p.operador and APROBAR_PRODUCCION in p.permisos):
        return "sí (todas las marcas)"
    return "no"


def _privado(p: Person) -> str:
    """Contexto privado de Oscar (memoria, ClickUp, finanzas, correo): Oscar y quien opera en todas las marcas."""
    return "sí" if p.is_owner or (p.operador and TODAS_LAS_MARCAS in p.brands) else "no"


def tabla(lista: list[Person], pendientes: list[str]) -> str:
    lines = [
        MARK_START,
        "### Tabla de permisos (GENERADA desde tools/telegram/people.yaml con sincronizar_permisos.py; no editar a mano)",
        "",
        "Identifica al remitente SOLO por su User ID. Quien no esté en esta tabla no recibe respuesta (Hermes ya lo",
        "filtra por TELEGRAM_ALLOWED_USERS); si aun así te llega, no uses herramientas y no des contexto.",
        "",
        "| Persona | Telegram ID | Rol | Marcas | Qué puede hacer contigo | Aprueba producción | Contexto privado de Oscar |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in lista:
        celdas = (p.name, p.user_id, p.role, _marcas(p), _puede(p), _aprueba(p), _privado(p))
        lines.append("| " + " | ".join(_celda(c) for c in celdas) + " |")
    if pendientes:
        lines += ["", "Sin alta (user_id vacío, Hermes NO les contesta): " + ", ".join(pendientes) + "."]
    lines.append(MARK_END)
    return "\n".join(lines)


def _eol(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def nuevo_env(text: str, valor: str) -> tuple[str, list[str]]:
    """Sustituye solo las claves ENV_KEYS (las añade al final si faltan). Devuelve (texto, claves cambiadas)."""
    eol, cambiadas = _eol(text), []
    for key in ENV_KEYS:
        # Todas las apariciones (dotenv se queda con la última), con o sin `export` y espacios alrededor del `=`.
        pat = re.compile(rf"^[ \t]*(?:export[ \t]+)?{key}[ \t]*=.*$", re.MULTILINE)
        linea = f"{key}={valor}"
        if pat.search(text):
            # con ^...$ el \r de CRLF queda dentro del match: se conserva
            nuevo = pat.sub(lambda m: linea + ("\r" if m.group(0).endswith("\r") else ""), text)
            if nuevo != text:
                text = nuevo
                cambiadas.append(key)
        else:
            text = text + ("" if text.endswith(("\n", "\r\n")) or not text else eol) + linea + eol
            cambiadas.append(key)
    return text, cambiadas


def nuevo_soul(text: str, bloque: str) -> str:
    eol = _eol(text)
    bloque = bloque.replace("\n", eol)
    hay_inicio, hay_fin = text.count(MARK_START), text.count(MARK_END)
    if hay_inicio or hay_fin:
        if hay_inicio != 1 or hay_fin != 1 or text.index(MARK_END) < text.index(MARK_START):
            raise ValueError("marcadores EQUIPO-PERMISOS rotos en SOUL.md (faltan, se repiten o están al revés)")
        a = text.index(MARK_START)
        b = text.index(MARK_END) + len(MARK_END)
        return text[:a] + bloque + text[b:]
    if ANCHOR in text:
        i = text.index(ANCHOR) + len(ANCHOR)
        return text[:i] + eol + bloque + text[i:]
    return text.rstrip("\r\n") + eol + eol + bloque + eol


def _leer(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


def _escribir(path: Path, text: str, stamp: str) -> None:
    """Copia .bak-sync-<stamp> (sin pisar una anterior) y escritura atómica (tmp + os.replace): el .env lleva secretos."""
    bak = path.with_name(f"{path.name}.bak-sync-{stamp}")
    n = 1
    while bak.exists():
        bak, n = path.with_name(f"{path.name}.bak-sync-{stamp}-{n}"), n + 1
    shutil.copy2(path, bak)
    tmp = path.with_name(f"{path.name}.tmp-sync")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--hermes-home")
    ap.add_argument("--people", default=str(PEOPLE))
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import yaml
    raw = yaml.safe_load(Path(a.people).read_text(encoding="utf-8")) or {}
    entries = raw.get("people") or {}
    owner = os.environ.get("OWNER_TELEGRAM_ID") or DEFAULT_OWNER
    people = load_people(owner, a.people)
    # `hermes: false` en people.yaml = la persona sigue en el equipo (avisos, panel, bot de Trabajos) pero Hermes no le
    # contesta (Oscar 24-10: Hermes tiene terminal y ficheros en Telegram y no se puede limitar por persona).
    sin_hermes = {k for k, v in entries.items() if (v or {}).get("hermes") is False}
    lista = [p for p in personas(people, list(entries)) if p.key not in sin_hermes]
    alta = {p.key for p in lista}
    pendientes = [str((v or {}).get("name") or k) + (" (sin Hermes)" if k in sin_hermes else "")
                  for k, v in entries.items() if k not in alta and (v or {}).get("role") != "owner"]
    home = hermes_home(a.hermes_home)
    env_path, soul_path = home / ".env", home / "SOUL.md"
    for path in (env_path, soul_path):
        if not path.exists():
            print(f"ERROR: no existe {path}")
            return 2
    env_old, soul_old = _leer(env_path), _leer(soul_path)
    env_new, claves = nuevo_env(env_old, ids_permitidos(lista))
    try:
        soul_new = nuevo_soul(soul_old, tabla(lista, pendientes))
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 2
    print("Con acceso a Hermes: " + ", ".join(p.name for p in lista))
    if pendientes:
        print("Sin alta (no se les contesta): " + ", ".join(pendientes))
    print(f".env: {'cambia ' + ', '.join(claves) if claves else 'sin cambios'}")
    print(f"SOUL.md: {'cambia la tabla de permisos' if soul_new != soul_old else 'sin cambios'}")
    if env_new == env_old and soul_new == soul_old:
        return 0
    if not a.apply:
        print("MODO SECO. Repite con --apply para escribir.")
        return 1
    stamp = time.strftime("%Y%m%d%H%M%S")
    if env_new != env_old:
        _escribir(env_path, env_new, stamp)
    if soul_new != soul_old:
        _escribir(soul_path, soul_new, stamp)
    print("Aplicado. Reinicia Hermes: hermes gateway restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
