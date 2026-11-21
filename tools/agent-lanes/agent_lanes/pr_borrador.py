"""PR en borrador automático tras una aprobación de review (mejoras/pr-borrador-al-aprobar).

Hasta ahora el carril de review completa la tarjeta (done) y nadie abre un PR: el PR nace solo con el botón de Oscar o con
el lote diario (batch.py). Resultado: tarjetas aprobadas sin PR y sin integrar. Este módulo abre, justo después de un
`complete` correcto, un PR EN BORRADOR de `lane/<id>` hacia la base del carril, para que los conflictos salgan el primer
día y el trabajo sea visible. NO fusiona nada: la fusión sigue pidiendo el botón de Oscar.

APAGADO POR DEFECTO. Un PR en borrador puede disparar previews de Vercel y workflows de GitHub Actions por cada tarjeta
y Oscar decidió no pagar Actions. Se enciende repo a repo en `pr_borrador.yaml` (se relee en cada llamada: no hace falta
reiniciar el runner; ausente o inválido = defaults de `Ajustes`):

    activo: false   # true = interruptor general
    repos: []       # lista blanca de slugs github (p. ej. oscarsao/oscar-hq); vacía = ninguno

Solo se abre si se cumple TODO: (a) ajuste activo y `lane.github` en la lista blanca, (b) la rama existe en el remoto,
(c) tiene commits por delante de <remote>/<base>, (d) no existe ya un PR con esa rama como head (abierto ni fusionado) y
(e) el carril no es de tipo `ops`. Un fallo (gh no responde, timeout, rama sin remoto) NUNCA lanza: devuelve un
`Resultado` con `estado="fallo"` y el llamador (review.py) lo registra y deja un único comentario en la tarjeta.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import proc as _proc
from .config import ROOT, Lane

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "pr_borrador.yaml"
GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"
MAX_RESUMEN = 1500
NOTA = "Abierto automáticamente tras la revisión; la fusión pide el botón de Oscar"

# Estados de `Resultado`: "abierto" y "fallo" son los únicos que el llamador comenta en la tarjeta.
ABIERTO, OMITIDO, FALLO = "abierto", "omitido", "fallo"


@dataclass(frozen=True)
class Ajustes:
    activo: bool = False
    repos: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resultado:
    estado: str          # abierto | omitido | fallo
    motivo: str = ""
    url: str = ""


def load_ajustes(path: Path | str | None = None) -> Ajustes:
    """Ajustes desde el YAML; lo que falte o no sea válido queda en su valor por defecto (apagado)."""
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return Ajustes()
    if not isinstance(raw, dict):
        return Ajustes()
    activo = raw["activo"] if isinstance(raw.get("activo"), bool) else False
    repos = raw.get("repos")
    lista = tuple(r.strip() for r in repos if isinstance(r, str) and r.strip()) if isinstance(repos, list) else ()
    return Ajustes(activo=activo, repos=lista)


def _cuerpo(summary: str | None) -> str:
    resumen = re.sub(r"\n{3,}", "\n\n", (summary or "").strip())
    if len(resumen) > MAX_RESUMEN:
        resumen = resumen[:MAX_RESUMEN - 1].rstrip() + "…"
    return "\n\n".join(p for p in (resumen, NOTA) if p)


def abrir_pr_borrador(lane: Lane, tid: str, title: str, summary: str | None, *, ajustes: Ajustes | None = None,
                      runner=_proc.run, gh_exe: str = GH_EXE, sleep=time.sleep) -> Resultado:
    """Abre el PR en borrador de lane/<tid> si se cumplen las condiciones del módulo. Nunca lanza."""
    try:
        return _abrir(lane, tid, title, summary, ajustes or load_ajustes(), runner, gh_exe, sleep)
    except Exception as exc:  # incluye TimeoutExpired y OSError (gh o git no responden)
        log.warning("%s: PR en borrador: %s", tid, exc)
        return Resultado(FALLO, f"{type(exc).__name__}: {exc}"[:300])


def _abrir(lane, tid, title, summary, ajustes, runner, gh_exe, sleep) -> Resultado:
    slug = (getattr(lane, "github", "") or "").strip()
    if not ajustes.activo or not slug or slug not in ajustes.repos:
        return Resultado(OMITIDO, "apagado para este repo")
    if getattr(lane, "kind", "implement") == "ops":
        return Resultado(OMITIDO, "carril ops")
    branch = f"lane/{tid}"

    def run(cmd, label, *, lectura=True, cwd=None):
        extra = {"cwd": cwd} if cwd else {}
        return _proc.run_retrying(cmd, runner=runner, sleep=sleep, label=label, retry_timeout=lectura,
                                  transient=_proc.gh_network_error if lectura and cmd[0] == gh_exe else None,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace", **extra)

    # (b) la rama existe en el remoto
    cp = run(["git", "-C", lane.repo, "ls-remote", lane.remote, f"refs/heads/{branch}"], "git ls-remote")
    if cp.returncode != 0:
        return Resultado(FALLO, f"git ls-remote falló: {(cp.stderr or '').strip()[:200]}")
    if not (cp.stdout or "").strip():
        return Resultado(OMITIDO, f"{branch} no existe en {lane.remote}")
    # (c) commits por delante de <remote>/<base>
    cp = run(["git", "-C", lane.repo, "fetch", lane.remote, lane.base,
              f"+refs/heads/{branch}:refs/remotes/{lane.remote}/{branch}"], "git fetch")
    if cp.returncode != 0:
        return Resultado(FALLO, f"git fetch falló: {(cp.stderr or '').strip()[:200]}")
    cp = run(["git", "-C", lane.repo, "rev-list", "--count", f"{lane.remote}/{lane.base}..{lane.remote}/{branch}"],
             "git rev-list")
    try:
        ahead = int((cp.stdout or "").strip()) if cp.returncode == 0 else None
    except ValueError:
        ahead = None
    if ahead is None:
        return Resultado(FALLO, f"git rev-list falló: {(cp.stderr or '').strip()[:200]}")
    if ahead <= 0:
        return Resultado(OMITIDO, f"{branch} no tiene commits sobre {lane.remote}/{lane.base}")
    # (d) ya hay un PR con esa rama como head (abierto o fusionado)
    for estado in ("open", "merged"):
        cp = run([gh_exe, "pr", "list", "--repo", slug, "--head", branch, "--state", estado, "--json", "number,url",
                  "--limit", "1"], "gh pr list", cwd=tempfile.gettempdir())
        if cp.returncode != 0:
            return Resultado(FALLO, f"gh pr list falló: {(cp.stderr or '').strip()[:200]}")
        try:
            existe = bool(json.loads(cp.stdout or "[]"))
        except json.JSONDecodeError:
            return Resultado(FALLO, "gh pr list devolvió una respuesta no válida")
        if existe:
            return Resultado(OMITIDO, f"ya existe un PR ({estado}) de {branch}")
    # (e) otro PR abierto (de otra rama) ya contiene los commits de esta rama: no se abre un duplicado
    tip = (run(["git", "-C", lane.repo, "rev-parse", f"{lane.remote}/{branch}"], "git rev-parse").stdout or "").strip()
    if tip:
        cp = run([gh_exe, "pr", "list", "--repo", slug, "--state", "open", "--json", "number,commits", "--limit", "200"],
                 "gh pr list", cwd=tempfile.gettempdir())
        if cp.returncode != 0:
            return Resultado(FALLO, f"gh pr list falló: {(cp.stderr or '').strip()[:200]}")
        try:
            abiertos = json.loads(cp.stdout or "[]")
        except json.JSONDecodeError:
            return Resultado(FALLO, "gh pr list devolvió una respuesta no válida")
        for pr in abiertos if isinstance(abiertos, list) else []:
            if tip in {c.get("oid") for c in (pr.get("commits") or []) if isinstance(c, dict)}:
                return Resultado(OMITIDO, f"el PR #{pr.get('number')} (otra rama) ya contiene los commits de {branch}")
    cp = run([gh_exe, "pr", "create", "--draft", "--repo", slug, "--base", lane.base, "--head", branch,
              "--title", (title or tid)[:200], "--body", _cuerpo(summary)], "gh pr create", lectura=False,
             cwd=tempfile.gettempdir())
    if cp.returncode != 0:
        return Resultado(FALLO, f"gh pr create falló: {(cp.stderr or '').strip()[:200]}")
    m = re.search(r"https://github\.com/\S+/pull/\d+", cp.stdout or "")
    return Resultado(ABIERTO, url=m.group(0) if m else "")
