"""Texto libre de Oscar como respuesta a la decisión pendiente del tema (auditoría t_dddcc232, P1, opción A).

Si Oscar escribe en un chat/tema sin hacer "reply" a un force_reply, y ahí hay alguna decisión pendiente suya (una
pregunta needs_input o una tarjeta de /decisiones), NO se asume que el texto sea la respuesta (t_61a4baa6): el bot
pregunta con botones «¿es tu respuesta a t_xxx o un mensaje nuevo?» (con varias, a cuál). Al elegir tarea, mismo
camino que la respuesta por reply (`DecisionDesk._apply_reply`), con eco "Entendido: «…» para t_xxx" y [↩️ Deshacer]
como red de seguridad. Con ninguna, todo sigue como antes (ayuda en el DM; silencio en grupos).

Nunca aplica a acciones de producción: solo cuentan los avisos que ofrecen ✍️ (OTHER / CARD_ASK); promover, fusionar,
desplegar y aprobar siguen exigiendo su botón (y su doble confirmación). Ventana y tope de botones: `telegram.free_text`
de lanes.yaml; sin esa clave, la función está apagada.

Deshacer: Hermes no borra comentarios. Mientras la tarea siga en `ready` (el worker no la ha recogido) se vuelve a
bloquear como needs_input: un bloqueo posterior a la respuesta la deja fuera de la ronda (ver hermes.answer_progress),
y un comentario ANULADA-OSCAR lo deja escrito. Si ya está en marcha, lo dice y no toca nada. Las tarjetas de
/decisiones se cierran al responder (done): sin botón de deshacer.
"""
from __future__ import annotations

import logging
import time

from .hermes import OSCAR_AUTHOR
from .notices import DECISION_CARD_STATUSES, truncate

log = logging.getLogger("agent_lanes")

FREE_PICK = "free_pick"      # varias pendientes: el botón elige a cuál va el texto
FREE_CANCEL = "free_cancel"  # ✖ Ninguna: el texto no era una respuesta
FREE_UNDO = "free_undo"      # ↩️ Deshacer sobre el eco
FREE_ACTIONS = (FREE_PICK, FREE_CANCEL, FREE_UNDO)
ANNULLED_PREFIX = "ANULADA-OSCAR:"
PICK_TITLE_MAX = 22


def scope_of(msg: dict) -> tuple[str, str]:
    """(chat, hilo) de un mensaje; hilo "0" fuera de un tema."""
    thread = str(msg.get("message_thread_id") or 0) if msg.get("is_topic_message") else "0"
    return str((msg.get("chat") or {}).get("id")), thread


class FreeText:
    def __init__(self, desk, *, window_hours: float, max_choices: int = 5):
        self.desk = desk
        self.window = float(window_hours) * 3600
        self.max_choices = max(2, int(max_choices))

    # --- candidatas -----------------------------------------------------------------------------------

    def candidates(self, chat_id: str, thread_id: str) -> list[dict]:
        """Avisos con ✍️ vivos y recientes cuya copia está en ESTE chat/tema (sin llamar a Hermes). Uno por tarea
        (el teclado más reciente), los más nuevos primero."""
        d = self.desk
        if d.messages is None:
            return []
        cutoff = time.time() - self.window
        best: dict[str, dict] = {}
        for token, rec in d.store.live():
            actions = {b.get("action") for b in rec.get("buttons") or ()}
            if not actions & set(d_actions()):
                continue
            if any(str(a).startswith(("int_", "promo_", "deploy")) for a in actions):
                continue
            if rec.get("group") or rec.get("accept_all") or any(rec.get(f) for f in FREE_ACTIONS):
                continue
            if float(rec.get("created") or 0) < cutoff or not rec.get("task_id"):
                continue
            where = next(({"chat_id": str(m["chat_id"]), "thread_id": str(m.get("thread_id") or 0),
                           "message_id": m["message_id"]}
                          for m in d.messages.all_messages(rec["task_id"])
                          if m.get("token") == token and str(m["chat_id"]) == chat_id
                          and str(m.get("thread_id") or 0) == thread_id), None)
            if not where:
                continue
            cur = best.get(rec["task_id"])
            if not cur or rec["created"] > cur["rec"]["created"]:
                best[rec["task_id"]] = {"token": token, "rec": rec, "where": where}
        return sorted(best.values(), key=lambda c: -float(c["rec"].get("created") or 0))

    def _still_pending(self, c: dict) -> bool:
        d, rec = self.desk, c["rec"]
        if not rec.get("card"):
            return d._pending_show(rec) is not None
        h = d._hermes(rec)
        if not hasattr(h, "show"):
            return True
        try:
            status = (h.show(rec["task_id"]).get("task") or {}).get("status")
        except Exception:
            return False
        return status in DECISION_CARD_STATUSES

    # --- entrada --------------------------------------------------------------------------------------

    def handle(self, msg: dict) -> bool:
        """True si hay al menos una decisión pendiente aquí y el texto se tratará como su respuesta (en un hilo
        aparte: consulta el kanban). False = nada pendiente, que siga el flujo de siempre."""
        chat_id, thread_id = scope_of(msg)
        sender = (msg.get("from") or {}).get("id")  # operador que no es Oscar: sus respuestas se firman con su nombre
        puede = getattr(self.desk, "puede_con", None)  # operador acotado por marca: solo decisiones de las suyas
        found = [c for c in self.candidates(chat_id, thread_id) if not puede or puede(sender, c["rec"])]
        if not found:
            return False
        run = lambda: self._run(msg, found)  # noqa: E731
        wrap = getattr(self.desk, "_en_nombre_de", None)
        self.desk._spawn(wrap(sender, run) if wrap else run)
        return True

    def _run(self, msg: dict, found: list[dict]) -> None:
        live = [c for c in found if self._still_pending(c)]
        text = (msg.get("text") or "").strip()
        if not live:
            return
        # Escrito como respuesta (reply) a uno de los avisos: es la respuesta de ESE aviso, sin preguntar.
        replied = (msg.get("reply_to_message") or {}).get("message_id")
        exact = [c for c in live if replied is not None and str(c["where"]["message_id"]) == str(replied)]
        if len(exact) == 1:
            self._apply(exact[0], text, msg.get("message_id"))
            return
        # Texto suelto: nunca se asume que es una respuesta; con una o varias pendientes se pregunta con botones.
        # Deshacer queda como red de seguridad.
        self._ask_which(live[:self.max_choices], text, msg)

    # --- aplicar --------------------------------------------------------------------------------------

    def _apply(self, c: dict, text: str, reply_to, *, edit_where: dict | None = None) -> bool:
        """Anota `text` como la respuesta de la decisión `c`. `edit_where`: mensaje donde mostrar el eco (el selector)."""
        d, rec, token, where = self.desk, c["rec"], c["token"], c["where"]
        if not d.store.consume(token):  # ya se decidió con un botón
            return False
        button = {"action": d_actions()[1] if rec.get("card") else d_actions()[0], "text": ""}
        done = {"ok": False}

        def act() -> bool:
            done["ok"] = bool(d._apply_reply(rec, button, where, text))
            return done["ok"]

        d._safe(token, rec, where, act, after=lambda: self._echo(rec, token, where, text, reply_to, edit_where)
                if done["ok"] else None)
        return done["ok"]

    def _echo(self, rec: dict, token: str, where: dict, text: str, reply_to, edit_where: dict | None) -> None:
        d, tid = self.desk, rec["task_id"]
        body = f"Entendido: «{truncate(text, 120)}» para {tid}"
        markup = None
        if rec.get("card"):
            body += " (tarjeta cerrada; si te equivocaste, reábrela desde el panel)"
        else:
            urec = {"task_id": tid, "board": rec.get("board") or "", "lane": rec.get("lane") or "", "free_undo": True,
                    "notice_token": token, "text": truncate(text, 200)}
            markup = d.store.issue(urec, [[{"text": "↩️ Deshacer", "action": FREE_UNDO}]])[1]
        try:
            if edit_where:
                d.notifier.edit(edit_where["chat_id"], edit_where["message_id"], body, html=False, reply_markup=markup)
            else:
                d.notifier.send_to(where["chat_id"], where["thread_id"], body, html=False, reply_to=reply_to,
                                   **({"reply_markup": markup} if markup else {}))
        except Exception as exc:
            log.warning("%s: no se pudo enviar el eco de la respuesta libre: %s", tid, exc)

    # --- varias pendientes ----------------------------------------------------------------------------

    def _ask_which(self, live: list[dict], text: str, msg: dict) -> None:
        d = self.desk
        first = live[0]["where"]
        rows = [[{"text": f"{c['rec']['task_id']} · {truncate(c['rec'].get('title') or '', PICK_TITLE_MAX)}",
                  "action": FREE_PICK, "index": i}] for i, c in enumerate(live)]
        rows.append([{"text": "✖ Ninguna" if len(live) > 1 else "✖ Es un mensaje nuevo", "action": FREE_CANCEL}])
        rec = {"task_id": "texto-libre", "board": "", "lane": "", "free_pick": True, "text": text[:3000],
               "reply_to": msg.get("message_id"),
               "candidates": [{"token": c["token"], "task_id": c["rec"]["task_id"], "where": c["where"],
                               "board": c["rec"].get("board") or "", "lane": c["rec"].get("lane") or ""} for c in live]}
        markup = d.store.issue(rec, rows)[1]
        if len(live) > 1:
            body = f"Tienes {len(live)} decisiones pendientes aquí. ¿Para cuál es «{truncate(text, 120)}»?"
        else:
            body = (f"Tienes pendiente {live[0]['rec']['task_id']}. ¿«{truncate(text, 120)}» es tu respuesta, "
                    f"o es un mensaje nuevo?")
        try:
            d.notifier.send_to(first["chat_id"], first["thread_id"], body, html=False,
                               reply_to=msg.get("message_id"), reply_markup=markup)
        except Exception as exc:
            log.warning("no se pudo preguntar a qué decisión va el texto libre: %s", exc)

    # --- botones --------------------------------------------------------------------------------------

    def on_button(self, action: str, rec: dict, button: dict, where: dict) -> bool:
        d = self.desk
        if action == FREE_CANCEL:
            d._edit_text(where, "Vale, no lo tomo como respuesta a ninguna decisión.")
            return True
        if action == FREE_PICK:
            idx = button.get("index")
            cand = (rec.get("candidates") or [])[idx] if isinstance(idx, int) and 0 <= idx < len(rec.get("candidates") or []) else None
            target = d.store.get(cand["token"]) if cand else None
            if not target:
                d._edit_text(where, "Esa decisión ya se resolvió; no apunto nada.")
                return True
            c = {"token": cand["token"], "rec": target, "where": cand["where"]}
            if not self._apply(c, rec.get("text") or "", rec.get("reply_to"), edit_where=where):
                d._edit_text(where, f"No pude anotar la respuesta en {cand['task_id']}; escríbela de nuevo.")
            return True
        return self._undo(rec, where)

    def _undo(self, rec: dict, where: dict) -> bool:
        d, tid = self.desk, rec["task_id"]
        h = d._hermes(rec)
        try:
            show = h.show(tid)
        except Exception as exc:
            log.warning("%s: no pude leer la tarjeta para deshacer: %s", tid, exc)
            return False  # los botones vuelven: se puede reintentar
        status = (show.get("task") or {}).get("status")
        if status != "ready":
            d._edit_text(where, f"{tid}: ya no se puede deshacer, el worker la ha recogido ({status}). "
                                f"Si hay que cambiar algo, dímelo en la tarjeta.")
            return True
        block = last_block(show)
        reason = ((block or {}).get("payload") or {}).get("reason") or "Respuesta deshecha: vuelve a esperar a Oscar"
        if not h.block(tid, "needs_input", str(reason)[:1000]):
            return False
        h.comment(tid, f"{ANNULLED_PREFIX} Oscar deshizo su respuesta «{rec.get('text') or ''}» desde Telegram"[:3000],
                  author=OSCAR_AUTHOR)
        # El aviso original vuelve a tener botones: se repone su teclado y se redibuja a "esperando".
        for tok in {*d._tokens({"task_id": tid}), rec.get("notice_token")} - {None}:
            d.store.restore(tok)
        notice = d.store.get(rec.get("notice_token") or "")
        if notice:
            try:
                d._edit(notice, None, "needs_input", "↩️ respuesta deshecha · vuelve a esperar tu decisión",
                        markup=d.store.markup(rec["notice_token"], notice))
            except Exception as exc:
                log.info("%s: no se pudo redibujar el aviso tras deshacer: %s", tid, exc)
        d._edit_text(where, f"↩️ Deshecho: {tid} vuelve a esperar tu decisión.")
        return True


def d_actions() -> tuple[str, str]:
    """(OTHER, CARD_ASK): import diferido, decisions importa este módulo."""
    from .decisions import CARD_ASK, OTHER
    return OTHER, CARD_ASK


def last_block(show: dict) -> dict | None:
    return next((ev for ev in reversed(show.get("events") or []) if ev.get("kind") == "blocked"), None)
