"""Agrupar tarjetas abiertas (t_a0110f3d, entrega A1: `/agrupar`). Propone; NO aplica nada sin botón.

`proponer` mira las tarjetas vivas (todos los tableros o uno) y las reparte en grupos, cada tarjeta en uno solo y en este
orden de precedencia:
1. bandeja   no es de carril: épicas, retenidas, backlog del bot, «tuyo»/«decidir», palabras de `agrupar.yaml` o assignee
             que no es un carril (oscar, personas). Se listan aparte y NUNCA se fusionan ni encadenan.
2. duplicadas título normalizado parecido (`duplicadas_umbral`, difflib) en el mismo repo → [🔗 Fusionar].
3. ficheros   comparten fichero/carpeta (lógica de `solapes`, sin duplicarla; las ya encadenadas padre/hija no cuentan)
             → [⛓ Encadenar] (la de rama/progreso primero, luego la más antigua) o [🔗 Fusionar].
4. decision  tarjetas bloqueadas con la misma pregunta pendiente → [📌 Una sola pregunta].
5. lote      mismo repo, en review y sin solape de ficheros → [📦 Mismo lote].
«Dejar» guarda las parejas en `solapes_descartados.json` (el mismo fichero de «Se complementan») y no vuelven a salir.

Los botones Fusionar / Encadenar / Dejar son los de `fusion.py` (fus_merge / fus_chain / fus_ok): misma semántica, con
confirmación si hay una tarjeta en curso. Solo «Mismo lote» y «Una sola pregunta» son nuevos (prefijo `agr_`): dejan un
comentario en cada tarjeta. Los ajustes viven en `agrupar.yaml` (se relee en cada uso; ausente o inválido = defaults).
"""
from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from . import fusion, solapes, titulos
from .config import ROOT

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "agrupar.yaml"
AGR_LOTE, AGR_UNA = "agr_lote", "agr_una"
AGR_ACTIONS = (AGR_LOTE, AGR_UNA)
DUPLICADAS, FICHEROS, DECISION, LOTE = "duplicadas", "ficheros", "decision", "lote"
ETIQUETA = {DUPLICADAS: "🔗 Duplicadas", FICHEROS: "⛓ Mismo fichero/área", DECISION: "📌 Misma decisión pendiente",
            LOTE: "📦 Lote de integración"}
AUTHOR = "oscar-telegram"
_ID_RE = re.compile(r"\bt_[0-9a-f]{8}\b")
_PREFIJO_RE = re.compile(r"^\s*\[[^\]]*\]\s*")
_PASO_RE = re.compile(r"\s*·\s*[^·]*$")


@dataclass(frozen=True)
class Ajustes:
    activo: bool = True
    duplicadas_umbral: float = 0.8
    estados: tuple = ("ready", "todo", "running", "blocked", "review")
    palabras_bandeja: tuple = ("épica", "epica", "retenida", "tuyo", "decidir")
    max_grupos: int = 8               # grupos que se envían por pasada (el resto, «y N más»)
    max_tarjetas: int = 8             # tarjetas que se listan por grupo


def load_ajustes(path: Path | str | None = None) -> Ajustes:
    """Ajustes desde el YAML; lo que falte o no sea válido queda en su valor por defecto."""
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return Ajustes()
    if not isinstance(raw, dict):
        return Ajustes()
    out = {}
    for f in fields(Ajustes):
        v = raw.get(f.name)
        if v is None:
            continue
        if f.name == "activo" and isinstance(v, bool):
            out[f.name] = v
        elif f.name == "duplicadas_umbral" and isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 1:
            out[f.name] = float(v)
        elif f.name in ("max_grupos", "max_tarjetas") and isinstance(v, int) and not isinstance(v, bool) and v >= 1:
            out[f.name] = v
        elif f.name in ("estados", "palabras_bandeja") and isinstance(v, list) and v \
                and all(isinstance(x, str) and x for x in v):
            out[f.name] = tuple(x.lower() for x in v)
    return Ajustes(**out)


@dataclass
class Tarjeta:
    tid: str
    board: str
    lane: str
    status: str
    title: str
    repo: str = ""
    rutas: set = field(default_factory=set)
    since: float = 0.0


@dataclass
class GrupoProp:
    tipo: str
    cards: list
    motivo: str = ""
    repo: str = ""


@dataclass
class Propuesta:
    grupos: list = field(default_factory=list)
    bandeja: list = field(default_factory=list)   # [(Tarjeta, motivo)]


def _fold(text: str) -> str:
    nfd = unicodedata.normalize("NFD", " ".join((text or "").split()).casefold())
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


def titulo_norm(title: str) -> str:
    """Título comparable: sin prefijo [Hub], sin « · paso», sin ids, minúsculas y sin acentos."""
    t = _ID_RE.sub(" ", _PASO_RE.sub("", _PREFIJO_RE.sub("", title or "")))
    return _fold(re.sub(r"[^\w\s]", " ", t))


def motivo_bandeja(task: dict, lanes: dict, ajustes: Ajustes) -> str | None:
    """Por qué NO es una tarjeta de carril (None = sí lo es)."""
    if task.get("assignee") not in lanes:
        return f"a nombre de {task.get('assignee') or 'nadie'}"
    paso = titulos.step_of(task)
    if paso in titulos.NOT_LIVE or paso in titulos.OSCAR_STEPS:
        return f"paso «{paso}»"
    folded = _fold(task.get("title") or "")
    for w in ajustes.palabras_bandeja:
        if re.search(rf"\b{re.escape(_fold(w))}\b", folded):
            return f"«{w}» en el título"
    return None


def preparar(items: list[dict], lanes: dict, ajustes: Ajustes) -> tuple[list[Tarjeta], list[tuple[Tarjeta, str]]]:
    """[{board, task, since}] (CommandCenter.board_tasks) -> (tarjetas de carril, bandeja con motivo)."""
    sol = solapes.Ajustes()
    cards, bandeja = [], []
    for it in items:
        t = it.get("task") or {}
        if not t.get("id") or (t.get("status") or "") == "triage":
            continue
        lane = lanes.get(t.get("assignee"))
        repo = solapes._norm_repo(getattr(lane, "repo", "") if lane is not None else "") or (t.get("assignee") or "")
        c = Tarjeta(t["id"], it.get("board") or "", t.get("assignee") or "", t.get("status") or "",
                    t.get("title") or "", repo, set(), float(it.get("since") or 0))
        why = motivo_bandeja(t, lanes, ajustes)
        if why:
            bandeja.append((c, why))
        elif c.status in ajustes.estados:
            c.rutas = solapes.extraer_rutas(f"{c.title}\n{t.get('body') or ''}", sol,
                                            getattr(lane, "repo", "") if lane is not None else "")
            cards.append(c)
    return cards, bandeja


def _componentes(cards: list[Tarjeta], unidos) -> list[list[Tarjeta]]:
    parent = {c.tid: c.tid for c in cards}

    def raiz(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, a in enumerate(cards):
        for b in cards[i + 1:]:
            if unidos(a, b):
                parent[raiz(a.tid)] = raiz(b.tid)
    comps: dict[str, list[Tarjeta]] = {}
    for c in cards:
        comps.setdefault(raiz(c.tid), []).append(c)
    return [m for m in comps.values() if len(m) > 1]


def orden(cards: list[Tarjeta]) -> list[Tarjeta]:
    """En curso primero (lleva rama), luego la más antigua."""
    return sorted(cards, key=lambda c: (c.status != "running", c.since or float("inf"), c.tid))


def proponer(cards: list[Tarjeta], ajustes: Ajustes | None = None, descartes: set[str] | None = None, *,
             enlazadas=None, preguntas: dict[str, str] | None = None) -> list[GrupoProp]:
    """Grupos de las tarjetas de carril. `enlazadas(a, b)`: ¿padre/hija? (no se encadenan de nuevo); `preguntas`:
    tid -> pregunta pendiente (para la misma decisión)."""
    ajustes, descartes = ajustes or Ajustes(), descartes or set()
    enlazadas = enlazadas or (lambda a, b: False)
    sol = solapes.Ajustes()
    libres = list(cards)
    grupos: list[GrupoProp] = []

    def misma_repo(a, b):
        return a.repo == b.repo and solapes.par(a.tid, b.tid) not in descartes

    def duplicadas(a, b):
        na, nb = titulo_norm(a.title), titulo_norm(b.title)
        return bool(na and nb) and misma_repo(a, b) and \
            difflib.SequenceMatcher(None, na, nb).ratio() >= ajustes.duplicadas_umbral

    def ficheros(a, b):
        return misma_repo(a, b) and bool(solapes.compartido(a.rutas, b.rutas, sol)) and not enlazadas(a, b)

    for tipo, unidos in ((DUPLICADAS, duplicadas), (FICHEROS, ficheros)):
        for miembros in _componentes(libres, unidos):
            if tipo == FICHEROS:
                comunes = sorted({x for i, a in enumerate(miembros) for b in miembros[i + 1:]
                                  if ficheros(a, b) for x in solapes.compartido(a.rutas, b.rutas, sol)})
                motivo = "Comparten: " + "; ".join(comunes[:5])
            else:
                motivo = "Títulos casi iguales"
            grupos.append(GrupoProp(tipo, orden(miembros), motivo, miembros[0].repo))
            libres = [c for c in libres if c not in miembros]

    por_pregunta: dict[str, list[Tarjeta]] = {}
    for c in libres:
        q = _fold((preguntas or {}).get(c.tid) or "") if c.status == "blocked" else ""
        if q:
            por_pregunta.setdefault(q, []).append(c)
    for miembros in por_pregunta.values():
        miembros = [c for i, c in enumerate(miembros)
                    if any(solapes.par(c.tid, o.tid) not in descartes for o in miembros if o is not c)]
        if len(miembros) > 1:
            grupos.append(GrupoProp(DECISION, orden(miembros), "Misma pregunta pendiente de Oscar", ""))
            libres = [c for c in libres if c not in miembros]

    por_repo: dict[str, list[Tarjeta]] = {}
    for c in libres:
        if c.status == "review":
            por_repo.setdefault(c.repo, []).append(c)
    for repo, miembros in sorted(por_repo.items()):
        miembros = [c for c in miembros
                    if any(solapes.par(c.tid, o.tid) not in descartes for o in miembros if o is not c)]
        if len(miembros) > 1:
            grupos.append(GrupoProp(LOTE, orden(miembros), "Mismo repo, en review, sin tocar lo mismo", repo))
    return grupos


# --- presentación y botones -----------------------------------------------------------------------------------------

def texto_grupo(g: GrupoProp, n: int, total: int, ajustes: Ajustes | None = None) -> str:
    ajustes = ajustes or Ajustes()
    repo = f" · {g.repo.rsplit('/', 1)[-1]}" if g.repo else ""
    lines = [f"{n}/{total} {ETIQUETA[g.tipo]}{repo}: {len(g.cards)} tarjetas"]
    for c in g.cards[:ajustes.max_tarjetas]:
        lines.append(f"• {c.tid} «{c.title[:70]}» ({c.lane}, {c.status})")
    if len(g.cards) > ajustes.max_tarjetas:
        lines.append(f"  … y {len(g.cards) - ajustes.max_tarjetas} más")
    if g.motivo:
        lines.append(g.motivo)
    lines.append("No se aplica nada hasta que pulses un botón.")
    return "\n".join(lines)


def texto_bandeja(bandeja: list[tuple[Tarjeta, str]], ajustes: Ajustes | None = None) -> str:
    ajustes = ajustes or Ajustes()
    lines = [f"🗂 Bandeja de Oscar · {len(bandeja)} tarjetas que no son de carril (no se agrupan)"]
    lines += [f"• {c.tid} «{c.title[:60]}» · {why}" for c, why in bandeja[:ajustes.max_tarjetas * 2]]
    if len(bandeja) > ajustes.max_tarjetas * 2:
        lines.append(f"  … y {len(bandeja) - ajustes.max_tarjetas * 2} más")
    return "\n".join(lines)


def teclado(g: GrupoProp) -> list[list[dict]]:
    """Botones según el tipo. Fusionar / Encadenar / Dejar son los de fusion.py; Dejar = «se complementan»."""
    merge = {"text": "🔗 Fusionar", "action": fusion.FUS_MERGE}
    chain = {"text": "⛓ Encadenar", "action": fusion.FUS_CHAIN}
    dejar = {"text": "✋ Dejar", "action": fusion.FUS_OK}
    por_tipo = {DUPLICADAS: [merge], FICHEROS: [chain, merge],
                DECISION: [{"text": "📌 Una sola pregunta", "action": AGR_UNA}],
                LOTE: [{"text": "📦 Mismo lote", "action": AGR_LOTE}]}
    return [por_tipo[g.tipo] + [dejar]]


def registro(g: GrupoProp) -> dict | None:
    return fusion.registro_tarjetas([{"tid": c.tid, "board": c.board, "lane": c.lane, "title": c.title}
                                     for c in g.cards])


def on_button(action: str, rec: dict, where: dict | None, desk, hermes_for) -> bool:
    """«Mismo lote» / «Una sola pregunta»: dejan un comentario en cada tarjeta; no mueven ni cierran nada."""
    cards = [c for c in rec.get("cards") or () if c.get("tid")]
    tids = [c["tid"] for c in cards]
    if len(tids) < 2:
        desk._edit_text(where, "No reconozco el grupo; vuelve a lanzar /agrupar")
        return True
    who = desk._who()
    fallos = []
    for c in cards:
        otras = ", ".join(t for t in tids if t != c["tid"])
        texto = (f"{who} agrupó esta tarjeta con {otras} para integrarlas en el mismo lote (/agrupar)."
                 if action == AGR_LOTE else
                 f"{who} marcó que {otras} esperan la misma decisión que esta: se responde una vez (/agrupar).")
        try:
            hermes_for(c["board"]).comment(c["tid"], texto, author=AUTHOR)
        except Exception as exc:
            log.warning("agrupar: comentario en %s falló: %s", c["tid"], exc)
            fallos.append(c["tid"])
    msg = "📦 Mismo lote" if action == AGR_LOTE else "📌 Una sola pregunta"
    desk._edit_text(where, f"{msg}: {', '.join(tids)} anotadas ({who})"
                    + (f" · no pude comentar en {', '.join(fallos)}" if fallos else ""))
    return not fallos
