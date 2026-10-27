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

Aviso temprano (24-10): la clase `temprano` salta ANTES de que haya conflicto: dos unidades que ya tocan los mismos
ficheros (git las fusiona limpio hoy) y alguna con PR abierto. Con las fuentes del 25-10 (solo `tarjetas`) son dos
tarjetas activas con rama lane/<id> empujada; el PR se lee aunque `pr` esté apagado como fuente. Es una DECISIÓN
(tipo «choque» de canal_avisos: «¿cuál primero?»), con botones. Va primero la que ya tiene PR; el aviso solo pide
[✅ Confirmar: X primero, la otra espera] (se anota en `.state/choques_orden.json`; no frena nada por sí mismo: lo que
frena es la prevención al lanzar). Las ramas de carril o de persona sueltas solo entran si `fuentes` las activa. Antes de cada barrido
se hace `git fetch --prune <remote>` (`fetch`), para ver ramas nuevas sin esperar a que alguien las traiga.

Fuente `pr_tarjetas` (F1-05, D30-A ampliado): PR abiertos cuya rama o «Cierra: t_x» los liga a una tarjeta VIVA
(`estados`, o running/review/done con el PR aún abierto), incluidas las ramas `fix/*`/`feat/*` de la consola. La
unidad es la tarjeta con la zona de la rama del PR. Con `pr: false` el resto de PR sueltos sigue sin contar.

Botones (23-10): TODO aviso lleva teclado. Entre dos tarjetas, los de fusion.py (fusionar/encadenar/…); siempre
[🙈 Ignorar este par] (no vuelve a avisar de ese par, sea cual sea la clase, hasta `recordar_dias`), un
[🔇 Silenciar <rama>] por cada rama sin tarjeta (lista editable en `.state/choques_silenciadas.json`; deja de contar en
avisos y en la prevención al lanzar) y [Ver PR #n] (botón URL) si la unidad tiene PR. La prevención al lanzar vive en
prevencion.py.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from . import proc as _proc
from . import fusion, solapes
from .config import ROOT

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "choques.yaml"
SILENCIADAS_PATH = ROOT / ".state" / "choques_silenciadas.json"  # ["angel/fix", …]: ramas que no cuentan
IGNORADOS_PATH = ROOT / ".state" / "choques_ignorados.json"      # {par(uid_a, uid_b): epoch del «Ignorar»}
ORDEN_PATH = ROOT / ".state" / "choques_orden.json"              # {par: {"primero": uid, "cuando": epoch}}
CHO_IGNORAR, CHO_SILENCIAR = "cho_ign", "cho_sil"
CHO_ORDEN = "cho_ord"
CHO_ACTIONS = (CHO_IGNORAR, CHO_SILENCIAR, CHO_ORDEN)
MAX_BOTONES_RAMA = 2
MAX_ESTADO = 5000  # tope de seguridad; el olvido normal es por edad (Ajustes.recordar_dias), no por número
MERGE_TREE_MIN = (2, 38)  # `merge-tree --write-tree` existe desde git 2.38
PATRONES = (
    (r"a[ñn]ad|agreg|crea|activ|habilit|introduc", r"quita|elimin|borra|desactiv|deshabilit|retira"),
    (r"renombr", r"borra|elimin"),
    (r"mueve|mover|trasl", r"deja en su sitio|no mover"),
)
FUENTES = ("tarjetas", "ramas_lane", "pr", "ramas_personas", "pr_tarjetas")
VIVAS_CON_PR = ("running", "review", "done")  # tarjetas que siguen vivas mientras su PR esté abierto
CLASES = ("aditivo", "temprano", "compatible", "conflicto", "revisar")


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
    recordar_dias: int = 30         # un par ya avisado no se repite hasta pasados N días
    temprano: bool = True             # aviso antes del conflicto: mismos ficheros y alguna rama con PR abierto
    temprano_ignorar: tuple = ()      # patrones fnmatch de ficheros que no cuentan (*/changelog.md, *.lock…)
    fetch: bool = True                # git fetch --prune del remote antes de cada barrido
    avisar_compatibles: bool = False  # «misma zona, compatibles» solo se registra; avisan conflicto y revisar
    patrones: tuple = PATRONES        # ((regex A, regex B), …) de `contradiccion.patrones`
    # Prevención al lanzar (prevencion.py). Sin YAML legible queda apagada: ante la duda, se lanza como siempre.
    prevenir_al_lanzar: bool = False
    prevenir_nivel: str = "archivo"   # archivo = solo si comparten fichero (modulo encolaría medio hub)
    prevenir_estados: tuple = ("running", "review")
    prevenir_ramas_lane: bool = True  # también ramas lane/* sin fusionar (PR no fusionado ni cerrado)
    prevenir_max_horas: int = 24      # una tarjeta no espera más de N horas por un choque: después se lanza
    prevenir_cache_minutos: float = 2  # caché de git/gh entre pasadas del planificador


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
    for k in ("avisar_compatibles", "prevenir_al_lanzar", "prevenir_ramas_lane", "temprano", "fetch"):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    if raw.get("prevenir_nivel") in ("archivo", "modulo"):
        out["prevenir_nivel"] = raw["prevenir_nivel"]
    ti = raw.get("temprano_ignorar")
    if isinstance(ti, list) and all(isinstance(x, str) and x for x in ti):
        out["temprano_ignorar"] = tuple(x.strip().replace("\\", "/").lower() for x in ti)
    pe = raw.get("prevenir_estados")
    if isinstance(pe, list) and pe and all(isinstance(x, str) and x for x in pe):
        out["prevenir_estados"] = tuple(pe)
    pc = raw.get("prevenir_cache_minutos")
    if isinstance(pc, (int, float)) and not isinstance(pc, bool) and pc >= 0:
        out["prevenir_cache_minutos"] = float(pc)
    for k in ("ramas_personas_dias", "modulo_min_profundidad", "max_unidades", "max_avisos", "comparten_max",
              "recordar_dias", "prevenir_max_horas"):
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
    board: str = ""                # solo tarjetas: tablero y carril (para los botones del aviso)
    lane: str = ""
    url: str = ""                  # URL del PR (botón «Ver PR»)


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

    def fetch(self, repo: str, remote: str) -> bool:
        """`git fetch --prune`: solo toca refs/remotes. False si falla (se sigue con lo que haya)."""
        return self._out(["fetch", "--prune", "--quiet", remote], repo)[0] == 0

    def ficheros(self, repo: str, remote: str, base: str, rama: str) -> set[str] | None:
        rc, out = self._out(["diff", "--name-only", f"{remote}/{base}...{remote}/{rama}"], repo)
        return {x.strip().replace("\\", "/").lower() for x in out.splitlines() if x.strip()} if rc == 0 else None

    def prs(self, repo: str) -> list[dict]:
        rc, out = self._out(["pr", "list", "--state", "open", "--json", "number,headRefName,title,author,url,body",
                             "--limit", "50"],
                            repo, cmd="gh")
        try:
            data = json.loads(out) if rc == 0 else []
        except ValueError:
            return []
        return [p for p in data if isinstance(p, dict) and p.get("headRefName")]

    def ramas_cerradas(self, repo: str) -> set[str]:
        """Ramas cuyo PR ya está fusionado o cerrado. Con squash o con commits después del merge, git las sigue viendo
        «sin fusionar» y, como la rama remota no se borra, chocaban para siempre con todo lo nuevo de la misma zona
        (23-10: 13 de las 15 ramas que más avisos daban tenían el PR en MERGED). Si gh falla, conjunto vacío."""
        rc, out = self._out(["pr", "list", "--state", "closed", "--json", "headRefName,state", "--limit", "300"],
                            repo, cmd="gh")
        try:
            data = json.loads(out) if rc == 0 else []
        except ValueError:
            return set()
        return {str(p["headRefName"]) for p in data if isinstance(p, dict) and p.get("headRefName")}

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
            temprano = _temprano(a, b, ajustes)
            return ("temprano", temprano) if temprano else ("aditivo", comun)
    return "compatible", comun


def _temprano(a: Unidad, b: Unidad, ajustes: Ajustes) -> list[str]:
    """Ficheros EXACTOS que tocan las dos ramas cuando aún se fusionan limpias y alguna tiene PR abierto."""
    if not ajustes.temprano or not (a.pr or b.pr):
        return []
    return [f for f in sorted(a.rutas & b.rutas)
            if not any(fnmatch.fnmatch(f.lower(), p) for p in ajustes.temprano_ignorar)]


def primero_y_despues(a: Unidad, b: Unidad) -> tuple[Unidad, Unidad]:
    """Va primero la que ya tiene PR (con las dos, el PR más antiguo); luego la que ya tiene rama."""
    peso = lambda u: (bool(u.pr), -u.pr, bool(u.rama))
    return (b, a) if peso(b) > peso(a) else (a, b)


def propuesta(clase: str, a: Unidad, b: Unidad) -> str:
    primero, despues = primero_y_despues(a, b)
    if clase == "temprano":
        return (f"{primero.etiqueta} va primero (ya tiene PR); {despues.etiqueta} espera a que se integre y entonces "
                f"se rebasa sobre la base nueva. Confirma el orden con el botón.")
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
                  ahora: float, silenciadas=frozenset(), buscar=None) -> list[Unidad]:
    """Unidades abiertas de un repo con su zona. `tarjetas`: dicts de Hermes ya filtrados a este repo. Las ramas de
    `silenciadas` (botón «Silenciar rama») no cuentan: ni como unidad propia ni como zona de su tarjeta.
    `buscar(tid)` -> dict de la tarjeta (con `status`) o None: para las tarjetas que solo se conocen por su PR."""
    sol = _solapes_aj(ajustes)
    ramas = {r: (ts, autor) for r, ts, autor in git.ramas_remotas(repo, remote, base)}
    pr_vivos = "pr_tarjetas" in ajustes.fuentes
    # PR abiertos: con fuente `pr`/`pr_tarjetas` crean unidades y reactivan ramas cerradas; con solo tarjetas (25-10)
    # se leen igual, pero solo para marcar qué tarjeta ya tiene PR (lo que pide el aviso `temprano`).
    usa_prs = "pr" in ajustes.fuentes or pr_vivos
    prs_abiertos = {p["headRefName"]: p for p in git.prs(repo)} if (usa_prs or ajustes.temprano) else {}
    prs = prs_abiertos if usa_prs else {}
    cerradas = getattr(git, "ramas_cerradas", lambda _r: set())(repo) - set(prs)  # un PR nuevo abierto la reactiva
    ramas = {r: v for r, v in ramas.items() if r not in cerradas and r not in silenciadas}
    unidades: dict[str, Unidad] = {}
    usadas: set[str] = set()

    def con_rama(u: Unidad, rama: str):
        z = git.ficheros(repo, remote, base, rama)
        if z is not None:
            u.rama, u.rutas = rama, z
        usadas.add(rama)
        if rama in prs_abiertos:
            u.pr = int(prs_abiertos[rama].get("number") or 0)
            u.url = str(prs_abiertos[rama].get("url") or "")
            u.dueno = u.dueno or str((prs_abiertos[rama].get("author") or {}).get("login") or "")

    if "tarjetas" in ajustes.fuentes:
        for t in tarjetas:
            tid = str(t.get("id") or "")
            if not tid:
                continue
            texto = f"{t.get('title') or ''}\n{t.get('body') or ''}"
            u = Unidad(tid, _etiq(tid, t.get("title") or ""), rutas=solapes.extraer_rutas(texto, sol, repo), texto=texto,
                       dueno=str(t.get("assignee") or ""), origen="tarjeta",
                       board=str(t.get("_board") or ""), lane=str(t.get("assignee") or ""))
            if f"lane/{tid}" in ramas:
                con_rama(u, f"lane/{tid}")
                u.texto = texto
            unidades[tid] = u
    if pr_vivos:
        from .reconcile import pr_refs  # import tardío: reconcile no depende de choques
        conocidas = {str(t.get("id") or ""): t for t in tarjetas}
        for rama, p in sorted(prs.items()):
            if rama in usadas or rama in silenciadas or rama not in ramas:
                continue
            for tid in pr_refs(p):
                t = conocidas.get(tid)
                if t is None and buscar is not None:
                    try:
                        t = buscar(tid)
                    except Exception as exc:
                        log.debug("choques: %s no leída: %s", tid, exc)
                        t = None
                if not t or (tid not in conocidas and str(t.get("status") or "") not in VIVAS_CON_PR):
                    continue
                u = unidades.get(tid)
                if u is None:
                    texto = f"{t.get('title') or ''}\n{t.get('body') or ''}"
                    u = Unidad(tid, _etiq(tid, t.get("title") or ""), texto=texto, dueno=str(t.get("assignee") or ""),
                               origen="tarjeta", board=str(t.get("_board") or ""), lane=str(t.get("assignee") or ""))
                    unidades[tid] = u
                if not u.rama:
                    con_rama(u, rama)
                break
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


def detectar(lanes: dict, hermes_for, git: Git, ajustes: Ajustes | None = None, ahora: float | None = None,
             silenciadas=frozenset()) -> list[Choque]:
    """Choques no aditivos de todos los repos de los carriles de código. Un tablero o repo ilegible no corta el resto."""
    ajustes = ajustes or Ajustes()
    ahora = time.time() if ahora is None else ahora
    repos: dict[str, tuple] = {}
    slugs: dict[str, str] = {}  # repo -> owner/repo de GitHub (URL del PR si `gh` no la dio)
    for l in lanes.values():
        if getattr(l, "kind", "") == "implement" and getattr(l, "repo", "") and getattr(l, "base", ""):
            repos.setdefault(solapes._norm_repo(l.repo), (l.repo, getattr(l, "remote", "origin") or "origin", l.base, []))
            if getattr(l, "github", ""):
                slugs.setdefault(solapes._norm_repo(l.repo), l.github)
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
                    repos[key][3].append({**t, "_board": board})
    cache: dict[str, dict | None] = {}

    def buscar(tid: str) -> dict | None:
        """Tarjeta ligada a un PR abierto que no está en `estados` (p. ej. done con el PR sin fusionar)."""
        if tid not in cache:
            cache[tid] = None
            for board in boards:
                try:
                    task = (hermes_for(board).show(tid) or {}).get("task") or {}
                except Exception:
                    continue
                if task.get("id") or task.get("status"):
                    cache[tid] = {**task, "id": tid, "_board": board}
                    break
        return cache[tid]

    out: list[Choque] = []
    for repo, remote, base, tarjetas in repos.values():
        try:
            if ajustes.fetch:
                getattr(git, "fetch", lambda _r, _m: False)(repo, remote)
            us = unidades_repo(repo, remote, base, tarjetas, git, ajustes, ahora, silenciadas, buscar=buscar)
            slug = slugs.get(solapes._norm_repo(repo), "")
            for u in us:
                if u.pr and not u.url and slug:
                    u.url = f"https://github.com/{slug}/pull/{u.pr}"
            for i, a in enumerate(us):
                for b in us[i + 1:]:
                    if a.rama and a.rama == b.rama:
                        continue
                    if not a.rama and not b.rama and not contradice(a, b, ajustes.patrones):
                        continue  # dos tarjetas solo de texto: ya las avisa solapes.py
                    clase, comun = clasificar(a, b, repo, remote, git, ajustes)
                    if clase == "aditivo":
                        log.debug("choques: %s y %s avanzan en paralelo (aditivo)", a.uid, b.uid)  # F1-06: era INFO
                        continue
                    out.append(Choque(solapes._norm_repo(repo), clase, a, b, comun, propuesta(clase, a, b)))
        except Exception as exc:
            log.warning("choques: %s no analizado: %s", repo, exc)
    return out


def clave(c: Choque) -> str:
    return c.clase + ":" + "|".join(sorted((c.a.uid, c.b.uid)))


_ICONO = {"temprano": "⏳", "compatible": "🔗", "conflicto": "⛔", "revisar": "❓"}
_TITULO = {"temprano": "tocan los mismos ficheros (aún sin conflicto)", "compatible": "misma zona, compatibles", "conflicto": "conflicto real de merge",
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
        *(["Hoy git las fusiona limpio; si no se respeta el orden, el conflicto llegará cuando una se integre."]
          if c.clase == "temprano" else []),
        "Propuesta: " + c.propuesta,
        ("Lo que esté en ready y choque no se lanza en paralelo (espera a la otra); el resto lo decides tú."
         if ajustes.prevenir_al_lanzar else "No se frena ni se encadena nada solo: decides tú.")])


def registro(c: Choque) -> dict | None:
    """Registro del teclado si las dos unidades son tarjetas (los botones actúan sobre tarjetas, no sobre ramas)."""
    return fusion.registro_tarjetas([{"tid": u.uid, "board": u.board, "lane": u.lane, "title": u.etiqueta}
                                     for u in (c.a, c.b) if u.origen == "tarjeta" and u.board])


def par_uid(c: Choque) -> str:
    """Par sin la clase: «Ignorar» vale aunque el par pase de compatible a conflicto."""
    return solapes.par(c.a.uid, c.b.uid)


def teclado(c: Choque) -> tuple[dict, list[list[dict]], list[list[dict]]]:
    """(registro, botones con callback, filas de botones URL) para CUALQUIER aviso de choque.

    El registro siempre lleva `task_id` (el despachador lo usa para retirar copias): con dos tarjetas es el de fusion.py
    (sus botones siguen igual) y, si no, una clave `choque-<hash del par>` que no es ninguna tarjeta. `board`/`lane` solo se ponen
    si hay tarjeta: con ellos el despachador aplica los permisos por marca; un par solo de ramas lo decide Oscar."""
    base = registro(c)
    silenciables = [u.rama for u in (c.a, c.b) if u.rama and u.origen != "tarjeta"][:MAX_BOTONES_RAMA]
    urls = [(u.pr, u.url) for u in (c.a, c.b) if u.pr and u.url]
    # task_id solo con caracteres seguros: MessageStore lo usa como nombre de fichero (`:`, `|` y `/` no valen en Windows)
    rec = dict(base) if base else {"task_id": "choque-" + hashlib.sha1(par_uid(c).encode()).hexdigest()[:10]}
    tarjeta = next((u for u in (c.a, c.b) if u.origen == "tarjeta" and u.board), None)
    if not base and tarjeta:
        rec.update(board=tarjeta.board, lane=tarjeta.lane)
    rec.update(choque=True, par=par_uid(c), clase=c.clase, repo=c.repo, silenciables=silenciables,
               urls=[{"text": f"🔎 Ver PR #{n}", "url": u} for n, u in urls])
    spec = fusion.teclado_solape() if base else []
    if c.clase == "temprano":
        primero, despues = primero_y_despues(c.a, c.b)
        rec.update(primero=primero.uid, despues=despues.uid)
        ref = f"PR #{primero.pr}" if primero.pr else primero.uid
        spec = [[{"text": f"✅ Confirmar: {ref} primero, la otra espera", "action": CHO_ORDEN}]] + spec
    fila = [{"text": "🙈 Ignorar este par", "action": CHO_IGNORAR}]
    fila += [{"text": f"🔇 Silenciar {r[:24]}", "action": CHO_SILENCIAR, "index": i} for i, r in enumerate(silenciables)]
    spec += [fila[:2]] + ([fila[2:]] if fila[2:] else [])
    return rec, spec, [[{"text": b["text"], "url": b["url"]} for b in rec["urls"]]] if rec["urls"] else []


def load_orden(path: Path | str | None = None) -> dict:
    """{par: {"primero": uid, "cuando": epoch}} de los órdenes confirmados. Ilegible = ninguno."""
    try:
        data = json.loads(Path(path or ORDEN_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def load_silenciadas(path: Path | str | None = None) -> set[str]:
    """Ramas silenciadas (lista JSON editable a mano). Ilegible = ninguna."""
    try:
        data = json.loads(Path(path or SILENCIADAS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(x).strip() for x in data if str(x).strip()} if isinstance(data, list) else set()


def load_ignorados(path: Path | str | None = None, *, ahora: float | None = None, dias: int | None = None) -> dict:
    """{par: epoch} de pares ignorados; con `dias`, solo los que siguen vigentes."""
    try:
        data = json.loads(Path(path or IGNORADOS_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))} if isinstance(data, dict) else {}
    if dias is not None:
        ahora = time.time() if ahora is None else ahora
        out = {k: t for k, t in out.items() if ahora - t < dias * 86400}
    return out


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


class ChoquesTicker:
    """Pasada periódica en el bucle del runner. Avisa una vez por par + clase (estado en `state_path`); nunca lanza."""

    def __init__(self, lanes: dict, hermes_for, *, send, state_path: Path, settings_path: Path | str | None = None,
                 git: Git | None = None, clock=time.monotonic, issue=None, wall=time.time,
                 silenciadas_path: Path | None = None, ignorados_path: Path | None = None,
                 orden_path: Path | None = None):
        self.lanes, self.hermes_for, self.send = lanes, hermes_for, send
        self.state_path, self.settings_path, self.git, self._clock = state_path, settings_path, git or Git(), clock
        self._wall = wall  # reloj de pared para la caducidad de lo ya avisado (el de `clock` es monotónico)
        self.issue = issue  # CallbackStore.issue: con él, TODO aviso lleva botones y se llama `send(texto, markup)`
        self.silenciadas_path = Path(silenciadas_path or SILENCIADAS_PATH)
        self.ignorados_path = Path(ignorados_path or IGNORADOS_PATH)
        self.orden_path = Path(orden_path or ORDEN_PATH)
        self._last: float | None = None

    def _enviar(self, c: Choque, ajustes: Ajustes) -> None:
        if not self.issue:
            self.send(aviso(c, ajustes))
            return
        rec, spec, urls = teclado(c)
        markup = self.issue(rec, spec)[1]
        markup = {**markup, "inline_keyboard": markup["inline_keyboard"] + urls}  # URL: Telegram los abre, no llegan
        self.send(aviso(c, ajustes), markup)

    # --- botones 🙈 Ignorar / 🔇 Silenciar (los despacha DecisionDesk._act con CHO_ACTIONS) --------------------------

    def on_button(self, action: str, rec: dict, where: dict | None, desk, index=None) -> bool:
        who = desk._who() if hasattr(desk, "_who") else "Oscar"
        if action == CHO_IGNORAR:
            par = str(rec.get("par") or "")
            if not par:
                return False
            data = load_ignorados(self.ignorados_path)
            data[par] = self._wall()
            try:
                _write_json(self.ignorados_path, data)
            except OSError as exc:
                log.warning("choques: no se pudo guardar el par ignorado: %s", exc)
                return False
            dias = load_ajustes(self.settings_path).recordar_dias
            desk._edit_text(where, f"🙈 {par.replace('|', ' y ')}: ignorado ({who}); no se vuelve a avisar de este par en "
                                   f"{dias} días ni frena lanzamientos")
            return True
        if action == CHO_ORDEN:
            par, primero = str(rec.get("par") or ""), str(rec.get("primero") or "")
            if not par or not primero:
                return False
            data = load_orden(self.orden_path)
            data[par] = {"primero": primero, "cuando": self._wall()}
            try:
                _write_json(self.orden_path, data)
            except OSError as exc:
                log.warning("choques: no se pudo guardar el orden confirmado: %s", exc)
                return False
            desk._edit_text(where, f"✅ {primero} va primero ({who}); la otra espera a que se integre y se rebasa "
                                   "sobre la base nueva. El hub no la frena sola: avisa a quien la lleve")
            return True
        if action == CHO_SILENCIAR:
            try:
                rama = (rec.get("silenciables") or [])[int(index)]
            except (TypeError, ValueError, IndexError):
                desk._edit_text(where, "No reconozco la rama elegida; espera al próximo aviso")
                return True
            ramas = load_silenciadas(self.silenciadas_path) | {rama}
            try:
                _write_json(self.silenciadas_path, sorted(ramas))
            except OSError as exc:
                log.warning("choques: no se pudo guardar la rama silenciada: %s", exc)
                return False
            desk._edit_text(where, f"🔇 {rama}: silenciada ({who}). No sale en avisos de choque ni frena tarjetas. Para "
                                   f"reactivarla, quítala de .state/{self.silenciadas_path.name}")
            return True
        return False

    def _seen(self) -> dict[str, float]:
        """{clave: momento del aviso}. Antes era una lista recortada a los últimos 500: con decenas de ramas hay
        miles de pares, los más viejos salían de la lista y en la pasada siguiente volvían a avisarse (bucle de avisos
        del 23-10). Una lista antigua se migra con la hora actual."""
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if isinstance(data, list):
            now = self._wall()
            return {str(k): now for k in data}
        if isinstance(data, dict):
            return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}
        return {}

    def _save(self, seen: dict[str, float]) -> None:
        keep = sorted(seen.items(), key=lambda kv: kv[1])[-MAX_ESTADO:]
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(dict(keep)), encoding="utf-8")

    def tick(self) -> list[Choque]:
        try:
            ajustes = load_ajustes(self.settings_path)
            now = self._clock()
            if not ajustes.activo or (self._last is not None and now - self._last < ajustes.minutos * 60):
                return []
            self._last = now
            wall = self._wall()
            choques = detectar(self.lanes, self.hermes_for, self.git, ajustes,
                               silenciadas=frozenset(load_silenciadas(self.silenciadas_path)))
            ignorados = load_ignorados(self.ignorados_path, ahora=wall, dias=ajustes.recordar_dias)
            choques = [c for c in choques if par_uid(c) not in ignorados]
            seen = self._seen()
            vigentes = {k: t for k, t in seen.items() if wall - t < ajustes.recordar_dias * 86400}
            changed = len(vigentes) != len(seen)
            seen = vigentes
            nuevos = [c for c in choques if clave(c) not in seen]
            silenciosos = [c for c in nuevos if c.clase == "compatible" and not ajustes.avisar_compatibles]
            for c in silenciosos:  # se registran sin mensaje: misma zona pero compatibles, no hace falta decidir nada
                log.info("choques: %s y %s, misma zona y compatibles (no se avisa)", c.a.uid, c.b.uid)
                seen[clave(c)] = wall
                changed = True
            fresh = [c for c in nuevos if c not in silenciosos][:ajustes.max_avisos]
            try:
                for c in fresh:  # si un envío falla, ese par se reintenta en la pasada siguiente
                    self._enviar(c, ajustes)
                    seen[clave(c)] = wall
                    changed = True
            finally:
                if changed:
                    self._save(seen)
            return choques
        except Exception as exc:
            log.warning("choques falló: %s", exc)
            return []
