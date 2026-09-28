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

Autocuración (28-09, t_6a2fdf4d): una tarea bloqueada needs_input con TODAS sus preguntas respondidas en la tarjeta
(`Respuesta de Oscar: <pregunta> → …` posteriores al bloqueo, hermes.answer_progress) que sigue parada se devuelve al
carril en esta misma vuelta: `unblock` o, si Hermes la pasó a triage por bloqueo repetido, `requeue_triage`. En triage
solo si su último bloqueo fue needs_input y está respondido entero: nunca se anula la protección anti-bucle de Hermes
con respuestas a medias.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from .hermes import answer_progress, hermes_answer_text, last_needs_input_block, pending_hermes_answer, resume
from .notices import truncate

log = logging.getLogger("agent_lanes")

ANSWERED_VIA_HERMES = "✅ respondido vía Hermes"
HEALED = "💬 respondida"  # + " (N/N)": todas sus preguntas contestadas en la tarjeta


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
            try:
                stuck = h.list_status(name, "triage") if hasattr(h, "list_status") else []
            except Exception as exc:
                log.info("respuestas vía Hermes: triage de %s no leído: %s", name, exc)
                stuck = []
            for t in [*blocked, *stuck]:
                try:
                    show = h.show(t["id"])
                except Exception as exc:
                    log.info("respuestas vía Hermes: %s no leída: %s", t.get("id"), exc)
                    continue
                comment = pending_hermes_answer(show)
                if comment:
                    if self.apply(lane, h, show, comment):
                        done.append(t["id"])
                elif self.heal(lane, h, show):
                    done.append(t["id"])
        return done

    def heal(self, lane, h, show: dict) -> bool:
        """Autocuración: blocked/triage con su último bloqueo needs_input y TODAS las preguntas respondidas después."""
        task = show.get("task") or {}
        if task.get("status") not in ("blocked", "triage") or last_needs_input_block(show) is None:
            return False
        from .renotify import block_questions  # perezoso: renotify arrastra runner/review
        questions = block_questions(show)
        answers, nxt = answer_progress(show, questions)
        if not questions or nxt is not None:
            return False
        tid, n = task["id"], len(questions)
        log.info("%s: autocuración: %d/%d preguntas respondidas en la tarjeta y seguía en %s; se devuelve al carril",
                 tid, n, n, task.get("status"))
        if self.desk:
            rec = {"task_id": tid, "board": lane.board, "lane": lane.name, "title": task.get("title") or "",
                   "body": (task.get("body") or "")[:4000]}
            last = answers.get(n - 1) or ""
            try:
                self.desk.answered_elsewhere(rec, f"{HEALED} ({n}/{n})" + (f": {truncate(last, 100)}" if last else ""))
            except Exception as exc:
                log.warning("%s: no se pudieron actualizar los avisos tras la autocuración: %s", tid, exc)
        status = resume(h, tid)
        if not status:
            log.warning("%s: autocuración: unblock y requeue_triage fallaron; se reintenta en la próxima vuelta", tid)
            return False
        log.info("%s: autocuración hecha (queda en %s)", tid, status)
        return True

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
        if not resume(h, tid):
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
