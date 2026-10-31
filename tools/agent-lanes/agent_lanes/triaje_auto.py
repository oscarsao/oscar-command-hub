"""Triaje automático de bloqueos (tarjeta t_73351cea): remedios de una LISTA BLANCA CERRADA para tarjetas en blocked o triage.

Sin LLM: solo reglas. Lo que no casa con la lista blanca no se toca aquí; sigue su camino de siempre (el aviso con botones
de decisions.py) y queda anotado como `no_casa` para que la retro semanal proponga ampliar la lista.

Patrones (config en bloqueos.yaml `triaje:`; lo que falte vale DEFAULTS):
  1. transitorio   fallo transitorio de git/SQLite («could not lock config file», «read-only»)  -> reintentar   (auto)
  2. espacios      verificación mecánica por espacios finales en ficheros de terceros           -> .gitattributes + reintentar (auto)
  3. repo          «esto no está en este repo, está en <repo>»                                  -> PROPONE [Recrear en <carril>] [Dejar]
  5. triage        tarjeta en triage tras bloqueos repetidos cuya causa es 1 o 2                -> requeue_triage (auto)

Modos (ajuste `triaje.modo`): apagado | aviso (propone con botones; por defecto) | auto (aplica 1, 2 y 5; el 3 siempre propone).
Nunca automático: producción, migraciones, secretos, borrados, MigraTeam y tarjetas con una decisión de Oscar pendiente
(bloqueo `needs_input`). Tope de intentos por tarjeta (`triaje.tope_intentos`): al alcanzarlo se escala sin más.
Cada acción queda en `.state/triaje.jsonl` y, si se aplica, como comentario «Triaje automático: …» en la tarjeta.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import time
from collections import Counter
from pathlib import Path

from .config import ROOT

log = logging.getLogger("agent_lanes")

LOG_PATH = ROOT / ".state" / "triaje.jsonl"
AUTHOR = "triaje-auto"
APLICAR, DEJAR = "triaje_aplicar", "triaje_dejar"
ACTIONS = (APLICAR, DEJAR)
MODOS = ("apagado", "aviso", "auto")
AUTO_REMEDIOS = ("reintentar", "gitattributes", "requeue")
STATUSES = ("blocked", "triage")
ESCALADA = re.compile(r"\s*Bloqueada \d+ veces por lo mismo\b.*?\n\s*Último detalle:(?P<detalle>.*)", re.DOTALL | re.IGNORECASE)

DEFAULTS: dict = {
    "patrones": [
        {"id": "transitorio", "remedio": "reintentar",
         "regex": r"could not lock config file|base de datos read-?only|readonly database|database is locked|attempt to write a readonly"},
        {"id": "espacios", "remedio": "gitattributes",
         "regex": r"trailing whitespace|espacios finales|diff --check"},
        {"id": "repo", "remedio": "recrear",
         "regex": r"(?:no est[aá] en este repo|not in this repo|repo equivocado)[^\n]{0,120}?(?:est[aá] en|is in|vive en)\s+"
                  r"(?P<repo>oscar-hq|scraper|nextjobs|migrateam|oscar-command-hub)"},
    ],
    # repo citado en la tarjeta -> carril donde recrearla. MigraTeam no está: nunca se propone moverla ahí.
    "carril_por_repo": {"oscar-hq": "claude-oscarhq", "scraper": "claude-scraper", "nextjobs": "claude-nextjobs",
                        "oscar-command-hub": "claude-hub"},
    # Nunca automático (se busca en título, motivo y últimos comentarios): se escala siempre a Oscar.
    "nunca": r"producci[oó]n|\bprod\b|production|migraci[oó]n|alembic|secret|token|credencial|\.env\b|\bdrop\b|truncate|"
             r"borrar|delete|rm -rf|migrateam|deploy|despleg|desplieg",
    "gitattributes_texto": "Añade a .gitattributes la línea `<ruta del fichero de terceros> -whitespace` (o `* -whitespace` "
                           "solo para esa carpeta) y vuelve a lanzar la verificación; no reformatees ficheros de terceros.",
    "comentarios": 3,          # últimos comentarios que se leen como «último resumen»
    "umbral_retro": 3,         # veces que un motivo sin patrón debe repetirse en la semana para proponerlo en la retro
}


def load_config() -> dict:
    """DEFAULTS + sección `triaje:` de bloqueos.yaml (un valor de otro tipo que el del defecto se ignora). Nunca lanza."""
    from . import bloqueos
    cfg = copy.deepcopy(DEFAULTS)
    over = (bloqueos.load_config().get("triaje") or {})
    if isinstance(over, dict):
        for k, v in over.items():
            if k in cfg and type(v) is type(cfg[k]):
                cfg[k] = v
    return cfg


def modo() -> str:
    from . import ajustes_hub
    m = str(ajustes_hub.valor("triaje.modo") or "aviso")
    return m if m in MODOS else "aviso"


def tope() -> int:
    from . import ajustes_hub
    try:
        return max(1, int(ajustes_hub.valor("triaje.tope_intentos")))
    except (TypeError, ValueError):
        return 2


# --- registro -------------------------------------------------------------------------------------------

def registrar(tid: str, patron: str, remedio: str, resultado: str, *, firma: str = "", motivo: str = "", extra: dict | None = None,
              path: Path | None = None, now: float | None = None) -> None:
    rec = {"ts": now if now is not None else time.time(), "tarjeta": tid, "patron": patron, "remedio": remedio,
           "resultado": resultado, "firma": firma, "motivo": _norm(motivo), **(extra or {})}
    p = Path(path or LOG_PATH)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("triaje: no pude anotar en %s: %s", p, exc)


def leer(path: Path | None = None) -> list[dict]:
    out = []
    try:
        for line in Path(path or LOG_PATH).read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return out


def _norm(text: str) -> str:
    """Motivo sin ids, rutas ni números, para contar «el mismo bloqueo» entre tarjetas."""
    t = (str(text or "").strip().splitlines() or [""])[0].lower()
    t = re.sub(r"t_[0-9a-f]{8}|\b[0-9a-f]{7,40}\b|[a-z]:[\\/]\S+|(?:\S*[\\/]\S+)|\d+", "…", t)
    return re.sub(r"\s+", " ", t)[:90]


# --- lectura de la tarjeta -------------------------------------------------------------------------------

def situacion(show: dict, n_comentarios: int = 3) -> dict:
    """Estado, motivo del último bloqueo y texto («último resumen») de una tarjeta a partir de su `show`."""
    task = show.get("task") or {}
    blocks = [ev for ev in show.get("events") or [] if ev.get("kind") == "blocked"]
    payload = (blocks[-1].get("payload") or {}) if blocks else {}
    reason = str(payload.get("reason") or "")
    comments = [str(c.get("body") or "") for c in (show.get("comments") or [])][-n_comentarios:] if n_comentarios > 0 else []
    firma = hashlib.sha1(f"{len(blocks)}|{reason}".encode("utf-8", "replace")).hexdigest()[:10]
    # runner._block, al 2º bloqueo transitorio igual, lo reescribe como needs_input «Bloqueada N veces por lo mismo… Último
    # detalle: <causa>»: es un transitorio escalado, no una pregunta del worker; casa por la causa (el «Último detalle»).
    esc = ESCALADA.match(reason)
    if esc:
        reason = esc.group("detalle").strip() or reason
    return {"status": task.get("status"), "title": str(task.get("title") or ""), "assignee": str(task.get("assignee") or ""),
            "reason": reason, "kind": payload.get("kind"), "escalada": bool(esc), "texto": "\n".join([reason, *comments]),
            "firma": firma}


def decision_pendiente(sit: dict) -> bool:
    """Pregunta real del worker a Oscar (needs_input que no es un transitorio escalado): solo el patrón 3, que propone con botones."""
    return sit.get("kind") == "needs_input" and not sit.get("escalada")


def es_nunca(sit: dict, cfg: dict) -> bool:
    """Producción, migraciones, secretos, borrados o MigraTeam: jamás automático. (La decisión pendiente de Oscar va aparte:
    decision_pendiente(); solo deja pasar el patrón 3, que únicamente propone.)"""
    if "migrateam" in (sit.get("assignee") or "").lower():
        return True
    try:
        return bool(re.search(cfg["nunca"], f"{sit.get('title', '')}\n{sit.get('texto', '')}", re.IGNORECASE))
    except re.error:
        return True  # regex rota = ante la duda, no se hace nada solo


def casar(sit: dict, cfg: dict) -> dict | None:
    """Primer patrón de la lista blanca que casa: {patron, remedio, destino?} o None. El 5 (triage) se deduce aquí."""
    from . import bloqueos
    pendiente = decision_pendiente(sit)
    for pat in cfg.get("patrones") or ():
        if pendiente and pat.get("remedio") != "recrear":
            continue
        try:
            m = re.search(pat["regex"], sit["texto"], re.IGNORECASE)
        except (KeyError, TypeError, re.error):
            continue
        if not m:
            continue
        remedio = pat.get("remedio")
        if remedio == "recrear":
            repo = (m.groupdict().get("repo") or "").lower()
            carril = (cfg.get("carril_por_repo") or {}).get(repo)
            if not carril:
                return None  # repo desconocido o no permitido (MigraTeam): no se propone mover nada
            return {"patron": pat["id"], "remedio": "recrear", "destino": carril}
        if remedio not in ("reintentar", "gitattributes"):
            continue
        if sit["status"] == "triage":  # patrón 5: sacar de triage una vez resuelta la causa
            return {"patron": f"triage+{pat['id']}", "remedio": "requeue", "previo": remedio}
        return {"patron": pat["id"], "remedio": remedio}
    if not pendiente and sit["status"] == "triage" and bloqueos.clasificar(sit["reason"]).clase == bloqueos.TRANSITORIO:
        return {"patron": "triage+transitorio", "remedio": "requeue", "previo": None}
    return None


def intentos(tid: str, path: Path | None = None) -> int:
    return sum(1 for r in leer(path) if r.get("tarjeta") == tid and r.get("resultado") == "aplicado")


# --- remedios -------------------------------------------------------------------------------------------

def _comentar(h, tid: str, texto: str) -> None:
    try:
        h.comment(tid, f"Triaje automático: {texto}", author=AUTHOR)
    except Exception as exc:  # noqa: BLE001 - el comentario es informativo
        log.warning("triaje: no pude comentar %s: %s", tid, exc)


def aplicar(h, tid: str, dec: dict, sit: dict, cfg: dict, *, hermes_for=None, lanes=None, quien: str = "automático") -> tuple[bool, str]:
    """Ejecuta UN remedio de la lista blanca. Devuelve (ok, qué se hizo). Nunca lanza."""
    remedio = dec["remedio"]
    if remedio not in AUTO_REMEDIOS + ("recrear",):
        return False, f"remedio fuera de la lista blanca: {remedio}"
    try:
        if remedio == "recrear":
            lane = (lanes or {}).get(dec.get("destino"))
            if lane is None or hermes_for is None:
                return False, "no encuentro el carril destino"
            show = h.show(tid)
            task = show.get("task") or {}
            nota = f"Recreada desde {tid} ({sit['assignee']}): la tarjeta apuntaba al repo equivocado.\n\n"
            nuevo = hermes_for(lane.board).create(sit["title"], nota + str(task.get("body") or ""), lane.name,
                                                  key=f"triaje-recrear-{tid}")
            _comentar(h, tid, f"recreada en {lane.name} como {nuevo} (repo equivocado, {quien}); esta se archiva")
            if not h.archive(tid):
                return False, f"recreada en {lane.name} como {nuevo}, pero NO pude archivar la vieja {tid}: archívala tú"
            return True, f"recreada en {lane.name} como {nuevo}"
        previo = dec.get("previo") or remedio
        if previo == "gitattributes":
            _comentar(h, tid, "la verificación falló por espacios finales en ficheros de terceros. "
                              + cfg["gitattributes_texto"] + " Reanudo la tarjeta.")
        elif remedio == "reintentar":
            _comentar(h, tid, "fallo transitorio de git/SQLite; reintento la tarjeta.")
        else:
            _comentar(h, tid, "la causa del bloqueo repetido era transitoria; saco la tarjeta de triage.")
        ok = bool(h.requeue_triage(tid)) if (remedio == "requeue" or sit["status"] == "triage") else bool(h.unblock(tid))
        return ok, ("tarjeta reanudada" if ok else "Hermes no pudo reanudarla")
    except Exception as exc:  # noqa: BLE001
        log.warning("triaje: %s %s falló: %s", tid, remedio, exc)
        return False, f"error: {type(exc).__name__}"


def _texto_propuesta(tid: str, dec: dict, sit: dict) -> str:
    if dec["remedio"] == "recrear":
        return (f"🧭 Triaje · {tid}: la tarjeta «{sit['title'][:80]}» apunta al repo equivocado. Propongo recrearla en "
                f"{dec['destino']} y archivar la vieja (cambia de carril, por eso te lo pregunto).")
    que = {"reintentar": "reintentarla (fallo transitorio)", "gitattributes": "indicar el .gitattributes y desbloquearla",
           "requeue": "sacarla de triage"}[dec["remedio"]]
    return f"🧭 Triaje · {tid}: propongo {que}. Patrón: {dec['patron']}."


def _proponer(i, lane, tid: str, dec: dict, sit: dict) -> bool:
    desk, notifier = getattr(i, "desk", None), getattr(i, "notifier", None)
    owner = str(getattr(desk, "owner_id", "") or "")
    if not (desk and notifier and owner):
        return False
    rec = {"kind": "triaje_auto", "task_id": tid, "board": lane.board, "lane": lane.name, "title": sit["title"],
           "patron": dec["patron"], "remedio": dec["remedio"], "destino": dec.get("destino"), "previo": dec.get("previo"),
           "firma": sit["firma"]}
    etiqueta = f"🔁 Recrear en {dec['destino']}" if dec["remedio"] == "recrear" else "✅ Aplicar"
    markup = desk.store.issue(rec, [[{"text": etiqueta, "action": APLICAR}, {"text": "✋ Dejar", "action": DEJAR}]])[1]
    notifier.send_to(owner, None, _texto_propuesta(tid, dec, sit), html=False, reply_markup=markup)
    return True


# --- pasada ---------------------------------------------------------------------------------------------

def procesar(i, *, log_path: Path | None = None, now: float | None = None) -> list[dict]:
    """Una pasada sobre blocked/triage de todos los tableros. Devuelve lo hecho: [{tarjeta, patron, remedio, resultado}]."""
    m = modo()
    if m == "apagado":
        return []
    cfg, topes, hechos = load_config(), tope(), []
    previo = leer(log_path)
    vistas = {(r.get("tarjeta"), r.get("firma")) for r in previo}
    for board in sorted({ln.board for ln in i.lanes.values() if ln.board}):
        h = i.hermes_for(board)
        for status in STATUSES:
            try:
                tareas = h.list_status_all(status)
            except Exception as exc:  # noqa: BLE001 - un tablero caído no para el triaje
                log.warning("triaje: %s/%s sin leer: %s", board, status, exc)
                continue
            for t in tareas:
                tid = str(t.get("id") or "")
                lane = i.lanes.get(str(t.get("assignee") or ""))
                if not tid or lane is None:
                    continue
                try:
                    sit = situacion(h.show(tid), int(cfg["comentarios"]))
                except Exception as exc:  # noqa: BLE001
                    log.info("triaje: %s sin leer: %s", tid, exc)
                    continue
                if (tid, sit["firma"]) in vistas or sit["status"] not in STATUSES:
                    continue
                if es_nunca(sit, cfg):
                    continue  # a Oscar por el camino de siempre; ni se anota (no es un hueco de la lista blanca)
                dec = casar(sit, cfg)
                if dec is None and decision_pendiente(sit):
                    continue  # pregunta real del worker sin patrón: a Oscar por el camino de siempre; ni se anota
                if dec is None:
                    registrar(tid, "-", "-", "no_casa", firma=sit["firma"], motivo=sit["reason"], path=log_path, now=now)
                    vistas.add((tid, sit["firma"]))
                    hechos.append({"tarjeta": tid, "patron": "-", "remedio": "-", "resultado": "no_casa"})
                    continue
                if intentos(tid, log_path) >= topes:
                    res = "tope"  # anti-bucle: a Oscar por el camino de siempre
                elif m == "auto" and dec["remedio"] in AUTO_REMEDIOS:
                    ok, que = aplicar(h, tid, dec, sit, cfg)
                    res = "aplicado" if ok else "fallo"
                else:  # aviso, o recrear (siempre con botones)
                    res = "propuesto" if _proponer(i, lane, tid, dec, sit) else "sin_canal"
                registrar(tid, dec["patron"], dec["remedio"], res, firma=sit["firma"], motivo=sit["reason"], path=log_path, now=now)
                vistas.add((tid, sit["firma"]))
                hechos.append({"tarjeta": tid, "patron": dec["patron"], "remedio": dec["remedio"], "resultado": res})
    return hechos


def check(i) -> list[dict] | None:
    """Paso del integrador (como backmerge/retro). Solo lectura salvo la lista blanca en modo auto."""
    if getattr(i, "dry_run", False):
        return None
    return procesar(i)


def on_button(action: str, rec: dict, where: dict | None, desk) -> bool:
    """[✅ Aplicar]/[🔁 Recrear] y [✋ Dejar] de una propuesta. Aplica solo remedios de la lista blanca y respeta el tope."""
    tid, integ = rec.get("task_id"), getattr(desk, "integrator", None)
    if integ is None or not tid:
        return False
    say = (lambda t: desk.notifier.send_to(where["chat_id"], where.get("thread_id"), t, html=False)) \
        if where and getattr(desk, "notifier", None) else (lambda t: None)
    if action == DEJAR:
        registrar(tid, rec.get("patron", "-"), rec.get("remedio", "-"), "dejada", firma=rec.get("firma", ""))
        say(f"✋ {tid}: la dejo como está")
        return True
    h, cfg = integ.hermes_for(rec["board"]), load_config()
    try:
        sit = situacion(h.show(tid), int(cfg["comentarios"]))
    except Exception:  # noqa: BLE001
        say(f"{tid}: no pude leer la tarjeta ahora")
        return False
    if sit["status"] not in STATUSES or es_nunca(sit, cfg) or (decision_pendiente(sit) and rec.get("remedio") != "recrear"):
        say(f"{tid}: ya no está bloqueada o necesita tu decisión; no toco nada")
        return True
    if intentos(tid) >= tope():
        say(f"{tid}: ya se intentó el máximo de veces; mírala tú")
        return True
    dec = {"patron": rec.get("patron"), "remedio": rec.get("remedio"), "destino": rec.get("destino"), "previo": rec.get("previo")}
    ok, que = aplicar(h, tid, dec, sit, cfg, hermes_for=integ.hermes_for, lanes=integ.lanes, quien="a petición de Oscar")
    registrar(tid, dec["patron"], dec["remedio"], "aplicado" if ok else "fallo", firma=sit["firma"], motivo=sit["reason"])
    say(f"{'✅' if ok else '⚠️'} {tid}: {que}")
    return ok


# --- retro ----------------------------------------------------------------------------------------------

def sin_patron(now: float, days: int = 7, path: Path | None = None, umbral: int | None = None) -> list[tuple[str, int]]:
    """Motivos de bloqueo que NO casan con la lista blanca y se repiten en `days` días (candidatos a ampliarla)."""
    umbral = umbral if umbral is not None else int(load_config()["umbral_retro"])
    c = Counter(r.get("motivo") or "" for r in leer(path)
                if r.get("resultado") == "no_casa" and now - float(r.get("ts") or 0) <= days * 86400)
    return [(m, n) for m, n in c.most_common() if m and n >= umbral]


def retro_texto(now: float, path: Path | None = None) -> str:
    cand = sin_patron(now, path=path)
    if not cand:
        return ""
    return "Triaje · bloqueos sin remedio automático (¿añadir a la lista blanca?):\n" + "\n".join(f"- {n}× {m}" for m, n in cand[:5])


def proponer_ampliacion(i, week: str, now: float, path: Path | None = None) -> str | None:
    """Tarjeta de decisión para Oscar con los patrones nuevos; la lista blanca NUNCA se amplía sola."""
    cand = sin_patron(now, path=path)
    if not cand:
        return None
    lane = next((ln for ln in i.lanes.values() if ln.name == "claude-hub"), None) or next(iter(i.lanes.values()), None)
    if lane is None:
        return None
    cuerpo = ("DECISIÓN DE OSCAR pendiente. Estos bloqueos se repitieron la última semana y no casan con la lista blanca del "
              "triaje automático (bloqueos.yaml `triaje:`):\n\n" + "\n".join(f"- {n}× {m}" for m, n in cand[:8])
              + "\n\nOpciones: A) añadir el patrón a la lista blanca (tarjeta de implementación)  B) dejarlo como está.\n"
                "Recomendada: B salvo que el remedio sea siempre el mismo y reversible.")
    return i.hermes_for(lane.board).create(f"[Hub · Triaje] ¿Ampliar la lista blanca? Semana {week}", cuerpo, "oscar",
                                           key=f"triaje-retro-{week}")
