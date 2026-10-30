"""Solape de alcance entre tarjetas activas del mismo repo (t_2bce5755): aviso ANTES de duplicar carriles.

El lote del integrador y la detección de conflicto de PR son reactivos (cuando las ramas ya existen). Esto es proactivo:
mira las tarjetas abiertas y avisa a Oscar de que varias van a tocar lo mismo, con la sugerencia «fusionar en una sola»
o «dejarlas, se complementan». Oscar decide; aquí no se fusiona, mueve ni cierra nada.

Dónde se comprueba: en una pasada periódica del runner (`SolapeTicker`), no en `HermesCLI.create`. Las tarjetas las crea
también el Coordinador, otros workers o personas con `hermes kanban create` directo, que no pasan por este código; el
barrido sobre el tablero las cubre todas, con el retraso de `minutos`.

CRITERIO (sin IA; lo que se compara y nada más):
1. Mismo repo: el repo del carril al que está asignada la tarjeta (`lanes.yaml`, `repo`). Las tarjetas de assignees que no
   son un carril con repo (oscar, personas, claude-ops) no se comparan: su repo no se conoce de forma fiable.
2. Estados vigilados (`estados`): ready, todo, running, blocked. review y done quedan fuera.
3. De título + cuerpo se extraen rutas: algo con «/» y extensión de `extensiones` (`tools/agent-lanes/lanes.yaml`), un
   nombre de fichero suelto (`commands.py`) o una carpeta con «/» final. Se ignoran URLs y, dentro de un camino
   absoluto, el prefijo de la ruta del repo. Un identificador entre comillas inversas que no parezca ruta NO cuenta
   (`create` casaría con todo).
4. Solape = comparten fichero (misma ruta; un nombre suelto casa por nombre con cualquier ruta que lo termine) o, con
   `nivel: modulo`, la carpeta contenedora (al menos `modulo_min_profundidad` tramos; así `src/` solo no cuenta).
5. Hace falta solape entre 2 o más tarjetas. Dos tarjetas encadenadas (padre/hija, p. ej. de /flujo) son secuenciales a
   propósito y no se avisan entre sí.
Un conjunto de tarjetas se avisa una sola vez (clave = ids ordenados). Los ajustes viven en `solapes.yaml` (se relee en
cada pasada; ausente o inválido = defaults de `Ajustes`).

BOTONES (t_ee6ad56c): el aviso lleva [🔗 Fusionar en una] [⛓ Encadenar] [✅ Se complementan, seguir] [🗂 Duplicada]
[⏸ Pausar una] [⏸ Luego]; los cinco primeros viven en `fusion.py` (fusionan, encadenan, archivan o pausan de verdad, con
confirmación si hay una tarjeta en curso). «Se complementan» guarda la pareja en `.state/solapes_descartados.json` y
`detectar` no vuelve a juntarla. «Luego» no hace nada (la clave ya está en `seen`, no se repite). Los botones antiguos
(t_e31e4d9c: sol_fusionar / sol_distintas) siguen atendidos aquí por si queda un aviso viejo en el chat.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from . import fusion
from .config import ROOT

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "solapes.yaml"
EXTENSIONES = ("py", "ts", "tsx", "js", "jsx", "yaml", "yml", "md", "json", "sql", "css", "html", "ps1", "sh", "toml")
ESTADOS = ("ready", "todo", "running", "blocked")
URL_RE = re.compile(r"https?://\S+")
TOKEN_RE = re.compile(r"[\w.\-]+(?:/[\w.\-]+)*/?")
# ruta absoluta: «c:/…», «~/…» o «/algo/otro…» (con al menos dos tramos), no pegada a otra palabra o ruta
ABS_RE = re.compile(r"(?:(?<![\w.\-/])[a-z]:/|(?<![\w.\-/:])~/|(?<![\w.\-/:])/(?=[\w.\-]+/))[\w.\-/]*")
ZONA_RE = re.compile(r"^[ \t>*-]*zona[ \t]*:[ \t]*(.+)$", re.IGNORECASE | re.MULTILINE)
ZONA = "zona:"  # prefijo interno de las carpetas declaradas en «Zona:»: chocan por contener/ser contenidas, a cualquier nivel
MAX_ESTADO = 500  # claves recordadas en el fichero de estado
DESCARTES_PATH = ROOT / ".state" / "solapes_descartados.json"
SOL_FUSIONAR, SOL_DISTINTAS, SOL_LUEGO = "sol_fusionar", "sol_distintas", "sol_luego"
SOL_ACTIONS = (SOL_FUSIONAR, SOL_DISTINTAS, SOL_LUEGO)


@dataclass(frozen=True)
class Ajustes:
    activo: bool = True
    minutos: float = 10               # cada cuánto se barre el tablero
    nivel: str = "modulo"             # archivo = solo comparten fichero; modulo = también la misma carpeta
    modulo_min_profundidad: int = 2   # tramos mínimos de una carpeta para que cuente como módulo
    estados: tuple = ESTADOS
    extensiones: tuple = EXTENSIONES
    max_tarjetas: int = 8             # tarjetas que se listan en un aviso (el resto, «y N más»)
    comparten_max: int = 5            # rutas compartidas que se citan en el aviso


def load_ajustes(path: Path | str | None = None) -> Ajustes:
    """Ajustes desde el YAML; lo que falte o no sea válido queda en su valor por defecto."""
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return Ajustes()
    if not isinstance(raw, dict):
        return Ajustes()
    d, out = Ajustes(), {}
    for f in fields(Ajustes):
        v = raw.get(f.name)
        if v is None:
            continue
        if f.name == "activo" and isinstance(v, bool):
            out[f.name] = v
        elif f.name == "minutos" and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            out[f.name] = float(v)
        elif f.name == "nivel" and v in ("archivo", "modulo"):
            out[f.name] = v
        elif f.name in ("modulo_min_profundidad", "max_tarjetas", "comparten_max") \
                and isinstance(v, int) and not isinstance(v, bool) and v >= 1:
            out[f.name] = v
        elif f.name in ("estados", "extensiones") and isinstance(v, list) and v \
                and all(isinstance(x, str) and x for x in v):
            out[f.name] = tuple(x.lower().lstrip(".") if f.name == "extensiones" else x for x in v)
    return Ajustes(**{**{f.name: getattr(d, f.name) for f in fields(Ajustes)}, **out})


def par(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


def load_descartes(path: Path | str | None = None) -> set[str]:
    """Parejas de tarjetas que Oscar marcó como «no son duplicadas» (claves `par`). Ilegible = ninguna."""
    try:
        data = json.loads(Path(path or DESCARTES_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(x) for x in data} if isinstance(data, list) else set()


def _norm_repo(path: str) -> str:
    return (path or "").replace("\\", "/").rstrip("/").lower()


def _zona_declarada(texto: str, ajustes: Ajustes, base: str) -> set[str]:
    """Rutas de las líneas «Zona: a/b, c.py» (sin distinguir mayúsculas). Un fichero va tal cual; lo demás se toma por
    carpeta («dashboard/src/app/ideas» = «dashboard/src/app/ideas/») y, además, con el prefijo ZONA para que dos
    tarjetas con la misma zona choquen aunque el nivel sea «archivo»."""
    exts, out = set(ajustes.extensiones), set()
    for linea in ZONA_RE.findall((texto or "").replace("\\", "/")):
        for item in linea.split(","):
            r = item.strip().strip("`'\"()[]").lower().removeprefix("./").lstrip("/")
            if base and r.startswith(base + "/"):
                r = r[len(base) + 1:]
            partes = [p for p in r.split("/") if p]
            if not partes or any(c.isspace() for c in r) or r.startswith(("http", "~")):
                continue
            ext = partes[-1].rsplit(".", 1)[-1] if "." in partes[-1] else ""
            if ext in exts and len(partes[-1]) > len(ext) + 1:
                out.add("/".join(partes))
            else:
                carpeta = "/".join(partes) + "/"
                out.update({carpeta, ZONA + carpeta})
    return out


def extraer_rutas(texto: str, ajustes: Ajustes, repo: str = "") -> set[str]:
    """Rutas (ficheros y carpetas con «/» final) mencionadas en un texto, normalizadas en minúsculas y con «/».
    La línea «Zona:» de la tarjeta (contract/task-template.md) se lee primero y cuenta como zona de la tarjeta."""
    exts = set(ajustes.extensiones)
    base = re.sub(r"^[a-z]:/", "", _norm_repo(repo))  # el TOKEN_RE no recoge «c:»
    out: set[str] = _zona_declarada(texto, ajustes, base)

    def _absoluta(m: re.Match) -> str:
        """Una ruta absoluta solo cuenta si está dentro del repo de la tarjeta (queda relativa); carpetas temporales,
        otros repos y el home no son ficheros comunes de nadie."""
        ruta = m.group(0)
        if ruta.startswith("~/"):
            return " "
        ruta = ruta[3:] if ruta[1:2] == ":" else re.sub(r"^/(?:[a-z]/)?", "", ruta)   # «c:/» o «/c/» (git-bash) o «/»
        if base and ruta.startswith(base + "/"):
            return " " + ruta[len(base) + 1:]
        return " "

    texto_norm = ABS_RE.sub(_absoluta, URL_RE.sub(" ", (texto or "").replace("\\", "/")).lower())
    for tok in TOKEN_RE.findall(texto_norm):
        tok = tok.strip(".-")
        if base and tok.startswith(base + "/"):
            tok = tok[len(base) + 1:]
        if not tok or tok.startswith("/"):
            continue
        es_dir = tok.endswith("/")
        partes = [p for p in tok.strip("/").split("/") if p]
        if not partes:
            continue
        ext = partes[-1].rsplit(".", 1)[-1] if "." in partes[-1] else ""
        if es_dir:
            if len(partes) >= 2 or re.sub(r"[_-]", "", partes[0]).isalpha():
                out.add("/".join(partes) + "/")
        elif ext in exts and len(partes[-1]) > len(ext) + 1:
            out.add("/".join(partes))
    return out


def _carpeta(ruta: str) -> str | None:
    if ruta.endswith("/"):
        return ruta.rstrip("/")
    return ruta.rsplit("/", 1)[0] if "/" in ruta else None


def _casan_ficheros(a: str, b: str) -> bool:
    if a.endswith("/") or b.endswith("/"):
        return False
    if a == b:
        return True
    return ("/" not in a and b.endswith("/" + a)) or ("/" not in b and a.endswith("/" + b))


def compartido(a: set[str], b: set[str], ajustes: Ajustes) -> list[str]:
    """Qué comparten dos conjuntos de rutas: ficheros comunes y, con nivel «modulo», carpetas comunes."""
    found: list[str] = []
    for x in sorted(a):
        for y in sorted(b):
            if _casan_ficheros(x, y):
                found.append(f"archivo {max(x, y, key=len)}")
            elif x.startswith(ZONA) and y.startswith(ZONA) and (x.startswith(y) or y.startswith(x)):
                found.append(f"zona {max(x, y, key=len)[len(ZONA):]}")  # una carpeta declarada contiene a la otra
    if ajustes.nivel == "modulo":
        da = {c for c in map(_carpeta, a) if c and not c.startswith(ZONA)}
        db = {c for c in map(_carpeta, b) if c and not c.startswith(ZONA)}
        for c in sorted(da & db):
            if len(c.split("/")) >= ajustes.modulo_min_profundidad:
                found.append(f"carpeta {c}/")
    return list(dict.fromkeys(found))


@dataclass
class Card:
    tid: str
    board: str
    lane: str
    status: str
    title: str
    rutas: set = field(default_factory=set)


@dataclass
class Grupo:
    repo: str
    cards: list
    comparten: list


def tarjetas_activas(lanes: dict, hermes_for, ajustes: Ajustes) -> dict[str, list[Card]]:
    """repo -> tarjetas vigiladas con sus rutas. Un tablero ilegible no corta el resto."""
    by_repo: dict[str, list[Card]] = {}
    vistos: set[tuple[str, str]] = set()
    boards = list(dict.fromkeys(l.board for l in lanes.values()
                                if getattr(l, "board", "") and getattr(l, "kind", "") in ("implement",)))
    for board in boards:
        for status in ajustes.estados:
            try:
                tasks = hermes_for(board).list_status_all(status)
            except Exception as exc:
                log.warning("solapes: %s/%s no leído: %s", board, status, exc)
                continue
            for t in tasks:
                lane = lanes.get(t.get("assignee"))
                repo = getattr(lane, "repo", "") if lane is not None else ""
                tid = t.get("id")
                if not (repo and tid) or (board, tid) in vistos:
                    continue
                vistos.add((board, tid))
                rutas = extraer_rutas(f"{t.get('title') or ''}\n{t.get('body') or ''}", ajustes, repo)
                if rutas:
                    by_repo.setdefault(_norm_repo(repo), []).append(
                        Card(tid, board, t["assignee"], t.get("status") or status, t.get("title") or "", rutas))
    return by_repo


def _enlazadas(a: Card, b: Card, hermes_for, cache: dict) -> bool:
    """¿Una es padre/hija de la otra? (cadenas de /flujo: secuenciales a propósito). Si no se puede saber, no."""
    def rel(c: Card) -> set[str]:
        if c.tid not in cache:
            try:
                show = hermes_for(c.board).show(c.tid)
                cache[c.tid] = {str(x.get("id") if isinstance(x, dict) else x)
                                for k in ("parents", "children") for x in (show.get(k) or [])}
            except Exception:
                cache[c.tid] = set()
        return cache[c.tid]
    return b.tid in rel(a) or a.tid in rel(b)


def detectar(lanes: dict, hermes_for, ajustes: Ajustes | None = None, descartes: set[str] | None = None) -> list[Grupo]:
    """Conjuntos de 2+ tarjetas del mismo repo con solape (componentes conexos de los pares que solapan).
    `descartes`: parejas (`par`) que Oscar ya dijo que no son duplicadas; no se juntan."""
    ajustes = ajustes or Ajustes()
    descartes = descartes or set()
    grupos: list[Grupo] = []
    cache: dict = {}
    for repo, cards in sorted(tarjetas_activas(lanes, hermes_for, ajustes).items()):
        parent = {c.tid: c.tid for c in cards}

        def raiz(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        comun: dict[str, list[str]] = {}
        for i, a in enumerate(cards):
            for b in cards[i + 1:]:
                c = compartido(a.rutas, b.rutas, ajustes)
                if c and par(a.tid, b.tid) not in descartes and not _enlazadas(a, b, hermes_for, cache):
                    parent[raiz(a.tid)] = raiz(b.tid)
                    comun.setdefault(a.tid, []).extend(c)
                    comun.setdefault(b.tid, []).extend(c)
        comps: dict[str, list[Card]] = {}
        for c in cards:
            comps.setdefault(raiz(c.tid), []).append(c)
        for miembros in comps.values():
            if len(miembros) > 1:
                comparten = list(dict.fromkeys(x for m in miembros for x in comun.get(m.tid, [])))
                grupos.append(Grupo(repo, sorted(miembros, key=lambda m: m.tid), comparten))
    return grupos


def clave(g: Grupo) -> str:
    return "|".join(c.tid for c in g.cards)


def aviso(g: Grupo, ajustes: Ajustes | None = None) -> str:
    ajustes = ajustes or Ajustes()
    nombre = g.repo.rsplit("/", 1)[-1]
    lines = [f"🔀 Posible solape · {nombre}: {len(g.cards)} tarjetas activas tocan lo mismo"]
    for c in g.cards[:ajustes.max_tarjetas]:
        lines.append(f"• {c.tid} «{c.title[:70]}» ({c.lane}, {c.status})")
    if len(g.cards) > ajustes.max_tarjetas:
        lines.append(f"  … y {len(g.cards) - ajustes.max_tarjetas} más")
    lines.append("Comparten: " + "; ".join(g.comparten[:ajustes.comparten_max]))
    lines.append("Sugerencia: fusionarlas en una sola (evita ramas paralelas que chocan al integrar) o dejarlas si se "
                 "complementan. Decides tú; no se fusiona nada solo.")
    return "\n".join(lines)


def teclado(g: Grupo) -> list[list[dict]]:
    return fusion.teclado_solape() + [[{"text": "⏸ Luego", "action": SOL_LUEGO}]]


def registro(g: Grupo) -> dict:
    """Registro del teclado (CallbackStore.issue): la primera tarjeta da task_id/board/lane; `cards` las lista todas."""
    first = g.cards[0]
    return {"task_id": first.tid, "board": first.board, "lane": first.lane, "title": first.title, "solape": True,
            "cards": [{"tid": c.tid, "board": c.board, "lane": c.lane, "title": c.title} for c in g.cards]}


class SolapeTicker:
    """Pasada periódica en el bucle del runner. Avisa una vez por conjunto de tarjetas (estado en `state_path`); nunca
    lanza: un fallo de Hermes, de Telegram o del YAML solo se registra."""

    def __init__(self, lanes: dict, hermes_for, *, send, state_path: Path, settings_path: Path | str | None = None,
                 clock=time.monotonic, issue=None, descartes_path: Path | None = None):
        """`issue(rec, spec)` -> (token, reply_markup) (CallbackStore.issue): con él el aviso lleva botones y se llama
        `send(texto, markup)`; sin él, `send(texto)` y solo texto."""
        self.lanes, self.hermes_for, self.send = lanes, hermes_for, send
        self.state_path, self.settings_path, self._clock = state_path, settings_path, clock
        self.issue = issue
        self.descartes_path = descartes_path or state_path.with_name("solapes_descartados.json")
        self._last: float | None = None

    def _enviar(self, g: Grupo, ajustes: Ajustes) -> None:
        if self.issue:
            self.send(aviso(g, ajustes), self.issue(registro(g), teclado(g))[1])
        else:
            self.send(aviso(g, ajustes))

    def on_button(self, action: str, rec: dict, where: dict | None, desk) -> bool:
        """Botones del aviso. Nunca fusiona, mueve ni cierra tarjetas."""
        cards = [c for c in rec.get("cards") or () if c.get("tid")]
        tids = [c["tid"] for c in cards]
        if action == SOL_LUEGO:
            desk._edit_text(where, f"⏸ Solape de {', '.join(tids)}: lo dejas para luego (no se repite este aviso)")
            return True
        if action == SOL_DISTINTAS:
            descartes = load_descartes(self.descartes_path)
            descartes.update(par(a, b) for i, a in enumerate(tids) for b in tids[i + 1:])
            try:
                self.descartes_path.parent.mkdir(parents=True, exist_ok=True)
                self.descartes_path.write_text(json.dumps(sorted(descartes)), encoding="utf-8")
            except OSError as exc:
                log.warning("solapes: no se pudo guardar el descarte: %s", exc)
                desk._edit_text(where, "No se pudo guardar el descarte; detalle en el log")
                return False
            desk._edit_text(where, f"✅ {', '.join(tids)}: no son duplicadas; no se vuelve a avisar de esta pareja")
            return True
        who = desk._who()
        for c in cards:  # SOL_FUSIONAR: solo deja constancia; fusionar lo decide Oscar/Hermes
            otras = ", ".join(t for t in tids if t != c["tid"])
            try:
                self.hermes_for(c["board"]).comment(
                    c["tid"], f"{who} marcó como duplicada con {otras} (aviso de solape). Pendiente de fusionar: decide "
                              "Oscar/Hermes; no se ha movido ni cerrado nada.", author="oscar-telegram")
            except Exception as exc:
                log.warning("solapes: comentario en %s falló: %s", c["tid"], exc)
        desk._edit_text(where, f"🔀 {', '.join(tids)}: anotado como duplicadas en las tarjetas; no se fusiona nada solo")
        return True

    def _seen(self) -> list[str]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return [str(k) for k in data] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def tick(self) -> list[Grupo]:
        try:
            ajustes = load_ajustes(self.settings_path)
            now = self._clock()
            if not ajustes.activo or (self._last is not None and now - self._last < ajustes.minutos * 60):
                return []
            self._last = now
            grupos = detectar(self.lanes, self.hermes_for, ajustes, load_descartes(self.descartes_path))
            seen = self._seen()
            fresh = [g for g in grupos if clave(g) not in seen]
            n = len(seen)
            try:
                for g in fresh:  # si un envío falla, ese conjunto se reintenta en la pasada siguiente
                    self._enviar(g, ajustes)
                    seen.append(clave(g))
            finally:
                if len(seen) > n:
                    self.state_path.parent.mkdir(parents=True, exist_ok=True)
                    self.state_path.write_text(json.dumps(seen[-MAX_ESTADO:]), encoding="utf-8")
            return grupos
        except Exception as exc:
            log.warning("solapes falló: %s", exc)
            return []
