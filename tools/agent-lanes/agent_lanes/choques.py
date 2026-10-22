"""Choques entre tareas (t_b28e05bd, entrega E1): mira lo que hay en git, no solo el texto de las tarjetas.

Compara, por repo, las «unidades de trabajo» abiertas: tarjetas activas, ramas remotas `lane/*`, PR abiertos y ramas de
personas (no `lane/*`) con commits recientes sin fusionar. La zona de cada unidad sale de `git diff --name-only
<base>...<rama>` cuando hay rama y del texto de la tarjeta (`solapes.extraer_rutas`) cuando no. Cada par se clasifica:

- aditivo     sin zona común, o mismo fichero y `git merge-tree --write-tree` limpio: avanza en paralelo, no se avisa;
- compatible  misma zona y no se puede probar con git (alguna es solo texto): se propone fusionar o encadenar;
- conflicto   `merge-tree` con conflicto real entre dos ramas: se propone cuál va primero;
- revisar     mismo módulo y objetivos opuestos (`choques.yaml` → `contradiccion`): lo decide Oscar.

E1 SOLO AVISA a Oscar (una vez por conjunto + clase): no frena, no encola, no menciona (E2) ni toca el ciclo de review
(E4). Un fallo de git/`gh`/Hermes degrada a «sin información»: nunca lanza. Ajustes en `choques.yaml` (se relee en cada
pasada; ausente o inválido = defaults de `Ajustes`). Spec: specs/choques-entre-tareas.md.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from . import proc as _proc
from . import solapes
from .config import ROOT

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "choques.yaml"
MAX_ESTADO = 500
MERGE_TREE_MIN = (2, 38)  # `merge-tree --write-tree` existe desde git 2.38
PATRONES = (
    (r"a[ñn]ad|agreg|crea|activ|habilit|introduc", r"quita|elimin|borra|desactiv|deshabilit|retira"),
    (r"renombr", r"borra|elimin"),
    (r"mueve|mover|trasl", r"deja en su sitio|no mover"),
)
FUENTES = ("tarjetas", "ramas_lane", "pr", "ramas_personas")
CLASES = ("aditivo", "compatible", "conflicto", "revisar")


@dataclass(frozen=True)
class Ajustes:
    activo: bool = True
    minutos: float = 10
    fuentes: tuple = FUENTES
    ramas_personas_dias: int = 14
    estados: tuple = solapes.ESTADOS
    nivel: str = "modulo"
    modulo_min_profundidad: int = 2
    max_unidades: int = 40
    max_avisos: int = 6
    comparten_max: int = 5
    patrones: tuple = PATRONES        # ((regex A, regex B), …) de `contradiccion.patrones`


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v >= 1


def load_ajustes(path: Path | str | None = None) -> Ajustes:
    """Ajustes desde el YAML; lo que falte o no sea válido queda en su valor por defecto."""
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return Ajustes()
    if not isinstance(raw, dict):
        return Ajustes()
    out: dict = {}
    if isinstance(raw.get("activo"), bool):
        out["activo"] = raw["activo"]
    m = raw.get("minutos")
    if isinstance(m, (int, float)) and not isinstance(m, bool) and m > 0:
        out["minutos"] = float(m)
    if raw.get("nivel") in ("archivo", "modulo"):
        out["nivel"] = raw["nivel"]
    for k in ("ramas_personas_dias", "modulo_min_profundidad", "max_unidades", "max_avisos", "comparten_max"):
        if _int(raw.get(k)):
            out[k] = raw[k]
    f = raw.get("fuentes")
    if isinstance(f, dict):
        out["fuentes"] = tuple(k for k in FUENTES if f.get(k, True) is not False)
    e = raw.get("estados")
    if isinstance(e, list) and e and all(isinstance(x, str) and x for x in e):
        out["estados"] = tuple(e)
    c = raw.get("contradiccion")
    pats = c.get("patrones") if isinstance(c, dict) else None
    if isinstance(pats, list):
        ok = []
        for p in pats:
            if isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) and x for x in p):
                try:
                    re.compile(p[0]), re.compile(p[1])
                    ok.append((p[0], p[1]))
                except re.error:
                    continue
        out["patrones"] = tuple(ok)
    return Ajustes(**{**{x.name: getattr(Ajustes(), x.name) for x in fields(Ajustes)}, **out})


def _solapes_aj(a: Ajustes) -> solapes.Ajustes:
    return solapes.Ajustes(nivel=a.nivel, modulo_min_profundidad=a.modulo_min_profundidad)


@dataclass
class Unidad:
    uid: str                       # clave estable: id de tarjeta si la hay, si no la rama
    etiqueta: str
    rama: str = ""                 # rama remota sin el remote (lane/t_x, angel/fix); "" = solo tarjeta
    rutas: set = field(default_factory=set)
    texto: str = ""
    dueno: str = ""                # autor de la rama / assignee de la tarjeta (E2 lo convertirá en mención)
    origen: str = "tarjeta"        # tarjeta | lane | pr | persona
    pr: int = 0


@dataclass
class Choque:
    repo: str
    clase: str
    a: Unidad
    b: Unidad
    comparten: list
    propuesta: str


class Git:
    """Lectura de git/gh con runner inyectable (los tests no tocan git ni gh reales). Todo falla a «sin información»."""

    def __init__(self, runner=_proc.run):
        self._run = runner
        self._merge_tree_ok: bool | None = None

    def _out(self, args: list[str], repo: str | None = None, cmd: str = "git") -> tuple[int, str]:
        full = [cmd, "-C", repo, *args] if repo and cmd == "git" else [cmd, *args]
        try:
            cp = self._run(full, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                           **({"cwd": repo} if cmd != "git" and repo else {}))
        except Exception as exc:
            log.debug("choques: %s falló: %s", " ".join(full[:4]), exc)
            return 127, ""
        return cp.returncode, cp.stdout or ""

    def ramas_remotas(self, repo: str, remote: str, base: str) -> list[tuple[str, int, str]]:
        """(rama sin remote, epoch del último commit, autor) de las ramas remotas sin fusionar en la base."""
        rc, nomerged = self._out(["branch", "-r", "--no-merged", f"{remote}/{base}", "--format=%(refname:short)"], repo)
        if rc != 0:
            return []
        pend = {x.strip() for x in nomerged.splitlines() if x.strip()}
        rc, out = self._out(["for-each-ref", f"refs/remotes/{remote}", "--format=%(refname:short)|%(committerdate:unix)|%(authorname)"], repo)
        if rc != 0:
            return []
        res = []
        for line in out.splitlines():
            ref, _, rest = line.partition("|")
            ts, _, autor = rest.partition("|")
            if ref in pend and ref.startswith(remote + "/") and ref not in (f"{remote}/HEAD", f"{remote}/{base}"):
                res.append((ref[len(remote) + 1:], int(ts) if ts.isdigit() else 0, autor.strip()))
        return res

    def ficheros(self, repo: str, remote: str, base: str, rama: str) -> set[str] | None:
        rc, out = self._out(["diff", "--name-only", f"{remote}/{base}...{remote}/{rama}"], repo)
        return {x.strip().replace("\\", "/").lower() for x in out.splitlines() if x.strip()} if rc == 0 else None

    def prs(self, repo: str) -> list[dict]:
        rc, out = self._out(["pr", "list", "--state", "open", "--json", "number,headRefName,title,author", "--limit", "50"],
                            repo, cmd="gh")
        try:
            data = json.loads(out) if rc == 0 else []
        except ValueError:
            return []
        return [p for p in data if isinstance(p, dict) and p.get("headRefName")]

    def merge_tree_disponible(self, repo: str) -> bool:
        if self._merge_tree_ok is None:
            rc, out = self._out(["--version"], repo)
            m = re.search(r"(\d+)\.(\d+)", out)
            self._merge_tree_ok = rc == 0 and bool(m) and (int(m[1]), int(m[2])) >= MERGE_TREE_MIN
        return self._merge_tree_ok

    def choque_real(self, repo: str, remote: str, a: str, b: str) -> bool | None:
        """True = conflicto real, False = fusión limpia, None = no se pudo saber (git viejo o error)."""
        if not self.merge_tree_disponible(repo):
            return None
        rc, _ = self._out(["merge-tree", "--write-tree", f"{remote}/{a}", f"{remote}/{b}"], repo)
        return True if rc == 1 else False if rc == 0 else None


def contradice(a: Unidad, b: Unidad, patrones: tuple) -> bool:
    ta, tb = a.texto.lower(), b.texto.lower()
    if not (ta and tb):
        return False
    for x, y in patrones:
        ax, ay, bx, by = (bool(re.search(p, t)) for t in (ta, tb) for p in (x, y))
        if (ax and not ay and by and not bx) or (ay and not ax and bx and not by):
            return True
    return False


def clasificar(a: Unidad, b: Unidad, repo: str, remote: str, git: Git, ajustes: Ajustes) -> tuple[str, list[str]]:
    """(clase, qué comparten). Sin zona común es aditivo; con ella, merge-tree (dos ramas) o texto (compatible)."""
    comun = solapes.compartido(a.rutas, b.rutas, _solapes_aj(ajustes))
    if not comun:
        return "aditivo", []
    if contradice(a, b, ajustes.patrones):
        return "revisar", comun
    if a.rama and b.rama:
        real = git.choque_real(repo, remote, a.rama, b.rama)
        if real is True:
            return "conflicto", comun
        if real is False:
            return "aditivo", comun
    return "compatible", comun


def propuesta(clase: str, a: Unidad, b: Unidad) -> str:
    peso = lambda u: (bool(u.pr), bool(u.rama))  # va primero la que ya tiene PR, luego la que ya tiene rama
    primero, despues = (b, a) if peso(b) > peso(a) else (a, b)
    if clase == "conflicto":
        return (f"{primero.etiqueta} primero (ya tiene {'PR' if primero.pr else 'rama'}); {despues.etiqueta} espera a que se "
                f"integre y se rehace sobre la base nueva, o se fusionan en una sola tarjeta.")
    if clase == "revisar":
        return (f"Parecen pedir cosas opuestas sobre lo mismo. Decide cuál manda: {primero.etiqueta} primero y "
                f"{despues.etiqueta} se reescribe o se descarta; no se resuelve solo.")
    return (f"Misma zona y compatibles: fusionarlas en una sola, o encadenar {despues.etiqueta} detrás de "
            f"{primero.etiqueta} para que parta de la base nueva.")


def _etiq(tid: str, titulo: str) -> str:
    return f"{tid} «{titulo[:60]}»" if titulo else tid


def unidades_repo(repo: str, remote: str, base: str, tarjetas: list[dict], git: Git, ajustes: Ajustes,
                  ahora: float) -> list[Unidad]:
    """Unidades abiertas de un repo con su zona. `tarjetas`: dicts de Hermes ya filtrados a este repo."""
    sol = _solapes_aj(ajustes)
    ramas = {r: (ts, autor) for r, ts, autor in git.ramas_remotas(repo, remote, base)}
    prs = {p["headRefName"]: p for p in git.prs(repo)} if "pr" in ajustes.fuentes else {}
    unidades: dict[str, Unidad] = {}
    usadas: set[str] = set()

    def con_rama(u: Unidad, rama: str):
        z = git.ficheros(repo, remote, base, rama)
        if z is not None:
            u.rama, u.rutas = rama, z
        usadas.add(rama)
        if rama in prs:
            u.pr = int(prs[rama].get("number") or 0)
            u.dueno = u.dueno or str((prs[rama].get("author") or {}).get("login") or "")

    if "tarjetas" in ajustes.fuentes:
        for t in tarjetas:
            tid = str(t.get("id") or "")
            if not tid:
                continue
            texto = f"{t.get('title') or ''}\n{t.get('body') or ''}"
            u = Unidad(tid, _etiq(tid, t.get("title") or ""), rutas=solapes.extraer_rutas(texto, sol, repo), texto=texto,
                       dueno=str(t.get("assignee") or ""), origen="tarjeta")
            if f"lane/{tid}" in ramas:
                con_rama(u, f"lane/{tid}")
                u.texto = texto
            unidades[tid] = u
    for rama, (ts, autor) in sorted(ramas.items()):
        if rama in usadas:
            continue
        es_lane = rama.startswith("lane/")
        if es_lane and "ramas_lane" not in ajustes.fuentes:
            continue
        if not es_lane:
            reciente = ts and ahora - ts <= ajustes.ramas_personas_dias * 86400
            if "ramas_personas" not in ajustes.fuentes or not reciente:
                if rama not in prs or "pr" not in ajustes.fuentes:
                    continue
        u = Unidad(rama, rama, dueno=autor, origen="lane" if es_lane else "persona",
                   texto=str((prs.get(rama) or {}).get("title") or ""))
        con_rama(u, rama)
        if u.rama and u.rutas:
            unidades[rama] = u
    return [u for u in unidades.values() if u.rutas][:ajustes.max_unidades]


def detectar(lanes: dict, hermes_for, git: Git, ajustes: Ajustes | None = None, ahora: float | None = None) -> list[Choque]:
    """Choques no aditivos de todos los repos de los carriles de código. Un tablero o repo ilegible no corta el resto."""
    ajustes = ajustes or Ajustes()
    ahora = time.time() if ahora is None else ahora
    repos: dict[str, tuple] = {}
    for l in lanes.values():
        if getattr(l, "kind", "") == "implement" and getattr(l, "repo", "") and getattr(l, "base", ""):
            repos.setdefault(solapes._norm_repo(l.repo), (l.repo, getattr(l, "remote", "origin") or "origin", l.base, []))
    boards = list(dict.fromkeys(l.board for l in lanes.values()
                                if getattr(l, "board", "") and getattr(l, "kind", "") == "implement"))
    vistos: set[tuple[str, str]] = set()
    for board in boards:
        for status in ajustes.estados:
            try:
                tasks = hermes_for(board).list_status_all(status)
            except Exception as exc:
                log.warning("choques: %s/%s no leído: %s", board, status, exc)
                continue
            for t in tasks:
                lane = lanes.get(t.get("assignee"))
                key = solapes._norm_repo(getattr(lane, "repo", "")) if lane is not None else ""
                if key in repos and t.get("id") and (board, t["id"]) not in vistos:
                    vistos.add((board, t["id"]))
                    repos[key][3].append(t)
    out: list[Choque] = []
    for repo, remote, base, tarjetas in repos.values():
        try:
            us = unidades_repo(repo, remote, base, tarjetas, git, ajustes, ahora)
            for i, a in enumerate(us):
                for b in us[i + 1:]:
                    if a.rama and a.rama == b.rama:
                        continue
                    if not a.rama and not b.rama and not contradice(a, b, ajustes.patrones):
                        continue  # dos tarjetas solo de texto: ya las avisa solapes.py
                    clase, comun = clasificar(a, b, repo, remote, git, ajustes)
                    if clase == "aditivo":
                        log.info("choques: %s y %s avanzan en paralelo (aditivo)", a.uid, b.uid)
                        continue
                    out.append(Choque(solapes._norm_repo(repo), clase, a, b, comun, propuesta(clase, a, b)))
        except Exception as exc:
            log.warning("choques: %s no analizado: %s", repo, exc)
    return out


def clave(c: Choque) -> str:
    return c.clase + ":" + "|".join(sorted((c.a.uid, c.b.uid)))


_ICONO = {"compatible": "🔗", "conflicto": "⛔", "revisar": "❓"}
_TITULO = {"compatible": "misma zona, compatibles", "conflicto": "conflicto real de merge",
           "revisar": "intención contradictoria"}


def _linea(u: Unidad) -> str:
    extra = ", ".join(x for x in (u.origen, f"PR #{u.pr}" if u.pr else "", u.dueno) if x)
    return f"• {u.etiqueta} ({extra})"


def aviso(c: Choque, ajustes: Ajustes | None = None) -> str:
    ajustes = ajustes or Ajustes()
    nombre = c.repo.rsplit("/", 1)[-1]
    return "\n".join([
        f"{_ICONO.get(c.clase, '🔀')} Choque entre tareas · {nombre}: {_TITULO.get(c.clase, c.clase)}",
        _linea(c.a), _linea(c.b),
        "Comparten: " + "; ".join(c.comparten[:ajustes.comparten_max]),
        "Propuesta: " + c.propuesta,
        "Solo aviso: no se frena ni se encadena nada solo. Decides tú."])


class ChoquesTicker:
    """Pasada periódica en el bucle del runner. Avisa una vez por par + clase (estado en `state_path`); nunca lanza."""

    def __init__(self, lanes: dict, hermes_for, *, send, state_path: Path, settings_path: Path | str | None = None,
                 git: Git | None = None, clock=time.monotonic):
        self.lanes, self.hermes_for, self.send = lanes, hermes_for, send
        self.state_path, self.settings_path, self.git, self._clock = state_path, settings_path, git or Git(), clock
        self._last: float | None = None

    def _seen(self) -> list[str]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return [str(k) for k in data] if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def tick(self) -> list[Choque]:
        try:
            ajustes = load_ajustes(self.settings_path)
            now = self._clock()
            if not ajustes.activo or (self._last is not None and now - self._last < ajustes.minutos * 60):
                return []
            self._last = now
            choques = detectar(self.lanes, self.hermes_for, self.git, ajustes)
            seen = self._seen()
            fresh = [c for c in choques if clave(c) not in seen][:ajustes.max_avisos]
            n = len(seen)
            try:
                for c in fresh:  # si un envío falla, ese par se reintenta en la pasada siguiente
                    self.send(aviso(c, ajustes))
                    seen.append(clave(c))
            finally:
                if len(seen) > n:
                    self.state_path.parent.mkdir(parents=True, exist_ok=True)
                    self.state_path.write_text(json.dumps(seen[-MAX_ESTADO:]), encoding="utf-8")
            return choques
        except Exception as exc:
            log.warning("choques falló: %s", exc)
            return []
