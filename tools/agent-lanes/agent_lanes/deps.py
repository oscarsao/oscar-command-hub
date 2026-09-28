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

import logging
import threading
import time
from typing import Callable

from .notices import truncate

log = logging.getLogger("agent_lanes")

DONE_STATUSES = ("done", "archived")
INTEGRATED_PREFIX = "INTEGRADO"         # = integrator.INTEGRATED_PREFIX
INTEGRATOR_AUTHOR = "lane-integrator"   # = integrator.INTEGRATOR_AUTHOR


def _link_ids(show: dict, key: str) -> list[str]:
    """`show --json` de hermes 0.21.4 trae `parents`/`children` como listas de ids (kanban.py _cmd_show); se toleran
    también objetos {id, ...}."""
    out = []
    for p in (show or {}).get(key) or []:
        pid = p if isinstance(p, str) else (p or {}).get("id")
        if pid and str(pid) not in out:
            out.append(str(pid))
    return out


def _parent_ids(show: dict) -> list[str]:
    return _link_ids(show, "parents")


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


# --- árbol padre/hijas en los avisos y en /tarea, /tareas -----------------------------------------------------------
# Los enlaces padre -> hija solo se veían en el kanban (28-09). Un aviso añade como mucho tres líneas:
#   🔗 Parte de: t_x · título corto      padres sin código pendiente (épica, spec ya integrada...)
#   ⏸ Depende de: t_y (en review)        padres que aún bloquean la integración (pending_parents)
#   ↳ 3 subtareas: ✅ 1 · ▶️ 1 · ⏳ 1      hijas por estado
# Un padre sale en UNA sola línea. Estados de las hijas: ✅ done/archived · ▶️ running/review/blocked (alguien está con
# ella, aunque sea esperando) · ⏳ el resto (todo, ready, triage...).

CHILD_BUCKETS = (("✅", DONE_STATUSES), ("▶️", ("running", "review", "blocked")))
PENDING_BUCKET = "⏳"
TREE_TITLE_MAX = 40
TREE_LIST_MAX = 10      # padres/hermanas/hijas listados en /tarea (el resto, "+N más")
CHILDREN_READ_MAX = 30  # hijas cuyo estado se lee para el recuento de un aviso


def child_bucket(status: str | None) -> str:
    return next((emoji for emoji, statuses in CHILD_BUCKETS if status in statuses), PENDING_BUCKET)


class ShowCache:
    """`show --json` cacheados por pasada y como mucho `ttl` segundos (un aviso "done" al final de un trabajo de 2 h
    no debe pintar estados de hace 2 h). Un aviso con árbol lee la tarea, sus padres y sus hijas: sin caché, cada
    aviso multiplicaría las llamadas a la CLI de Hermes. Seguro entre hilos; los errores no se cachean."""

    def __init__(self, ttl: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self._clock = clock
        self._data: dict[tuple, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(hermes, tid: str) -> tuple:
        return (getattr(hermes, "board", None) or id(hermes), tid)

    def show(self, hermes, tid: str) -> dict:
        key, now = self._key(hermes, tid), self._clock()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        value = hermes.show(tid) or {}
        with self._lock:
            self._data[key] = (now, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class _Reader:
    """Adaptador `show(tid)` (lo que espera pending_parents) sobre la caché, o directo si no hay caché."""

    def __init__(self, hermes, cache: ShowCache | None):
        self.hermes, self.cache = hermes, cache

    def show(self, tid: str) -> dict:
        return self.cache.show(self.hermes, tid) if self.cache else (self.hermes.show(tid) or {})


def brief(reader, tid: str) -> dict:
    """{id, title, status} de una tarea; ilegible -> título vacío y estado "?" (nunca lanza)."""
    try:
        task = (reader.show(tid) or {}).get("task") or {}
    except Exception:
        task = {}
    return {"id": tid, "title": task.get("title") or "", "status": str(task.get("status") or "?")}


def _more(total: int, shown: int) -> str:
    return f" y {total - shown} más" if total > shown else ""


def tree_lines(hermes, tid: str, show: dict | None = None, *, cache: ShowCache | None = None,
               with_waiting: bool = True) -> list[str]:
    """Líneas 🔗/⏸/↳ de un aviso, en texto plano (render las escapa). [] si la tarea no tiene enlaces o si algo
    falla: un aviso nunca se pierde por el árbol. `with_waiting=False`: la ficha de Integración ya lista los padres
    pendientes en "Depende de" (no se repiten, ni en 🔗)."""
    try:
        reader = _Reader(hermes, cache)
        show = show if show is not None else reader.show(tid)
        pids, cids = _link_ids(show, "parents"), _link_ids(show, "children")
        lines: list[str] = []
        if pids:
            waiting = pending_parents(reader, tid, show)
            wait_ids = {w["id"] for w in waiting}
            context = [p for p in pids if p not in wait_ids]
            if context:
                parts = []
                for b in (brief(reader, p) for p in context[:2]):
                    parts.append(f"{b['id']} · {truncate(b['title'], TREE_TITLE_MAX)}" if b["title"] else b["id"])
                lines.append("🔗 Parte de: " + ", ".join(parts) + _more(len(context), 2))
            if waiting and with_waiting:
                lines.append("⏸ Depende de: " + ", ".join(f"{w['id']} ({w['reason']})" for w in waiting[:3])
                             + _more(len(waiting), 3))
        if cids:
            counts: dict[str, int] = {}
            for c in cids[:CHILDREN_READ_MAX]:
                emoji = child_bucket(brief(reader, c)["status"])
                counts[emoji] = counts.get(emoji, 0) + 1
            order = [emoji for emoji, _ in CHILD_BUCKETS] + [PENDING_BUCKET]
            parts = [f"{emoji} {counts[emoji]}" for emoji in order if counts.get(emoji)]
            lines.append(f"↳ {len(cids)} subtarea{'s' if len(cids) != 1 else ''}: " + " · ".join(parts))
        return lines
    except Exception as exc:
        log.info("%s: árbol de dependencias no leído: %s", tid, exc)
        return []


def family(hermes, tid: str, show: dict | None = None, *, cache: ShowCache | None = None) -> dict:
    """Árbol de /tarea: {parents, siblings, children} como listas de {id, title, status} (hermanas = otras hijas de
    los padres), cada una con como mucho TREE_LIST_MAX y su total en `n_<clave>`. Nunca lanza."""
    reader = _Reader(hermes, cache)
    out: dict = {"parents": [], "siblings": [], "children": [], "n_parents": 0, "n_siblings": 0, "n_children": 0}
    try:
        show = show if show is not None else reader.show(tid)
        pids, cids = _link_ids(show, "parents"), _link_ids(show, "children")
        sibs: list[str] = []
        for p in pids:
            try:
                ps = reader.show(p)
            except Exception:
                continue
            sibs += [s for s in _link_ids(ps, "children") if s != tid and s not in sibs]
        for key, ids in (("parents", pids), ("siblings", sibs), ("children", cids)):
            out[f"n_{key}"] = len(ids)
            out[key] = [brief(reader, i) for i in ids[:TREE_LIST_MAX]]
    except Exception as exc:
        log.info("%s: familia no leída: %s", tid, exc)
    return out


class TreeReader:
    """`tree(board, tid)` para los avisos de runner/review/decisiones/integrador, con una caché compartida que el
    bucle principal vacía en cada pasada."""

    def __init__(self, hermes_for: Callable[[str], object], cache: ShowCache | None = None):
        self.hermes_for = hermes_for
        self.cache = cache if cache is not None else ShowCache()

    def __call__(self, board: str | None, tid: str, show: dict | None = None, *, with_waiting: bool = True) -> list[str]:
        if not board or not tid:
            return []
        try:
            hermes = self.hermes_for(board)
        except Exception:
            return []
        return tree_lines(hermes, tid, show, cache=self.cache, with_waiting=with_waiting)
