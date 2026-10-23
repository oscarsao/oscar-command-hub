"""Tareas grandes (23-10, Oscar): una tarjeta grande no debe tapar el carril de su proyecto.

Una tarjeta es GRANDE si cumple cualquiera de estas señales (título, cuerpo, etiquetas o campos de la tarjeta):
  - etiqueta `[GRANDE]` / `[XL]` / `[WORKFLOW]` en el título o en el cuerpo, o una etiqueta equivalente;
  - línea `Tamaño: L | XL | GRANDE` (también `Size:`) en el cuerpo;
  - línea `Runtime estimado: N min|h` por encima de `big_minutes` (45 por defecto).
Las grandes corren en un hueco «grande» propio por proyecto (Service.tick), fuera de `max_parallel` del carril.
`marcar()` deja la señal escrita en el cuerpo al crear la tarjeta (Hermes.create), para que no dependa de que alguien
se acuerde: así lo ven también el panel y las siguientes pasadas.

También detecta las tarjetas que tocan rutas que no deben correr a la vez en el mismo carril (migraciones).
"""
from __future__ import annotations

import re

DEFAULT_BIG_MINUTES = 45
TAG_RE = re.compile(r"\[\s*(?:GRANDE|XL|WORKFLOW)\s*\]", re.I)
SIZE_RE = re.compile(r"^[ \t>*_-]*(?:tama[ñn]o|size)[ \t*_]*[:=][ \t*_]*(GRANDE|XL|L)\b", re.I | re.M)
RUNTIME_RE = re.compile(r"^[ \t>*_-]*(?:runtime|duraci[oó]n|tiempo)(?:[ \t]+estimad[oa])?[ \t*_]*[:=][ \t*_]*~?[ \t]*"
                        r"(\d+(?:[.,]\d+)?)[ \t]*(h|hr|hrs|horas?|min|mins|minutos?|m)\b", re.I | re.M)
LABEL_WORDS = {"grande", "xl", "workflow"}
SIZE_LINE = "Tamaño: GRANDE"


def _labels(task: dict) -> set[str]:
    out: set[str] = set()
    for key in ("labels", "tags", "label"):
        val = task.get(key)
        if isinstance(val, str):
            val = [val]
        for item in val or ():
            name = item.get("name") if isinstance(item, dict) else item
            if isinstance(name, str):
                out.add(name.strip().strip("[]").lower())
    return out


def runtime_minutes(text: str) -> float | None:
    """Mayor «Runtime estimado» declarado en el texto, en minutos (None = no declara ninguno)."""
    best = None
    for num, unit in RUNTIME_RE.findall(text or ""):
        value = float(num.replace(",", "."))
        minutes = value * 60 if unit.lower().startswith(("h", "hr")) else value
        best = minutes if best is None else max(best, minutes)
    return best


def reason(task: dict, big_minutes: float = DEFAULT_BIG_MINUTES) -> str | None:
    """Por qué es grande (texto corto para logs/panel) o None si es normal."""
    title, body = str(task.get("title") or ""), str(task.get("body") or "")
    if TAG_RE.search(title) or TAG_RE.search(body):
        return "etiqueta [GRANDE]"
    if _labels(task) & LABEL_WORDS:
        return "etiqueta GRANDE"
    if m := SIZE_RE.search(body):
        return f"tamaño {m.group(1).upper()}"
    minutes = runtime_minutes(body)
    if minutes is not None and minutes > big_minutes:
        return f"runtime estimado {minutes:.0f} min"
    return None


def is_big(task: dict, big_minutes: float = DEFAULT_BIG_MINUTES) -> bool:
    return reason(task, big_minutes) is not None


def touches(task: dict, paths) -> bool:
    """¿La tarjeta cita alguna de estas rutas (p. ej. supabase/migrations)? Dos así en un mismo carril no corren a la vez."""
    text = f"{task.get('title') or ''}\n{task.get('body') or ''}".lower().replace("\\", "/")
    return any(p.lower().replace("\\", "/").rstrip("/") in text for p in paths or () if p)


def marcar(title: str, body: str, big_minutes: float = DEFAULT_BIG_MINUTES) -> str:
    """Cuerpo con la línea `Tamaño: GRANDE` si la tarjeta es grande y aún no lo dice. Idempotente."""
    body = body or ""
    if SIZE_RE.search(body) or not is_big({"title": title, "body": body}, big_minutes):
        return body
    return body.rstrip("\n") + f"\n\n{SIZE_LINE}\n"
