"""Dependencias lógicas entre tareas (enlaces padre -> hijo del kanban de Hermes).

Pregunta de Oscar (28-09): "¿qué pasa si apruebo el #10 sin haber aprobado el #9?". Los gates del Integrador
detectan CONFLICTOS de código (merge de prueba sobre la base), pero no que #10 necesite el código de #9 para tener
sentido. Esa relación solo existe si alguien la declaró con `hermes kanban link <padre> <hijo>`; este módulo la lee.

Un padre bloquea la integración del hijo mientras tenga código sin integrar:
- tiene rama de carril (`branch_name`) y aún no hay comentario `INTEGRADO` del integrador, o
- todavía no está terminado (ready, running, review, blocked...).
Un padre sin rama (tarea manual o de decisión) deja de bloquear en cuanto está done/archived.
"""
from __future__ import annotations

DONE_STATUSES = ("done", "archived")
INTEGRATED_PREFIX = "INTEGRADO"         # = integrator.INTEGRATED_PREFIX
INTEGRATOR_AUTHOR = "lane-integrator"   # = integrator.INTEGRATOR_AUTHOR


def _parent_ids(show: dict) -> list[str]:
    out = []
    for p in show.get("parents") or []:
        pid = p if isinstance(p, str) else (p or {}).get("id")
        if pid:
            out.append(str(pid))
    return out


def _integrated(comments: list[dict]) -> bool:
    return any(is_integration_comment(c) for c in comments or [])


def is_integration_comment(c: dict, *, require_author: bool = True) -> bool:
    """`INTEGRADO <sha> · PR ...` del integrador. Ojo: sus otros comentarios empiezan por "INTEGRADOR:" y un
    startswith("INTEGRADO") a secas los contaba como integración (bug visto el 28-09)."""
    body = c.get("body") or ""
    return body.startswith(INTEGRATED_PREFIX + " ") and (not require_author or c.get("author") == INTEGRATOR_AUTHOR)


def pending_parents(hermes, tid: str, show: dict | None = None) -> list[dict]:
    """Padres de `tid` que aún bloquean su integración: [{id, title, status, reason}]. Fail-closed: un padre que
    no se puede leer cuenta como pendiente (mejor esperar que fusionar sin saber)."""
    show = show if show is not None else (hermes.show(tid) or {})
    out = []
    for pid in _parent_ids(show):
        try:
            ps = hermes.show(pid) or {}
        except Exception:
            ps = {}
        task = ps.get("task") or {}
        if not task:
            out.append({"id": pid, "title": "", "status": "?", "reason": "no se pudo leer"})
            continue
        status = str(task.get("status") or "")
        if _integrated(ps.get("comments")):
            continue
        if status in DONE_STATUSES and not task.get("branch_name"):
            continue
        reason = "sin integrar" if status in DONE_STATUSES else f"en {status or '?'}"
        out.append({"id": pid, "title": task.get("title") or "", "status": status, "reason": reason})
    return out


def waiting_line(waiting: list[dict], limit: int = 3) -> str:
    """'⏸ espera a t_x (en review), t_y (sin integrar)'."""
    parts = [f"{w['id']} ({w['reason']})" for w in waiting[:limit]]
    more = len(waiting) - limit
    return "⏸ espera a " + ", ".join(parts) + (f" y {more} más" if more > 0 else "")


def dependency_order(items: list, tid_of, parents_of) -> list:
    """Orden recomendado: cada tarea después de sus padres presentes en la lista; si no hay relación, se mantiene
    el orden de entrada (antigüedad). `parents_of(item)` -> ids de padres. Tolera ciclos (los deja al final)."""
    ids = [tid_of(i) for i in items]
    present = set(ids)
    pending = {tid_of(i): [p for p in parents_of(i) if p in present] for i in items}
    by_id = {tid_of(i): i for i in items}
    done: list[str] = []
    placed: set[str] = set()
    progress = True
    while progress and len(done) < len(ids):
        progress = False
        for tid in ids:
            if tid not in placed and all(p in placed for p in pending[tid]):
                done.append(tid)
                placed.add(tid)
                progress = True
    done += [t for t in ids if t not in placed]  # ciclo: al final, en su orden
    return [by_id[t] for t in done]
