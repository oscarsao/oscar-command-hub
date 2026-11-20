#!/usr/bin/env python3
"""Suite nocturna local de oscar-hq (sustituye a GitHub Actions, que no se paga: decisión de Oscar 20-11-2026).

Qué hace, en orden:
  1. Toma un candado (un solo proceso de pruebas a la vez) y mira la RAM. Si supera el umbral (85 %), NO ejecuta:
     deja un informe «OMITIDO» con el aviso (la máquina va justa). `--forzar` ignora la RAM.
  2. Clona en una carpeta temporal propia el repo de oscar-hq (clon local: el repo original no se toca, ni
     siquiera sus referencias) y trae `origin/<rama>` del remoto real (solo lectura).
  3. Ejecuta pytest (`pytest tests -q -rfE --timeout=60`) y vitest (`corepack pnpm@10.18.3 exec vitest run`)
     uno detrás de otro, nunca a la vez.
  4. Compara los fallos con la línea base (`linea_base.json`) y escribe `AAAA-MM-DD.markdown` con fecha, SHA,
     conclusión y fallos nuevos.
  5. Borra el clon temporal.

No usa secretos, no escribe en el repo de oscar-hq, no toca producción ni GitHub Actions, no manda mensajes.

Uso manual:
  py -3.12 tools/ci-local/suite_nocturna.py
  py -3.12 tools/ci-local/suite_nocturna.py --sin-vitest --rama master
  py -3.12 tools/ci-local/suite_nocturna.py --actualizar-base   # los fallos de esta pasada pasan a ser la línea base

Código de salida: 0 = pasa o sin fallos nuevos; 1 = fallos nuevos o error de ejecución; 2 = omitido (RAM o candado).
Programación en Task Scheduler: ver README.md de esta carpeta.
"""
import argparse
import ctypes
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO_DEF = r"C:\Users\oscar\dev\oscar-hq"
SALIDA_DEF = r"C:\Users\oscar\integrador-docs\ci_local"
UMBRAL_RAM_DEF = 85.0
CANDADO_CADUCA_H = 6  # un candado más viejo se considera de un proceso muerto
PYTEST_CMD = ["py", "-3.12", "-m", "pytest", "tests", "-q", "-rfE", "--timeout=60", "-p", "no:cacheprovider"]
PNPM = "pnpm@10.18.3"

FALLO_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)")
VITEST_FALLO_RE = re.compile(r"^\s*(?:FAIL|×)\s+(\S.*?)\s*$")
RESUMEN_RE = re.compile(r"=+ .*?(\d+ (?:passed|failed|error).*?) in [\d.]+s.*=+|^(\d+ (?:passed|failed).*? in [\d.]+s.*)$")


# ---------- utilidades puras (con pruebas) ----------

def porcentaje_ram() -> float:
    """Porcentaje de RAM física en uso (Windows, sin dependencias). Fuera de Windows devuelve 0."""
    if os.name != "nt":
        return 0.0

    class MEM(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]

    m = MEM()
    m.dwLength = ctypes.sizeof(MEM)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return float(m.dwMemoryLoad)


def fallos_pytest(salida: str) -> list:
    """Identificadores únicos (ruta::test) de las líneas FAILED/ERROR del resumen `-rfE` de pytest."""
    vistos = []
    for linea in salida.splitlines():
        m = FALLO_RE.match(linea.strip())
        if m and m.group(1) not in vistos:
            vistos.append(m.group(1))
    return vistos


def fallos_vitest(salida: str) -> list:
    """Líneas `FAIL fichero > suite > test` de vitest (únicas)."""
    vistos = []
    for linea in salida.splitlines():
        m = VITEST_FALLO_RE.match(linea)
        if m:
            clave = "vitest:" + re.sub(r"\s+", " ", m.group(1))
            if clave not in vistos:
                vistos.append(clave)
    return vistos


def resumen_pytest(salida: str) -> str:
    for linea in reversed(salida.splitlines()):
        m = RESUMEN_RE.search(linea.strip())
        if m:
            return (m.group(1) or m.group(2)).strip()
    return "sin línea de resumen"


def conclusion(estado: str, fallos: list, base: list, errores: list) -> str:
    """PASA / SIN FALLOS NUEVOS / FALLA / ERROR."""
    if errores:
        return "ERROR"
    if not fallos:
        return "PASA"
    nuevos = [f for f in fallos if f not in base]
    return "FALLA" if nuevos else "SIN FALLOS NUEVOS"


def candado_tomar(ruta: Path, ahora: float = None) -> bool:
    """Crea el candado de forma atómica. Devuelve False si otro proceso lo tiene (y no ha caducado)."""
    ahora = time.time() if ahora is None else ahora
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.exists():
        if ahora - ruta.stat().st_mtime < CANDADO_CADUCA_H * 3600:
            return False
        ruta.unlink()
    try:
        fd = os.open(str(ruta), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def leer_base(ruta: Path) -> dict:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def informe_md(d: dict) -> str:
    """Markdown del informe a partir de un dict con: fecha, sha, rama, conclusion, ram, umbral, pasos, nuevos,
    arreglados, base_sha, avisos, duracion_s."""
    L = [f"# Suite nocturna oscar-hq — {d['fecha']}", ""]
    L += [f"- **Conclusión:** {d['conclusion']}",
          f"- **SHA probado:** `{d.get('sha') or 'desconocido'}` (origin/{d['rama']})",
          f"- **Línea base:** {('SHA `' + d['base_sha'] + '`') if d.get('base_sha') else 'aún no existe (esta pasada la crea)'}",
          f"- **RAM al empezar:** {d['ram']:.0f} % (umbral {d['umbral']:.0f} %)",
          f"- **Duración:** {int(d.get('duracion_s', 0))} s", ""]
    if d.get("avisos"):
        L += ["## Avisos", ""] + [f"- {a}" for a in d["avisos"]] + [""]
    L += ["## Pasos", ""]
    for p in d.get("pasos", []):
        L.append(f"- **{p['nombre']}**: {p['estado']} — {p.get('detalle', '')}".rstrip(" —"))
    L += ["", "## Fallos nuevos frente a la línea base", ""]
    L += [f"- `{f}`" for f in d.get("nuevos", [])] or ["- Ninguno."]
    if d.get("arreglados"):
        L += ["", "## Fallos de la línea base que ya no fallan", ""] + [f"- `{f}`" for f in d["arreglados"]]
    L.append("")
    return "\n".join(L)


# ---------- ejecución ----------

def _rmtree(ruta: Path) -> None:
    def quitar(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    shutil.rmtree(ruta, onerror=quitar)


def ejecutar(cmd, cwd, timeout):
    """(código, salida combinada). Nunca lanza: un fallo al arrancar es código -1."""
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return -2, f"TIMEOUT tras {timeout} s"
    except OSError as e:
        return -1, f"no se pudo ejecutar {cmd[0]}: {e}"


def preparar_clon(repo: str, rama: str, destino: Path, run=ejecutar):
    """Clon local de `repo` + fetch del remoto real. Devuelve (sha, aviso|None). Lanza RuntimeError si falla."""
    c, o = run(["git", "clone", "--quiet", "--no-checkout", repo, str(destino)], None, 600)
    if c != 0:
        raise RuntimeError(f"git clone falló: {o.strip()[:300]}")
    c, url = run(["git", "-C", repo, "remote", "get-url", "origin"], None, 60)
    aviso = None
    if c == 0 and url.strip():
        run(["git", "-C", str(destino), "remote", "set-url", "origin", url.strip()], None, 60)
        c, o = run(["git", "-C", str(destino), "fetch", "--quiet", "origin", rama], None, 900)
        ref = "FETCH_HEAD"
        if c != 0:
            aviso = f"No se pudo traer origin/{rama} ({o.strip()[:120]}); se probó la copia local de {rama}."
            ref = rama
    else:
        aviso = f"El repo no tiene remoto origin; se probó la copia local de {rama}."
        ref = rama
    c, sha = run(["git", "-C", str(destino), "rev-parse", ref], None, 60)
    if c != 0:
        raise RuntimeError(f"no se pudo resolver {ref}: {sha.strip()[:200]}")
    sha = sha.strip()
    c, o = run(["git", "-C", str(destino), "checkout", "--quiet", "--detach", sha], None, 900)
    if c != 0:
        raise RuntimeError(f"git checkout falló: {o.strip()[:300]}")
    return sha, aviso


def main(argv=None, *, ram=porcentaje_ram, run=ejecutar, ahora=datetime.now) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=REPO_DEF)
    ap.add_argument("--rama", default="master")
    ap.add_argument("--salida", default=SALIDA_DEF)
    ap.add_argument("--tmp", default=None, help="carpeta donde crear el clon temporal (por defecto, la temporal del sistema)")
    ap.add_argument("--umbral-ram", type=float, default=UMBRAL_RAM_DEF)
    ap.add_argument("--forzar", action="store_true", help="ejecuta aunque la RAM supere el umbral")
    ap.add_argument("--sin-vitest", action="store_true")
    ap.add_argument("--sin-pytest", action="store_true")
    ap.add_argument("--actualizar-base", action="store_true")
    a = ap.parse_args(argv)

    t0 = time.time()
    salida = Path(a.salida)
    salida.mkdir(parents=True, exist_ok=True)
    fecha = ahora().strftime("%Y-%m-%d")
    informe = salida / f"{fecha}.markdown"
    base_f = salida / "linea_base.json"
    d = {"fecha": fecha, "rama": a.rama, "umbral": a.umbral_ram, "ram": ram(), "pasos": [], "avisos": [],
         "nuevos": [], "arreglados": [], "sha": None}

    def cerrar(codigo):
        d["duracion_s"] = time.time() - t0
        informe.write_text(informe_md(d), encoding="utf-8", newline="\n")
        print(f"{d['conclusion']} — informe en {informe}")
        return codigo

    candado = salida / ".candado"
    if not candado_tomar(candado):
        d["conclusion"] = "OMITIDO"
        d["avisos"].append("Hay otra pasada en marcha (candado vivo): no se lanza una segunda.")
        # No machaca un informe bueno del mismo día con uno de omitido.
        if informe.exists():
            print("OMITIDO: otra pasada en marcha; el informe del día se conserva.")
            return 2
        return cerrar(2)
    clon = None
    try:
        if d["ram"] > a.umbral_ram:
            d["avisos"].append(f"⚠ RAM al {d['ram']:.0f} % (más del {a.umbral_ram:.0f} %): suite omitida. "
                               "Cierra programas y relanza a mano, o usa --forzar.")
            if not a.forzar:
                d["conclusion"] = "OMITIDO"
                return cerrar(2)
            d["avisos"].append("Se ejecuta igualmente por --forzar.")

        raiz = Path(a.tmp) if a.tmp else Path(tempfile.gettempdir())
        clon = raiz / f"ci_local_ohq_{fecha}"
        if clon.exists():
            _rmtree(clon)
        errores, fallos = [], []
        try:
            sha, aviso = preparar_clon(a.repo, a.rama, clon, run)
        except RuntimeError as e:
            d["conclusion"] = "ERROR"
            d["avisos"].append(str(e))
            return cerrar(1)
        d["sha"] = sha
        if aviso:
            d["avisos"].append(aviso)

        if not a.sin_pytest:
            c, o = run(PYTEST_CMD, clon, 90 * 60)
            f = fallos_pytest(o)
            ok = c in (0, 1)  # 1 = hay tests fallidos; otro código = no se pudo ejecutar bien
            if not ok:
                errores.append("pytest")
            fallos += f
            d["pasos"].append({"nombre": "pytest", "estado": "pasa" if c == 0 else ("fallos" if ok else "ERROR"),
                               "detalle": resumen_pytest(o) if ok else f"código {c}: {o.strip()[-200:]}"})
        if not a.sin_vitest:
            dash = clon / "dashboard"
            if dash.exists():
                c, o = run(["corepack", PNPM, "install", "--frozen-lockfile"], dash, 20 * 60)
                if c != 0:
                    errores.append("pnpm install")
                    d["pasos"].append({"nombre": "vitest", "estado": "ERROR", "detalle": f"pnpm install falló: {o.strip()[-200:]}"})
                else:
                    c, o = run(["corepack", PNPM, "exec", "vitest", "run"], dash, 30 * 60)
                    f = fallos_vitest(o)
                    fallos += f
                    ok = c == 0 or bool(f)
                    if not ok:
                        errores.append("vitest")
                    d["pasos"].append({"nombre": "vitest", "estado": "pasa" if c == 0 else ("fallos" if ok else "ERROR"),
                                       "detalle": f"código {c}" if c == 0 or not f else f"{len(f)} fallos"})
            else:
                d["pasos"].append({"nombre": "vitest", "estado": "omitido", "detalle": "no hay carpeta dashboard/"})

        base = leer_base(base_f)
        base_ids = base.get("fallos", [])
        d["base_sha"] = base.get("sha")
        d["nuevos"] = [f for f in fallos if f not in base_ids] if base else []
        d["arreglados"] = [f for f in base_ids if f not in fallos] if not errores else []
        d["conclusion"] = conclusion("", fallos, base_ids if base else fallos, errores)
        if not base and fallos and not errores:
            d["avisos"].append("No existía línea base: los fallos de esta pasada se guardan como base (no cuentan como nuevos).")
        if (a.actualizar_base or not base) and not errores:
            base_f.write_text(json.dumps({"sha": sha, "fecha": fecha, "fallos": fallos}, indent=2), encoding="utf-8", newline="\n")
        return cerrar(1 if d["conclusion"] in ("FALLA", "ERROR") else 0)
    finally:
        if clon is not None and clon.exists():
            try:
                _rmtree(clon)
            except OSError:
                pass
        try:
            candado.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
