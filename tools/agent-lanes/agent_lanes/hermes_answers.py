"""Respuestas de Oscar que llegan por Hermes (clarify en su DM) y no por los botones del bot de Trabajos.

Visto el 28-09: Oscar respondía a Hermes, Hermes lo apuntaba en la tarjeta y el runner no lo reconocía; la tarea
seguía bloqueada y volvía a salir en /decisiones. Ahora Hermes escribe en la tarjeta, con su perfil `default`:

    RESPUESTA-OSCAR: 1) Sí, adelante 2) 19 €

Este vigilante corre en su propio hilo (el bucle principal puede estar esperando 2 h a un worker) y, cada
`interval` segundos, busca en las tareas bloqueadas de los carriles un comentario válido posterior al último bloqueo
needs_input (hermes.pending_hermes_answer). Al encontrarlo hace lo mismo que un botón:
  1. retira los teclados del aviso y edita todas sus copias (tema, DM, bandeja) a "✅ respondido vía Hermes";
  2. `unblock`: la tarea vuelve a ready y el worker recibe la respuesta (hermes.oscar_answers).
Si el unblock falla, la siguiente vuelta lo reintenta (el comentario sigue siendo posterior al bloqueo).
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from .hermes import hermes_answer_text, pending_hermes_answer
from .notices import truncate

log = logging.getLogger("agent_lanes")

ANSWERED_VIA_HERMES = "✅ respondido vía Hermes"


class HermesAnswers:
    def __init__(self, lanes: dict, *, hermes_for: Callable[[str], object], desk=None, interval: float = 60):
        """`desk`: DecisionDesk (bot de carriles) para retirar botones y editar copias; sin él solo se desbloquea."""
        self.lanes = {n: l for n, l in lanes.items() if getattr(l, "kind", "implement") in ("implement", "ops")}
        self.hermes_for = hermes_for
        self.desk = desk
        self.interval = interval
        self._stop = threading.Event()

    def tick(self) -> list[str]:
        """Una vuelta por los carriles. Devuelve las tareas desbloqueadas."""
        done = []
        for name, lane in self.lanes.items():
            try:
                h = self.hermes_for(lane.board)
                blocked = h.list_status(name, "blocked")
            except Exception as exc:  # un tablero ilegible no para los demás
                log.warning("respuestas vía Hermes: %s no leído: %s", name, exc)
                continue
            for t in blocked:
                try:
                    show = h.show(t["id"])
                except Exception as exc:
                    log.info("respuestas vía Hermes: %s no leída: %s", t.get("id"), exc)
                    continue
                comment = pending_hermes_answer(show)
                if comment and self.apply(lane, h, show, comment):
                    done.append(t["id"])
        return done

    def apply(self, lane, h, show: dict, comment: dict) -> bool:
        task = show.get("task") or {}
        tid = task["id"]
        text = hermes_answer_text(comment) or ""
        if self.desk:
            rec = {"task_id": tid, "board": lane.board, "lane": lane.name, "title": task.get("title") or "",
                   "body": (task.get("body") or "")[:4000]}
            try:  # los avisos nunca impiden el desbloqueo
                self.desk.answered_elsewhere(rec, f"{ANSWERED_VIA_HERMES}: {truncate(text, 120)}")
            except Exception as exc:
                log.warning("%s: no se pudieron actualizar los avisos tras la respuesta vía Hermes: %s", tid, exc)
        if not h.unblock(tid):
            log.warning("%s: respuesta vía Hermes detectada pero el unblock falló; se reintenta", tid)
            return False
        log.info("%s: respondida vía Hermes (%s); desbloqueada", tid, comment.get("author"))
        return True

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # nunca tumba el runner
                log.warning("respuestas vía Hermes: %s", exc)
            self._stop.wait(self.interval)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="lane-hermes-answers", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
