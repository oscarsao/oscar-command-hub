#!/usr/bin/env python3
"""Informe de estado global: fusionado, desplegado y visible por tarjeta (t_4f865ca0).

SOLO LECTURA. Lee los tableros de Hermes (`hermes kanban --board X list/stats --json`), los PR abiertos
(`gh pr list`), los ficheros `.env.example` del repo y el último despliegue de producción de Vercel
(`vercel api` con GET). No escribe en kanban, GitHub ni Vercel, y no usa tokens propios: tira de la sesión ya
autenticada de cada CLI. Lo único que escribe es el informe, con `--salida` (para que lo publique quien
mantiene el artefacto de Signal Desarrollo o uno propio).

Uso:
  py -3.12 tools/estado/estado_global.py                      # informe en la salida estándar
  py -3.12 tools/estado/estado_global.py --salida informe.md  # además, lo guarda en un fichero

Si falta una herramienta (hermes, gh, git) el script termina con código 2 y dice cuál. Si falla UNA fuente
(p. ej. Vercel sin sesión) el informe sale igualmente con esa sección marcada como «no disponible».
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TABLEROS = ("default", "oscarhq", "migrateam")
REPOS_PR = ("oscarsao/oscar-command-hub", "oscarsao/oscar-hq", "PildoraDigital/OCR-PDF-and-images")
VERCEL_PROYECTO = "dashboard"
VERCEL_SCOPE = "oscarsaos-projects"
ORDEN = ("triage", "todo", "ready", "running", "blocked", "review", "done")
HERMES_EXE = Path.home() / "AppData/Local/hermes/bin/hermes.exe"
VERCEL_JS = Path.home() / "AppData/Roaming/npm/node_modules/vercel/dist/index.js"
SHA = re.compile(r"\b[0-9a-f]{40}\b")
ENV_VAR = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
ENV_CONTEXTO = re.compile(r"variables? de entorno|env vars?|environment variables?|\.env\b", re.I)
# Palabras en mayúsculas con guion bajo que no son variables de entorno.
NO_ENV = {"NOT_NULL", "FOR_UPDATE", "ON_CONFLICT", "IF_NOT", "SELECT_FROM"}


class FaltaHerramienta(RuntimeError):
    pass


def _herramienta(nombre: str, exe: Path | None = None) -> str:
    ruta = shutil.which(nombre) or (str(exe) if exe and Path(exe).exists() else None)
    if not ruta:
        raise FaltaHerramienta(f"falta la herramienta `{nombre}` (no está en el PATH): instálala o inicia sesión en ella")
    return ruta


def ejecutar(cmd: list[str], timeout: int = 120) -> str:
    """Lanza un comando de SOLO lectura y devuelve su stdout; lanza RuntimeError si falla."""
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}…: código {cp.returncode}: {(cp.stderr or '').strip()[:200]}")
    return cp.stdout


# ---------------------------------------------------------------- fuentes (se inyectan en las pruebas)
def leer_tablero(tablero: str, run=ejecutar) -> list[dict]:
    exe = _herramienta("hermes", HERMES_EXE)
    return json.loads(run([exe, "kanban", "--board", tablero, "list", "--json"]) or "[]")


def leer_stats(tablero: str, run=ejecutar) -> dict | None:
    """`stats --json` del tablero, para contrastar cifras. None si esta versión de hermes no lo ofrece."""
    exe = _herramienta("hermes", HERMES_EXE)
    try:
        data = json.loads(run([exe, "kanban", "--board", tablero, "stats", "--json"]))
    except (RuntimeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def leer_prs(repo: str, run=ejecutar) -> list[dict]:
    gh = _herramienta("gh")
    return json.loads(run([gh, "pr", "list", "--repo", repo, "--state", "open", "--limit", "300", "--json",
                           "number,title,isDraft,createdAt,headRefName,url"]) or "[]")


def leer_vercel(run=ejecutar) -> dict | None:
    """Último despliegue de producción del proyecto (GET a la API: solo lectura)."""
    node = _herramienta("node")
    if not VERCEL_JS.exists():
        raise FaltaHerramienta(f"falta la CLI de Vercel ({VERCEL_JS})")
    out = run([node, str(VERCEL_JS), "api", f"/v6/deployments?app={VERCEL_PROYECTO}&target=production&limit=1",
               "--scope", VERCEL_SCOPE])
    deps = json.loads(out).get("deployments") or []
    return deps[0] if deps else None


def contiene_sha(sha: str, run=ejecutar, base: str = "origin/main") -> bool | None:
    """True/False si `sha` está en la historia de `base`; None si el SHA no existe en este clon."""
    git = _herramienta("git")
    try:
        run([git, "-C", str(REPO), "cat-file", "-e", f"{sha}^{{commit}}"])
    except RuntimeError:
        return None
    try:
        run([git, "-C", str(REPO), "merge-base", "--is-ancestor", sha, base])
        return True
    except RuntimeError:
        return False


def vars_conocidas() -> set[str]:
    """Nombres (nunca valores) de `.env.example`/`.env.sample` del repo."""
    nombres: set[str] = set()
    for raiz, carpetas, ficheros in os.walk(REPO):
        carpetas[:] = [d for d in carpetas if d not in ("node_modules", ".git", ".state", "__pycache__")]
        for nombre in ficheros:
            if nombre not in (".env.example", ".env.sample"):
                continue
            for linea in (Path(raiz) / nombre).read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", linea)
                if m:
                    nombres.add(m.group(1))
    return nombres


# ---------------------------------------------------------------- análisis puro
def _texto(t: dict, *claves: str) -> str:
    return " ".join(str(t.get(c) or "") for c in claves)


def por_estado(tarjetas: list[dict]) -> dict[str, int]:
    cuenta: dict[str, int] = {}
    for t in tarjetas:
        cuenta[t.get("status") or "?"] = cuenta.get(t.get("status") or "?", 0) + 1
    return cuenta


def por_carril(tarjetas: list[dict]) -> dict[str, dict[str, int]]:
    carriles: dict[str, dict[str, int]] = {}
    for t in tarjetas:
        c = carriles.setdefault(t.get("assignee") or "(sin carril)", {})
        c[t.get("status") or "?"] = c.get(t.get("status") or "?", 0) + 1
    return carriles


def motivo_bloqueo(t: dict) -> str:
    for k in ("blocked_reason", "block_reason", "reason", "last_failure_error", "result"):
        v = t.get(k)
        if isinstance(v, str) and v.strip():
            return re.sub(r"\s+", " ", v.strip())[:160]
    return "(sin motivo en la tarjeta)"


def sha_de(t: dict) -> str | None:
    """SHA de la entrega: de `result`/`metadata` de la tarjeta (40 hex). None si no consta."""
    cands = SHA.findall(_texto(t, "result", "metadata", "summary"))
    return cands[-1] if cands else None


def hechas_sin_fusionar(tarjetas: list[dict], esta_en_base=contiene_sha) -> tuple[list[tuple[dict, str]], int]:
    """Tarjetas `done` cuyo SHA NO está en la rama base. Devuelve (lista, nº de hechas sin SHA verificable)."""
    fuera, sin_dato = [], 0
    for t in tarjetas:
        if t.get("status") != "done":
            continue
        sha = sha_de(t)
        dentro = esta_en_base(sha) if sha else None
        if dentro is False:
            fuera.append((t, sha))
        elif dentro is None:
            sin_dato += 1
    return fuera, sin_dato


def vars_sin_constar(tarjetas: list[dict], conocidas: set[str]) -> dict[str, list[str]]:
    """Variables de entorno que una tarjeta viva dice necesitar y no constan en ningún `.env.example`."""
    falta: dict[str, list[str]] = {}
    for t in tarjetas:
        if t.get("status") in ("done", "archived"):
            continue
        texto = _texto(t, "title", "body")
        if not ENV_CONTEXTO.search(texto):
            continue
        for v in set(ENV_VAR.findall(texto)) - conocidas - NO_ENV:
            falta.setdefault(v, []).append(t.get("id", "?"))
    return falta


def antiguedad_dias(creado, ahora: datetime) -> int | None:
    if creado is None:
        return None
    try:
        if isinstance(creado, (int, float)):
            f = datetime.fromtimestamp(creado, timezone.utc)
        else:
            f = datetime.fromisoformat(str(creado).replace("Z", "+00:00"))
    except (ValueError, OSError):
        return None
    return max(0, (ahora - f).days)


# ---------------------------------------------------------------- informe
def tabla_estados(titulo: str, tarjetas: list[dict], stats: dict | None) -> list[str]:
    c = por_estado(tarjetas)
    estados = [e for e in ORDEN if e in c] + sorted(e for e in c if e not in ORDEN)
    out = [f"### {titulo} — {len(tarjetas)} tarjetas", "", "| Estado | Tarjetas |", "|---|---:|"]
    out += [f"| {e} | {c[e]} |" for e in estados]
    if stats:
        difiere = [f"{e}: informe {c.get(e, 0)} ≠ stats {stats[e]}" for e in estados
                   if isinstance(stats.get(e), int) and stats[e] != c.get(e, 0)]
        out += ["", "Contraste con `hermes kanban stats`: " + ("; ".join(difiere) if difiere else "coincide.")]
    carriles = por_carril(tarjetas)
    out += ["", "| Carril | " + " | ".join(estados) + " |", "|---|" + "---:|" * len(estados)]
    for car in sorted(carriles):
        out.append(f"| {car} | " + " | ".join(str(carriles[car].get(e, 0)) for e in estados) + " |")
    return out + [""]


def construir(tableros: dict[str, list[dict]], stats: dict[str, dict | None], prs: dict[str, list[dict] | str],
              vercel: dict | str | None, conocidas: set[str], esta_en_base=contiene_sha,
              ahora: datetime | None = None) -> str:
    ahora = ahora or datetime.now(timezone.utc)
    out = [f"# Estado global — {ahora:%Y-%m-%d %H:%M} UTC", "",
           "Niveles por tarjeta: **fusionada** (su SHA está en main) → **desplegada** → **visible**. "
           "«Hecha» no implica ninguna de las tres.", "", "## 1. Tableros", ""]
    for nombre, tarjetas in tableros.items():
        out += tabla_estados(f"Tablero `{nombre}`", tarjetas, stats.get(nombre))
    out += ["## 2. Tarjetas bloqueadas y su motivo", ""]
    hay = False
    for nombre, tarjetas in tableros.items():
        for t in tarjetas:
            if t.get("status") == "blocked":
                hay = True
                out.append(f"- `{nombre}` {t.get('id')} ({t.get('assignee') or '-'}): {t.get('title', '')[:70]} — "
                           f"{motivo_bloqueo(t)}")
    out += ["" if hay else "Ninguna.", ""]
    out += ["## 3. «Hechas» que NO están en main (hecha no es integrada)", ""]
    for nombre, tarjetas in tableros.items():
        fuera, sin_dato = hechas_sin_fusionar(tarjetas, esta_en_base)
        out.append(f"**`{nombre}`**: {len(fuera)} con SHA fuera de main; {sin_dato} sin SHA verificable en este clon.")
        out += [f"- {t.get('id')} ({t.get('assignee') or '-'}): {t.get('title', '')[:70]} — `{s[:10]}`"
                for t, s in fuera[:30]]
        if len(fuera) > 30:
            out.append(f"- … y {len(fuera) - 30} más")
        out.append("")
    out += ["## 4. PR abiertos", ""]
    for repo, lista in prs.items():
        if isinstance(lista, str):
            out.append(f"- `{repo}`: no disponible ({lista})")
            continue
        borr = [p for p in lista if p.get("isDraft")]
        out.append(f"- `{repo}`: {len(lista)} abiertos, {len(borr)} en borrador")
        for p in sorted(borr, key=lambda p: p.get("createdAt") or ""):
            d = antiguedad_dias(p.get("createdAt"), ahora)
            out.append(f"  - #{p.get('number')} {p.get('title', '')[:60]} — borrador, {d if d is not None else '?'} d")
    out += ["", f"## 5. Producción de `{VERCEL_PROYECTO}` (Vercel)", ""]
    if isinstance(vercel, str):
        out.append(f"No disponible: {vercel}")
    elif not vercel:
        out.append("Sin despliegues de producción.")
    else:
        meta = vercel.get("meta") or {}
        out.append(f"Último: {vercel.get('url', '?')} · estado {vercel.get('readyState') or vercel.get('state', '?')} · "
                   f"commit `{str(meta.get('githubCommitSha', '?'))[:10]}`")
    out += ["", "## 6. Variables de entorno que las tarjetas piden y no constan", ""]
    hay = False
    for nombre, tarjetas in tableros.items():
        for var, ids in sorted(vars_sin_constar(tarjetas, conocidas).items()):
            hay = True
            out.append(f"- `{var}` ({nombre}): {', '.join(sorted(ids)[:5])}")
    out += ["" if hay else "Ninguna.", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None, run=ejecutar) -> int:
    ap = argparse.ArgumentParser(description="Informe de estado global (solo lectura).")
    ap.add_argument("--salida", type=Path, help="guarda además el informe en este fichero")
    ap.add_argument("--tableros", nargs="+", default=list(TABLEROS))
    ap.add_argument("--repos", nargs="+", default=list(REPOS_PR))
    a = ap.parse_args(argv)
    try:
        _herramienta("git")
        _herramienta("gh")
        tableros = {t: leer_tablero(t, run) for t in a.tableros}
        stats = {t: leer_stats(t, run) for t in a.tableros}
    except FaltaHerramienta as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (RuntimeError, json.JSONDecodeError) as e:
        print(f"ERROR leyendo el kanban: {e}", file=sys.stderr)
        return 1
    prs: dict[str, list[dict] | str] = {}
    for r in a.repos:
        try:
            prs[r] = leer_prs(r, run)
        except (RuntimeError, json.JSONDecodeError) as e:
            prs[r] = str(e)[:120]
    try:
        vercel: dict | str | None = leer_vercel(run)
    except (FaltaHerramienta, RuntimeError, json.JSONDecodeError) as e:
        vercel = str(e)[:120]
    informe = construir(tableros, stats, prs, vercel, vars_conocidas(), lambda s: contiene_sha(s, run))
    print(informe)
    if a.salida:
        a.salida.write_text(informe, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
