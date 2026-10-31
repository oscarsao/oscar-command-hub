"""Retro semanal automática: el informe de fricción de la retrospectiva del 22-10, cada lunes (tarjeta t_e2b5abde).

Cuenta, de los últimos 7 días: alertas más repetidas (alerts.log del monitor), bloqueos y preguntas repetidas, bloqueos
por guard, tarjetas en triage y tiempos desde que se crea una tarjeta hasta que queda hecha. Solo lectura: kanban de
Hermes y ficheros del monitor. Guarda el informe completo en docs/retro/<fecha>.md del checkout vivo (sin versionar:
el checkout vivo no se toca con git) y manda un resumen al DM de Oscar. Una vez por semana ISO; la hora sale del ajuste
`avisos.retro_hora` (por defecto 9, Madrid).
"""
from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from .config import ROOT
from .health import MONITOR_STATE_DIR

log = logging.getLogger("agent_lanes")

RETRO_DIR = ROOT.parents[1] / "docs" / "retro"
DAYS = 7
TOP = 8
STATUSES = ("blocked", "triage", "done")
LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\w+) (.*)$")


def _norm(text: str) -> str:
    """Primera línea sin cifras, para agrupar «RAM al 95%» y «RAM al 97%»."""
    return re.sub(r"\d+(?:[.,]\d+)?", "N", (text or "").split("\n")[0]).strip()[:90]


def alertas(log_path: Path, now: float, days: int = DAYS) -> Counter:
    """Avisos por texto normalizado en los últimos `days` días de alerts.log."""
    out: Counter = Counter()
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        m = LINE_RE.match(ln)
        if not m or m.group(2) in ("INFO", "RESUELTO"):
            continue
        try:
            at = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        if now - at <= days * 86400:
            out[f"[{m.group(2)}] {_norm(m.group(3))}"] += 1
    return out


def _txt(t: dict) -> str:
    return " ".join(str(t.get(k) or "") for k in ("title", "block_reason", "result", "summary"))


def analizar(tasks: dict[str, list[dict]], now: float, days: int = DAYS) -> dict:
    """Métricas del kanban. Los campos que Hermes no devuelva cuentan como ausentes (nunca lanza)."""
    blocked = tasks.get("blocked") or []
    reasons = Counter(_norm(str(t.get("block_reason") or t.get("title") or "")) for t in blocked)
    done_recent, tiempos = [], []
    for t in tasks.get("done") or []:
        try:
            end = float(t.get("completed_at") or 0)
            start = float(t.get("created_at") or 0)
        except (TypeError, ValueError):
            continue
        if end and now - end <= days * 86400:
            done_recent.append(t)
            if start and end > start:
                tiempos.append((end - start) / 3600)
    triage = tasks.get("triage") or []
    ages = []
    for t in triage:
        try:
            ages.append((now - float(t.get("created_at") or now)) / 3600)
        except (TypeError, ValueError):
            continue
    return {
        "bloqueadas": len(blocked),
        "repetidas": [(r, n) for r, n in reasons.most_common(TOP) if n > 1 and r],
        "guard": sum(1 for t in blocked if re.search(r"guard", _txt(t), re.I)),
        "triage": len(triage),
        "triage_max_h": max(ages) if ages else 0.0,
        "hechas": len(done_recent),
        "mediana_h": statistics.median(tiempos) if tiempos else None,
        "max_h": max(tiempos) if tiempos else None,
    }


# --- autoaprendizaje: la retro PROPONE reglas (nunca las aplica) ------------------------------------------------------------

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
SECRETO_RE = re.compile(
    r"(?:sk-|ghp_|gho_|github_pat_|xox[bp]-|AKIA)[\w-]{6,}|\b\d{6,}:[\w-]{20,}|\b[A-Za-z0-9_\-]{32,}\b"
    r"|[\w.+-]+@[\w-]+\.[\w.]+|(?:token|secret|password|passwd|api[_-]?key|clave)\s*[=:]\s*\S+", re.I)
PROPUESTAS_MAX = 5
EJEMPLO_MAX = 160


def recortar(text: str, n: int = EJEMPLO_MAX) -> str:
    """Ejemplo para la tarjeta: primera línea, sin secretos ni correos, recortada."""
    line = SECRETO_RE.sub("«oculto»", (text or "").strip().split("\n")[0])
    return line if len(line) <= n else line[: n - 1] + "…"


def regex_sugerida(text: str) -> str:
    """Regex para bloqueos.yaml sacada del motivo: cifras -> \\d+, resto escapado (Oscar la afina al aceptar)."""
    line = recortar(text, 70)
    return r"\d+".join(re.escape(p) for p in NUM_RE.split(line))


def _ts(t: dict) -> float | None:
    for k in ("blocked_at", "updated_at", "created_at"):
        try:
            if t.get(k):
                return float(t[k])
        except (TypeError, ValueError):
            continue
    return None


def propuestas(blocked: list[dict], now: float, umbral: int, days: int = DAYS, cfg: dict | None = None) -> list[dict]:
    """Patrones de bloqueo SIN regla (bloqueos.clasificar = desconocido) que se repiten >= `umbral` veces en `days` días.
    Una propuesta: patrón normalizado, conteo, ids de tarjeta (evidencia), ejemplo recortado y regex sugerida."""
    from . import bloqueos
    grupos: dict[str, list[dict]] = {}
    for t in blocked:
        at = _ts(t)
        if at is not None and now - at > days * 86400:
            continue
        reason = str(t.get("block_reason") or "")
        if not reason.strip() or bloqueos.clasificar(reason, cfg).clase != bloqueos.DESCONOCIDO:
            continue
        grupos.setdefault(_norm(reason), []).append(t)
    out = [{"patron": p, "n": len(ts), "ids": [str(t.get("id")) for t in ts if t.get("id")][:10],
            "ejemplo": recortar(str(ts[0].get("block_reason"))), "regex": regex_sugerida(str(ts[0].get("block_reason"))),
            "guard": any(re.search(r"guard", _txt(t), re.I) for t in ts)}
           for p, ts in grupos.items() if len(ts) >= max(1, umbral)]
    return sorted(out, key=lambda p: -p["n"])[:PROPUESTAS_MAX]


def siguiente_id(lecs) -> str:
    n = max([int(l.id[2:]) for l in lecs] or [0]) + 1
    return "L-%04d" % n


def aprendizaje(now: float, props: list[dict], retirables, creadas: int, lecs) -> dict:
    """Cifras de la medida «tras 2 semanas»: propuestas creadas, lecciones activas (= reglas aceptadas) y retiradas."""
    return {"propuestas": len(props), "creadas": creadas, "retirables": [l.id for l in retirables],
            "activas": sum(1 for l in lecs if l.estado == "activa"), "retiradas": sum(1 for l in lecs if l.estado == "retirada")}


def cuerpo_propuestas(week: str, props: list[dict], retirables, siguiente: str) -> str:
    L = [f"Retro {week}: propongo reglas nuevas con lo que se repite. NO se ha aplicado nada.", "",
         "Si aceptas una, pide a claude-hub una tarjeta que: (1) cree docs/lecciones/<id>.md con el hecho comprobado y "
         "(2) añada la regla citando `leccion: <id>`. Si no, rechaza esta tarjeta.", ""]
    nxt = int(siguiente[2:])
    for n, p in enumerate(props):
        L += [f"## {n + 1}. {p['patron']}", f"- Repeticiones en la ventana: {p['n']}",
              f"- Tarjetas (evidencia): {', '.join(p['ids']) or '—'}", f"- Ejemplo: {p['ejemplo']}",
              f"- Lección que se crearía: L-{nxt + n:04d}",
              "- Regla sugerida (bloqueos.yaml" + (" o línea de preflight.py" if p["guard"] else "") + "):",
              "```yaml", f"- clase: estructural   # o transitorio", f"  regex: '{p['regex']}'",
              f"  resumen: <qué pasa, en llano>", f"  que_falta: <qué hay que hacer>", f"  leccion: L-{nxt + n:04d}",
              "```", ""]
    if retirables:
        L += ["## Reglas candidatas a retirar (sin dispararse en el plazo)", ""]
        L += [f"- {l.id}: {l.titulo or l.regla} ({l.regla})" for l in retirables]
        L.append("")
    return "\n".join(L)


def render(now: float, al: Counter, k: dict, ap: dict | None = None) -> str:
    fecha = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    med = "sin datos" if k["mediana_h"] is None else f"{k['mediana_h']:.1f} h (máx. {k['max_h']:.1f} h)"
    L = [f"# Retro semanal · {fecha}", "", f"Últimos {DAYS} días. Generada sola por el integrador (agent_lanes/retro.py).", "",
         "## Alertas más repetidas", ""]
    L += [f"- {n}× {t}" for t, n in al.most_common(TOP)] or ["- Ninguna."]
    L += ["", "## Bloqueos y preguntas repetidas", "",
          f"- Tarjetas bloqueadas ahora: {k['bloqueadas']} (por guard: {k['guard']})"]
    L += [f"- {n}× {r}" for r, n in k["repetidas"]]
    L += ["", "## Triage", "", f"- En triage: {k['triage']}" + (f" (la más vieja, {k['triage_max_h']:.0f} h)" if k["triage"] else ""),
          "", "## Tiempos", "", f"- Hechas en la semana: {k['hechas']}",
          f"- De crearse a quedar hecha (mediana): {med}", ""]
    if ap:
        L += ["## Autoaprendizaje", "", f"- Propuestas de reglas esta semana: {ap['propuestas']} (en total: {ap['creadas']})",
              f"- Reglas aceptadas (lecciones activas): {ap['activas']} · retiradas: {ap['retiradas']}",
              f"- Candidatas a retirar: {', '.join(ap['retirables']) or 'ninguna'}", ""]
    return "\n".join(L)


def resumen(now: float, al: Counter, k: dict, path: Path | None, ap: dict | None = None) -> str:
    top = [f"• {n}× {t}" for t, n in al.most_common(3)] or ["• sin alertas"]
    med = "—" if k["mediana_h"] is None else f"{k['mediana_h']:.1f} h"
    apr = (f"\n🧠 {ap['propuestas']} propuesta(s) de regla (tarjeta [DECISIÓN]) · aceptadas {ap['activas']} · "
           f"retiradas {ap['retiradas']}" if ap and (ap["propuestas"] or ap["retirables"]) else "")
    return ("📋 Retro semanal\n" + "\n".join(top) + f"\nBloqueadas {k['bloqueadas']} (guard {k['guard']}) · triage {k['triage']}"
            f" · hechas {k['hechas']} (mediana {med})" + apr + (f"\nInforme: {path}" if path else ""))


def due(now: float, hour: int, last_week: str | None) -> bool:
    d = datetime.fromtimestamp(now)
    return d.weekday() == 0 and d.hour >= hour and last_week != "%d-W%02d" % d.isocalendar()[:2]


def _autoaprendizaje(i, tasks: dict, now: float, week: str, lecciones_dir: Path | None) -> dict | None:
    """Propuestas de reglas + candidatas a retirar en UNA tarjeta [DECISIÓN] para oscar (key por semana: idempotente).
    Nunca aplica nada y nunca rompe la retro: un fallo se anota en el log."""
    from . import ajustes_hub, lecciones
    try:
        lecs = lecciones.cargar(lecciones_dir)
        props = propuestas(tasks.get("blocked") or [], now, int(ajustes_hub.valor("retro.umbral_propuesta")),
                           int(ajustes_hub.valor("retro.ventana_dias")))
        retir = lecciones.retirables(now, int(ajustes_hub.valor("lecciones.retirar_dias")), lecciones_dir)
        st = i._state("retro_propuestas")
        creadas = int(st.get("creadas") or 0)
        if (props or retir) and st.get("semana") != week:
            title = f"[DECISIÓN] Retro {week}: {len(props)} regla(s) propuesta(s), {len(retir)} a retirar"
            i.hermes_for("default").create(title, cuerpo_propuestas(week, props, retir, siguiente_id(lecs)), "oscar",
                                           key=f"retro-propuestas-{week}", created_by="retro")
            creadas += len(props)
            i._save("retro_propuestas", semana=week, creadas=creadas)
        return aprendizaje(now, props, retir, creadas, lecs)
    except Exception as exc:  # noqa: BLE001 - la propuesta es un extra: el informe semanal sale igual
        log.warning("retro: propuestas sin crear: %s", exc)
        return None


def check(i, *, out_dir: Path | None = None, alerts_log: Path | None = None,
          lecciones_dir: Path | None = None) -> str | None:
    """Pasada del integrador: si es lunes y toca, genera, guarda y avisa. Devuelve el resumen enviado, o None."""
    from . import ajustes_hub
    if i.dry_run or not (i.notifier and i.desk):
        return None
    now = i._now()
    st = i._state("retro")
    if not due(now, int(ajustes_hub.valor("avisos.retro_hora")), st.get("week")):
        return None
    tasks: dict[str, list[dict]] = {s: [] for s in STATUSES}
    for board in sorted({ln.board for ln in i.lanes.values() if ln.board}):
        for s in STATUSES:
            try:
                tasks[s] += i.hermes_for(board).list_status_all(s)
            except Exception as exc:  # noqa: BLE001 - un tablero caído no impide el informe
                log.warning("retro: %s/%s sin leer: %s", board, s, exc)
    week = "%d-W%02d" % datetime.fromtimestamp(now).isocalendar()[:2]
    al, k = alertas(alerts_log or MONITOR_STATE_DIR / "alerts.log", now), analizar(tasks, now)
    ap = _autoaprendizaje(i, tasks, now, week, lecciones_dir)
    path = None
    try:
        d = Path(out_dir or RETRO_DIR)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{datetime.fromtimestamp(now):%Y-%m-%d}.md"
        path.write_text(render(now, al, k, ap), encoding="utf-8", newline="\n")
    except OSError as exc:
        log.warning("retro: no pude guardar el informe: %s", exc)
        path = None
    text = resumen(now, al, k, path, ap)
    owner = str(getattr(i.desk, "owner_id", "") or "")
    if owner:
        i.notifier.send_to(owner, None, text, html=False)
    i._save("retro", week=week)
    return text
