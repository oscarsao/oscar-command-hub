"""Prueba en navegador sin bloquear (decisión de Oscar, 20-11): etiquetas «VERIFICACIÓN PENDIENTE» / «VERIFICACIÓN HECHA».

El carril no puede abrir un navegador. Si a una tarjeta le falta esa prueba (p. ej. 390 px en claro y oscuro), el worker
termina el código y las pruebas que SÍ puede ejecutar y deja en su resumen la etiqueta `VERIFICACIÓN PENDIENTE` (rutas,
anchos, claro/oscuro y arnés); la tarjeta pasa a review con ella. Otra sesión con navegador hace la prueba y deja un
comentario `VERIFICACIÓN HECHA` (capturas y resultado). Si falla, reabre la tarjeta y el worker vuelve a dejar la etiqueta.

Aquí solo hay lógica pura (sin red): saber en qué estado está una tarjeta (`estado`), listar las pendientes con su PR
(`pendientes`, `render_lista`) y el motivo por el que el integrador no debe fusionar (`problema_integracion`).
Una marca solo cuenta al INICIO de una línea: citarla en medio de una frase no la activa.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("agent_lanes")

TAG_PENDIENTE = "VERIFICACIÓN PENDIENTE"
TAG_HECHA = "VERIFICACIÓN HECHA"
TAG_OMITIDA = "VERIFICACIÓN OMITIDA"  # decisión expresa de Oscar de integrar sin la prueba
OSCAR_AUTHOR = "oscar-telegram"  # = hermes.OSCAR_AUTHOR (no se importa para que este módulo siga siendo puro)

PENDIENTE, HECHA, OMITIDA = "pendiente", "hecha", "omitida"
_MARCA = re.compile(r"^[ \t>#*_\-]*verificaci[oó]n[ \t]+(pendiente|hecha|omitida|fallida)\b", re.IGNORECASE | re.MULTILINE)
_PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/(\d+)")
MAX_REVISADAS = 15  # tarjetas que se abren (show) por pasada al listar pendientes

SECCION_WORKER = f"""
## Prueba en navegador: NO te pares por ella (decisión de Oscar, 20-11)
Tu carril no puede abrir un navegador (ni 390 px, ni claro/oscuro, ni capturas). Si a la tarjeta le falta una prueba
de navegador, NO devuelvas `needs_input` por eso: termina el código y las pruebas que SÍ puedes ejecutar, commit y
push, y devuelve `status: done`. Después:
- `summary` EMPIEZA con una línea que arranque por `{TAG_PENDIENTE}` y diga: qué rutas, qué anchos, claro y oscuro, y el
  arnés a ejecutar (cópialo tal cual de la tarjeta). Esa etiqueta es la que hace que la tarjeta aparezca en la lista
  «esperan prueba en navegador» y la que frena la integración hasta que alguien con navegador la haga.
- Si abres PR, ábrelo EN BORRADOR y repite la etiqueta en su cuerpo. Si no puedes abrirlo, basta con el `summary`
  (el runner lo copia a la tarjeta) y el `for_oscar`.
- La prueba la hace otra sesión con navegador y deja un comentario `{TAG_HECHA}` con capturas y resultado; si falla,
  reabre la tarjeta con el fallo concreto y tú vuelves a dejar la etiqueta al terminar.
- No cambies nada más de la revisión: las demás pruebas siguen siendo obligatorias.
"""


def _marcas(texto: str | None) -> list[str]:
    return [m.lower() for m in _MARCA.findall(texto or "")]


def _estado_de(marca: str) -> str:
    return {"pendiente": PENDIENTE, "fallida": PENDIENTE, "hecha": HECHA, "omitida": OMITIDA}[marca]


def estado(show: dict | None) -> str | None:
    """`pendiente` / `hecha` / `omitida` / None (la tarjeta no pide prueba de navegador).

    `show` es la salida de `hermes.show`: {task, comments, runs}. Decide la ÚLTIMA marca de los comentarios. Sin marcas
    en comentarios, la etiqueta puede venir del resumen del worker (`task.result` o el `summary` del último run): vale
    como pendiente. «OMITIDA» solo cuenta si la escribe Oscar; «HECHA» la puede dejar cualquier otra sesión."""
    show = show or {}
    ultimo = None
    for c in show.get("comments") or []:
        if not isinstance(c, dict):
            continue
        for marca in _marcas(c.get("body")):
            if marca == "omitida" and c.get("author") != OSCAR_AUTHOR:
                continue
            ultimo = _estado_de(marca)
    if ultimo:
        return ultimo
    task = show.get("task") or {}
    textos = [task.get("result")] + [(r.get("metadata") or {}).get("summary") or r.get("summary")
                                      for r in (show.get("runs") or []) if isinstance(r, dict)]
    if any(_estado_de(m) == PENDIENTE for t in textos if isinstance(t, str) for m in _marcas(t)):
        return PENDIENTE
    return None


def pr_de(show: dict | None) -> tuple[int, str] | None:
    """(número, url) del PR más reciente mencionado en la etiqueta, en los comentarios o en el resumen."""
    show = show or {}
    task = show.get("task") or {}
    textos = [task.get("result"), *[(r.get("metadata") or {}).get("summary") for r in (show.get("runs") or [])
                                    if isinstance(r, dict)],
              *[c.get("body") for c in (show.get("comments") or []) if isinstance(c, dict)]]
    found = None
    for t in textos:
        for m in _PR_URL.finditer(t if isinstance(t, str) else ""):
            found = (int(m.group(1)), m.group(0))
    return found


def problema_integracion(show: dict | None) -> str | None:
    """Motivo para NO integrar (o None). Solo frena el estado `pendiente`: `hecha` u `omitida` (Oscar) dejan pasar."""
    if estado(show) == PENDIENTE:
        return (f"tiene {TAG_PENDIENTE} (prueba en navegador) sin {TAG_HECHA} ni decisión expresa de Oscar "
                f"({TAG_OMITIDA})")
    return None


def pendientes(hermes, tasks: list[dict], limite: int = MAX_REVISADAS) -> list[dict]:
    """Tarjetas con verificación pendiente: [{id, title, pr, url}]. Un fallo de lectura de una tarjeta no tumba la lista."""
    out: list[dict] = []
    for t in list(tasks)[:limite]:
        tid = t.get("id")
        if not tid:
            continue
        try:
            show = hermes.show(tid)
        except Exception as exc:  # noqa: BLE001  (Hermes caído o tarjeta ilegible: se omite esa tarjeta)
            log.info("%s: no se pudo leer para la lista de verificación pendiente: %s", tid, exc)
            continue
        if estado(show) != PENDIENTE:
            continue
        pr = pr_de(show)
        out.append({"id": tid, "title": t.get("title") or (show.get("task") or {}).get("title") or "",
                    "pr": pr[0] if pr else None, "url": pr[1] if pr else None})
    return out


def render_lista(entries: list[dict], *, maximo: int = 8) -> str:
    """Lista de texto plano «esperan prueba en navegador», una línea por tarjeta con su PR. Vacía -> cadena vacía."""
    if not entries:
        return ""
    lines = ["🧪 Esperan prueba en navegador:"]
    for e in entries[:maximo]:
        pr = f" · PR #{e['pr']}" if e.get("pr") else " · sin PR"
        lines.append(f"- {e['id']} · {(e.get('title') or '')[:60]}{pr}")
    if len(entries) > maximo:
        lines.append(f"… y {len(entries) - maximo} más")
    return "\n".join(lines)
