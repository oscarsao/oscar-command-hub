"""Formato único de aviso y vistas de peticiones (pitch «Seguimiento de peticiones» §4.2–4.3 · t_211c9902, A2/A3).

Mismo esqueleto para los avisos, `/estado` y el parte de las 9:00 por petición:

    🧊 Hub · req_39 «Avisos por tema»
    Estado: atascada (PR sin salida) · paso 4/6
    Próximo paso: resolver el PR de t_a1 · lo da: el integrador
    (o escríbeme aquí la respuesta)

Texto plano (sin HTML). Nada se recorta sin ofrecer el resto: `resumir` devuelve el texto corto y lo que queda para
«Ver más», y `paginas` parte en trozos de Telegram sin perder una línea.
"""
from __future__ import annotations

from .notices import TEXT_MAX, hours_ago, truncate
from .peticiones import INTEGRADO_RE, PROYECTOS, TOTAL_PASOS, Estado, _comentarios

PIE_TEXTO_LIBRE = "(o escríbeme aquí la respuesta)"
EMOJI_ESTADO = {"hecha": "✅", "en curso": "🛠", "esperándote": "⏳", "atascada": "🧊"}
ORDEN_ESTADO = {"atascada": 0, "esperándote": 1, "en curso": 2, "hecha": 3}
REQ_MAS = "req_mas"            # botón «Ver más» (decisions.py -> CommandCenter.req_choice)
RESUMEN_MAX = 600              # un aviso largo se resume a esto y el resto va tras «Ver más»
LINEAS_MAX = 15                # peticiones por /estado (el resto se cuenta, no se esconde)
# Carril cuyo tema de Telegram recibe los avisos de cada proyecto (lanes.yaml: `telegram:` del carril).
PROYECTO_CARRIL = {"Hub": "claude-hub", "OHQ": "claude-oscarhq", "Pildora": "claude-oscarhq",
                   "MigraTeam": "claude-migrateam", "NextJobs": "claude-nextjobs", "Scraper": "claude-scraper",
                   "Signal": "claude-signal"}


def tema_de(proyecto: str, lanes: dict) -> tuple[str, str] | None:
    """(chat, thread) del tema del proyecto según el carril que lo lleva; None si no tiene (-> Atención/DM)."""
    return getattr(lanes.get(PROYECTO_CARRIL.get(proyecto, "")), "telegram", None)


def resumir(texto: str, n: int = RESUMEN_MAX) -> tuple[str, str | None]:
    """(corto, resto). Si cabe en `n`, (texto, None); si no, corta en un salto de línea o espacio y el resto íntegro va
    en la segunda parte, para el botón “Ver más”."""
    texto = (texto or "").strip()
    if len(texto) <= n:
        return texto, None
    corte = max(texto.rfind("\n", 0, n), texto.rfind(" ", 0, n))
    corte = corte if corte > n // 2 else n
    return texto[:corte].rstrip() + " …", texto[corte:].strip()


def paginas(texto: str, n: int = TEXT_MAX) -> list[str]:
    """Parte `texto` en mensajes de como mucho `n` caracteres por líneas, sin perder ninguna."""
    out, actual = [], ""
    for linea in (texto or "").split("\n"):
        while len(linea) > n:
            if actual:
                out.append(actual)
                actual = ""
            out.append(linea[:n])
            linea = linea[n:]
        if actual and len(actual) + 1 + len(linea) > n:
            out.append(actual)
            actual = ""
        actual = f"{actual}\n{linea}" if actual else linea
    return out + [actual] if actual else out or [""]


def botones_aviso(req_id: str | None) -> list[list[dict]]:
    """Botones del aviso de una petición. Hoy solo «Ver más» está cableado; Integrar / Dar seguimiento / Aparcar los
    añaden las entregas siguientes sin cambiar el esqueleto."""
    return [[{"text": "💬 Ver más", "action": REQ_MAS}]] if req_id else []


def aviso_texto(proyecto: str, titulo: str, estado: Estado, req_id: str | None = None) -> str:
    """El esqueleto único (§4.3) de un aviso sobre una petición."""
    cabecera = f"{EMOJI_ESTADO.get(estado.estado, '•')} {proyecto}" + (f" · {req_id}" if req_id else "")
    lineas = [f"{cabecera} «{truncate(titulo, 60)}»",
              f"Estado: {estado.etiqueta} · paso {estado.paso}/{TOTAL_PASOS}",
              f"Próximo paso: {estado.proximo or 'ninguno'} · lo da: {estado.quien or 'nadie'}", PIE_TEXTO_LIBRE]
    return "\n".join(lineas)


def linea_peticion(req: dict, est: Estado, ahora: float) -> str:
    """Una línea de /estado: «⏳ req_41 · MigraTeam · «Login» — esperándote: responder… Lo da: Oscar (hace 5 h).»"""
    txt = f"{EMOJI_ESTADO.get(est.estado, '•')} {req['id']} · {req['proyecto']} · «{truncate(req['titulo'], 50)}» — "
    if est.estado == "hecha":
        return txt + "hecha."
    txt += f"{est.etiqueta}: {est.proximo or 'ninguno'}. Lo da: {est.quien or 'nadie'}"
    return txt + (f" (hace {hours_ago(est.desde, ahora)} h)" if est.desde else "") + "."


def ordenar(items: list[tuple[dict, Estado]]) -> list[tuple[dict, Estado]]:
    """Atascadas primero, luego esperándote, en curso y hechas; dentro de cada grupo, la más reciente arriba."""
    return sorted(items, key=lambda it: (ORDEN_ESTADO.get(it[1].estado, 9), -(it[0].get("ultimo_movimiento") or 0)))


def estado_texto(items: list[tuple[dict, Estado]], ahora: float, proyecto: str | None = None) -> str:
    """`/estado`: una línea por petición, atascadas primero. Lo que no cabe se cuenta y se pide con el filtro."""
    dentro = [it for it in items if not proyecto or it[0]["proyecto"].lower() == proyecto.lower()]
    titulo = f"📍 Estado · {len(dentro)} {'petición' if len(dentro) == 1 else 'peticiones'}" \
             + (f" de {proyecto}" if proyecto else "")
    if not dentro:
        return titulo + "\n\nNada que seguir: ninguna petición registrada."
    lineas = [linea_peticion(r, e, ahora) for r, e in ordenar(dentro)]
    resto = len(lineas) - LINEAS_MAX
    if resto > 0:
        lineas = lineas[:LINEAS_MAX] + [f"… y {resto} más: filtra con /estado <proyecto> ({', '.join(PROYECTOS)})"]
    return titulo + "\n\n" + "\n".join(lineas)


def cadena_texto(req: dict, est: Estado, shows: dict[str, dict | None], ahora: float) -> str:
    """«Ver más»: la cadena entera de una petición (tarjetas, estado de cada una, integración) sin recortar."""
    lineas = [aviso_texto(req["proyecto"], req["titulo"], est, req["id"]).rsplit("\n", 1)[0],
              f"Origen: {req.get('origen')}" + (f" · Tipo: {req['tipo']}" if req.get("tipo") else "")
              + (f" · Urgencia: {req['urgencia']}" if req.get("urgencia") else "")
              + (f" · Modo: {req['modo_oasp']}" if req.get("modo_oasp") else ""), "", "Cadena:"]
    if not req.get("tarjetas"):
        lineas.append("• sin tarjeta enlazada (añade «req: " + req["id"] + "» al cuerpo o a un comentario)")
    for tid in req.get("tarjetas") or ():
        show = shows.get(tid)
        task = (show or {}).get("task") or {}
        if not show:
            lineas.append(f"• {tid} · no pude leerla")
            continue
        sha = next((m.group(1) for c in reversed(_comentarios(show)) if (m := INTEGRADO_RE.search(c["body"]))), None)
        lineas.append(f"• {tid} · {truncate(task.get('title'), 70)} · {task.get('status', '?')}"
                      + (f" · INTEGRADO {sha}" if sha else ""))
    lineas.append(f"Último movimiento: hace {hours_ago(req.get('ultimo_movimiento'), ahora)} h")
    return "\n".join(lineas)
