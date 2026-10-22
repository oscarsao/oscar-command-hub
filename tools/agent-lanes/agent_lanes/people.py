"""Personas del equipo con acceso por DM al bot de carriles (tools/telegram/people.yaml).

Falla CERRADO: fichero ausente o roto, entradas sin user_id (null), roles desconocidos, ids duplicados o que
coinciden con el de Oscar -> la persona simplemente no existe (recibe el rechazo de "no conocida"). Oscar no se
declara en el YAML: su id es OWNER_TELEGRAM_ID (decisions.py), que se pasa a `load_people`.

Las capacidades por rol son CONSTANTES de código. El YAML solo puede activar `carril` y solo para `coo`; la
capacidad `produccion` es exclusiva del owner y no sale nunca del YAML. Aparte, `operador: true` (booleano
estricto) da a la persona el MISMO acceso que Oscar al bot de carriles (comandos, botones y aprobaciones; decisión de
Oscar 19-10): se evalúa con `People.es_operador`, no con las capacidades.

Un operador solo actúa en las marcas de su `brands` (`People.puede_en`): `"*"` = todas (María). Sin `"*"`, con una
marca distinta o con un destino sin marca conocida se rechaza (falla cerrado). Oscar siempre puede.
"""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("agent_lanes")

PEOPLE_FILE = Path(__file__).resolve().parents[2] / "telegram" / "people.yaml"

CONSULTA, ESTADO, IDEA, CONTENIDO, CARRIL, PRODUCCION = "consulta", "estado", "idea", "contenido", "carril", "produccion"
APROBAR_PRODUCCION = "aprobar_produccion"
PERMISOS_YAML = frozenset({APROBAR_PRODUCCION})  # lo único que `permisos:` del YAML puede conceder
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
    operador: bool = False  # people.yaml `operador: true`: mismo acceso que Oscar en el bot (comandos, botones, aprobaciones)
    permisos: frozenset = field(default_factory=frozenset)  # people.yaml `permisos:` (solo los de PERMISOS_YAML)

    def can(self, cap: str) -> bool:
        return cap in self.caps

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


class People:
    def __init__(self, owner_id: str, persons: list[Person] | None = None):
        self.owner_id = str(owner_id)
        self._by_id = {p.user_id: p for p in persons or ()}
        self.owner = Person("oscar", "Oscar", self.owner_id, "owner", (), BASE_CAPS["owner"])

    def lookup(self, user_id) -> Person | None:
        """Persona por user_id de Telegram (int o str). None = desconocida."""
        uid = str(user_id)
        if uid == self.owner_id:
            return self.owner
        return self._by_id.get(uid)

    def es_operador(self, user_id) -> bool:
        """Oscar (owner) o una persona de people.yaml con `operador: true`. Única fuente de verdad del acceso al bot."""
        person = self.lookup(user_id)
        return bool(person and (person.is_owner or person.operador))

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

    def puede_aprobar_produccion(self, user_id) -> bool:
        """Oscar (owner) o quien tenga `permisos: [aprobar_produccion]`, en cualquier marca (decisión de Oscar 22-10).
        Es aparte de `puede_en`: no depende de `brands`. Quien no es operador no aprueba aunque figure el permiso."""
        person = self.lookup(user_id)
        if not person:
            return False
        return person.is_owner or (person.operador and APROBAR_PRODUCCION in person.permisos)

    def collaborator_ids(self) -> set[str]:
        return set(self._by_id)

    def __len__(self) -> int:
        return len(self._by_id)


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


def load_people(owner_id: str, path: Path | str | None = None) -> People:
    p = Path(path) if path else PEOPLE_FILE
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        entries = data.get("people") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            raise ValueError("falta el mapa `people:`")
    except Exception as exc:  # fallo cerrado: nadie más que Oscar
        log.warning("people.yaml no se pudo leer (%s); solo Oscar tiene acceso", type(exc).__name__)
        return People(owner_id)
    persons: list[Person] = []
    seen = {str(owner_id)}
    for key, cfg in entries.items():
        if not isinstance(cfg, dict):
            continue
        uid = _user_id(cfg.get("user_id"))
        role = str(cfg.get("role") or "").strip().lower()
        if uid is None:
            continue  # TODO sin rellenar: la entrada no existe todavía
        if role not in BASE_CAPS or role == "owner":
            log.warning("people.yaml: rol no válido para %s; ignorada", _clean(key))
            continue
        if uid in seen:
            log.warning("people.yaml: user_id repetido o igual al de Oscar (%s); ignorada", _clean(key))
            continue
        seen.add(uid)
        asked = cfg.get("can") if isinstance(cfg.get("can"), list) else []
        caps = BASE_CAPS[role] | (frozenset(str(c) for c in asked) & OPTIONAL_CAPS.get(role, frozenset()))
        brands = tuple(_clean(b) for b in (cfg.get("brands") or ()) if _clean(b)) if isinstance(
            cfg.get("brands"), (list, tuple)) else ()
        pedidos = cfg.get("permisos") if isinstance(cfg.get("permisos"), list) else []
        permisos = frozenset(str(x) for x in pedidos) & PERMISOS_YAML
        persons.append(Person(_clean(key), _clean(cfg.get("name") or key) or _clean(key), uid, role, brands, caps,
                              cfg.get("operador") is True, permisos))
    return People(owner_id, persons)
