"""Resumen diario de las 8:00 (tarjeta t_631cf0bc): un mensaje HTML al grupo Gestión · General.

Determinista (sin LLM): lee los boards de Hermes por la CLI (`list --json` + `show --json` solo donde hace falta),
clasifica y envía con el TelegramNotifier de agent-lanes (el token nunca sale de telegram.py).

    py -3.12 brief/brief_diario.py --dry-run     # imprime el mensaje, no envía
    py -3.12 brief/brief_diario.py               # envía a Gestión · General
    py -3.12 brief/brief_diario.py --prueba --borrar   # envío de prueba con "(prueba)" y deleteMessage después
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):  # ejecutado como script: agent-lanes/ al path para importar agent_lanes
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_lanes.notices import BRANDS, card_url, money, truncate  # noqa: E402

log = logging.getLogger("brief_diario")

BOARDS = ("oscarhq", "migrateam", "default", "personal")
GESTION_CHAT = "-1003530490339"  # grupo Gestión; sin message_thread_id = tema General
DAY = 24 * 3600
TEXT_MAX = 4000   # Telegram admite 4096; send_to corta en 4096 y partiría el HTML
MAX_LINES = 30
TITLE_MAX = 48
BOARD_BRANDS = {"migrateam": "MigraTeam", "oscarhq": "Píldora"}
BRAND_ORDER = ("MigraTeam", "Píldora", "NextJobs", "Otros")
OSCAR = "oscar"
DECISION_TAGS = ("[DECISIÓN", "[SEMANA", "· DECISIÓN]", "[IDEA")
# (decisión, integrar, error, ids por marca en "hecho ayer"): de más generoso a más compacto
CAPS = ((8, 4, 4, 8), (8, 3, 3, 6), (6, 2, 2, 5), (5, 2, 2, 4), (4, 1, 1, 3), (3, 1, 1, 2))
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
STATE_ICON = {"libre": "🟢", "ocupado": "🔵", "huérfano": "🟠"}


# --- clasificación (pura) ----------------------------------------------------------------------------

def brand(board: str, assignee: str | None) -> str:
    if assignee in BRANDS:
        return BRANDS[assignee]
    return BOARD_BRANDS.get(board, "Otros")


def block_kind(detail: dict | None) -> str | None:
    """`kind` del último evento `blocked` (hermes no lo expone en list --json)."""
    kinds = [(e.get("payload") or {}).get("kind") for e in (detail or {}).get("events") or ()
             if e.get("kind") == "blocked"]
    return kinds[-1] if kinds else None


def _comments_start(detail: dict | None, prefix: str) -> bool:
    return any((c.get("body") or "").lstrip().startswith(prefix) for c in (detail or {}).get("comments") or ())


def review_approved(detail: dict | None) -> bool:
    return any(((r.get("metadata") or {}).get("review") or {}).get("status") == "approve"
               for r in (detail or {}).get("runs") or ())


def run_cost(metadata: dict | None) -> float:
    """Coste de una ejecución. Si trae `review`, solo cuenta el del revisor: review.py copia en esa metadata la
    de la implementación (con su cost_usd), que ya se contó en su propia ejecución."""
    md = metadata or {}
    if isinstance(md.get("review"), dict):
        md = md["review"]
    for key in ("cost_usd", "total_cost_usd", "cost"):
        try:
            v = float(md.get(key))
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return 0.0


def _prio(item: dict) -> tuple:
    t = item["task"]
    return (-(t.get("priority") or 0), t.get("created_at") or 0, t["id"])


def classify(items: list[dict], now: float) -> dict:
    """items: [{"board", "task" (de list --json), "detail" (show --json o None)}] -> secciones."""
    since = now - DAY
    decide, integrate, errors, done, backlog = [], [], [], [], []
    cost = 0.0
    for it in items:
        t, d = it["task"], it.get("detail")
        status, assignee = t.get("status"), t.get("assignee")
        it = {**it, "brand": brand(it["board"], assignee)}
        kind = block_kind(d) if status == "blocked" else None
        if status == "blocked" and kind == "needs_input":
            decide.append({**it, "why": "needs_input"})
        elif assignee == OSCAR and status in ("ready", "blocked"):
            # Solo lo marcado como decisión o de la semana pide atención hoy; el resto es su backlog.
            if any(tag in (t.get("title") or "") for tag in DECISION_TAGS):
                decide.append({**it, "why": "oscar"})
            else:
                backlog.append(it)
        elif status == "blocked":  # transient o sin kind conocido
            errors.append(it)
        if status in ("done", "review") and not _comments_start(d, "INTEGRADO"):
            if _comments_start(d, "APROBADO-OSCAR"):
                integrate.append({**it, "why": "pr"})
            elif review_approved(d):
                integrate.append({**it, "why": "review"})
        if status == "done" and (t.get("completed_at") or 0) >= since:
            done.append(it)
        for r in (d or {}).get("runs") or ():
            if (r.get("ended_at") or 0) >= since:
                cost += run_cost(r.get("metadata"))
    decide.sort(key=_prio)
    integrate.sort(key=lambda i: (i["why"] != "pr",) + _prio(i))
    errors.sort(key=_prio)
    by_brand: dict[str, list] = {}
    for it in sorted(done, key=lambda i: -(i["task"].get("completed_at") or 0)):
        by_brand.setdefault(it["brand"], []).append(it)
    return {"decide": decide, "integrate": integrate, "errors": errors, "backlog": len(backlog),
            "done": {b: by_brand[b] for b in BRAND_ORDER if b in by_brand}, "cost": cost}


# --- render (puro) ----------------------------------------------------------------------------------

def fecha(now: float) -> str:
    dt = datetime.fromtimestamp(now)
    return f"{DIAS[dt.weekday()]} {dt.day} {MESES[dt.month - 1]}"


def task_link(it: dict, base_url: str | None) -> str:
    tid = it["task"]["id"]
    url = card_url(base_url, it["board"], tid)
    return f'<a href="{html.escape(url, quote=True)}">{tid}</a>' if url else f"<code>{tid}</code>"


def _line(it: dict, base_url: str | None, tag: str = "") -> str:
    title = html.escape(truncate(it["task"].get("title"), TITLE_MAX))
    return f"• {task_link(it, base_url)} <i>{it['brand']}</i>{tag} · {title}"


def _more(n: int) -> list[str]:
    return [f"  +{n} más"] if n > 0 else []


def _render(sec: dict, lanes: list[dict], now: float, base_url: str | None, caps: tuple, prueba: bool) -> str:
    c_dec, c_int, c_err, c_ids = caps
    out = [f"☀️ <b>Buenos días · {fecha(now)}</b>" + (" (prueba)" if prueba else "")]
    if sec["decide"]:
        out.append(f"\n❓ <b>Esperan tu decisión ({len(sec['decide'])})</b>")
        out += [_line(i, base_url, " ❓" if i["why"] == "needs_input" else "") for i in sec["decide"][:c_dec]]
        out += _more(len(sec["decide"]) - c_dec)
    else:
        out.append("\nNada pendiente de ti 🎉")
    if sec.get("backlog"):
        out.append(f"📋 Tu backlog: {sec['backlog']} tarjetas sin urgencia (en el panel)")
    if sec["integrate"]:
        out.append(f"\n✅ <b>Listas para integrar ({len(sec['integrate'])})</b>")
        out += [_line(i, base_url, " · PR aprobado" if i["why"] == "pr" else "") for i in sec["integrate"][:c_int]]
        out += _more(len(sec["integrate"]) - c_int)
    if sec["errors"]:
        out.append(f"\n⛔ <b>Bloqueadas por error ({len(sec['errors'])})</b>")
        out += [_line(i, base_url) for i in sec["errors"][:c_err]]
        out += _more(len(sec["errors"]) - c_err)
    n_done = sum(len(v) for v in sec["done"].values())
    if n_done:
        out.append(f"\n🏁 <b>Hecho ayer ({n_done})</b>")
        for b, its in sec["done"].items():
            ids = ", ".join(task_link(i, base_url) for i in its[:c_ids])
            out.append(f"{b} ({len(its)}): {ids}" + (f" +{len(its) - c_ids}" if len(its) > c_ids else ""))
    else:
        out.append("\n🏁 Hecho ayer: nada cerrado")
    if lanes:
        out.append("\n🛣 <b>Carriles</b>")
        for r in lanes:
            busy = f" ({', '.join(r['running'])})" if r["running"] else ""
            out.append(f"{STATE_ICON.get(r['state'], '•')} {r['lane']} · {r['state']}{busy} · "
                       f"cola {r['ready']}" + (f" · review {r['review']}" if r.get("review") else ""))
    cost = money(sec["cost"])
    if cost:
        out.append(f"\n💸 Coste de ayer: {cost}")
    return "\n".join(out)


def n_lines(text: str) -> int:
    return len([ln for ln in text.split("\n") if ln.strip()])


def render(sec: dict, lanes: list[dict], now: float, base_url: str | None = None, *, prueba: bool = False) -> str:
    """El primer nivel de CAPS que cabe en MAX_LINES líneas con texto y TEXT_MAX caracteres."""
    text = ""
    for caps in CAPS:
        text = _render(sec, lanes, now, base_url, caps, prueba)
        if n_lines(text) <= MAX_LINES and len(text) <= TEXT_MAX:
            return text
    if len(text) > TEXT_MAX:  # último recurso: sin enlaces (nunca cortar el HTML a medias)
        text = _render(sec, lanes, now, None, CAPS[-1], prueba)
    return text


# --- IO: hermes -------------------------------------------------------------------------------------

def needs_detail(task: dict, lane_names: set[str], now: float) -> bool:
    status = task.get("status")
    if status in ("blocked", "review", "running"):
        return True
    if status == "done":
        return task.get("assignee") in lane_names or (task.get("completed_at") or 0) >= now - DAY
    return status == "ready" and bool(task.get("started_at"))  # reabierta: puede tener ejecuciones de ayer


def collect(hermes_for, boards=BOARDS, lane_names: set[str] = frozenset(), now: float | None = None,
            workers: int = 4) -> tuple[list[dict], dict[str, list[dict]]]:
    """(items, listas por board). `hermes_for(board)` -> HermesCLI (o un doble en tests)."""
    now = time.time() if now is None else now
    lists: dict[str, list[dict]] = {}
    for b in boards:
        cp = hermes_for(b)._call("list", "--json")
        if cp.returncode != 0:
            log.warning("list del board %s falló: %s", b, (cp.stderr or "")[:200])
            lists[b] = []
            continue
        lists[b] = json.loads(cp.stdout or "[]")
    items = [{"board": b, "task": t, "detail": None} for b, ts in lists.items() for t in ts]
    todo = [it for it in items if needs_detail(it["task"], lane_names, now)]

    def fetch(it):
        try:
            it["detail"] = hermes_for(it["board"]).show(it["task"]["id"])
        except Exception as exc:  # una tarjeta ilegible no tumba el resumen
            log.warning("show %s falló: %s", it["task"]["id"], exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(fetch, todo))
    return items, lists


class ListsHermes:
    """Adaptador para status.lane_rows sobre las listas ya leídas (sin más llamadas a la CLI)."""

    def __init__(self, tasks: list[dict]):
        self.tasks = tasks

    def list_status(self, assignee: str, status: str, sort: str = "priority") -> list[dict]:
        out = [t for t in self.tasks if t.get("assignee") == assignee and t.get("status") == status]
        if sort == "completed-desc":
            out.sort(key=lambda t: -(t.get("completed_at") or 0))
        return out


def lane_status(lists: dict[str, list[dict]]) -> list[dict]:
    from agent_lanes.config import load_lanes
    from agent_lanes.runner import STATE_DIR, pid_alive
    from agent_lanes.status import lane_rows
    return lane_rows(load_lanes(), hermes_for=lambda b: ListsHermes(lists.get(b, [])), state_dir=STATE_DIR,
                     pid_alive=pid_alive)


# --- main -------------------------------------------------------------------------------------------

def build(now: float | None = None, *, prueba: bool = False) -> str:
    from agent_lanes.config import load_env, load_lanes, load_telegram_settings
    from agent_lanes.hermes import HermesCLI
    now = time.time() if now is None else now
    cache: dict[str, HermesCLI] = {}
    lane_names = {n for n, l in load_lanes().items() if l.kind == "implement"}
    items, lists = collect(lambda b: cache.setdefault(b, HermesCLI(b)), lane_names=lane_names, now=now)
    base = load_env().get("KANBAN_BASE_URL") or load_telegram_settings()["kanban_base_url"]
    return render(classify(items, now), lane_status(lists), now, base, prueba=prueba)


def notifiers() -> list:
    """Bot de carriles primero y el de Hermes de reserva (mismo orden que runner.py)."""
    from agent_lanes.config import load_env
    from agent_lanes.telegram import TelegramNotifier
    env = load_env()
    toks = [t for t in (env.get("CARRILES_BOT_TOKEN"), env.get("TELEGRAM_BOT_TOKEN")) if t]
    return [TelegramNotifier(t, GESTION_CHAT) for t in dict.fromkeys(toks)]


def send(bots: list, text: str):
    """(bot, {message_id...}). Un bot rechazado (401/403: token revocado, fuera del grupo) cede al siguiente."""
    from agent_lanes.telegram import TelegramAPIError
    last = None
    for bot in bots:
        try:
            return bot, bot.send_to(GESTION_CHAT, None, text)
        except TelegramAPIError as exc:
            if not re.search(r"HTTP (401|403)", str(exc)):
                raise
            log.warning("bot %s rechazado (%s); pruebo el siguiente", bot.bot_id, exc)
            last = exc
    raise last or RuntimeError("Telegram desactivado: no hay token de bot en agent-lanes/.env")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Resumen diario de Hermes a Gestión · General")
    ap.add_argument("--dry-run", action="store_true", help="imprime el mensaje en vez de enviarlo")
    ap.add_argument("--prueba", action="store_true", help='añade "(prueba)" al título')
    ap.add_argument("--borrar", action="store_true", help="borra el mensaje tras enviarlo (solo con --prueba)")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    from agent_lanes.config import ROOT
    state = ROOT / ".state"
    state.mkdir(exist_ok=True)
    logging.basicConfig(filename=str(state / "brief.log"), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        text = build(prueba=args.prueba)
        if args.dry_run:
            print(text)
            return 0
        tg, sent = send(notifiers(), text)
        log.info("resumen enviado por el bot %s: message_id=%s (%d caracteres)", tg.bot_id,
                 sent and sent.get("message_id"), len(text))
        print(f"enviado por el bot {tg.bot_id}: message_id={sent and sent.get('message_id')}")
        if args.prueba and args.borrar and sent:
            time.sleep(5)
            ok = tg.delete(GESTION_CHAT, sent["message_id"])
            log.info("prueba borrada: %s", ok)
            print(f"borrado={ok}")
        return 0
    except Exception:
        log.exception("resumen diario fallido")
        raise


if __name__ == "__main__":
    sys.exit(main())
