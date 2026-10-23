"""Sincroniza los cambios de usuarios de Oscar HQ con people.yaml, TELEGRAM_ALLOWED_CHATS y la allowlist de Hermes.

Oscar HQ escribe filas en `ohq_user_external_changes` cuando alguien vincula Telegram, se suspende, se reactiva, se
desvincula o se elimina. Cada fila tiene un destino (`people_yaml`, `carriles_env`, `hermes`) y se marca resuelta por
la API de Oscar HQ cuando el integrador aplica el cambio.

Este módulo es un proceso del INTEGRADOR (no del runner ni de un carril):
  - `plan`: lee las filas pendientes, calcula el cambio exacto y lo deja en un informe. Con `--escribir` aplica solo
    el cambio de `tools/telegram/people.yaml` en el checkout actual (para abrir un PR); nada más se toca.
  - `resolver`: marca filas como resueltas en Oscar HQ, solo tras aplicar y con el OK de Oscar.

Nunca escribe `.env` (solo muestra la línea exacta de TELEGRAM_ALLOWED_CHATS) ni `~/.hermes` (solo lista lo que el
integrador debe aplicar). Falla cerrado: una fila rara o sin persona identificable va a "revisión manual", no se adivina.

Forma esperada de cada fila (la API de Oscar HQ puede traer más campos; se ignoran):
  {id, action: link|suspend|reactivate|unlink|delete, destination: people_yaml|carriles_env|hermes,
   person_key|name, telegram_user_id, role?, brands?, status}
API (URL y clave por entorno, nada hardcodeado): GET {OHQ_API_URL}/api/admin/user-external-changes?status=pending
y POST {OHQ_API_URL}/api/admin/user-external-changes/{id}/resolve, con cabecera X-API-Key = ${OHQ_API_KEY}.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from agent_lanes.people import PEOPLE_FILE

ACCIONES = ("link", "suspend", "reactivate", "unlink", "delete")
DESTINOS = ("people_yaml", "carriles_env", "hermes")
ROLES = ("coo", "cmo", "marketing", "dev")
ENV_VAR = "TELEGRAM_ALLOWED_CHATS"
DA_ACCESO = {"link", "reactivate"}  # el resto quita el acceso


@dataclass
class Plan:
    """Resultado de `planificar`. `people_text` es el people.yaml resultante (None = sin cambios)."""
    people_text: str | None = None
    env_line: str | None = None
    hermes: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    filas: dict[str, list[str]] = field(default_factory=dict)  # destino -> ids de fila cubiertos
    ids_manual: list[str] = field(default_factory=list)


def _slug(texto) -> str:
    base = "".join(c for c in unicodedata.normalize("NFD", str(texto or "")) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "_", base.casefold()).strip("_")


def _uid(raw) -> str | None:
    s = str(raw if raw is not None else "").strip()
    return str(int(s)) if s.isdigit() and int(s) > 0 else None


def _valida(fila) -> str | None:
    """Motivo de rechazo o None. Una fila rara no se aplica."""
    if not isinstance(fila, dict) or not str(fila.get("id") or "").strip():
        return "fila sin id"
    if fila.get("action") not in ACCIONES:
        return f"acción desconocida ({str(fila.get('action'))[:20]})"
    if fila.get("destination") not in DESTINOS:
        return f"destino desconocido ({str(fila.get('destination'))[:20]})"
    if not (_slug(fila.get("person_key")) or _slug(fila.get("name")) or _uid(fila.get("telegram_user_id"))):
        return "sin persona identificable"
    return None


# --- people.yaml (edición de texto: conserva los comentarios del fichero) -----------------------------------------

_CLAVE = re.compile(r"^  ([A-Za-z0-9_]+):\s*(#.*)?$")


def _bloques(lineas: list[str]) -> dict[str, tuple[int, int]]:
    """clave -> (inicio, fin) del bloque `  clave:` dentro de `people:` (fin exclusivo; excluye comentarios finales
    de la sección siguiente)."""
    inicio = next((i for i, l in enumerate(lineas) if l.rstrip() == "people:"), None)
    if inicio is None:
        return {}
    marcas = [(i, m.group(1)) for i, l in enumerate(lineas) if i > inicio and (m := _CLAVE.match(l.rstrip("\r\n")))]
    out = {}
    for n, (i, clave) in enumerate(marcas):
        fin = marcas[n + 1][0] if n + 1 < len(marcas) else len(lineas)
        while fin > i + 1 and not lineas[fin - 1].strip():
            fin -= 1
        out[clave] = (i, fin)
    return out


def _uid_de(lineas: list[str], ini: int, fin: int) -> str | None:
    for l in lineas[ini:fin]:
        m = re.match(r"^\s+user_id:\s*['\"]?(\d+)['\"]?", l)
        if m:
            return m.group(1)
    return None


def _buscar(lineas, bloques, fila) -> str | None:
    uid = _uid(fila.get("telegram_user_id"))
    for clave, (i, f) in bloques.items():
        if uid and _uid_de(lineas, i, f) == uid:
            return clave
    for cand in (_slug(fila.get("person_key")), _slug(fila.get("name"))):
        if cand and cand in bloques:
            return cand
    return None


def _fijar_linea(lineas, ini, fin, campo, valor, nota) -> int:
    """Pone `    campo: valor   # nota` (reemplaza la línea o la inserta tras user_id). Devuelve el cambio de nº de líneas."""
    nueva = f"    {campo}: {valor}   # {nota}\n"
    for k in range(ini + 1, fin):
        if re.match(rf"^\s+{campo}:", lineas[k]):
            lineas[k] = nueva
            return 0
    pos = next((k + 1 for k in range(ini + 1, fin) if re.match(r"^\s+user_id:", lineas[k])), ini + 1)
    lineas.insert(pos, nueva)
    return 1


def _quitar_linea(lineas, ini, fin, campo) -> int:
    for k in range(ini + 1, fin):
        if re.match(rf"^\s+{campo}:", lineas[k]):
            del lineas[k]
            return -1
    return 0


def aplicar_people(texto: str, filas: list[dict]) -> tuple[str, list[str], list[str]]:
    """Aplica las filas de destino people_yaml. Devuelve (texto nuevo, ids aplicados, avisos de revisión manual)."""
    eol = "\r\n" if "\r\n" in texto else "\n"
    lineas = texto.replace("\r\n", "\n").splitlines(keepends=True)
    aplicados, manual = [], []
    for fila in filas:
        fid, accion = str(fila["id"]), fila["action"]
        nota = f"Oscar HQ {fid}"
        bloques = _bloques(lineas)
        clave = _buscar(lineas, bloques, fila)
        uid = _uid(fila.get("telegram_user_id"))
        if clave is None:
            rol = str(fila.get("role") or "").lower()
            nombre = str(fila.get("name") or "").strip()
            slug = _slug(fila.get("person_key")) or _slug(nombre)
            if accion == "link" and uid and slug and rol in ROLES:
                marcas = [str(b) for b in (fila.get("brands") or ()) if str(b).strip()]
                nuevo = (f"  {slug}:\n    name: {json.dumps(nombre or slug.capitalize(), ensure_ascii=False)}\n"
                         f"    user_id: \"{uid}\"   # {nota}\n    role: {rol}\n"
                         f"    brands: {json.dumps(marcas, ensure_ascii=False)}\n    can: []\n")
                if lineas and not lineas[-1].endswith("\n"):
                    lineas[-1] += "\n"
                lineas.append(nuevo)
                aplicados.append(fid)
            elif accion in ("link", "reactivate"):
                manual.append(f"{fid}: {accion} de «{nombre or slug or uid}» sin entrada en people.yaml ni rol válido; "
                              "darla de alta a mano (skill alta-persona-equipo)")
            else:
                aplicados.append(fid)  # nada que quitar: la persona ya no figura, la fila se puede resolver
            continue
        ini, fin = bloques[clave]
        if accion == "link":
            if not uid:
                manual.append(f"{fid}: link de {clave} sin telegram_user_id")
                continue
            fin += _fijar_linea(lineas, ini, fin, "user_id", f'"{uid}"', nota)
            _quitar_linea(lineas, ini, fin, "suspendido")
        elif accion == "suspend":
            _fijar_linea(lineas, ini, fin, "suspendido", "true", nota)
        elif accion == "reactivate":
            _quitar_linea(lineas, ini, fin, "suspendido")
        elif accion == "unlink":
            fin += _fijar_linea(lineas, ini, fin, "user_id", "null", nota)
            _quitar_linea(lineas, ini, fin, "suspendido")
        elif accion == "delete":
            del lineas[ini:fin]
        aplicados.append(fid)
    return "".join(lineas).replace("\n", eol), aplicados, manual


# --- TELEGRAM_ALLOWED_CHATS y Hermes (solo se proponen) -----------------------------------------------------------

def aplicar_env(valor_actual: str, filas: list[dict]) -> tuple[str, list[str], list[str]]:
    ids = [c.strip() for c in valor_actual.split(",") if c.strip()]
    aplicados, manual = [], []
    for fila in filas:
        uid = _uid(fila.get("telegram_user_id"))
        if not uid:
            manual.append(f"{fila['id']}: {fila['action']} sin telegram_user_id; no se puede tocar {ENV_VAR}")
            continue
        if fila["action"] in DA_ACCESO:
            if uid not in ids:
                ids.append(uid)
        else:
            ids = [c for c in ids if c != uid]
        aplicados.append(str(fila["id"]))
    return ",".join(ids), aplicados, manual


def _leer_env(texto: str, var: str = ENV_VAR) -> str:
    for l in texto.splitlines():
        if l.startswith(var + "="):
            return l.split("=", 1)[1].strip().strip("'\"")
    return ""


def listar_hermes(filas: list[dict]) -> list[str]:
    verbos = {"link": "AÑADIR a la allowlist de Hermes", "reactivate": "AÑADIR a la allowlist de Hermes",
              "suspend": "QUITAR de la allowlist de Hermes", "unlink": "QUITAR de la allowlist de Hermes",
              "delete": "QUITAR de la allowlist de Hermes"}
    return [f"{f['id']}: {verbos[f['action']]} el Telegram {_uid(f.get('telegram_user_id')) or '(sin id)'} "
            f"({f.get('name') or f.get('person_key') or '?'})" for f in filas]


def planificar(filas: list, people_text: str, env_text: str = "") -> Plan:
    plan = Plan()
    buenas = []
    for f in filas:
        motivo = _valida(f)
        if motivo:
            plan.manual.append(f"{(f.get('id') if isinstance(f, dict) else None) or '?'}: {motivo}")
            if isinstance(f, dict) and f.get("id"):
                plan.ids_manual.append(str(f["id"]))
        else:
            buenas.append(f)
    por = {d: [f for f in buenas if f["destination"] == d] for d in DESTINOS}
    if por["people_yaml"]:
        nuevo, ok, man = aplicar_people(people_text, por["people_yaml"])
        plan.manual += man
        plan.filas["people_yaml"] = ok
        plan.people_text = nuevo if nuevo != people_text else None
    if por["carriles_env"]:
        valor, ok, man = aplicar_env(_leer_env(env_text), por["carriles_env"])
        plan.manual += man
        plan.filas["carriles_env"] = ok
        plan.env_line = f"{ENV_VAR}={valor}"
    if por["hermes"]:
        plan.hermes = listar_hermes(por["hermes"])
        plan.filas["hermes"] = [str(f["id"]) for f in por["hermes"]]
    return plan


def informe(plan: Plan) -> str:
    out = ["# Sincronización de usuarios de Oscar HQ"]
    if plan.people_text is not None:
        out.append("\n## people.yaml\nCambio listo (con --escribir queda en el checkout para el PR). Filas: "
                   + ", ".join(plan.filas.get("people_yaml", [])))
    if plan.env_line:
        out.append("\n## tools/agent-lanes/.env (lo aplica el integrador a mano; el hub NO edita .env)\n"
                   f"`{plan.env_line}`\nFilas: " + ", ".join(plan.filas.get("carriles_env", [])))
    if plan.hermes:
        out.append("\n## Hermes (lo aplica el integrador con OK de Oscar; el carril nunca toca ~/.hermes)")
        out += [f"- {h}" for h in plan.hermes]
    if plan.manual:
        out.append("\n## Revisión manual (no se aplica nada de esto)")
        out += [f"- {m}" for m in plan.manual]
    if len(out) == 1:
        out.append("\nNada pendiente.")
    return "\n".join(out) + "\n"


# --- API de Oscar HQ ---------------------------------------------------------------------------------------------

def _http_real(metodo: str, url: str, clave: str, cuerpo: dict | None = None):
    req = urllib.request.Request(url, method=metodo, headers={"X-API-Key": clave, "Content-Type": "application/json"},
                                 data=json.dumps(cuerpo).encode() if cuerpo is not None else None)
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (URL de configuración, no de usuario)
        raw = r.read()
    return json.loads(raw) if raw else {}


class ApiOhq:
    """Cliente mínimo. `http(metodo, url, clave, cuerpo)` es inyectable para los tests."""

    def __init__(self, base: str, clave: str, http=_http_real):
        self.base, self.clave, self.http = base.rstrip("/"), clave, http

    @classmethod
    def desde_entorno(cls, env=None, http=_http_real) -> "ApiOhq":
        env = os.environ if env is None else env
        base, clave = env.get("OHQ_API_URL", ""), env.get("OHQ_API_KEY", "")
        if not base or not clave:
            raise SystemExit("faltan OHQ_API_URL y/o OHQ_API_KEY en el entorno")
        return cls(base, clave, http)

    def pendientes(self) -> list:
        data = self.http("GET", f"{self.base}/api/admin/user-external-changes?status=pending", self.clave, None)
        filas = data.get("items", data) if isinstance(data, dict) else data
        return filas if isinstance(filas, list) else []

    def resolver(self, fila_id: str) -> None:
        self.http("POST", f"{self.base}/api/admin/user-external-changes/{fila_id}/resolve", self.clave, {})


def resolver_filas(api: ApiOhq, ids: list[str]) -> tuple[list[str], list[str]]:
    """Marca como resueltas; un fallo no corta el resto. Devuelve (resueltas, fallidas)."""
    ok, mal = [], []
    for i in ids:
        try:
            api.resolver(i)
            ok.append(i)
        except Exception as exc:  # red/HTTP: se reintenta en la próxima ejecución
            mal.append(f"{i} ({type(exc).__name__})")
    return ok, mal


def main(argv=None, api: ApiOhq | None = None, people_file: Path = PEOPLE_FILE, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(prog="sync_usuarios", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="informe del cambio exacto")
    p.add_argument("--desde-fichero", help="JSON con las filas (en vez de la API)")
    p.add_argument("--env-file", help="copia legible de tools/agent-lanes/.env (solo lectura)")
    p.add_argument("--escribir", action="store_true", help="aplica el cambio de people.yaml en este checkout")
    r = sub.add_parser("resolver", help="marca filas como resueltas (tras aplicar y con OK de Oscar)")
    r.add_argument("ids", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "resolver":
        api = api or ApiOhq.desde_entorno()
        ok, mal = resolver_filas(api, a.ids)
        print(f"resueltas: {', '.join(ok) or '-'}" + (f"\nfallidas: {', '.join(mal)}" if mal else ""), file=out)
        return 1 if mal else 0
    if a.desde_fichero:
        filas = json.loads(Path(a.desde_fichero).read_text(encoding="utf-8"))
    else:
        filas = (api or ApiOhq.desde_entorno()).pendientes()
    env_text = Path(a.env_file).read_text(encoding="utf-8") if a.env_file else ""
    plan = planificar(filas, Path(people_file).read_text(encoding="utf-8"), env_text)
    if a.escribir and plan.people_text is not None:
        Path(people_file).write_text(plan.people_text, encoding="utf-8", newline="")
    print(informe(plan), file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
