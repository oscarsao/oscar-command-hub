"""Botonera estándar de las fichas de tarjeta (F1-14): catálogo en `botonera.yaml`, selección por estado y diagnóstico.

Funciones puras (sin red, sin Hermes). Lo que una ficha concreta ya ofrece se completa con `completar`, de modo que
ningún estado se quede sin salida: Responder, Integrador, Dar por hecha y Archivar están siempre; Reintentar solo si
el diagnóstico dice que puede servir (bloqueo transitorio y no repetido)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BOTONERA_PATH = ROOT / "botonera.yaml"
ESTRELLA = "⭐ "


def cargar(path: Path | str | None = None) -> dict:
    raw = yaml.safe_load(Path(path or BOTONERA_PATH).read_text(encoding="utf-8")) or {}
    for clave in ("botones", "orden", "estados", "obligatorios"):
        if clave not in raw:
            raise ValueError(f"botonera.yaml: falta «{clave}»")
    faltan = [b for b in raw["obligatorios"] if b not in raw["botones"]]
    if faltan:
        raise ValueError(f"botonera.yaml: obligatorios sin definir: {faltan}")
    return raw


def diagnostico_reintentable(*, kind: str | None, reason: str = "", repeticiones: int = 0,
                             commits: int | None = None) -> tuple[bool, str | None]:
    """(¿Reintentar puede servir?, recomendada). Reglas del anexo F1-14:
    - bloqueo repetido (≥2) o estructural → no se ofrece Reintentar;
    - «verificación fallida» sin commits → el trabajo puede existir ya en la base: se recomienda Dar por hecha;
    - transitorio no repetido → Reintentar."""
    if repeticiones >= 2 or kind == "estructural":
        return False, None
    if commits == 0 and re.search(r"verificaci[oó]n\s+fallida|verify", reason or "", re.I):
        return False, "hecha"
    return kind == "transient", "reintentar" if kind == "transient" else None


def _boton(cat: dict, bid: str, estrella: bool = False) -> dict:
    d = cat["botones"][bid]
    from . import decisions  # import tardío: decisions importa este módulo
    out = {"id": bid, "text": (ESTRELLA if estrella else "") + d["texto"], "action": getattr(decisions, d["accion"])}
    if d.get("irreversible"):
        out["confirmar"] = True      # dos toques
    if d.get("opciones_dias"):
        out["dias"] = list(d["opciones_dias"])
    return out


def ficha(estado: str, *, reintentable: bool = False, recomendada: str | None = None,
          path: Path | str | None = None) -> dict:
    """{'filas': [[botón…]], 'mas': [[botón…]]} de un estado. Mismo orden siempre; la estrella va en la recomendada."""
    cat = cargar(path)
    if estado not in cat["estados"]:
        raise ValueError(f"estado desconocido: {estado}")
    principal = [b for b in cat["orden"]["principal"]]
    if reintentable:
        principal.insert(0, "reintentar")
    filas = [[_boton(cat, b, b == recomendada) for b in principal[i:i + 2]] for i in range(0, len(principal), 2)]
    mas = [[_boton(cat, b, b == recomendada) for b in cat["orden"]["mas"][i:i + 2]]
           for i in range(0, len(cat["orden"]["mas"]), 2)]
    return {"filas": filas, "mas": mas}


def completar(filas: list[list[dict]], estado: str, **kw) -> list[list[dict]]:
    """Añade a las filas que ya trae una ficha los obligatorios que le falten (por acción), sin repetir ninguno."""
    cat = cargar()
    hay = {b.get("action") for fila in filas for b in fila}
    extra = [b for fila in ficha(estado, **kw)["filas"] for b in fila
             if b["id"] in cat["obligatorios"] and b["action"] not in hay]
    return [*filas, *[extra[i:i + 2] for i in range(0, len(extra), 2)]]


ESTADO_DE = {"todo": "ready", "stuck": "triage"}


def estado_de(status: str | None) -> str | None:
    """Estado del catálogo para un estado de Hermes (None = sin botonera: archivada o desconocida)."""
    status = ESTADO_DE.get(status or "", status)
    return status if status in cargar()["estados"] else None


def boton(bid: str) -> dict:
    return _boton(cargar(), bid)


def con_mas(filas: list[list[dict]], estado: str, **kw) -> list[list[dict]]:
    """Filas de una ficha completas: lo que ya traía + los obligatorios que falten + «➕ Más» (lo menos usado)."""
    return [*completar(filas, estado, **kw), [boton("mas")]]


def filas_mas() -> list[list[dict]]:
    return ficha(cargar()["estados"][0])["mas"]


def spec_de_registro(rec: dict) -> list[list[dict]]:
    """Filas (spec) de un teclado ya emitido, a partir de su registro del almacén."""
    buttons, filas, i = rec.get("buttons") or [], [], 0
    for n in rec.get("rows") or [len(buttons)]:
        filas.append([dict(b) for b in buttons[i:i + n]])
        i += n
    return filas
