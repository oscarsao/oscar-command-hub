#!/usr/bin/env python3
"""Puerta de decisión (CLAUDE.md global, §7): antes de decidir solo, busca lo que ya existe.

Solo lectura. Dado el texto de una idea o decisión, comprueba el grupo A (contexto) con datos reales y escribe
la «ficha» de una línea por criterio (✓ / ✗ / ⚠ / n/a) para pegarla en la tarjeta o el PR:

  A1  no existe ya ni está en marcha (tarjetas abiertas, PR abiertos, ramas lane/*)
  A2  no se decidió lo contrario (decisions/, pitches/ y memoria)
  A3  no hay tarjeta o PR igual o parecido reciente (hechas en los últimos 30 días)
  A4  no choca con ramas en curso

El resto (B producto, C seguridad, D reversibilidad, E verificación, F personas, G escalada) no se puede decidir con
una búsqueda: sale como lista a rellenar a mano, según el nivel (1: A + C + reversible; 2: todos).

    py -3.12 tools/puerta-decision/puerta.py "texto de la idea" --nivel 1
    py -3.12 tools/puerta-decision/puerta.py "texto" --prs oscarsao/oscar-hq --ramas C:/Users/oscar/dev/oscar-hq --json

Salida: 0 = sin bloqueos (todo ✓, ⚠ o n/a); 2 = hay al menos un ✗ (no se decide solo: se pregunta a Oscar con la
evidencia); 1 = error de uso. Nunca escribe ni crea nada. No imprime secretos: los fragmentos pasan por un redactor.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

BOARDS = ("default", "oscarhq", "migrateam")
UMBRAL_TARJETA = 0.45
UMBRAL_DOC_FUERTE = 0.6
UMBRAL_DOC_POSIBLE = 0.4
DIAS_RECIENTE = 30
STOP = set(
    "para como esta este estos estas desde hasta sobre entre cada todo toda todos todas porque cuando donde "
    "pero tambien solo mismo misma otra otro unos unas with from that this have will your into over".split()
)
SECRETO = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|ohq_pt_[A-Za-z0-9]+|eyJ[A-Za-z0-9_-]{15,}|AKIA[0-9A-Z]{12,}|xox[baprs]-[A-Za-z0-9-]+|"
    r"(?:password|passwd|token|secret|api[_-]?key)\s*[:=]\s*\S+)",
    re.I,
)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9_]{4,}", norm(s)) if t not in STOP}


def similitud(a: str, b: str) -> float:
    """0-1: mezcla solapamiento de palabras (sobre el texto más corto) y parecido de cadena."""
    ta, tb = tokens(a), tokens(b)
    solap = len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0
    return round(0.6 * solap + 0.4 * SequenceMatcher(None, norm(a), norm(b)).ratio(), 3)


def redacta(s: str) -> str:
    return SECRETO.sub("[oculto]", s)


# ---------------- fuentes ----------------
def leer_tarjetas(boards=BOARDS, timeout: int = 120):
    """(tarjetas, errores). Usa `hermes kanban list --json`; si un tablero falla se anota y se sigue."""
    cards, errores = [], {}
    for b in boards:
        try:
            r = subprocess.run(["hermes", "kanban", "--board", b, "list", "--json"], capture_output=True, text=True,
                               encoding="utf-8", timeout=timeout)
            if r.returncode != 0 or not r.stdout.strip():
                errores[b] = (r.stderr or "sin salida").strip()[:120]
                continue
            data = json.loads(r.stdout)
            for t in data if isinstance(data, list) else data.get("tasks", data.get("items", [])):
                t["_board"] = b
                cards.append(t)
        except Exception as e:  # noqa: BLE001 — una fuente caída no tumba la ficha
            errores[b] = f"{type(e).__name__}"
    return cards, errores


def buscar_tarjetas(texto, cards, ahora=None):
    ahora = ahora or time.time()
    abiertas, recientes = [], []
    for c in cards:
        st = c.get("status")
        if st == "archived":
            continue
        s = similitud(texto, c.get("title") or "")
        if s < UMBRAL_TARJETA:
            continue
        item = {"id": c.get("id"), "tablero": c.get("_board"), "estado": st, "asignado": c.get("assignee"),
                "titulo": redacta(c.get("title") or "")[:110], "similitud": s}
        if st != "done":
            abiertas.append(item)
        else:
            fin = c.get("completed_at") or c.get("created_at") or 0
            try:
                if ahora - float(fin) <= DIAS_RECIENTE * 86400:
                    recientes.append(item)
            except (TypeError, ValueError):
                pass
    key = lambda x: -x["similitud"]
    return sorted(abiertas, key=key)[:5], sorted(recientes, key=key)[:5]


def buscar_documentos(texto, carpetas, max_resultados=5):
    tk = tokens(texto)
    if not tk:
        return []
    res = []
    for carpeta in carpetas:
        base = Path(carpeta)
        if not base.is_dir():
            continue
        for p in base.rglob("*.md"):
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lineas = t.splitlines() or [""]
            nlin = [norm(ln) for ln in lineas]
            # Ventanas de 8 líneas: las palabras tienen que coincidir CERCA unas de otras, no repartidas por el documento.
            mejor, hit, inicio = 0.0, set(), 0
            for i in range(0, max(1, len(nlin) - 7), 4):
                bloque = " ".join(nlin[i:i + 8])
                h = {w for w in tk if w in bloque}
                if len(h) / len(tk) > mejor:
                    mejor, hit, inicio = len(h) / len(tk), h, i
            if mejor < UMBRAL_DOC_POSIBLE:
                continue
            linea = next((ln for ln, n in zip(lineas[inicio:inicio + 8], nlin[inicio:inicio + 8]) if any(w in n for w in hit)), "")
            res.append({"ruta": str(p), "coincide": round(mejor, 2), "palabras": sorted(hit)[:6],
                        "fragmento": redacta(linea.strip())[:120], "fuerte": mejor >= UMBRAL_DOC_FUERTE})
    return sorted(res, key=lambda x: -x["coincide"])[:max_resultados]


def buscar_prs(texto, repos):
    out, errores = [], {}
    q = " ".join(sorted(tokens(texto))[:6])
    for repo in repos:
        try:
            r = subprocess.run(["gh", "pr", "list", "-R", repo, "--state", "open", "--search", q, "--limit", "5",
                                "--json", "number,title,isDraft"], capture_output=True, text=True, encoding="utf-8", timeout=60)
            if r.returncode != 0:
                errores[repo] = (r.stderr or "").strip()[:100]
                continue
            for pr in json.loads(r.stdout or "[]"):
                s = similitud(texto, pr["title"])
                if s >= UMBRAL_TARJETA:
                    out.append({"repo": repo, "numero": pr["number"], "borrador": pr["isDraft"], "similitud": s,
                                "titulo": redacta(pr["title"])[:100]})
        except Exception as e:  # noqa: BLE001
            errores[repo] = type(e).__name__
    return out, errores


def buscar_ramas(texto, repos):
    tk = tokens(texto)
    out = []
    for repo in repos:
        try:
            r = subprocess.run(["git", "-C", repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes", "refs/heads"],
                               capture_output=True, text=True, encoding="utf-8", timeout=30)
        except Exception:  # noqa: BLE001
            continue
        minimo = 2 if len(tk) >= 4 else 1  # con ideas largas, una sola palabra suelta ("dashboard") es ruido
        for ref in r.stdout.split():
            if len(tk & tokens(ref.replace("/", " ").replace("-", " ").replace("_", " "))) >= minimo:
                out.append({"repo": repo, "rama": ref})
    return out[:8]


# ---------------- ficha ----------------
def ficha(texto, nivel, tarjetas_fn=None, carpetas=(), prs=(), ramas=()):
    cards, err_k = (tarjetas_fn or leer_tarjetas)()
    abiertas, recientes = buscar_tarjetas(texto, cards)
    docs = buscar_documentos(texto, carpetas)
    prs_hit, err_p = buscar_prs(texto, prs) if prs else ([], {})
    ramas_hit = buscar_ramas(texto, ramas) if ramas else []
    # «caído» = no hay datos; «parcial» = faltan tableros o búsquedas de PR: un ✓ exige haber mirado TODO.
    kanban_ok = bool(cards) and len(err_k) < len(BOARDS)
    faltan = sorted(err_k) + sorted(f"PR {r}" for r in err_p)
    lineas = []

    def fila(cod, titulo, estado, detalle):
        lineas.append({"criterio": cod, "titulo": titulo, "estado": estado, "detalle": detalle})

    # A1
    if abiertas or prs_hit:
        d = "; ".join([f"{a['id']} ({a['tablero']}, {a['estado']}) «{a['titulo']}»" for a in abiertas[:3]]
                      + [f"PR {p['repo']}#{p['numero']}{' borrador' if p['borrador'] else ''} «{p['titulo']}»" for p in prs_hit[:3]])
        fila("A1", "No existe ya ni está en marcha", "✗", d)
    elif not kanban_ok:
        fila("A1", "No existe ya ni está en marcha", "n/a", "kanban no disponible: " + (json.dumps(err_k) if err_k else "sin datos"))
    elif faltan:
        fila("A1", "No existe ya ni está en marcha", "⚠", "sin parecidos en lo que se pudo mirar, pero NO se miró: " + ", ".join(faltan))
    else:
        fila("A1", "No existe ya ni está en marcha", "✓", f"{len(cards)} tarjetas revisadas sin parecido ≥ {UMBRAL_TARJETA}"
             + (f"; {len(prs)} repos de PR" if prs else ""))
    # A2
    fuertes = [d for d in docs if d["fuerte"]]
    if fuertes:
        fila("A2", "No se decidió lo contrario", "✗", "; ".join(f"{d['ruta']} ({int(d['coincide']*100)} %)" for d in fuertes[:3]) + " — lee si decide algo distinto")
    elif docs:
        fila("A2", "No se decidió lo contrario", "⚠", "posibles: " + "; ".join(f"{d['ruta']} ({int(d['coincide']*100)} %)" for d in docs[:3]))
    elif carpetas:
        fila("A2", "No se decidió lo contrario", "✓", f"{len(list(carpetas))} carpetas revisadas sin coincidencias")
    else:
        fila("A2", "No se decidió lo contrario", "n/a", "no se indicaron carpetas (--docs)")
    # A3
    if recientes:
        fila("A3", f"Sin tarjeta o PR parecido reciente ({DIAS_RECIENTE} d)", "✗",
             "; ".join(f"{a['id']} hecha «{a['titulo']}»" for a in recientes[:3]))
    elif not kanban_ok:
        fila("A3", f"Sin tarjeta o PR parecido reciente ({DIAS_RECIENTE} d)", "n/a", "kanban no disponible")
    elif err_k:
        fila("A3", f"Sin tarjeta o PR parecido reciente ({DIAS_RECIENTE} d)", "⚠", "sin parecidos en lo que se pudo mirar, pero NO se miró: " + ", ".join(sorted(err_k)))
    else:
        fila("A3", f"Sin tarjeta o PR parecido reciente ({DIAS_RECIENTE} d)", "✓", "ninguna hecha en los últimos 30 días")
    # A4
    if ramas_hit:
        fila("A4", "No choca con ramas en curso", "⚠", "; ".join(f"{r['rama']}" for r in ramas_hit[:4]))
    elif ramas:
        fila("A4", "No choca con ramas en curso", "✓", f"{len(list(ramas))} repos revisados")
    else:
        fila("A4", "No choca con ramas en curso", "n/a", "no se indicaron repos (--ramas)")
    fila("A5", "Tengo los permisos que hacen falta", "n/a", "a mano")
    manual = [("C", "Seguridad: secretos, permisos, datos personales y legal cubiertos"),
              ("D", "Reversibilidad y coste: cómo se deshace, a quién afecta, coste medido")]
    if nivel >= 2:
        manual = [("B", "Producto: configurable desde la UI, granular, UI con URL, componentes del DS, estándar de mercado"),
                  ("C", manual[0][1]), ("D", manual[1][1]),
                  ("E", "Verificación: criterio de hecho comprobable, prueba desde la pantalla, señal de éxito"),
                  ("F", "Personas: responsable identificado y gente afectada avisada"),
                  ("G", "Escalada: si no sé algo, investigo; si sigo bloqueado, advisor(); solo después, Oscar")]
    for cod, t in manual:
        fila(cod, t, "n/a", "a rellenar a mano")
    bloqueo = any(f["estado"] == "✗" for f in lineas)
    return {"texto": redacta(texto)[:200], "nivel": nivel, "bloqueo": bloqueo, "lineas": lineas,
            "fuentes_caidas": {**err_k, **err_p}}


def a_texto(f):
    out = [f"Ficha de decisión (nivel {f['nivel']}) — «{f['texto']}»"]
    for l in f["lineas"]:
        out.append(f"- {l['criterio']} {l['estado']} {l['titulo']}: {l['detalle']}")
    out.append("")
    out.append("RESULTADO: " + ("hay un ✗ → NO se decide solo; pregunta a Oscar con esta evidencia." if f["bloqueo"]
                                else "sin ✗ en lo comprobable → rellena a mano lo que falte (n/a y ⚠) y, si todo pasa, decide y avisa en una línea."))
    out.append("LÍMITE: esta ficha NO cubre las reglas duras de §1 ni el hook de permisos. Un push o merge a master/main, un despliegue, "
               "una migración, un envío real o un cambio de variables NUNCA se decide por la ficha: va con la confirmación de Oscar.")
    if f["fuentes_caidas"]:
        out.append("Fuentes no disponibles: " + json.dumps(f["fuentes_caidas"], ensure_ascii=False))
    return "\n".join(out)


def main(argv=None, tarjetas_fn=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("texto", help="idea o decisión, en una o dos frases")
    ap.add_argument("--nivel", type=int, choices=(1, 2), default=2)
    ap.add_argument("--docs", action="append", default=None, help="carpeta con decisiones/pitches/memoria (repetible)")
    ap.add_argument("--prs", action="append", default=[], help="repo owner/nombre para buscar PR abiertos (repetible)")
    ap.add_argument("--ramas", action="append", default=[], help="ruta de un repo para buscar ramas parecidas (repetible)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if len(a.texto.strip()) < 8:
        print("El texto es demasiado corto para buscar.", file=sys.stderr)
        return 1
    carpetas = a.docs
    if carpetas is None:
        home = Path.home()
        carpetas = [p for p in (Path(__file__).resolve().parents[2] / "decisions", Path(__file__).resolve().parents[2] / "pitches")
                    if p.is_dir()] + [str(m) for m in (home / ".claude" / "projects").glob("*/memory")]
    f = ficha(a.texto, a.nivel, tarjetas_fn, carpetas, a.prs, a.ramas)
    print(json.dumps(f, ensure_ascii=False, indent=1) if a.json else a_texto(f))
    return 2 if f["bloqueo"] else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
