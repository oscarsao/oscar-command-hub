"""Lecciones con id (autoaprendizaje del hub, t_0f1d02ce): `docs/lecciones/L-NNNN.md` = un hecho comprobado + la regla que
se deriva. Aquí: carga tolerante, resumen corto para el prompt de los workers y contador de disparos.

- Frontmatter (YAML entre `---`): id (L-0001), fecha, origen (tarjeta/incidente), regla (dónde vive: «bloqueos.yaml: …»),
  estado (activa | retirada | propuesta). Opcional: carriles (lista; sin ella vale para todos los carriles).
- Una lección con frontmatter inválido se ignora con un aviso en el log (nunca rompe el prompt).
- El contador de disparos vive en `.state/lecciones/disparos.json` (NO en git): {id: {n, ultimo}}. Una regla que cita su
  lección (`leccion: L-0001` en bloqueos.yaml) suma un disparo cada vez que casa. Lo que lleve N días sin dispararse se
  PROPONE retirar (nunca se retira solo).
- Sin secretos ni datos de personas: el resumen solo lleva id + título + regla.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import ROOT

log = logging.getLogger("agent_lanes")

LECCIONES_DIR = ROOT.parents[1] / "docs" / "lecciones"
STATE_DIR = ROOT / ".state" / "lecciones"
ID_RE = re.compile(r"^L-\d{4}$")
ESTADOS = ("activa", "retirada", "propuesta")
FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.S)


@dataclass(frozen=True)
class Leccion:
    id: str
    fecha: str
    origen: str
    regla: str
    estado: str
    titulo: str = ""
    carriles: tuple = field(default_factory=tuple)


def parse(path: Path) -> Leccion | None:
    """Una lección o None (con aviso en el log) si el fichero no cumple el formato."""
    try:
        m = FRONT_RE.match(Path(path).read_text(encoding="utf-8"))
        if not m:
            raise ValueError("sin frontmatter")
        meta = yaml.safe_load(m.group(1))
        if not isinstance(meta, dict):
            raise ValueError("frontmatter no es un mapa")
        lid, estado = str(meta.get("id") or ""), str(meta.get("estado") or "")
        if not ID_RE.match(lid) or estado not in ESTADOS:
            raise ValueError(f"id {lid!r} o estado {estado!r} inválidos")
        if not all(str(meta.get(k) or "").strip() for k in ("fecha", "origen", "regla")):
            raise ValueError("faltan fecha, origen o regla")
        carriles = meta.get("carriles") or ()
        if not isinstance(carriles, (list, tuple)):
            raise ValueError("carriles debe ser una lista")
        titulo = next((ln.lstrip("# ").strip() for ln in m.group(2).splitlines() if ln.strip()), "")
        return Leccion(lid, str(meta["fecha"]), str(meta["origen"]).strip(), str(meta["regla"]).strip(), estado,
                       titulo, tuple(str(c) for c in carriles))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        log.warning("lección %s ignorada: %s", Path(path).name, exc)
        return None


def cargar(directorio: Path | None = None) -> list[Leccion]:
    """Todas las lecciones válidas, por id. Nunca lanza; los ids duplicados se ignoran (gana la primera)."""
    d = Path(directorio or LECCIONES_DIR)
    out: dict[str, Leccion] = {}
    for p in sorted(d.glob("L-*.md")):
        les = parse(p)
        if les and les.id in out:
            log.warning("lección %s ignorada: id %s duplicado", p.name, les.id)
        elif les:
            out[les.id] = les
    return list(out.values())


def resumen(carril: str = "", directorio: Path | None = None, max_chars: int | None = None) -> str:
    """Sección del prompt del worker con las lecciones activas (una línea cada una). Vacía si no hay: nada cambia."""
    if max_chars is None:
        from . import ajustes_hub
        max_chars = int(ajustes_hub.valor("lecciones.resumen_max"))
    activas = [l for l in cargar(directorio) if l.estado == "activa" and (not l.carriles or carril in l.carriles)]
    if not activas:
        return ""
    head = "\n## Lecciones del hub (hechos comprobados; respétalas)\n"
    body = ""
    for l in activas:
        ln = f"- {l.id}: {l.titulo or l.regla} → {l.regla}\n"
        if len(head) + len(body) + len(ln) > max_chars:
            body += f"- … y {len(activas) - body.count(chr(10))} más en docs/lecciones/\n"
            break
        body += ln
    return head + body


def section_for(carril: str = "", directorio: Path | None = None) -> str:
    """Enganche de worker.build_prompt: nunca falla ni bloquea el prompt."""
    try:
        return resumen(carril, directorio)
    except Exception as exc:  # noqa: BLE001
        log.warning("lecciones no cargadas: %s", type(exc).__name__)
        return ""


# --- contador de disparos (en .state, no en git) ----------------------------------------------------------------------

def _leer(state_dir: Path) -> dict:
    try:
        data = json.loads((Path(state_dir) / "disparos.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def registrar_disparo(leccion_id: str, *, now: float | None = None, state_dir: Path | None = None) -> None:
    """Suma un disparo a la lección. Nunca lanza (un fallo de disco no puede romper el aviso de un bloqueo)."""
    if not ID_RE.match(str(leccion_id or "")):
        return
    d = Path(state_dir or STATE_DIR)
    try:
        data = _leer(d)
        cur = data.get(leccion_id) if isinstance(data.get(leccion_id), dict) else {}
        data[leccion_id] = {"n": int(cur.get("n") or 0) + 1, "ultimo": now if now is not None else time.time()}
        d.mkdir(parents=True, exist_ok=True)
        (d / "disparos.json").write_text(json.dumps(data, indent=1), encoding="utf-8", newline="\n")
    except OSError as exc:
        log.warning("disparo de %s sin guardar: %s", leccion_id, exc)


def disparos(state_dir: Path | None = None) -> dict:
    return _leer(Path(state_dir or STATE_DIR))


def retirables(now: float, dias: int, directorio: Path | None = None, state_dir: Path | None = None) -> list[Leccion]:
    """Lecciones activas sin disparo en `dias` días (o nunca disparadas y con más de `dias` días de antigüedad)."""
    from datetime import datetime
    cont, out = disparos(state_dir), []
    for l in cargar(directorio):
        if l.estado != "activa":
            continue
        ultimo = (cont.get(l.id) or {}).get("ultimo")
        if ultimo is None:
            try:
                ultimo = datetime.strptime(str(l.fecha)[:10], "%Y-%m-%d").timestamp()
            except ValueError:
                continue
        if now - float(ultimo) > dias * 86400:
            out.append(l)
    return out
