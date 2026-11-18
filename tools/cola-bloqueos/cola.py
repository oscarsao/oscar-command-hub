"""Cola de bloqueos: de cada tarjeta bloqueada o en espera, su motivo y cuántas tarjetas y PR esperan por ella.

SOLO LECTURA. Ejecuta únicamente `hermes kanban --board <b> list --json`, `hermes kanban --board <b> show <id> --json`
y `gh pr list`. No escribe en Hermes, ni en el panel, ni en Telegram: deja un JSON en `salida/` para que una sesión
lo cargue en el panel «Al volver» con ArtifactData (colección `items`).

Qué entra: tarjetas en `blocked` (o en los estados de ESTADOS_ESPERA) y las que llevan la marca ESPERA_PADRES en el
cuerpo, mientras no estén hechas ni archivadas.
Cola de una tarjeta = hijas por enlace (`children` de `show`) + tarjetas vivas que la citan por id (t_xxxxxxxx) en su
cuerpo + PR abiertos cuya base es su rama (`branch_name`, o `lane/<id>` si no la tiene).
Urgente: cola >= UMBRAL_COLA o más de UMBRAL_HORAS de antigüedad. Orden: mayor cola primero, luego la más antigua.

Uso:  py -3.12 tools/cola-bloqueos/cola.py [--salida DIR] [--config FICHERO.json] [--hermes EXE] [--gh EXE]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
HERMES_EXE = Path.home() / "AppData/Local/hermes/bin/hermes.exe"

# Tablero de Hermes -> repos de GitHub (de lanes.yaml). Sobrescribible con --config {"tableros": {...}}.
TABLEROS = {
    "default": ["oscarsao/oscar-command-hub", "oscarsao/nextjobs-autoapply"],
    "oscarhq": ["oscarsao/oscar-hq", "oscarsao/icp-prospect-engine"],
    "migrateam": ["PildoraDigital/OCR-PDF-and-images"],
}
ESTADOS_ESPERA = ("blocked",)
ESTADOS_HECHOS = ("done", "archived")
MARCA_ESPERA = "ESPERA_PADRES"
UMBRAL_COLA = 3
UMBRAL_HORAS = 24
TIMEOUT = 60
ID_RE = re.compile(r"\bt_[0-9a-f]{8}\b")
TALLA_RE = re.compile(r"·\s*(XS|S|M|L|XL)\s*$")
CAMPOS = ("seccion", "proyecto", "titulo", "que", "estima", "bloquea", "cola", "resuelve", "desde", "ref", "orden")


def ejecutar(cmd: list[str]) -> str:
    """Lanza un comando de solo lectura (sin shell) y devuelve su stdout; falla con RuntimeError."""
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=TIMEOUT, shell=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise RuntimeError(f"{cmd[0]}: {e}") from e
    if cp.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])}: {(cp.stderr or '').strip()[:200]}")
    return cp.stdout or ""


def _json(texto: str):
    return json.loads(texto) if texto.strip() else []


def a_fecha(v) -> datetime | None:
    """Epoch (s o ms) o ISO 8601 -> datetime UTC; None si no se entiende."""
    if v in (None, ""):
        return None
    try:
        if isinstance(v, (int, float)) or str(v).replace(".", "", 1).isdigit():
            n = float(v)
            return datetime.fromtimestamp(n / 1000 if n > 1e11 else n, tz=timezone.utc)
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def es_candidata(t: dict) -> bool:
    estado = str(t.get("status") or "")
    if estado in ESTADOS_HECHOS:
        return False
    return estado in ESTADOS_ESPERA or MARCA_ESPERA in (t.get("body") or "")


def motivo(comentarios: list[dict], body: str) -> tuple[str, dict | None]:
    """El comentario de bloqueo = el último comentario (el que dejó quien bloqueó). Sin comentarios, la línea de
    ESPERA_PADRES del cuerpo."""
    if comentarios:
        c = comentarios[-1]
        return " ".join(str(c.get("body") or "").split())[:400], c
    for linea in (body or "").splitlines():
        if MARCA_ESPERA in linea:
            return linea.strip()[:400], None
    return "", None


def quien_resuelve(texto: str, autor: str, asignada: str) -> str:
    """Heurística documentada: Oscar si el motivo le pide algo; Integradora si habla de fusionar/integrar/PR o la
    dejó el integrador o está ESPERA_PADRES; si no, el carril asignado."""
    bajo = texto.lower()
    if "integrador" in (autor or "").lower() or MARCA_ESPERA.lower() in bajo:
        return "Integradora"
    if re.search(r"\boscar\b|decisi[oó]n|aprueb|aprobar|needs_input|confirm", bajo):
        return "Oscar"
    if re.search(r"integr|fusion|merge|\bpr\b|padres", bajo):
        return "Integradora"
    return f"carril {asignada}" if asignada else "carril"


def dependientes_por_texto(tid: str, tarjetas: list[dict]) -> list[str]:
    """Ids de tarjetas vivas (de cualquier tablero) cuyo cuerpo cita `tid`."""
    out = []
    for t in tarjetas:
        otro = str(t.get("id") or "")
        if otro == tid or str(t.get("status") or "") in ESTADOS_HECHOS:
            continue
        if tid in ID_RE.findall(t.get("body") or "") and otro not in out:
            out.append(otro)
    return out


def prs_sobre_rama(rama: str, prs: list[dict]) -> list[str]:
    return [f"PR #{p['number']}" for p in prs if rama and p.get("baseRefName") == rama]


def construir(tableros: dict[str, list[dict]], detalles: dict[tuple[str, str], dict], prs: dict[str, list[dict]],
              ahora: datetime) -> list[dict]:
    """Función pura: `tableros` {tablero: [tarjetas de list]}, `detalles` {(tablero, id): show}, `prs` {tablero:
    [PR abiertos]}. Devuelve los items ordenados y numerados."""
    todas = [t for ts in tableros.values() for t in ts]
    items = []
    for tablero, tarjetas in tableros.items():
        for t in tarjetas:
            if not es_candidata(t):
                continue
            tid = str(t["id"])
            show = detalles.get((tablero, tid)) or {}
            task = show.get("task") or t
            texto, com = motivo(show.get("comments") or [], task.get("body") or t.get("body") or "")
            hijas = [h if isinstance(h, str) else (h or {}).get("id") for h in show.get("children") or []]
            citas = dependientes_por_texto(tid, todas)
            rama = task.get("branch_name") or t.get("branch_name") or f"lane/{tid}"
            cola_ids = list(dict.fromkeys([*(str(h) for h in hijas if h), *citas]))
            cola_prs = prs_sobre_rama(rama, prs.get(tablero, []))
            desde = (a_fecha((com or {}).get("created_at")) or a_fecha(t.get("status_changed_at"))
                     or a_fecha(t.get("started_at")) or a_fecha(t.get("created_at")))
            horas = (ahora - desde).total_seconds() / 3600 if desde else 0.0
            cola = len(cola_ids) + len(cola_prs)
            resuelve = quien_resuelve(texto, (com or {}).get("author", ""),
                                      str(t.get("assignee") or ""))
            titulo = str(t.get("title") or "")
            talla = TALLA_RE.search(titulo)
            items.append({
                "seccion": "decision" if resuelve == "Oscar" else "bloqueo",
                "proyecto": tablero,
                "titulo": titulo,
                "que": texto or "(sin motivo escrito)",
                "estima": talla.group(1) if talla else "",
                "bloquea": ", ".join([*cola_ids, *cola_prs]),
                "cola": cola,
                "resuelve": resuelve,
                "desde": desde.isoformat(timespec="minutes") if desde else "",
                "ref": tid,
                "urgente": cola >= UMBRAL_COLA or horas > UMBRAL_HORAS,
                "_horas": horas,
            })
    items.sort(key=lambda i: (-i["cola"], -i["_horas"], i["ref"]))
    for n, i in enumerate(items, 1):
        i["orden"] = n
        del i["_horas"]
    return items


def recoger(hermes: str, gh: str, tableros: dict[str, list[str]], run=ejecutar):
    """Lee Hermes y GitHub (solo lectura). Un fallo por tablero/PR no tumba el resto: queda en `avisos`."""
    listas, detalles, prs, avisos = {}, {}, {}, []
    for tablero, repos in tableros.items():
        try:
            listas[tablero] = _json(run([hermes, "kanban", "--board", tablero, "list", "--json"]))
        except (RuntimeError, ValueError) as e:
            avisos.append(f"tablero {tablero}: no se pudo leer ({e})")
            continue
        for t in listas[tablero]:
            if not es_candidata(t):
                continue
            try:
                detalles[(tablero, str(t["id"]))] = _json(
                    run([hermes, "kanban", "--board", tablero, "show", str(t["id"]), "--json"]))
            except (RuntimeError, ValueError) as e:
                avisos.append(f"{t['id']}: show falló ({e})")
        prs[tablero] = []
        for repo in repos:
            try:
                prs[tablero] += _json(run([gh, "pr", "list", "--repo", repo, "--state", "open", "--limit", "200",
                                           "--json", "number,title,baseRefName,headRefName"]))
            except (RuntimeError, ValueError) as e:
                avisos.append(f"PR de {repo}: no se pudieron leer ({e})")
    return listas, detalles, prs, avisos


def main(argv=None, run=ejecutar, ahora: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--salida", default=str(AQUI / "salida"))
    ap.add_argument("--config", help='JSON opcional: {"tableros": {"default": ["owner/repo"]}}')
    ap.add_argument("--hermes", default=str(HERMES_EXE) if HERMES_EXE.exists() else "hermes")
    ap.add_argument("--gh", default="gh")
    a = ap.parse_args(argv)
    tableros = TABLEROS
    if a.config:
        tableros = json.loads(Path(a.config).read_text(encoding="utf-8")).get("tableros", TABLEROS)
    ahora = ahora or datetime.now(timezone.utc)
    listas, detalles, prs, avisos = recoger(a.hermes, a.gh, tableros, run)
    if not listas:
        print("No se pudo leer ningún tablero: " + "; ".join(avisos), file=sys.stderr)
        return 1
    items = construir(listas, detalles, prs, ahora)
    out = Path(a.salida)
    out.mkdir(parents=True, exist_ok=True)
    destino = out / f"cola-{ahora.strftime('%Y%m%d-%H%M%S')}.json"
    contenido = json.dumps({"generado": ahora.isoformat(timespec="seconds"), "campos": list(CAMPOS) + ["urgente"],
                            "items": items, "avisos": avisos}, ensure_ascii=False, indent=2)
    for f in (destino, out / "ultima.json"):
        f.write_text(contenido, encoding="utf-8", newline="\n")
    print(f"{len(items)} bloqueos ({sum(1 for i in items if i['urgente'])} urgentes) -> {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
