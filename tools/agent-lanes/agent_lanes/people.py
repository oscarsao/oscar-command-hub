"""Personas del equipo con acceso por DM al bot de carriles.

El people.yaml REAL no está en el repo (es público y lleva ids de Telegram de personas): se lee de la variable de
entorno AGENT_LANES_PEOPLE_FILE o, si no existe, de tools/agent-lanes/.state/people.yaml. En el repo solo hay
tools/telegram/people.example.yaml con ids ficticios.

Falla CERRADO: fichero ausente o roto, entradas sin user_id (null), roles desconocidos, ids duplicados o que
coinciden con el de Oscar -> la persona simplemente no existe (recibe el rechazo de "no conocida"). Oscar no se
declara en el YAML: su id es OWNER_TELEGRAM_ID (decisions.py), que se pasa a `load_people`.

Las capacidades por rol son CONSTANTES de código. El YAML solo puede activar `carril` y solo para `coo`; la
capacidad `produccion` es exclusiva del owner y no sale nunca del YAML.

Quién aprueba, integra y despliega NO está en el YAML (decisión de Oscar 28-10): se configura en el panel de Oscar HQ y
se lee de GET /api/equipo/permisos (X-API-Key de servicio, OHQ_API_KEY), con caché de 60 s. Respuesta esperada:
  {"personas": [{"telegram_id": "123", "permisos": {"MigraTeam": ["desarrollo.aprobar", "desarrollo.publicar"]}}]}
(la marca "*" = todas). `desarrollo.aprobar` de una marca = operar y aprobar en ella (`People.es_operador`,
`People.puede_en`); `desarrollo.publicar` = fusionar y desplegar (`People.puede_aprobar_produccion`). Falla CERRADO:
si HQ no responde, no hay clave o la persona no está enlazada, solo actúa Oscar (OWNER_TELEGRAM_ID). Oscar siempre puede.
"""
from __future__ import annotations

import json
import logging
import os
import time
import unicodedata
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

log = logging.getLogger("agent_lanes")

PEOPLE_ENV = "AGENT_LANES_PEOPLE_FILE"
PEOPLE_EXAMPLE = Path(__file__).resolve().parents[2] / "telegram" / "people.example.yaml"  # ids ficticios, versionado
PEOPLE_DEFAULT = Path(__file__).resolve().parents[1] / ".state" / "people.yaml"  # el real; .state/ no se versiona


def people_file() -> Path:
    """Ruta del people.yaml real: AGENT_LANES_PEOPLE_FILE si está definida; si no, .state/people.yaml."""
    raw = os.environ.get(PEOPLE_ENV, "").strip()
    return Path(raw) if raw else PEOPLE_DEFAULT

CONSULTA, ESTADO, IDEA, CONTENIDO, CARRIL, PRODUCCION = "consulta", "estado", "idea", "contenido", "carril", "produccion"
APROBAR_PRODUCCION = "aprobar_produccion"
PERM_APROBAR, PERM_PUBLICAR = "desarrollo.aprobar", "desarrollo.publicar"  # permisos de Oscar HQ (panel de equipo)
HQ_CACHE_S = 60
TODAS_LAS_MARCAS = "*"
_ALIAS_MARCA = {"oscarhq": "pildora"}  # OscarHQ es la marca Píldora para el control de acceso


def norm_marca(marca) -> str:
    """Marca comparable: sin tildes ni mayúsculas ('Píldora' == 'pildora' == 'OscarHQ')."""
    base = "".join(c for c in unicodedata.normalize("NFD", str(marca or "")) if not unicodedata.combining(c))
    base = base.strip().casefold()
    return _ALIAS_MARCA.get(base, base)


BASE_CAPS = {
    "owner": frozenset({CONSULTA, ESTADO, IDEA, CONTENIDO, CARRIL, PRODUCCION}),
    "coo": frozenset({CONSULTA, ESTADO, IDEA, CONTENIDO}),
    "cmo": frozenset({CONSULTA, ESTADO, IDEA, CONTENIDO}),
    "marketing": frozenset({CONSULTA, ESTADO, IDEA, CONTENIDO}),
    "dev": frozenset({CONSULTA, ESTADO, IDEA, CONTENIDO}),  # soporte/desarrollo de una marca; sin carril ni produccion
}
OPTIONAL_CAPS = {"coo": frozenset({CARRIL})}  # lo único que `can:` del YAML puede añadir, y a quién


@dataclass(frozen=True)
class Person:
    key: str
    name: str
    user_id: str
    role: str
    brands: tuple = ()
    caps: frozenset = field(default_factory=frozenset)
    operador: bool = False  # lo rellena People.lookup desde Oscar HQ (algún desarrollo.aprobar); no viene del YAML
    permisos: frozenset = field(default_factory=frozenset)  # idem: {APROBAR_PRODUCCION} si tiene algún desarrollo.publicar
    hermes: bool = True  # people.yaml `hermes: false` (estricto): Hermes no le contesta; el bot de carriles le avisa
    solo_menciones: str = ""  # people.yaml `solo_menciones: true` + `mencion: "@usuario"`: solo recibe avisos que la mencionan

    def can(self, cap: str) -> bool:
        return cap in self.caps

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def _http_hq(url: str, clave: str):
    req = urllib.request.Request(url, headers={"X-API-Key": clave})
    with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 (URL de configuración, no de usuario)
        return json.loads(r.read() or b"{}")


def hq_desde_entorno(env=None, http=_http_hq):
    """Proveedor de permisos de Oscar HQ: callable sin argumentos -> JSON de /api/equipo/permisos. Sin OHQ_API_URL u
    OHQ_API_KEY devuelve None (fallo cerrado). La clave solo se lee del entorno; nunca se escribe."""
    def _leer():
        from .config import load_env, ohq_api_url
        e = {**load_env(), **os.environ} if env is None else env
        base, clave = ohq_api_url(e), str(e.get("OHQ_API_KEY") or "")
        if not base or not clave:
            return None
        return http(base.rstrip("/") + "/api/equipo/permisos", clave)
    return _leer


def _parsear_hq(data) -> dict:
    """{user_id: {marca: frozenset(permisos)}}. Cualquier forma inesperada se descarta (fallo cerrado)."""
    out: dict = {}
    personas = data.get("personas") if isinstance(data, dict) else None
    for ent in personas if isinstance(personas, list) else ():
        if not isinstance(ent, dict):
            continue
        uid, perms = _user_id(ent.get("telegram_id")), ent.get("permisos")
        if uid is None or not isinstance(perms, dict):
            continue
        out[uid] = {str(m): frozenset(str(x) for x in v) for m, v in perms.items() if isinstance(v, list)}
    return out


class People:
    def __init__(self, owner_id: str, persons: list[Person] | None = None, hq=None, reloj=time.monotonic):
        """`hq`: proveedor de permisos de Oscar HQ (`hq_desde_entorno`). Con `hq`, brands/operador/permisos de cada
        Person salen SIEMPRE de HQ; sin `hq` (solo uso directo en tests) valen los de la propia Person."""
        self.owner_id = str(owner_id)
        self._base = {p.user_id: p for p in persons or ()}  # identidades del YAML, sin permisos
        self.owner = Person("oscar", "Oscar", self.owner_id, "owner", (), BASE_CAPS["owner"])
        self._hq, self._reloj = hq, reloj
        self._snap: dict = {}
        self._snap_t: float | None = None

    def _permisos_hq(self, user_id) -> dict:
        """Permisos de HQ de una persona {marca: frozenset}, con caché de 60 s. HQ caído o persona sin enlazar = {}."""
        ahora = self._reloj()
        if self._snap_t is None or ahora - self._snap_t >= HQ_CACHE_S:
            try:
                self._snap = _parsear_hq(self._hq())
            except Exception as exc:  # fallo cerrado: nadie más que Oscar hasta la siguiente lectura
                log.warning("Oscar HQ no dio los permisos del equipo (%s); solo Oscar actúa", type(exc).__name__)
                self._snap = {}
            self._snap_t = ahora
        return self._snap.get(str(user_id), {})

    def _con_permisos(self, person: Person) -> Person:
        if self._hq is None:
            return person
        perms = self._permisos_hq(person.user_id)
        aprobar = tuple(m for m, v in perms.items() if PERM_APROBAR in v)
        publica = any(PERM_PUBLICAR in v for v in perms.values())
        return replace(person, brands=aprobar, operador=bool(aprobar),
                       permisos=frozenset({APROBAR_PRODUCCION}) if publica else frozenset())

    @property
    def _by_id(self) -> dict:
        """Las personas del YAML con los permisos vigentes de HQ (lo que ven quienes recorren el equipo)."""
        return {uid: self._con_permisos(p) for uid, p in self._base.items()}

    def lookup(self, user_id) -> Person | None:
        """Persona por user_id de Telegram (int o str). None = desconocida."""
        uid = str(user_id)
        if uid == self.owner_id:
            return self.owner
        base = self._base.get(uid)
        return self._con_permisos(base) if base else None

    def es_operador(self, user_id) -> bool:
        """Oscar (owner) o una persona de people.yaml con `operador: true`. Única fuente de verdad del acceso al bot."""
        person = self.lookup(user_id)
        return bool(person and (person.is_owner or person.operador))

    def sin_hermes(self, user_id) -> bool:
        """Persona de people.yaml con `hermes: false`. Oscar y los desconocidos (no están en el YAML) nunca."""
        person = self.lookup(user_id)
        return bool(person and not person.is_owner and not person.hermes)

    def filtros_menciones(self) -> dict[str, str]:
        """{user_id: "@usuario"} de quien pidió `solo_menciones: true`. Sin `mencion` válida no hay filtro (por defecto,
        todo como hoy: no se silencia a nadie por un YAML a medias)."""
        return {p.user_id: p.solo_menciones for p in self._by_id.values() if p.solo_menciones}

    def restringido(self, user_id) -> bool:
        """True si es un operador acotado por marca (ni Oscar ni quien tenga `"*"`)."""
        person = self.lookup(user_id)
        if not person or person.is_owner:
            return False
        return TODAS_LAS_MARCAS not in person.brands

    def puede_en(self, user_id, marca) -> bool:
        """Oscar y quien tenga `"*"`: cualquier marca. Resto de operadores: solo las de su `brands`; `marca` None
        (destino sin marca conocida) solo la admiten los de todas las marcas. Quien no es operador no puede nada."""
        if not self.es_operador(user_id):
            return False
        if not self.restringido(user_id):
            return True
        wanted = norm_marca(marca)
        return bool(wanted) and wanted in {norm_marca(b) for b in self.lookup(user_id).brands}

    def puede_aprobar_produccion(self, user_id, marca=None) -> bool:
        """Fusionar y desplegar: Oscar o quien tenga `desarrollo.publicar` en Oscar HQ (en la `marca` dada o, sin ella,
        en alguna; "*" vale para todas). Además debe ser operador (`desarrollo.aprobar`)."""
        person = self.lookup(user_id)
        if not person:
            return False
        if person.is_owner:
            return True
        if not person.operador:
            return False
        if self._hq is None or marca is None:
            return APROBAR_PRODUCCION in person.permisos
        quiero = norm_marca(marca)
        return any(PERM_PUBLICAR in v and (m == TODAS_LAS_MARCAS or norm_marca(m) == quiero)
                   for m, v in self._permisos_hq(user_id).items())

    def operadores_todas_marcas(self) -> set[str]:
        """Operadores (no Oscar) con `"*"` (María): su DM no pasa por el filtro de colaboradores (F1-09, D4-A) y
        ven los avisos y botones completos, como Oscar. Los acotados por marca siguen filtrados: el aviso no siempre
        dice a qué marca apunta y el filtro falla cerrado."""
        return {p.user_id for p in self._by_id.values() if p.operador and TODAS_LAS_MARCAS in p.brands}

    def collaborator_ids(self) -> set[str]:
        return set(self._base)

    def __len__(self) -> int:
        return len(self._base)


def _clean(value, limit: int = 40) -> str:
    return "".join(ch for ch in str(value or "") if ch.isprintable()).strip()[:limit]


def _user_id(raw) -> str | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return str(raw) if raw > 0 else None
    if isinstance(raw, str) and raw.strip().isdigit() and int(raw.strip()) > 0:
        return str(int(raw.strip()))
    return None


def load_people(owner_id: str, path: Path | str | None = None, hq=None) -> People:
    """Identidades del YAML + permisos de Oscar HQ (`hq`; por defecto el del entorno, ver `hq_desde_entorno`)."""
    hq = hq or hq_desde_entorno()
    p = Path(path) if path else people_file()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        entries = data.get("people") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            raise ValueError("falta el mapa `people:`")
    except Exception as exc:  # fallo cerrado: nadie más que Oscar
        log.warning("people.yaml no se pudo leer en %s (%s); solo Oscar tiene acceso", p, type(exc).__name__)
        return People(owner_id, hq=hq)
    persons: list[Person] = []
    seen = {str(owner_id)}
    for key, cfg in entries.items():
        if not isinstance(cfg, dict):
            continue
        uid = _user_id(cfg.get("user_id"))
        role = str(cfg.get("role") or "").strip().lower()
        if uid is None:
            continue  # TODO sin rellenar: la entrada no existe todavía
        if cfg.get("suspendido") is True:
            continue  # suspendida desde Oscar HQ (sync_usuarios): sin acceso hasta que se reactive
        if role not in BASE_CAPS or role == "owner":
            log.warning("people.yaml: rol no válido para %s; ignorada", _clean(key))
            continue
        if uid in seen:
            log.warning("people.yaml: user_id repetido o igual al de Oscar (%s); ignorada", _clean(key))
            continue
        seen.add(uid)
        asked = cfg.get("can") if isinstance(cfg.get("can"), list) else []
        caps = BASE_CAPS[role] | (frozenset(str(c) for c in asked) & OPTIONAL_CAPS.get(role, frozenset()))
<<<<<<< HEAD
        persons.append(Person(_clean(key), _clean(cfg.get("name") or key) or _clean(key), uid, role, (), caps,
                              hermes=cfg.get("hermes") is not False))
    return People(owner_id, persons, hq=hq)
=======
        brands = tuple(_clean(b) for b in (cfg.get("brands") or ()) if _clean(b)) if isinstance(
            cfg.get("brands"), (list, tuple)) else ()
        pedidos = cfg.get("permisos") if isinstance(cfg.get("permisos"), list) else []
        permisos = frozenset(str(x) for x in pedidos) & PERMISOS_YAML
        mencion = _clean(cfg.get("mencion")).lstrip("@")
        solo = f"@{mencion}" if cfg.get("solo_menciones") is True and mencion else ""
        persons.append(Person(_clean(key), _clean(cfg.get("name") or key) or _clean(key), uid, role, brands, caps,
                              cfg.get("operador") is True, permisos, cfg.get("hermes") is not False, solo))
    return People(owner_id, persons)
>>>>>>> origin/main
