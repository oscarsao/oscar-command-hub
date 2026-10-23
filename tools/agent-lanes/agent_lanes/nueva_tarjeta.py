"""Vía única para crear tarjetas de carril: `lanes.py tarjeta nueva` (auditoría de bots 23-10, P0 #2 y P1 #5).

Hermes escribía a mano la línea `Origen-Telegram` (22 tarjetas la tenían mal, así que el resultado no volvía a quien
lo pidió), no aplicaba el veto de marca ni buscaba duplicadas. Aquí lo hace el código, siempre igual:

  1. Solicitante: una persona de tools/telegram/people.yaml (clave o user_id) u Oscar. Desconocida = se rechaza.
  2. Origen: `Origen-Telegram: chat=<id> thread=<id>` generado (primera línea del cuerpo), más `Pidió: <nombre>`.
     Sin --chat, el DM de la persona.
  3. Veto de marca: la persona solo crea en las marcas de su `brands` ("*" = todas; Oscar siempre). Falla cerrado.
  4. Duplicadas: parecido de título con las tarjetas abiertas o hechas hace poco (triaje.yaml). Si hay, no crea
     (código 3) salvo --aunque-duplicada.
  5. Carril y repo: --carril, o el único carril cuyas palabras clave (LANE_HINTS) salen en el texto/--proyecto;
     --repo se valida contra la lista blanca `repos:` del carril.
  6. Seguimiento: `hermes kanban notify-subscribe … --delivery-mode notify` al chat/tema de origen, sin notificador propio.

`reparar-origen` (en seco, nunca aplica) lista las tarjetas con el Origen-Telegram mal escrito y propone la línea correcta.

Códigos de salida: 0 creada · 2 datos no válidos o vetada · 3 duplicada.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from . import triaje
from .people import TODAS_LAS_MARCAS, People, Person, norm_marca

ESTADOS_DUPLICADO = ("triage", "todo", "ready", "running", "blocked", "review", "done")
ESTADOS_REPARAR = ("triage", "todo", "ready", "running", "blocked", "review")
PIDIO_RE = re.compile(r"^\s*Pidió:\s*(.+?)\s*$", re.M | re.I)
ORIGEN_BIEN_RE = re.compile(r"^\s*origen-telegram:\s*chat=-?\d+\s+thread=\d+\s*$", re.I)
ORIGEN_LINEA_RE = re.compile(r"^\s*origen[\w -]*:.*$", re.I | re.M)
OWNER_KEY = "oscar"


class Rechazo(Exception):
    """La tarjeta no se crea; `code` es el código de salida del CLI."""

    def __init__(self, msg: str, code: int = 2):
        super().__init__(msg)
        self.code = code


def pidio_de(body: str | None) -> str | None:
    """Nombre de la línea `Pidió: X` de la tarjeta (la ficha lo enseña), o None."""
    found = PIDIO_RE.search(body or "")
    return found.group(1) if found else None


def resolver_solicitante(people: People, quien: str) -> Person:
    """Persona por clave de people.yaml ('maria'), por user_id o 'oscar'. Desconocida = Rechazo (falla cerrado)."""
    quien = (quien or "").strip()
    if quien.casefold() == OWNER_KEY:
        return people.owner
    found = people.lookup(quien) if quien.isdigit() else None
    if found is None:
        found = next((p for p in people._by_id.values() if p.key.casefold() == quien.casefold()), None)
    if found is None:
        raise Rechazo(f"solicitante '{quien}' no está en people.yaml: no se crea la tarjeta")
    return found


def marca_permitida(persona: Person, marca: str | None) -> bool:
    """Veto de marca: Oscar y quien tenga '*' en todas; el resto, solo las de su `brands`. Marca desconocida (carril de
    sistema, p. ej. claude-hub) solo para los de todas las marcas."""
    if persona.is_owner or TODAS_LAS_MARCAS in persona.brands:
        return True
    return bool(marca) and norm_marca(marca) in {norm_marca(b) for b in persona.brands}


def origen_valido(chat, thread) -> tuple[str, str]:
    try:
        c, t = int(str(chat).strip()), int(str(thread).strip())
    except ValueError:
        raise Rechazo(f"origen no válido (chat={chat!r} thread={thread!r}): deben ser números") from None
    if c == 0 or t < 0:
        raise Rechazo(f"origen no válido (chat={c} thread={t})")
    return str(c), str(t)


def cuerpo(texto: str, persona: Person, chat: str, thread: str, *, mensaje: str | None = None,
           repo: str | None = None) -> str:
    """Cuerpo de la tarjeta. La línea Origen-Telegram va PRIMERA (ORIGIN_RE toma la primera coincidencia)."""
    lines = [f"Origen-Telegram: chat={chat} thread={thread}"]
    if mensaje:
        lines.append(f"Mensaje-Telegram: {mensaje}")
    lines.append(f"Pidió: {persona.name}")
    if repo:
        lines.append(f"Repo: {repo}")
    return "\n".join(lines) + "\n\n" + texto.strip() + "\n"


@dataclass
class Resultado:
    task_id: str | None
    lane: str
    chat: str
    thread: str
    avisado: bool = False
    notas: list[str] = field(default_factory=list)


def elegir_carril(lanes: dict, carril: str | None, texto: str, proyecto: str | None, hints: dict) -> str:
    implement = {n for n, l in lanes.items() if getattr(l, "kind", "implement") == "implement"}
    if carril:
        if carril not in implement:
            raise Rechazo(f"carril '{carril}' no es un carril de implementación")
        return carril
    low = " ".join((proyecto or texto).casefold().split())  # --proyecto manda sobre las pistas del texto
    named = [n for n in sorted(implement)
             if any(re.search(rf"(?<![\w-]){re.escape(h)}(?![\w-])", low)
                    for h in (*hints.get(n, ()), n.removeprefix("claude-")))]
    if len(named) != 1:
        raise Rechazo("no puedo decidir el carril " + (f"(salen {', '.join(named)})" if named else "(ninguna pista)")
                      + ": usa --carril o --proyecto")
    return named[0]


def crear(*, titulo: str, texto: str, solicitante: str, chat: str | None, thread: str | None, mensaje: str | None,
          carril: str | None, proyecto: str | None, repo: str | None, prioridad: int | None, forzar: bool,
          dry_run: bool, people: People, lanes: dict, hermes_for: Callable, brands: dict, hints: dict,
          cfg: dict | None = None, ahora: float | None = None) -> Resultado:
    persona = resolver_solicitante(people, solicitante)
    if not titulo.strip():
        raise Rechazo("falta el título")
    lane_name = elegir_carril(lanes, carril, f"{titulo} {texto}", proyecto, hints)
    lane = lanes[lane_name]
    marca = brands.get(lane_name)
    if not marca_permitida(persona, marca):
        raise Rechazo(f"{persona.name} no puede pedir tarjetas de {marca or 'sistema'} ({lane_name}): "
                      "sus marcas son " + (", ".join(persona.brands) or "ninguna"))
    if repo and repo not in (getattr(lane, "repos", None) or {}):
        raise Rechazo(f"repo '{repo}' no está en la lista blanca de {lane_name}")
    c, t = origen_valido(chat if chat not in (None, "") else persona.user_id,
                         thread if thread not in (None, "") else 0)
    cfg = cfg or triaje.load_config()
    h = hermes_for(lane.board)
    tarjetas: list[dict] = []
    for st in ESTADOS_DUPLICADO:
        try:
            tarjetas += h.list_status_all(st)
        except Exception:  # un estado ilegible no impide crear; el resto sí se mira
            continue
    dup = triaje.buscar_duplicado(titulo, tarjetas, cfg, ahora=ahora)
    if dup and not forzar:
        raise Rechazo(f"posible duplicada de {dup['id']} «{dup['title']}» ({dup['status']}, parecido {dup['score']}); "
                      "usa --aunque-duplicada si es distinta", code=3)
    res = Resultado(None, lane_name, c, t)
    if dup:
        res.notas.append(f"duplicada forzada ({dup['id']})")
    body = cuerpo(texto, persona, c, t, mensaje=mensaje, repo=repo)
    if dry_run:
        res.notas.append("dry-run: no se ha creado nada")
        return res
    slot = int((time.time() if ahora is None else ahora) // 600)
    key = "nueva-" + hashlib.sha1(f"{lane_name}|{c}|{t}|{slot}|{titulo}".encode()).hexdigest()[:16]
    res.task_id = h.create(titulo.strip(), body, lane_name, priority=prioridad, key=key, created_by=persona.key)
    try:
        res.avisado = h.subscribe_telegram(res.task_id, c, t)
    except Exception as exc:  # la tarjeta ya existe: se dice, no se pierde
        res.notas.append(f"creada, pero no se pudo suscribir el aviso: {type(exc).__name__}")
    return res


# --- reparar el origen de las tarjetas existentes (en seco) -------------------------------------------------------

def proponer_origen(task: dict, people: People) -> str | None:
    """Línea `Origen-Telegram` corregida para una tarjeta cuyo origen está mal, o None si está bien / no hay pista.
    Pista, por orden: los números chat=/thread= de la línea mal escrita; si no, el DM de la persona que la pidió
    (created_by o su nombre en la línea de origen)."""
    body = task.get("body") or ""
    lineas = ORIGEN_LINEA_RE.findall(body)
    if not lineas:
        return None
    if ORIGEN_BIEN_RE.match(lineas[0]):
        return None
    mala = lineas[0]
    chat = re.search(r"chat\s*[=:]\s*(-?\d+)", mala, re.I)
    thread = re.search(r"(?:thread|tema|hilo)\s*[=:]\s*(\d+)", mala, re.I)
    if chat:
        return f"Origen-Telegram: chat={chat.group(1)} thread={thread.group(1) if thread else 0}"
    claves = [str(task.get("created_by") or ""), mala]
    for p in people._by_id.values():
        if any(p.key.casefold() in c.casefold() or p.name.casefold() in c.casefold() for c in claves if c):
            return f"Origen-Telegram: chat={p.user_id} thread=0"
    return None


def reparar_main(argv: list[str], *, hermes_for: Callable | None = None, out: Callable[[str], None] = print,
                 people: People | None = None) -> int:
    """`lanes.py tarjeta reparar-origen [--board b]`: SOLO lista y propone; aplicar la corrección requiere el OK de Oscar."""
    ap = argparse.ArgumentParser(prog="lanes.py tarjeta reparar-origen")
    ap.add_argument("--board", default="default")
    args = ap.parse_args(argv)
    from .decisions import OWNER_TELEGRAM_ID
    from .hermes import HermesCLI
    from .people import load_people
    people = people or load_people(OWNER_TELEGRAM_ID)
    h = (hermes_for or (lambda b: HermesCLI(b, author="agent-lanes")))(args.board)
    n = sin_pista = 0
    for st in ESTADOS_REPARAR + ("done",):
        for t in h.list_status_all(st):
            if not ORIGEN_LINEA_RE.search(t.get("body") or ""):
                continue
            nueva = proponer_origen(t, people)
            mala = ORIGEN_LINEA_RE.findall(t.get("body") or "")
            if ORIGEN_BIEN_RE.match(mala[0]):
                continue
            if nueva:
                n += 1
                out(f"{t.get('id')} ({st}) «{(t.get('title') or '')[:50]}»\n  ahora:    {mala[0].strip()}\n  propongo: {nueva}")
            else:
                sin_pista += 1
                out(f"{t.get('id')} ({st}) «{(t.get('title') or '')[:50]}»\n  ahora:    {mala[0].strip()}\n  sin pista: hay que decidirlo a mano")
    out(f"\n{n} con corrección propuesta, {sin_pista} sin pista. En seco: no se ha cambiado nada.")
    return 0


def main(argv: list[str], *, hermes_for: Callable | None = None, out: Callable[[str], None] = print,
         people: People | None = None, lanes: dict | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lanes.py tarjeta nueva", description=__doc__.splitlines()[0])
    ap.add_argument("titulo")
    ap.add_argument("texto", nargs="*", help="cuerpo (si falta, el título)")
    ap.add_argument("--solicitante", required=True, help="clave de people.yaml, user_id u 'oscar'")
    ap.add_argument("--chat")
    ap.add_argument("--thread")
    ap.add_argument("--mensaje", help="id del mensaje de Telegram que la pidió")
    ap.add_argument("--carril")
    ap.add_argument("--proyecto")
    ap.add_argument("--repo")
    ap.add_argument("--prioridad", type=int)
    ap.add_argument("--aunque-duplicada", dest="forzar", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    from .commands import LANE_HINTS
    from .config import load_lanes
    from .decisions import OWNER_TELEGRAM_ID
    from .hermes import HermesCLI
    from .notices import BRANDS
    from .people import load_people
    cache: dict = {}
    hermes_for = hermes_for or (lambda b: cache.setdefault(b, HermesCLI(b, author="agent-lanes")))
    try:
        res = crear(titulo=args.titulo, texto=" ".join(args.texto) or args.titulo, solicitante=args.solicitante,
                    chat=args.chat, thread=args.thread, mensaje=args.mensaje, carril=args.carril,
                    proyecto=args.proyecto, repo=args.repo, prioridad=args.prioridad, forzar=args.forzar,
                    dry_run=args.dry_run, people=people or load_people(OWNER_TELEGRAM_ID),
                    lanes=lanes or load_lanes(), hermes_for=hermes_for, brands=BRANDS, hints=LANE_HINTS)
    except Rechazo as exc:
        out(f"✋ {exc}")
        return exc.code
    head = f"✅ Creada {res.task_id}" if res.task_id else "👀 Se crearía"
    out(f"{head} en {res.lane} · origen chat={res.chat} thread={res.thread}"
        + (" · aviso al solicitante suscrito" if res.avisado else "") + "".join(f"\n- {n}" for n in res.notas))
    return 0
