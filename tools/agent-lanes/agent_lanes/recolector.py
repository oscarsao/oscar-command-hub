"""Recolector nocturno de ramas y worktrees ya integrados (tarjetas t_88f59559 y t_96ab4df6; P18 del plan del hub).

Decisión de Oscar (5-nov-2026): «que el sistema borre las ramas una vez integradas; lo que no se pueda borrar en local, a
una carpeta legacy para que yo lo identifique y lo borre a mano».

Una vez al día (RecolectorTicker, bucle del runner; apagado por defecto, bloque `recolector:` de lanes.yaml) o a mano
(`python -m agent_lanes.recolector [--real]`) recorre los repos de los carriles y, por repo:

1. Worktrees `lane-<tid>` bajo la raíz del carril, solo de tarjetas done/archived y sin actividad reciente:
   - limpio, sin commits sin empujar y rama ya en la base → `worktree remove` + `branch -d`;
   - con cambios, commits sin empujar o rama sin fusionar → `git worktree move` a `<legacy>/<repo>/` con un LEEME.txt
     (una sola vez; el aviso «limpieza local pendiente» deja de repetirse).
2. Ramas remotas ya fusionadas en la base → `push --delete` por lotes; ramas locales fusionadas → `branch -d`.
3. Referencias locales `refs/remotes/pr/*` de PR ya fusionados.

Protegidas SIEMPRE: master, main, develop, staging, la base del carril, las ramas con PR abierto, las de tarjetas abiertas
(ready, running, blocked, review, triage, scheduled, todo), las de worktrees que se quedan, los worktrees con actividad en
las últimas `horas_actividad` h y la lista `protegidas:` de lanes.yaml (admite comodines). NUNCA `--force` ni `-D`.
Un fallo al listar tarjetas, PR o git = se conserva todo lo afectado. `seco: true` (por defecto) solo informa.
El informe queda en `.state/recolector.json` y, una vez al día, un resumen con contadores va a Telegram (nunca por tarjeta).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from . import proc as _proc
from .config import ROOT

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "recolector.json"
CERRADAS = ("done", "archived")  # únicos estados cuyo worktree puede quitarse
ABIERTAS = ("ready", "running", "blocked", "review", "triage", "scheduled", "todo")
ABIERTAS_OPCIONALES = ("scheduled", "todo")  # si el tablero no los conoce, no se invalida toda la pasada
PREFIJO = "lane-t_"
FIJAS = frozenset({"master", "main", "develop", "staging", "HEAD"})
TID_RE = re.compile(r"t_[0-9a-f]{6,}")
LOTE = 40
GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"
LEGACY_DEFECTO = "C:/Users/oscar/dev/_legacy"


@dataclass(frozen=True)
class Ajustes:
    activo: bool = False
    hora: int = 3
    seco: bool = True
    legacy: str = LEGACY_DEFECTO
    horas_actividad: float = 2.0
    protegidas: tuple = ()


def load(path: Path | str | None = None) -> Ajustes:
    """Bloque `recolector:` de lanes.yaml; lo que falte o no sea válido queda en su valor por defecto (nunca lanza)."""
    d = Ajustes()
    try:
        raw = (yaml.safe_load(Path(path or ROOT / "lanes.yaml").read_text(encoding="utf-8")) or {}).get("recolector")
    except (OSError, yaml.YAMLError, AttributeError):
        return d
    if not isinstance(raw, dict):
        return d
    hora, horas, legacy, prot = raw.get("hora"), raw.get("horas_actividad"), raw.get("legacy"), raw.get("protegidas")
    return Ajustes(activo=raw["enabled"] if isinstance(raw.get("enabled"), bool) else d.activo,
                   hora=hora if isinstance(hora, int) and not isinstance(hora, bool) and 0 <= hora <= 23 else d.hora,
                   seco=raw["seco"] if isinstance(raw.get("seco"), bool) else d.seco,
                   legacy=legacy.strip() if isinstance(legacy, str) and legacy.strip() else d.legacy,
                   horas_actividad=float(horas) if isinstance(horas, (int, float)) and not isinstance(horas, bool)
                   and horas >= 0 else d.horas_actividad,
                   protegidas=tuple(str(p) for p in prot if isinstance(p, str) and p.strip())
                   if isinstance(prot, list) else d.protegidas)


@dataclass
class Informe:
    seco: bool = True
    quitados: list[str] = field(default_factory=list)       # "<repo>: lane-t_x (rama lane/t_x)"
    conservados: list[tuple[str, str]] = field(default_factory=list)  # (elemento, motivo)
    legacy: list[str] = field(default_factory=list)         # "<repo>: lane-t_x → <destino>"
    remotas: int = 0       # ramas remotas borradas (o que se borrarían en seco)
    locales: int = 0       # ramas locales fusionadas borradas
    refs_pr: int = 0       # refs/remotes/pr/* de PR fusionados
    protegidas: int = 0    # ramas fusionadas que se dejan por estar protegidas

    @property
    def borradas(self) -> int:
        return len(self.quitados) + self.remotas + self.locales + self.refs_pr

    @property
    def protegidos(self) -> int:
        return len(self.conservados) + self.protegidas

    def texto(self) -> str:
        modo = "SECO (no se ha borrado nada)" if self.seco else "REAL"
        verbo = "Quitaría" if self.seco else "Quitados"
        lineas = [f"Recolector de worktrees · {modo}", f"{verbo}: {len(self.quitados)} · Se quedan: {len(self.conservados)}",
                  f"Ramas remotas: {self.remotas} · locales: {self.locales} · refs de PR: {self.refs_pr} · "
                  f"a legacy: {len(self.legacy)} · protegidas (fusionadas): {self.protegidas}"]
        lineas += [f"  - {q}" for q in self.quitados]
        lineas += [f"  > legacy {q}" for q in self.legacy]
        lineas += [f"  · {e}: {m}" for e, m in self.conservados]
        return "\n".join(lineas)

    def resumen(self, legacy_dir: str = "") -> str | None:
        """Mensaje único de Telegram con contadores; None si no hay nada que contar."""
        if not (self.borradas or self.legacy or self.protegidos):
            return None
        cab = ("🧹 Limpieza nocturna de ramas (simulación: no se ha borrado nada)" if self.seco
               else "🧹 Limpieza nocturna de ramas")
        verbo = "Se borrarían" if self.seco else "Borradas"
        lineas = [cab, f"{verbo}: {self.borradas} ({self.remotas} ramas remotas · {self.locales} locales · "
                       f"{self.refs_pr} refs de PR · {len(self.quitados)} worktrees)",
                  f"{'Irían' if self.seco else 'Movidas'} a legacy: {len(self.legacy)}", f"Protegidas: {self.protegidos}"]
        if self.legacy and not self.seco and legacy_dir:
            lineas.append(f"➡️ Próximo paso: mira {legacy_dir} (LEEME.txt) y borra a mano lo que ya no necesites · "
                          "Lo da: Oscar")
        return "\n".join(lineas)


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p))).replace("\\", "/")


def reciente(path: str, horas: float, ahora: float | None = None) -> bool:
    """¿Algún fichero del worktree se tocó en las últimas `horas` h? (ignora .git, node_modules y cachés). Ante un error de
    lectura devuelve True: en la duda, no se toca."""
    limite = (ahora if ahora is not None else time.time()) - horas * 3600
    saltar = {"node_modules", ".git", ".next", "__pycache__", ".venv", ".pytest_cache"}
    base = str(path).rstrip("/\\").count(os.sep)
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in saltar]
            for f in files:
                try:
                    if os.path.getmtime(os.path.join(root, f)) > limite:
                        return True
                except OSError:
                    pass
            if root.count(os.sep) - base > 4:
                dirs[:] = []
    except OSError:
        return True
    return False


def _gh_json(lane, run, *args: str):
    slug = getattr(lane, "github", "") or ""
    if not slug:
        return None
    try:
        cp = _proc.run_retrying([GH_EXE, "pr", "list", "--repo", slug, *args], runner=run, label="gh pr list",
                                transient=_proc.gh_network_error, capture_output=True, text=True, encoding="utf-8",
                                errors="replace")
        return json.loads(cp.stdout or "[]") if cp.returncode == 0 else None
    except Exception as exc:
        log.warning("recolector: gh pr list falló en %s: %s", slug, exc)
        return None


def gh_prs_abiertos(lane, run=_proc.run) -> set[str] | None:
    """Ramas con PR abierto; None si no se pudo saber (entonces no se toca ninguna rama remota)."""
    data = _gh_json(lane, run, "--state", "open", "--limit", "500", "--json", "headRefName")
    return None if data is None else {str(p.get("headRefName")) for p in data}


def gh_prs_fusionados(lane, run=_proc.run) -> set[int]:
    data = _gh_json(lane, run, "--state", "merged", "--limit", "1000", "--json", "number") or []
    return {int(p["number"]) for p in data if isinstance(p.get("number"), int)}


def mover_worktree(run, repo: str, path: str, legacy: str, rama: str | None, sucio: bool, fusionada: bool,
                   hoy: str | None = None) -> tuple[bool, str]:
    """`git worktree move` (nunca forzado) a `<legacy>/<repo>/<carpeta>` y una línea en su LEEME.txt. (ok, destino|error)."""
    carpeta = Path(legacy) / Path(repo).name
    dest = carpeta / Path(path).name
    n = 1
    while dest.exists():
        n += 1
        dest = carpeta / f"{Path(path).name}-{n}"
    try:
        carpeta.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"no se pudo crear {carpeta}: {exc}"
    cp = run(["git", "-C", repo, "worktree", "move", str(path), str(dest)], capture_output=True, text=True,
             encoding="utf-8", errors="replace", timeout=300)
    if cp.returncode != 0:
        return False, f"git no lo movió: {(cp.stderr or '').strip()[:120]}"
    try:
        leeme = carpeta / "LEEME.txt"
        nuevo = not leeme.exists()
        with open(leeme, "a", encoding="utf-8", newline="\n") as f:
            if nuevo:
                f.write(f"Worktrees de {Path(repo).name} movidos aquí por el recolector del hub con `git worktree move`.\n"
                        "Identifícalos y bórralos a mano cuando confirmes que no los necesitas\n"
                        "(`git worktree remove <ruta> --force`, o borra la carpeta y ejecuta `git worktree prune`).\n\n")
            f.write(f"- {dest.name} · {hoy or time.strftime('%Y-%m-%d')} · antes en {path} · rama {rama or '(sin rama)'} · "
                    f"cambios sin guardar: {'sí' if sucio else 'no'} · rama fusionada: {'sí' if fusionada else 'no'}\n")
    except OSError as exc:
        log.warning("recolector: LEEME.txt no escrito en %s: %s", carpeta, exc)
    return True, str(dest)


def legacy_tras_fallo(lane, tid: str, *, run=_proc.run, ajustes: Callable[[], Ajustes] = load) -> tuple[bool, str]:
    """Para review.py: cuando `cleanup` no puede quitar el worktree de una tarjeta terminada, lo mueve a legacy UNA vez
    (en lugar de repetir «limpieza local pendiente»). Solo con el recolector activo y fuera de simulación."""
    aj = ajustes()
    path = Path(lane.worktree_root) / f"lane-{tid}"
    if not aj.activo or aj.seco or not path.exists():
        return False, ""
    sucio = run(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120)
    return mover_worktree(run, lane.repo, str(path), aj.legacy, f"lane/{tid}", bool((sucio.stdout or "").strip()), False)


class Recolector:
    def __init__(self, lanes: dict, hermes_for: Callable, *, run=_proc.run, merged_pr: Callable | None = None,
                 state_path: Path | None = None, ajustes: Callable[[], Ajustes] = load,
                 prs_abiertos: Callable | None = None, prs_fusionados: Callable | None = None,
                 actividad: Callable[[str, float], bool] = reciente):
        self.lanes, self.hermes_for, self._run = lanes, hermes_for, run
        self.merged_pr = merged_pr or (lambda lane, tid: None)
        self.state_path = Path(state_path or STATE_PATH)
        self.ajustes, self.actividad = ajustes, actividad
        self.prs_abiertos = prs_abiertos  # None = no se sabe qué PR hay abiertos: no se toca ninguna rama remota
        self.prs_fusionados = prs_fusionados

    # --- git (solo lectura salvo worktree remove/move, branch -d, push --delete y update-ref -d, y solo con seco=False) -
    def _git(self, repo: str, *args: str, timeout: int = 120):
        return self._run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=timeout)

    def _ok(self, repo: str, *args: str) -> bool:
        return self._git(repo, *args).returncode == 0

    def _worktrees(self, repo: str) -> dict[str, tuple[str, str | None]]:
        """{ruta normalizada: (ruta, rama)} de los worktrees que git conoce, SIN el checkout principal (el primero)."""
        cp = self._git(repo, "worktree", "list", "--porcelain")
        if cp.returncode != 0:
            raise RuntimeError(f"git worktree list: {(cp.stderr or '').strip()[:150]}")
        bloques: list[list] = []
        for l in (cp.stdout or "").splitlines():
            if l.startswith("worktree "):
                bloques.append([l[len("worktree "):].strip(), None])
            elif l.startswith("branch ") and bloques:
                bloques[-1][1] = l[len("branch "):].strip().replace("refs/heads/", "", 1)
        return {_norm(p): (p, b) for p, b in bloques[1:]}

    def _estados(self, lanes: list) -> tuple[set[str], set[str]] | None:
        """(ids done/archived, ids abiertos) de estos carriles. None si no se pudo leer algún tablero (se conserva todo)."""
        cerradas: set[str] = set()
        abiertas: set[str] = set()
        try:
            for lane in lanes:
                h = self.hermes_for(lane.board)
                for st in CERRADAS:
                    cerradas |= {t["id"] for t in h.list_status(lane.name, st)}
                for st in ABIERTAS:
                    try:
                        abiertas |= {t["id"] for t in h.list_status(lane.name, st)}
                    except Exception:
                        if st not in ABIERTAS_OPCIONALES:
                            raise
        except Exception as exc:
            log.warning("recolector: no se pudo listar tarjetas: %s", exc)
            return None
        return cerradas, abiertas

    def _prs(self, lane) -> set[str] | None:
        if not self.prs_abiertos:
            return None
        try:
            return self.prs_abiertos(lane)
        except Exception as exc:
            log.warning("recolector: PR abiertos no leídos: %s", exc)
            return None

    # --- decisión por worktree/rama de tarjeta -----------------------------------------------------------------------
    def decidir(self, lane, tid: str, path: str | None, cerradas: set[str], abiertas: set[str], prs: set[str] | None,
                aj: Ajustes) -> tuple[str, object]:
        """('quitar', None) | ('legacy', (sucio, fusionada)) | ('conservar', motivo)."""
        repo, branch = lane.repo, f"lane/{tid}"
        if tid in abiertas:
            return "conservar", "la tarjeta sigue abierta"
        if tid not in cerradas:
            return "conservar", "la tarjeta no está en done/archived"
        if prs is not None and branch in prs:
            return "conservar", "su rama tiene un PR abierto"
        if any(fnmatch.fnmatch(branch, p) for p in aj.protegidas):
            return "conservar", "rama en la lista de protección"
        tiene_rama = self._ok(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}")
        fusionada = tiene_rama and (self._ok(repo, "merge-base", "--is-ancestor", branch, lane.base_ref)
                                    or bool(self.merged_pr(lane, tid)))
        if not path:
            if not tiene_rama:
                return "conservar", "sin rama local"
            return ("quitar", None) if fusionada else ("conservar", "rama sin fusionar en la base")
        if self.actividad(path, aj.horas_actividad):
            return "conservar", f"actividad en las últimas {aj.horas_actividad:g} h"
        estado = self._git(path, "status", "--porcelain", "--untracked-files=all")
        if estado.returncode != 0:
            return "conservar", "no se pudo leer el estado del worktree"
        sucio = bool((estado.stdout or "").strip())
        sin_empujar = self._git(path, "rev-list", "--count", "HEAD", "--not", "--remotes")
        try:
            hay_sin_empujar = sin_empujar.returncode != 0 or int(sin_empujar.stdout.strip()) > 0
        except ValueError:
            hay_sin_empujar = True
        if not sucio and not hay_sin_empujar and (fusionada or not tiene_rama):
            return "quitar", None
        if prs is None:
            return "conservar", "no se pudieron leer los PR abiertos: no se mueve nada"
        return "legacy", (sucio or hay_sin_empujar, fusionada)

    # --- pasada -----------------------------------------------------------------------------------------------------
    def recoger(self, seco: bool = True) -> Informe:
        inf = Informe(seco=seco)
        aj = self.ajustes()
        implement = [l for l in self.lanes.values()
                     if getattr(l, "kind", "implement") == "implement" and l.repo and l.worktree_root and l.base]
        por_repo: dict[str, list] = {}
        for lane in implement:
            por_repo.setdefault(_norm(lane.repo), []).append(lane)
        estados = self._estados(implement) if implement else None
        for lanes in por_repo.values():
            lane = lanes[0]
            nombre = Path(lane.repo).name
            if estados is None:
                inf.conservados.append((nombre, "no se pudieron leer las tarjetas: se conserva todo"))
                continue
            cerradas, abiertas = estados
            try:
                worktrees = self._worktrees(lane.repo)
                self._git(lane.repo, "fetch", "--prune", lane.remote, timeout=300)  # si falla se usa lo que hay
            except Exception as exc:
                inf.conservados.append((nombre, f"git no respondió: {exc}"))
                continue
            prs = self._prs(lane)
            raiz = _norm(lane.worktree_root)
            tratadas: set[str] = set()
            vivas = {b for _, b in worktrees.values() if b}  # ramas con worktree: protegidas salvo que se quite
            for _clave, (path, rama) in sorted(worktrees.items()):
                pp = Path(path)
                if pp.name[:len(PREFIJO)] != PREFIJO or _norm(pp.parent) != raiz:
                    continue  # solo los worktrees de carril bajo la raíz configurada; jamás el checkout principal
                if self._uno(inf, lane, nombre, pp.name[len("lane-"):], path, cerradas, abiertas, prs, aj, seco, tratadas):
                    vivas.discard(rama)
            ramas = self._git(lane.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/lane/t_*")
            con_worktree = {Path(p).name[len("lane-"):] for p, _ in worktrees.values()}
            for rama in (ramas.stdout or "").split():
                tid = rama[len("lane/"):]
                if tid not in con_worktree:
                    self._uno(inf, lane, nombre, tid, None, cerradas, abiertas, prs, aj, seco, tratadas)
            self._ramas(inf, lane, nombre, abiertas, prs, vivas, tratadas, aj, seco)
            self._refs_pr(inf, lane, seco)
        self._guardar(inf)
        return inf

    def _uno(self, inf: Informe, lane, nombre: str, tid: str, path: str | None, cerradas: set[str], abiertas: set[str],
             prs: set[str] | None, aj: Ajustes, seco: bool, tratadas: set[str]) -> bool:
        """True si el worktree/rama queda quitado (o se quitaría en seco); False si se conserva o va a legacy."""
        etiqueta = f"{nombre}: {'lane-' + tid if path else 'lane/' + tid}"
        branch = f"lane/{tid}"
        try:
            accion, dato = self.decidir(lane, tid, path, cerradas, abiertas, prs, aj)
        except Exception as exc:
            accion, dato = "conservar", f"error al comprobar: {exc}"
        if accion == "conservar":
            inf.conservados.append((etiqueta, str(dato)))
            return False
        if accion == "legacy":
            sucio, fusionada = dato
            self._a_legacy(inf, lane, nombre, tid, path, sucio, fusionada, aj, seco, etiqueta)
            return False  # su rama sigue viva (la lleva el worktree movido)
        tratadas.add(branch)
        if seco:
            inf.quitados.append(etiqueta)
            return True
        if path:
            cp = self._git(lane.repo, "worktree", "remove", path, timeout=300)  # sin --force: si falla, se queda
            if cp.returncode != 0:
                inf.conservados.append((etiqueta, f"git no lo quitó: {(cp.stderr or '').strip()[:120]}"))
                return False
        if self._ok(lane.repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
            cp = self._git(lane.repo, "branch", "-d", branch)  # -d, nunca -D: git rechaza lo no fusionado
            if cp.returncode != 0:
                inf.quitados.append(f"{etiqueta} (worktree quitado)")
                inf.conservados.append((f"{nombre}: {branch}", f"branch -d la rechazó: {(cp.stderr or '').strip()[:120]}"))
                return True
        inf.quitados.append(etiqueta)
        return True

    def _a_legacy(self, inf: Informe, lane, nombre: str, tid: str, path: str | None, sucio: bool, fusionada: bool,
                  aj: Ajustes, seco: bool, etiqueta: str) -> bool:
        if not path:
            return False
        destino = Path(aj.legacy) / nombre / Path(path).name
        if seco:
            inf.legacy.append(f"{etiqueta} → {destino}")
            return True
        ok, res = mover_worktree(self._run, lane.repo, path, aj.legacy, f"lane/{tid}", sucio, fusionada)
        if ok:
            inf.legacy.append(f"{etiqueta} → {res}")
        else:
            inf.conservados.append((etiqueta, res))
        return ok

    # --- ramas remotas/locales ya fusionadas y refs de PR -------------------------------------------------------------
    def _protegida(self, rama: str, fijas: set[str], abiertas: set[str], aj: Ajustes) -> bool:
        return (rama in fijas or any(fnmatch.fnmatch(rama, p) for p in aj.protegidas)
                or any(t in abiertas for t in TID_RE.findall(rama)))

    def _ramas(self, inf: Informe, lane, nombre: str, abiertas: set[str], prs: set[str] | None, vivas: set[str],
               tratadas: set[str], aj: Ajustes, seco: bool) -> None:
        repo, remoto = lane.repo, lane.remote
        if prs is None:
            inf.conservados.append((f"{nombre}: ramas", "no se pudieron leer los PR abiertos: no se toca ninguna rama"))
            return
        fijas = set(FIJAS) | {lane.base} | prs | vivas | tratadas
        base_sha = (self._git(repo, "rev-parse", lane.base_ref).stdout or "").strip()
        puntas: dict[str, str] = {}
        for l in (self._git(repo, "for-each-ref", "--format=%(refname:short) %(objectname)",
                            f"refs/remotes/{remoto}/").stdout or "").splitlines():
            partes = l.split()
            if len(partes) == 2:
                puntas[partes[0]] = partes[1]
        candidatas, protegidas = [], 0
        for l in (self._git(repo, "branch", "-r", "--merged", lane.base_ref).stdout or "").splitlines():
            l = l.strip()
            if not l.startswith(remoto + "/") or "->" in l:
                continue
            b = l[len(remoto) + 1:]
            if puntas.get(l) and puntas[l] == base_sha:
                continue  # apunta al mismo commit que la base: rama recién creada, aún sin trabajo propio
            if self._protegida(b, fijas, abiertas, aj):
                protegidas += 1
            else:
                candidatas.append(b)
        inf.protegidas += protegidas
        if seco:
            inf.remotas += len(candidatas)
        else:
            for i in range(0, len(candidatas), LOTE):
                lote = candidatas[i:i + LOTE]
                if self._git(repo, "push", remoto, "--delete", *lote, timeout=300).returncode == 0:
                    inf.remotas += len(lote)
                else:
                    inf.remotas += sum(self._git(repo, "push", remoto, "--delete", b, timeout=300).returncode == 0
                                       for b in lote)
        locales = [b.strip() for b in (self._git(repo, "branch", "--merged", lane.base_ref,
                                                   "--format=%(refname:short)").stdout or "").splitlines()]
        locales = [b for b in locales if b and not self._protegida(b, fijas, abiertas, aj)]
        if seco:
            inf.locales += len(locales)
        else:
            inf.locales += sum(self._git(repo, "branch", "-d", b).returncode == 0 for b in locales)  # -d: nunca -D

    def _refs_pr(self, inf: Informe, lane, seco: bool) -> None:
        repo = lane.repo
        salida = self._git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/pr/").stdout or ""
        refs = [l.split() for l in salida.splitlines() if len(l.split()) == 2]
        if not refs:
            return
        try:
            fusionados = set(self.prs_fusionados(lane)) if self.prs_fusionados else set()
        except Exception:
            fusionados = set()
        for ref, sha in refs:
            n = ref.rsplit("/", 1)[-1]
            if not (n.isdigit() and int(n) in fusionados) and not self._ok(repo, "merge-base", "--is-ancestor", sha,
                                                                            lane.base_ref):
                continue
            if seco or self._ok(repo, "update-ref", "-d", ref):
                inf.refs_pr += 1

    # --- tras integrar una tarjeta (PR fusionado) --------------------------------------------------------------------
    def borrar_rama_integrada(self, pr: dict, tid: str, status: str | None = None) -> bool:
        """Reconciler: un PR de `lane/<tid>` se fusionó → borra su rama remota y la local (`-d`). Solo con el recolector
        activo y fuera de simulación; nunca con la tarjeta en running. Devuelve True si borró la remota."""
        aj = self.ajustes()
        branch = f"lane/{tid}"
        if not aj.activo or aj.seco or status == "running" or str(pr.get("headRefName") or "") != branch:
            return False
        m = re.search(r"github\.com/([^/]+/[^/]+)/pull/", str(pr.get("url") or ""))
        lane = next((l for l in self.lanes.values() if m and getattr(l, "github", "") == m.group(1)
                     and getattr(l, "kind", "implement") == "implement" and l.repo), None)
        if lane is None or any(fnmatch.fnmatch(branch, p) for p in aj.protegidas):
            return False
        borrada = self._ok(lane.repo, "push", lane.remote, "--delete", branch)
        if self._ok(lane.repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
            self._ok(lane.repo, "branch", "-d", branch)  # si aún hay worktree o no está fusionada, git la rechaza
        return borrada

    def _guardar(self, inf: Informe) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        data["ultimo"] = {"cuando": time.strftime("%Y-%m-%d %H:%M:%S"), "seco": inf.seco, "quitados": inf.quitados,
                          "conservados": [list(c) for c in inf.conservados], "legacy": inf.legacy,
                          "remotas": inf.remotas, "locales": inf.locales, "refs_pr": inf.refs_pr,
                          "protegidas": inf.protegidas}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        except OSError as exc:
            log.info("recolector: estado no guardado: %s", exc)


class RecolectorTicker:
    """Bucle del runner: una vez al día pasada la hora del bloque `recolector:` de lanes.yaml (Madrid). Apagado por defecto.
    Un fallo no tumba el runner. `aviso(texto)` publica el resumen diario (un mensaje, nunca uno por tarjeta)."""

    def __init__(self, recolector: Recolector, ajustes: Callable[[], Ajustes] = load, minutes: float = 30,
                 clock: Callable[[], float] = time.time, aviso: Callable[[str], object] | None = None):
        self.recolector, self.ajustes, self.minutes, self.clock = recolector, ajustes, minutes, clock
        self.aviso = aviso
        self._last = 0.0

    def tick(self) -> Informe | None:
        from .resumen_carriles import MADRID
        now = self.clock()
        if now - self._last < self.minutes * 60:
            return None
        self._last = now
        try:
            cfg = self.ajustes()
            local = datetime.fromtimestamp(now, MADRID)
            dia = local.strftime("%Y-%m-%d")
            if not cfg.activo or local.hour < cfg.hora or self._ultimo_dia() == dia:
                return None
            self._marcar_dia(dia)  # antes de ejecutar: sin bucles si algo falla
            inf = self.recolector.recoger(seco=cfg.seco)
            log.info("%s", inf.texto())
            resumen = inf.resumen(cfg.legacy)
            if resumen and self.aviso:
                try:
                    self.aviso(resumen)
                except Exception as exc:
                    log.warning("recolector: resumen no enviado: %s", exc)
            return inf
        except Exception as exc:
            log.warning("recolector falló: %s", exc)
            return None

    def _ultimo_dia(self) -> str:
        try:
            return str(json.loads(self.recolector.state_path.read_text(encoding="utf-8")).get("ultimo_dia", ""))
        except (OSError, ValueError, AttributeError):
            return ""

    def _marcar_dia(self, dia: str) -> None:
        p = self.recolector.state_path
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        data["ultimo_dia"] = dia
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def main(argv: list[str], *, recolector: Recolector | None = None, out: Callable[[str], None] = print) -> int:
    """`python -m agent_lanes.recolector` = en seco (informa, no borra). `--real` borra (solo lo que cumple todo)."""
    ap = argparse.ArgumentParser(prog="recolector")
    ap.add_argument("--real", action="store_true", help="borrar de verdad (por defecto: seco)")
    args = ap.parse_args(argv)
    if recolector is None:
        from .config import load_lanes
        from .git_ops import GitOps
        from .kanban_api import backend_for
        recolector = Recolector(load_lanes(), lambda board: backend_for(board), merged_pr=GitOps().merged_pr,
                                prs_abiertos=gh_prs_abiertos, prs_fusionados=gh_prs_fusionados)
    out(recolector.recoger(seco=not args.real).texto())
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
