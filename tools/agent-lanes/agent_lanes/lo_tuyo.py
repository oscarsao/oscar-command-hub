"""Bandeja única «Lo tuyo» (/pendiente en Telegram y `lanes.py pendiente` para Hermes; t_d6ff8363).

Oscar preguntaba «¿qué tengo pendiente?» ~10 veces por semana porque la respuesta estaba repartida (decisiones, /aprobar,
cola del integrador, tarjetas «· tuyo», integrador_estado.md). Aquí se junta en UN mensaje agrupado por prioridad, con una
línea y un botón por cosa. Lógica pura y sin red: CommandCenter y la CLI aportan los datos ya leídos; los botones los emite
CommandCenter (cada uno abre la ficha de siempre con sus botones: este módulo no aprueba ni responde nada por sí solo).

Ítem = {kind, tid, title, board, since, prio, url, extra}. `kind`:
  pregunta   tarea de carril en needs_input         -> ✍️ Responder
  atascada   🧊 Hermes la paró por bloquearse        -> 👁 Ver
  aprobar    tarea lista para integrar (PR listo)   -> ✅ Aprobar (abre la ficha con [✅ Aprobar])
  cola       bloqueo de LIMITACIÓN-GUARD en la cola del integrador (integrador_cola.json) -> 👁 Ver
  externo    PR de sesión interactiva que espera tu OK (integrador_externos.json)          -> 🔗 PR (enlace)
  tuyo       tarjeta tuya («· tuyo» / «· decidir» o de decisión)                           -> 👁 Ver
  integrador paso de integrador_estado.md que espera a Oscar                                -> 📖 Ver más

Los umbrales viven en `lo_tuyo.yaml`; con el fichero ausente o un valor inválido valen los defaults de `Ajustes`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import Path

import yaml

from .config import ROOT
from .notices import hours_ago, truncate

SETTINGS_PATH = ROOT / "lo_tuyo.yaml"
URGENTE, NORMAL, BAJA = "urgente", "normal", "baja"
GRUPOS = ((URGENTE, "🔴 Urgente"), (NORMAL, "🟡 Normal"), (BAJA, "⚪ Cuando puedas"))
# kind -> (verbo del botón, orden dentro del grupo)
KINDS = {"pregunta": ("✍️ Responder", 0), "aprobar": ("✅ Aprobar", 1), "atascada": ("👁 Ver", 2), "cola": ("👁 Ver", 3),
         "externo": ("🔗 PR", 4), "tuyo": ("👁 Ver", 5), "integrador": ("📖 Ver más", 6)}
SIEMPRE_URGENTE = ("atascada", "cola")  # bloquean a otros sin que nadie lo vea
_ESPERA_RE = re.compile(r"\boscar\b|esper|pendiente|bloquead|necesit|pide|ok\b", re.I)


@dataclass(frozen=True)
class Ajustes:
    urgente_prioridad: int = 10   # prioridad de la tarjeta a partir de la cual va en «Urgente»
    urgente_horas: float = 24     # lo que lleva esperando a Oscar para subir a «Urgente»
    max_lineas: int = 20          # ítems con línea y botón; el resto se cuenta en «N más»
    max_texto: int = 3500         # tope de caracteres (el límite de Telegram es 4096)


def load_ajustes(path: Path | str | None = None) -> Ajustes:
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return Ajustes()
    if not isinstance(raw, dict):
        return Ajustes()
    d, out = Ajustes(), {}
    for f in fields(Ajustes):
        v = raw.get(f.name)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        if f.name == "urgente_prioridad":
            out[f.name] = int(v)
        elif f.name == "urgente_horas" and v > 0:
            out[f.name] = float(v)
        elif f.name == "max_lineas" and v >= 1:
            out[f.name] = min(int(v), 40)   # un teclado de Telegram admite 100 botones; 40 sigue siendo legible
        elif f.name == "max_texto" and v >= 500:
            out[f.name] = min(int(v), 3900)
    return Ajustes(**{**{f.name: getattr(d, f.name) for f in fields(Ajustes)}, **out})


def item(kind: str, tid: str, title: str | None, *, board: str = "", since: float | None = None, prio: int | None = None,
         url: str | None = None, extra: str = "") -> dict:
    return {"kind": kind, "tid": tid, "title": title or "", "board": board, "since": since, "prio": prio, "url": url,
            "extra": extra}


def _prio(task: dict | None) -> int | None:
    p = (task or {}).get("priority")
    return p if isinstance(p, int) and not isinstance(p, bool) else None


def de_pendientes(pendings: list, kind: str) -> list[dict]:
    """Ítems desde los `Pending` del renotifier (preguntas, atascadas o listas para aprobar)."""
    return [item(kind, p.tid, (p.task or {}).get("title"), board=p.lane.board, since=p.since, prio=_prio(p.task))
            for p in pendings]


def de_cola_guard(entries: dict[str, dict], board_of) -> list[dict]:
    """Ítems desde `GuardQueue.entries()`; `board_of(lane)` da el tablero del carril."""
    return [item("cola", e.get("tid") or tid, e.get("title"), board=board_of(e.get("lane")) or "",
                 since=e.get("queued_at"), extra=str(e.get("need") or ""))
            for tid, e in entries.items()]


def de_externos(items: dict[str, dict]) -> list[dict]:
    """PR de sesiones interactivas SIN tu OK sobre su commit actual (con OK ya no esperan a Oscar)."""
    out = []
    for it in items.values():
        if it.get("ok_sha") and it.get("ok_sha") == it.get("sha"):
            continue
        slug, pr = it.get("slug") or "", it.get("pr")
        out.append(item("externo", f"{it.get('repo', '?')}#{pr}", it.get("title"), since=it.get("queued_at"),
                        url=f"https://github.com/{slug}/pull/{pr}" if slug and pr else None, extra=str(it.get("sha") or "")[:9]))
    return out


def de_tarjetas(cards: list[dict]) -> list[dict]:
    """Ítems desde [{board, task, since}] (tarjetas a nombre de Oscar)."""
    return [item("tuyo", c["task"]["id"], c["task"].get("title"), board=c.get("board") or "", since=c.get("since"),
                 prio=_prio(c["task"])) for c in cards]


def de_estado(est: dict | None) -> list[dict]:
    """Pasos de integrador_estado.md que esperan a Oscar: el último parte de cada paso que lo nombra o está en espera."""
    if not est:
        return []
    last: dict[str, dict] = {}
    for p in est.get("pasos") or ():
        last[p["paso"]] = p   # el último bloque de un paso manda
    out = []
    for p in last.values():
        if _ESPERA_RE.search(f"{p.get('estado', '')} {p.get('siguiente', '')}") and not re.match(
                r"\s*(hecho|ok|listo|integrad|cerrad|fusionad|desplegad|terminad)", p.get("estado", ""), re.I):
            ref = " ".join(x for x in (p.get("repo"), f"PR #{p['pr'].lstrip('#')}" if p.get("pr") else "") if x)
            out.append(item("integrador", ref or p["paso"], p["paso"], since=est.get("actualizado"),
                            extra=(p.get("siguiente") or p.get("estado") or "")))
    return out


def de_kanban(lanes: dict, hermes_for, extra_boards=("default",)) -> list[dict]:
    """Lectura directa del kanban, sin el bot (herramienta de Hermes / CLI): preguntas, tareas por aprobar y tarjetas de
    Oscar. Un tablero o tarea ilegible se salta. Es una vista algo más tosca que la de /pendiente (sin aplazadas)."""
    from . import titulos
    from .notices import OSCAR_ASSIGNEE, decision_since, is_decision_card
    out: list[dict] = []
    boards = dict.fromkeys([l.board for l in lanes.values() if l.board] + list(extra_boards))
    for lane in lanes.values():
        if lane.kind not in ("implement", "ops") or not lane.board:
            continue
        h = hermes_for(lane.board)
        try:
            for t in h.list_status(lane.name, "done"):
                out.append(item("aprobar", t["id"], t.get("title"), board=lane.board, since=decision_since(t), prio=_prio(t)))
            for t in h.list_status(lane.name, "blocked"):
                kinds = [(e.get("payload") or {}).get("kind") for e in h.show(t["id"]).get("events") or ()
                         if e.get("kind") == "blocked"]
                if (not kinds or kinds[-1] == "needs_input") and titulos.is_live(t):
                    out.append(item("pregunta", t["id"], t.get("title"), board=lane.board, since=decision_since(t),
                                    prio=_prio(t)))
        except Exception:
            continue
    for board in boards:
        h = hermes_for(board)
        for status in ("ready", "todo", "blocked"):
            try:
                tasks = h.list_status(OSCAR_ASSIGNEE, status)
            except Exception:
                continue
            for t in tasks:
                if titulos.is_live(t) and (is_decision_card(t) or titulos.step_of(t) in titulos.OSCAR_STEPS):
                    out.append(item("tuyo", t["id"], t.get("title"), board=board, since=decision_since(t), prio=_prio(t)))
    return out


def de_state(state_dir: Path | str, board_of=lambda lane: "") -> list[dict]:
    """Cola del integrador, PR de sesiones interactivas y pasos pendientes de integrador_estado.md, desde `.state/`."""
    from . import integrador_visibilidad as iv
    from .guard_route import GuardQueue
    root = Path(state_dir)
    return (de_cola_guard(GuardQueue(root / "integrador_cola.json").entries(), board_of)
            + de_externos(iv.ColaExternos(root / iv.EXTERNOS_FILE).items())
            + de_estado(iv.leer_estado(root / iv.ESTADO_FILE)))


def grupo_de(it: dict, aj: Ajustes, now: float) -> str:
    prio, since = it.get("prio"), it.get("since")
    if it["kind"] in SIEMPRE_URGENTE or (prio is not None and prio >= aj.urgente_prioridad):
        return URGENTE
    if prio is not None and prio < 0:
        return BAJA
    if since and it["kind"] in ("pregunta", "aprobar", "externo") and now - since >= aj.urgente_horas * 3600:
        return URGENTE
    return NORMAL


def ordenar(items: list[dict], aj: Ajustes, now: float) -> list[dict]:
    """Por grupo (urgente primero) y, dentro, tipo de ítem y la más antigua antes. Sin repetir la misma cosa (kind, tid)."""
    seen, uniq = set(), []
    for it in items:
        if (it["kind"], it["tid"]) not in seen:
            seen.add((it["kind"], it["tid"]))
            uniq.append(it)
    orden = {g: i for i, (g, _) in enumerate(GRUPOS)}
    return sorted(uniq, key=lambda it: (orden[grupo_de(it, aj, now)], KINDS[it["kind"]][1], it.get("since") or 0, it["tid"]))


def verbo(it: dict) -> str:
    return KINDS[it["kind"]][0]


def _linea(n: int, it: dict, now: float) -> str:
    cuando = f" · hace {hours_ago(it['since'], now)} h" if it.get("since") else ""
    extra = f" · {truncate(it['extra'], 50)}" if it.get("extra") and it["kind"] in ("cola", "integrador", "externo") else ""
    return f"{n}. {verbo(it).split(' ')[0]} {it['tid']} · {truncate(it['title'], 55)}{extra}{cuando}"


def render(items: list[dict], aj: Ajustes, now: float, scope: str = "") -> tuple[str, list[dict]]:
    """(texto, ítems mostrados en el orden de las líneas numeradas). Sin nada: «Nada pendiente de ti 🎉»."""
    items = ordenar(items, aj, now)
    if not items:
        return f"Nada pendiente de ti{scope} 🎉", []
    shown = items[:aj.max_lineas]
    lines = [f"🙋 Lo tuyo{scope} · {len(items)} pendiente{'s' if len(items) != 1 else ''}"]
    n = 0
    for g, titulo in GRUPOS:
        grupo = [it for it in shown if grupo_de(it, aj, now) == g]
        if grupo:
            lines.append(f"\n{titulo} ({sum(1 for it in items if grupo_de(it, aj, now) == g)})")
        for it in grupo:
            n += 1
            lines.append(_linea(n, it, now))
    shown = [it for g, _ in GRUPOS for it in shown if grupo_de(it, aj, now) == g]   # mismo orden que las líneas
    if len(items) > len(shown):
        lines.append(f"\n➕ {len(items) - len(shown)} más: /atencion las enseña todas")
    text = "\n".join(lines)
    return (text if len(text) <= aj.max_texto else text[:aj.max_texto].rsplit("\n", 1)[0] + "\n…"), shown


def cli(argv: list[str], lanes: dict, hermes_for, state_dir: Path | str, now: float | None = None) -> int:
    """`lanes.py pendiente [--json]`: la misma bandeja como texto (o JSON con cómo actuar), solo lectura. Es la herramienta
    para Hermes: no manda nada a Telegram ni toca el kanban; los botones están en /pendiente del bot de carriles."""
    import argparse
    import json
    import time
    ap = argparse.ArgumentParser(prog="lanes.py pendiente", description="todo lo que espera a Oscar, en una lista")
    ap.add_argument("--json", action="store_true", help="salida estructurada (grupo, tipo, id, título, cómo actuar)")
    args = ap.parse_args(argv)
    now = time.time() if now is None else now
    aj = load_ajustes()
    found = de_kanban(lanes, hermes_for) + de_state(state_dir, lambda lane: getattr(lanes.get(lane), "board", ""))
    text, shown = render(found, aj, now)
    if not args.json:
        print(text + ("\n\nPara actuar: /pendiente en el bot de carriles (botones) o /tarea <id>." if shown else ""))
        return 0
    print(json.dumps([{"grupo": grupo_de(it, aj, now), "tipo": it["kind"], "id": it["tid"], "titulo": it["title"],
                       "tablero": it["board"], "url": it["url"], "accion": verbo(it),
                       "como": "/tarea " + it["tid"] if it["tid"].startswith("t_") else (it["url"] or "/integrador")}
                      for it in ordenar(found, aj, now)], ensure_ascii=False, indent=2))
    return 0
