"""Inventario de claves (solo metadatos, nunca valores). Subcomandos de solo lectura salvo `rotada`, que anota en
tools/claves/registro.jsonl fecha y persona. Sin red.

  claves.py lista | guia <clave> | estado | rotada <clave> [--persona X] [--fecha AAAA-MM-DD]

`estado` mira solo si el NOMBRE de la variable aparece en los .env conocidos: extrae los nombres de las líneas
NOMBRE=... y descarta el resto; los valores no se guardan ni se imprimen."""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parent.parent
PTE = "por completar por Oscar"
_NOMBRE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def cargar(inv: Path) -> dict:
    return yaml.safe_load(inv.read_text(encoding="utf-8")) or {}


def _ruta(txt: str) -> Path:
    txt = txt.replace("%LOCALAPPDATA%", os.environ.get("LOCALAPPDATA", "~"))
    p = Path(os.path.expanduser(txt))
    return p if p.is_absolute() else REPO / p


def nombres_en_env(fichero: Path):
    """Nombres de variable de un .env (None si no existe). Nunca devuelve valores."""
    try:
        txt = fichero.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return {m.group(1) for l in txt.splitlines() if (m := _NOMBRE.match(l))}


def leer_registro(reg: Path) -> dict:
    """Última fecha de rotación por clave según el registro."""
    ult = {}
    if reg.exists():
        for l in reg.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l)
                ult[r["clave"]] = max(ult.get(r["clave"], ""), r["fecha"])
            except (ValueError, KeyError, TypeError):
                continue
    return ult


def _fecha(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def ultima(c: dict, reg: dict):
    fechas = [f for f in (_fecha(c.get("ultima_rotacion")), _fecha(reg.get(c["nombre"]))) if f]
    return max(fechas) if fechas else None


def buscar(datos: dict, nombre: str):
    return next((c for c in datos.get("claves", []) if c["nombre"].lower() == nombre.lower()), None)


def cmd_lista(datos, out):
    for c in datos.get("claves", []):
        out(f"{c['nombre']:<22} {c.get('proveedor', PTE):<22} {c.get('para_que', '')}")


def cmd_guia(datos, nombre, out) -> int:
    c = buscar(datos, nombre)
    if not c:
        out(f"Clave desconocida: {nombre}")
        return 2
    out(f"# {c['nombre']} — {c.get('para_que', '')}")
    out(f"1. Proveedor ({c.get('proveedor', PTE)}): {c.get('url', PTE)}")
    for i, p in enumerate(c.get("pasos") or [PTE], 1):
        out(f"   {i}) {p}")
    out("2. Pegarla en estos destinos, por este orden (variable exacta):")
    for i, d in enumerate(c.get("destinos") or [], 1):
        donde = d.get("fichero") or d.get("servicio") or d.get("nombre") or PTE
        out(f"   {i}) [{d.get('tipo', '?')}] {donde} -> {d.get('variable', c['nombre'])}")
    out(f"3. Comprobar: `py -3.12 tools/claves/claves.py estado` y anotar con `claves.py rotada {c['nombre']}`.")
    out(f"Rota: {c.get('quien_rota', PTE)} · cada {c.get('cada_dias', PTE)} días · depende: {c.get('depende', PTE)}")
    if c.get("notas"):
        out(f"Notas: {c['notas']}")
    return 0


def cmd_estado(datos, reg, out, hoy=None) -> int:
    hoy = hoy or date.today()
    avisos = 0
    for c in datos.get("claves", []):
        u = ultima(c, reg)
        lim = c.get("cada_dias")
        if u is None:
            edad, av = "sin fecha", "AVISO sin fecha de rotación"
        else:
            dias = (hoy - u).days
            edad = f"{dias} d"
            av = f"AVISO caducada ({dias} > {lim} d)" if isinstance(lim, int) and dias > lim else "ok"
        avisos += av != "ok"
        out(f"{c['nombre']}: {edad} · {av}")
        for d in c.get("destinos", []):
            if d.get("tipo") == "fichero_env":
                nombres = nombres_en_env(_ruta(d["fichero"]))
                est = "fichero no encontrado" if nombres is None else ("presente" if d["variable"] in nombres else "FALTA")
                out(f"    - {d['fichero']} {d['variable']}: {est}")
            else:
                out(f"    - [{d.get('tipo', '?')}] {d.get('servicio') or d.get('nombre') or PTE}: no comprobable desde aquí")
    out(f"{avisos} clave(s) con aviso.")
    return 0


def cmd_rotada(datos, nombre, reg_path, persona, fecha, out) -> int:
    c = buscar(datos, nombre)
    if not c:
        out(f"Clave desconocida: {nombre}")
        return 2
    f = _fecha(fecha) or date.today()
    with reg_path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"clave": c["nombre"], "fecha": f.isoformat(), "persona": persona}, ensure_ascii=False) + "\n")
    out(f"Anotada la rotación de {c['nombre']} el {f.isoformat()} por {persona}.")
    return 0


def main(argv=None, out=print, inv: Path = AQUI / "inventario.yaml", reg: Path = AQUI / "registro.jsonl") -> int:
    ap = argparse.ArgumentParser(prog="claves.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("lista")
    sub.add_parser("estado")
    g = sub.add_parser("guia")
    g.add_argument("clave")
    r = sub.add_parser("rotada")
    r.add_argument("clave")
    r.add_argument("--persona", default=os.environ.get("USERNAME") or os.environ.get("USER") or "desconocida")
    r.add_argument("--fecha")
    a = ap.parse_args(argv)
    datos = cargar(inv)
    if a.cmd == "lista":
        cmd_lista(datos, out)
        return 0
    if a.cmd == "guia":
        return cmd_guia(datos, a.clave, out)
    if a.cmd == "estado":
        return cmd_estado(datos, leer_registro(reg), out)
    return cmd_rotada(datos, a.clave, reg, a.persona, a.fecha, out)


if __name__ == "__main__":
    sys.exit(main())
