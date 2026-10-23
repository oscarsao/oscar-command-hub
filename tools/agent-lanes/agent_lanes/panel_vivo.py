"""Mensaje vivo del tema de Integración (22-10): UN solo mensaje fijado que se edita en cada pasada del runner.

Extiende el fijado «🚦 PENDIENTE DE INTEGRAR» (integration.PinnedSummary): delante de esa lista añade
🔧 en marcha (por carril, con fase y minutos), ⏳ siguientes N en cola, ✅ últimas N terminadas y
⚠️ conflictos y bloqueos ACTUALES con botones [🛠 Resolver] [👁 Ver] [⏸ Aparcar]. Solo lo vigente: la línea de un
conflicto desaparece sola al resolverse. Se configura en `live_panel:` de lanes.yaml (activar, nº en cola, nº
terminadas...). Lógica de texto pura; la lectura del kanban y los botones usan el integrador.
"""
from __future__ import annotations

import html
import json
import logging
import os
import time

from .notices import TEXT_MAX, truncate

log = logging.getLogger("agent_lanes")

PV_RESOLVER, PV_VER, PV_APARCAR = "pv_resolver", "pv_ver", "pv_aparcar"
PV_ACTIONS = (PV_RESOLVER, PV_VER, PV_APARCAR)
TITLE_MAX = 40
PARKED_FILE = "panel_parked.json"
PHASES = {"running": "implementando", "review": "review"}


class PanelSettings:
    """Bloque `live_panel:` de lanes.yaml. Sin el bloque (o con enabled: false) el fijado queda como siempre."""

    def __init__(self, enabled: bool = False, queue: int = 3, done: int = 5, done_hours: float = 24.0,
                 conflicts: int = 6):
        self.enabled, self.queue, self.done = bool(enabled), max(0, int(queue)), max(0, int(done))
        self.done_hours, self.conflicts = float(done_hours), max(0, int(conflicts))

    @classmethod
    def from_cfg(cls, cfg) -> "PanelSettings":
        if not isinstance(cfg, dict):
            return cls()
        pick = lambda key, default: default if cfg.get(key) is None else cfg[key]  # noqa: E731  (0 es un valor válido)
        return cls(enabled=pick("enabled", False), queue=pick("queue", 3), done=pick("done", 5),
                   done_hours=pick("done_hours", 24), conflicts=pick("conflicts", 6))


def _minutes(since, now: float) -> str:
    """Minutos en saltos de 5: el texto no cambia (ni se edita el mensaje) en cada pasada."""
    try:
        m = max(0, int((now - float(since)) // 60))
    except (TypeError, ValueError):
        return "?"
    return f"{m // 5 * 5} min" if m < 60 else f"{m // 60} h {m % 60 // 5 * 5:02d} min"


def _task(t: dict) -> str:
    return f"<code>{html.escape(str(t.get('id') or '?'))}</code> · {html.escape(truncate(t.get('title'), TITLE_MAX))}"


def render_sections(*, running, queue, done, conflicts, now: float, cfg: PanelSettings, slots: str = "") -> str:
    """running: [{lane,id,title,status,since}] · queue: [{id,title,priority}] · done: [{id,title}] ·
    conflicts: [{tid,pr,title,text,url?}]. Cada sección vacía lo dice en una línea (no desaparece)."""
    e = html.escape
    out = ["<b>🔧 EN MARCHA</b>"]
    if slots:  # huecos normales/grandes/reservados ocupados (23-10): se ve de un vistazo si hay plaza libre
        out.append(e(slots))
    out += [f"{e(str(t.get('lane') or '?'))}: {_task(t)} · {PHASES.get(t.get('status'), t.get('status') or '?')}"
            f" · {_minutes(t.get('since'), now)}" for t in running] or ["nada en marcha"]
    out.append("\n<b>⏳ SIGUIENTES</b>")
    out += [f"{_task(t)} · /tarea {e(str(t.get('id')))}" for t in queue[:cfg.queue]] or ["cola vacía"]
    out.append("\n<b>✅ TERMINADAS (24 h)</b>")
    out += [f"{_task(t)} · /tarea {e(str(t.get('id')))}" for t in done[:cfg.done]] or ["ninguna"]
    out.append("\n<b>⚠️ CONFLICTOS Y BLOQUEOS</b>")
    shown = conflicts[:cfg.conflicts]
    for c in shown:
        pr = f"#{int(c['pr'])}" if c.get("pr") else ""
        pr = f'<a href="{e(c["url"], quote=True)}">{pr}</a>' if c.get("url") and pr else pr
        out.append(f"{pr} {_task({'id': c['tid'], 'title': c.get('title')})} · {e(truncate(c.get('text'), 70))}".strip())
    if len(conflicts) > len(shown):
        out.append(f"… y {len(conflicts) - len(shown)} más")
    if not conflicts:
        out.append("ninguno")
    return "\n".join(out)


def compose(sections: str, pending: str) -> str:
    """Secciones del panel + lista de pendiente de integrar. Nunca pasa de TEXT_MAX (se recorta lo pendiente)."""
    text = f"{sections}\n\n{pending}"
    return text if len(text) <= TEXT_MAX else text[:TEXT_MAX - 2].rsplit("\n", 1)[0] + "\n…"


class LivePanel:
    """Estado y botones del panel vivo de un integrador (`i`)."""

    def __init__(self, integrator, cfg: PanelSettings):
        self.i, self.cfg = integrator, cfg
        self.conflicts: list[dict] = []  # los de la última pasada: de ellos salen los botones

    # --- aparcados (⏸): persistidos; se olvidan solos cuando el conflicto deja de existir -----------------

    @property
    def _path(self):
        return self.i.state_dir / PARKED_FILE

    def parked(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _store_parked(self, data: dict) -> None:
        self.i.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self._path)

    # --- lectura -------------------------------------------------------------------------------------

    def gather(self) -> dict:
        """En marcha / cola / terminadas desde el kanban (lanza si Hermes falla: el fijado no se toca)."""
        i, now = self.i, self.i._now()
        running, queue, done = [], [], []
        boards: dict[str, None] = {}
        for name, lane in i.lanes.items():
            if getattr(lane, "kind", "") not in ("implement", "ops"):
                continue
            h = i.hermes_for(lane.board)
            boards[lane.board] = None
            for t in h.list_status(name, "running"):
                running.append({"lane": name, "id": t.get("id"), "title": t.get("title"), "status": "running",
                                "since": t.get("started_at")})
            queue += h.list_status(name, "ready")
        for board in boards:
            h = i.hermes_for(board)
            for t in h.list_status_all("review"):
                running.append({"lane": t.get("assignee") or "review", "id": t.get("id"), "title": t.get("title"),
                                "status": "review", "since": t.get("started_at")})
            for t in h.list_status_all("done"):
                at = t.get("completed_at")
                try:
                    if at is not None and now - float(at) <= self.cfg.done_hours * 3600:
                        done.append({"id": t.get("id"), "title": t.get("title"), "at": float(at)})
                except (TypeError, ValueError):
                    continue
        def prio(t):
            try:
                return int(t.get("priority"))
            except (TypeError, ValueError):
                return 0
        queue.sort(key=lambda t: -prio(t))
        done.sort(key=lambda t: -t["at"])
        from . import drain
        from .status import busy_slots, slots_text
        return {"running": running, "queue": queue, "done": done, "slots": slots_text(busy_slots(drain.BUSY_FILE))}

    def conflict_entries(self) -> list[dict]:
        """Conflictos/bloqueos VIGENTES: gates fallidos y problemas de PR del integrador + PR en conflicto vistos
        por el repaso de PR abiertos. Los aparcados no salen; la tarjeta ya resuelta tampoco (no hay estado)."""
        i, found = self.i, {}
        for lane, policy, tid in list(i._seen):
            st = i._state(tid)
            if st.get("status") not in ("failed", "pr_problem") or not st.get("pr"):
                continue
            text = ("conflicto con la base" if st.get("conflict") else
                    "; ".join(st.get("fail_reasons") or ()) or st.get("pr_problem") or "gates fallidos")
            if st.get("status") == "pr_problem" and (st.get("pr_problem") or "").startswith("el PR está "):
                continue  # fusionado/cerrado a mano: ya no es un problema
            found[tid] = {"tid": tid, "lane": lane.name, "pr": st["pr"], "title": st.get("title") or tid,
                          "text": text, "url": st.get("pr_url"), "conflict": bool(st.get("conflict")),
                          "since": st.get("since")}
        for e in getattr(i.pr_watch, "last_entries", ()) or ():
            if e.get("state") == "CONFLICTING" and e.get("tid") and e["tid"] not in found:
                found[e["tid"]] = {"tid": e["tid"], "lane": e.get("lane"), "pr": e["number"], "title": e["title"],
                                   "text": "conflicto con la base", "url": e.get("url"), "conflict": True,
                                   "since": None}
        parked = self.parked()
        alive = {t: v for t, v in parked.items() if t in found}
        if alive != parked:
            try:
                self._store_parked(alive)
            except OSError as exc:
                log.warning("panel vivo: no se pudo limpiar los aparcados: %s", exc)
        rows = [c for t, c in found.items() if t not in alive]
        rows.sort(key=lambda c: (float(c.get("since") or 0), c["pr"]))
        self.conflicts = rows[:self.cfg.conflicts]
        return rows

    # --- botones -------------------------------------------------------------------------------------

    def markup_rows(self, issue) -> list[list[dict]]:
        """Una fila [🛠 Resolver] [👁 Ver] [⏸ Aparcar] por conflicto mostrado (Resolver solo si es de rebase).
        `issue(rec, spec)` = store.issue: devuelve (token, markup)."""
        rows = []
        for c in self.conflicts:
            spec = ([{"text": "🛠 Resolver", "action": PV_RESOLVER}] if c.get("conflict") else []) + [
                {"text": "👁 Ver", "action": PV_VER}, {"text": "⏸ Aparcar", "action": PV_APARCAR}]
            rec = {"task_id": c["tid"], "board": "", "lane": c.get("lane") or "", "kind": "panel",
                   "pr": c["pr"], "url": c.get("url") or "", "title": c.get("title") or ""}
            rows.append(issue(rec, [spec])[1]["inline_keyboard"][0])
        return rows

    def on_button(self, action: str, rec: dict, where: dict | None, desk) -> bool:
        """Nunca fusiona ni cierra nada: Ver informa, Aparcar oculta la línea, Resolver crea/reanuda la tarjeta
        de rebase (misma clave idempotente que la automática). No edita el mensaje fijado con texto suelto."""
        i, tid, number = self.i, str(rec.get("task_id") or ""), rec.get("pr")
        say = lambda text: (desk.notifier.send_to((where or {}).get("chat_id"), (where or {}).get("thread_id"),  # noqa: E731
                                                   text, html=False, silent=True) if where else None)
        if action == PV_VER:
            say(f"👁 {tid} · PR #{number}\n/tarea {tid}" + (f"\n{rec['url']}" if rec.get("url") else ""))
            i.reissue_panel_markup()
            return True
        if action == PV_APARCAR:
            parked = self.parked()
            parked[tid] = time.time()
            try:
                self._store_parked(parked)
            except OSError as exc:
                log.warning("panel vivo: no se pudo aparcar %s: %s", tid, exc)
                return False
            i.refresh_summary()
            say(f"⏸ {tid}: aparcado; vuelve a aparecer si se resuelve y reaparece el conflicto")
            return True
        if action == PV_RESOLVER:
            lane = i.lanes.get(rec.get("lane"))
            ok = bool(lane) and i.rebase_card(lane, tid, int(number or 0))
            say(f"🛠 {tid}: tarjeta de rebase del PR #{number} " + ("creada o ya existente en el carril" if ok
                                                                     else "no creada (mira el log)"))
            i.reissue_panel_markup()
            return ok
        return False
