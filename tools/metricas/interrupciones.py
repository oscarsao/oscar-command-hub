#!/usr/bin/env python3
"""Metricas semanales de interrupciones a Oscar, a partir de las transcripciones de Claude Code.

Solo lectura: recorre ~/.claude/projects/**/*.jsonl, cuenta cuantas veces se le pregunto con botones
(AskUserQuestion), cuantas veces acepto la opcion recomendada, cuanto tardo en contestar y cuantas veces
repitio un «recuerda…». No guarda texto de preguntas ni de respuestas: solo cifras.

Uso:
  python tools/metricas/interrupciones.py                 # ultimos 28 dias, tabla por semana
  python tools/metricas/interrupciones.py --dias 14 --json
  python tools/metricas/interrupciones.py --raiz RUTA     # otra carpeta de transcripciones (pruebas)

Limites conocidos (se imprimen al final): los avisos del hook de permisos no quedan en la transcripcion,
asi que NO se cuentan; los carriles (lane/*) son workers y no preguntan, se excluyen de «sesiones».
"""
import argparse
import json
import os
import re
import statistics as st
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

RECOMENDADA = re.compile(r"recomend|recommend", re.I)
RECUERDA = re.compile(r"\brecuerda(?:me)?\b", re.I)
# Carpetas que no son conversaciones con Oscar: carriles (workers) y pruebas del scratchpad.
EXCLUIR = ("lanes-lane", "scratchpad", "scratch-workspaces")


def semana(d: str) -> str:
    """Semana ISO ('2026-W46') de una fecha AAAA-MM-DD."""
    y, w, _ = datetime.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def _t(s: str):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def respuestas(texto: str, preguntas: list[str]) -> dict[str, str]:
    """Extrae «pregunta»=«respuesta» del tool_result de AskUserQuestion."""
    out = {}
    for q in preguntas:
        i = texto.find(f'"{q}"="')
        if i < 0:
            continue
        i += len(q) + 4
        j = texto.find('", "', i)
        out[q] = texto[i : j if j >= 0 else texto.rfind('"')]
    return out


def analiza_fichero(ruta: str, desde: float):
    """Devuelve (preguntas, recuerdas) de un transcript. preguntas: dicts sin texto de la pregunta."""
    pend, preguntas, recuerdas = {}, [], []
    with open(ruta, encoding="utf-8", errors="replace") as f:
        for linea in f:
            if "AskUserQuestion" not in linea and "tool_result" not in linea and "recuerda" not in linea.lower():
                continue
            try:
                d = json.loads(linea)
            except ValueError:
                continue
            ts = d.get("timestamp") or ""
            m = d.get("message") or {}
            c = m.get("content")
            if d.get("type") == "user" and isinstance(c, str) and RECUERDA.search(c) and ts:
                recuerdas.append(ts[:10])
            if not isinstance(c, list):
                continue
            for b in c:
                t = b.get("type")
                if t == "tool_use" and b.get("name") == "AskUserQuestion" and ts:
                    qs = (b.get("input") or {}).get("questions") or []
                    pend[b["id"]] = (ts, qs)
                elif t == "tool_result" and b.get("tool_use_id") in pend:
                    ts0, qs = pend.pop(b["tool_use_id"])
                    cont = b.get("content")
                    if isinstance(cont, list):
                        cont = " ".join(x.get("text", "") for x in cont if isinstance(x, dict))
                    cont = cont or ""
                    try:
                        if _t(ts0).timestamp() < desde:
                            continue
                        espera = (_t(ts) - _t(ts0)).total_seconds() / 60 if ts else None
                    except ValueError:
                        continue
                    r = respuestas(cont, [q.get("question", "") for q in qs])
                    for q in qs:
                        ops = q.get("options") or []
                        rec = next((o.get("label", "") for o in ops if RECOMENDADA.search(o.get("label", ""))), None)
                        resp = r.get(q.get("question", ""))
                        preguntas.append({
                            "dia": ts0[:10],
                            "tanda": f"{ruta}:{ts0}",
                            "espera": espera,
                            "tiene_rec": rec is not None,
                            "acepto_rec": bool(rec and resp is not None and resp == rec),
                            "contesto": resp is not None,
                            "n_opciones": len(ops),
                        })
    return preguntas, [d for d in recuerdas if _t(d + "T00:00:00Z").timestamp() >= desde]


def recorre(raiz: str, dias: int):
    desde = time.time() - dias * 86400
    P, R, sesiones = [], [], defaultdict(set)
    for carpeta in sorted(os.listdir(raiz)):
        if any(x in carpeta for x in EXCLUIR):
            continue
        base = os.path.join(raiz, carpeta)
        if not os.path.isdir(base):
            continue
        for nombre in os.listdir(base):
            if not nombre.endswith(".jsonl"):
                continue
            ruta = os.path.join(base, nombre)
            if os.path.getmtime(ruta) < desde:
                continue
            p, r = analiza_fichero(ruta, desde)
            for x in p:
                sesiones[x["dia"]].add(nombre)
            P += p
            R += r
    return P, R, sesiones


def resumen(P, R, sesiones):
    por = defaultdict(lambda: {"preguntas": 0, "tandas": set(), "con_rec": 0, "acepto": 0, "esperas": [], "dias": set(), "sesiones": set()})
    for x in P:
        s = por[semana(x["dia"])]
        s["preguntas"] += 1
        s["tandas"].add(x["tanda"])
        s["dias"].add(x["dia"])
        s["con_rec"] += x["tiene_rec"]
        s["acepto"] += x["acepto_rec"]
        if x["espera"] is not None and x["espera"] < 1440:
            s["esperas"].append(x["espera"])
    for d, ss in sesiones.items():
        por[semana(d)]["sesiones"] |= ss
    rec = defaultdict(int)
    for d in R:
        rec[semana(d)] += 1
    filas = []
    for k in sorted(set(por) | set(rec)):
        s = por[k]
        nd = max(1, len(s["dias"]))
        filas.append({
            "semana": k,
            "tandas": len(s["tandas"]),
            "tandas_por_dia_activo": round(len(s["tandas"]) / nd, 1),
            "preguntas": s["preguntas"],
            "dias_activos": len(s["dias"]),
            "sesiones": len(s["sesiones"]),
            "pct_recomendada_aceptada": round(100 * s["acepto"] / s["con_rec"]) if s["con_rec"] else None,
            "espera_mediana_min": round(st.median(s["esperas"]), 1) if s["esperas"] else None,
            "recuerda_repetidos": rec[k],
        })
    return filas


def imprime(filas):
    cab = ("semana", "tandas (interrupciones)", "tandas/día", "preguntas", "días", "sesiones", "% rec. aceptada", "espera med. (min)", "«recuerda…»")
    print(" | ".join(cab))
    print(" | ".join("---" for _ in cab))
    for f in filas:
        print(" | ".join("-" if v is None else str(v) for v in f.values()))
    print()
    print("Límites: no cuenta los avisos del hook de permisos (no quedan en la transcripción); excluye carriles y pruebas;")
    print("«% rec. aceptada» solo cuenta preguntas con opción marcada Recomendada/Recommended; «tandas» = veces que se te mostró una pantalla de botones (una tanda puede llevar hasta 4 preguntas);")
    print("«/día» = por día con actividad (hay semanas sin días de descanso).")


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dias", type=int, default=28)
    ap.add_argument("--raiz", default=os.path.join(os.path.expanduser("~"), ".claude", "projects"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not os.path.isdir(a.raiz):
        print(f"No existe {a.raiz}", file=sys.stderr)
        return 2
    P, R, sesiones = recorre(a.raiz, a.dias)
    filas = resumen(P, R, sesiones)
    if a.json:
        print(json.dumps(filas, ensure_ascii=False, indent=2))
    else:
        imprime(filas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
