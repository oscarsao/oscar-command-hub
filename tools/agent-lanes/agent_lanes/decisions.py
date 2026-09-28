"""Decisiones de Oscar con un toque desde Telegram: botones inline en los avisos y escucha de pulsaciones.

Solo con CARRILES_BOT_TOKEN (bot propio de los carriles). Con el bot de Hermes no hay botones ni escucha: Hermes
ya consume getUpdates de su bot y dos consumidores del mismo bot se pisan (409 Conflict).

Flujo de una pulsación:
  1. El hilo de escucha recibe el callback_query y responde answerCallbackQuery al momento (<3 s).
  2. La acción (hermes / gh) corre en un hilo aparte, nunca en el pool de workers (puede estar ocupado 2 h).
  3. El aviso se edita con lo que pasó de verdad y sin botones; si la acción falla, vuelven los botones.

callback_data = "<token>:<n>" (≤64 bytes). El token apunta a .state/callbacks/<token>.json con la tarea, el
tablero, el carril y los botones; un token se consume una sola vez (doble toque = una sola acción).
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import proc as _proc
from .hermes import ANSWER_PREFIX, OSCAR_AUTHOR, REVIEW_AUTHOR
from .notices import card_url, normalize_questions, render, truncate
from .review import CHANGES_PREFIX

log = logging.getLogger("agent_lanes")

OWNER_TELEGRAM_ID = "6744452215"  # Oscar; configurable con OWNER_TELEGRAM_ID en .env
NOT_OWNER = "Solo Oscar puede decidir"
GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"
CALLBACK_DATA_MAX = 64  # bytes, límite de Telegram
PARK_PROFILE = "oscar"
PR_FOOTER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"

APPROVE, CHANGES, PARK, RETRY, OPTION, OTHER = "approve", "changes", "park", "retry", "option", "other"
REPLY_ACTIONS = (CHANGES, OTHER)  # piden texto a Oscar con force_reply


def keyboard_spec(state: str, *, block_kind: str | None = None, questions=None) -> list[list[dict]] | None:
    """Botones por estado: filas de {text, action, index}. None = aviso sin botones (en curso, en review...)."""
    park = {"text": "🗄 Aparcar", "action": PARK}
    if state == "done":
        return [[{"text": "✅ Aprobar", "action": APPROVE}, {"text": "🔁 Pedir cambios", "action": CHANGES}, park]]
    if state == "needs_input":
        rows = []
        qs = normalize_questions(questions)
        if qs and qs[0]["options"]:  # botones solo para la primera pregunta; el resto con ✍️
            for i, opt in enumerate(qs[0]["options"]):
                star = "⭐ " if i == qs[0]["recommended"] else ""
                rows.append([{"text": f"{star}{i + 1}) {opt}", "action": OPTION, "index": i}])
        rows.append([{"text": "✍️ Otra respuesta", "action": OTHER}, park])
        return rows
    if state == "blocked" and block_kind == "transient":
        return [[{"text": "🔄 Reintentar", "action": RETRY}, park]]
    return None


class CallbackStore:
    """.state/callbacks/<token>.json (un teclado) y reply-<chat>_<msg>.json (force_reply pendiente)."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _write(self, path: Path, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    @staticmethod
    def _read(path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def issue(self, rec: dict, spec: list[list[dict]]) -> tuple[str, dict]:
        """Guarda el registro y devuelve (token, reply_markup)."""
        token = secrets.token_urlsafe(9)  # 12 caracteres
        buttons, rows = [], []
        for row in spec:
            out = []
            for b in row:
                data = f"{token}:{len(buttons)}"
                assert len(data.encode()) <= CALLBACK_DATA_MAX
                buttons.append({"action": b["action"], "index": b.get("index"), "text": b["text"]})
                out.append({"text": b["text"], "callback_data": data})
            rows.append(out)
        self._write(self.root / f"{token}.json", {**rec, "buttons": buttons, "rows": [len(r) for r in rows],
                                                  "created": time.time()})
        self.prune()
        return token, {"inline_keyboard": rows}

    def prune(self, max_age_days: float = 30) -> None:
        """Borra teclados y respuestas pendientes viejos (Telegram no deja editar mensajes de >48 h igualmente)."""
        cutoff = time.time() - max_age_days * 86400
        for p in self.root.glob("*.*"):
            try:
                if p.suffix in (".json", ".used") and p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass

    def get(self, token: str) -> dict | None:
        if not re.fullmatch(r"[\w-]{6,32}", token or ""):
            return None
        return self._read(self.root / f"{token}.json")

    def consume(self, token: str) -> bool:
        """Uso único: el primero que renombra gana; un segundo toque (o el otro botón) recibe False."""
        try:
            os.replace(self.root / f"{token}.json", self.root / f"{token}.used")
            return True
        except OSError:
            return False

    def restore(self, token: str) -> None:
        try:
            os.replace(self.root / f"{token}.used", self.root / f"{token}.json")
        except OSError:
            pass

    def markup(self, token: str, rec: dict) -> dict:
        """Teclado original de un registro (para devolver los botones si la acción falla)."""
        buttons, rows, i = rec.get("buttons") or [], [], 0
        for n in rec.get("rows") or [len(buttons)]:
            rows.append([{"text": b["text"], "callback_data": f"{token}:{i + k}"} for k, b in enumerate(buttons[i:i + n])])
            i += n
        return {"inline_keyboard": rows}

    def _reply_path(self, chat_id, message_id) -> Path:
        return self.root / f"reply-{str(chat_id).lstrip('-')}_{int(message_id)}.json"

    def put_reply(self, chat_id, message_id, data: dict) -> None:
        self._write(self._reply_path(chat_id, message_id), data)

    def pop_reply(self, chat_id, message_id) -> dict | None:
        path = self._reply_path(chat_id, message_id)
        taken = path.with_suffix(".taken")
        try:
            os.replace(path, taken)
        except OSError:
            return None
        data = self._read(taken)
        taken.unlink(missing_ok=True)
        return data


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="lane-decision", daemon=True).start()


class DecisionDesk:
    """Construye los teclados de los avisos y ejecuta lo que Oscar pulsa."""

    def __init__(self, notifier, store: CallbackStore, *, lanes: dict, hermes_for: Callable[[str], object],
                 links=None, owner_id: str = OWNER_TELEGRAM_ID, runner=_proc.run, gh_exe: str = GH_EXE,
                 spawn: Callable[[Callable[[], None]], None] = _spawn, now: Callable[[], datetime] = datetime.now):
        self.notifier = notifier
        self.store = store
        self.lanes = lanes
        self.hermes_for = hermes_for
        self.links = links
        self.owner_id = str(owner_id)
        self._run = runner
        self.gh_exe = gh_exe
        self._spawn = spawn
        self._now = now
        self.integrator = None  # carril Integrador (INTEGRATOR_ENABLED): botones int_* de fusionar/desplegar

    # --- teclados -------------------------------------------------------------------------------------

    def markup(self, state: str, *, task: dict, lane, block_kind: str | None = None, questions=None,
               summary: str | None = None, changed_files=None) -> dict | None:
        spec = keyboard_spec(state, block_kind=block_kind, questions=questions)
        if not spec:
            return None
        qs = normalize_questions(questions)
        rec = {"task_id": task["id"], "board": lane.board, "lane": lane.name, "title": task.get("title") or "",
               "body": (task.get("body") or "")[:4000], "question": qs[0] if qs else None,
               "summary": (summary or "")[:1500], "changed_files": list(changed_files or [])[:50],
               "n_questions": len(qs)}
        # ✍️ con varias preguntas y sin opción elegida: el texto libre responde a todas.
        if len(qs) > 1:
            rec["other_label"] = "todas las preguntas"
        return self.store.issue(rec, spec)[1]

    # --- entrada: updates de getUpdates ---------------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        if update.get("callback_query"):
            self._callback(update["callback_query"])
        elif update.get("message"):
            self._reply(update["message"])

    @staticmethod
    def _where(msg: dict) -> dict:
        return {"chat_id": str((msg.get("chat") or {}).get("id")), "thread_id": str(msg.get("message_thread_id") or 0),
                "message_id": msg.get("message_id")}

    def _callback(self, cq: dict) -> None:
        answer = self.notifier.answer_callback
        if str((cq.get("from") or {}).get("id")) != self.owner_id:
            answer(cq.get("id"), NOT_OWNER, alert=True)
            return
        token, _, n = (cq.get("data") or "").partition(":")
        rec = self.store.get(token)
        buttons = (rec or {}).get("buttons") or []
        if not rec or not n.isdigit() or int(n) >= len(buttons):
            answer(cq.get("id"), "Esta decisión ya no está activa")
            return
        button = buttons[int(n)]
        where = self._where(cq.get("message") or {})
        if button["action"] in REPLY_ACTIONS:
            answer(cq.get("id"), "Responde al mensaje que te envío")
            self._spawn(lambda: self._ask(token, rec, button, where))
            return
        if not self.store.consume(token):
            answer(cq.get("id"), "Ya está en marcha")
            return
        answer(cq.get("id"), "Hecho, lo aplico")
        self._spawn(lambda: self._safe(token, rec, where, lambda: self._act(rec, button, where)))

    def _reply(self, msg: dict) -> None:
        """Respuesta de Oscar a un force_reply: pedir cambios o respuesta libre."""
        if str((msg.get("from") or {}).get("id")) != self.owner_id:
            return
        replied = msg.get("reply_to_message") or {}
        text = (msg.get("text") or "").strip()
        if not replied or not text:
            return
        chat_id = (msg.get("chat") or {}).get("id")
        pending = self.store.pop_reply(chat_id, replied.get("message_id"))
        if not pending:
            return
        token, rec, button, where = pending["token"], pending["rec"], pending["button"], pending["where"]
        if not self.store.consume(token):  # ya se decidió con otro botón
            self._cleanup_messages(chat_id, [replied.get("message_id"), msg.get("message_id")])
            return
        self._spawn(lambda: self._safe(token, rec, where, lambda: self._apply_reply(rec, button, where, text),
                                       after=lambda: self._cleanup_messages(
                                           chat_id, [replied.get("message_id"), msg.get("message_id")])))

    # --- acciones -------------------------------------------------------------------------------------

    def _safe(self, token: str, rec: dict, where: dict, fn: Callable[[], bool], after=None) -> None:
        """Ejecuta la acción; si falla, devuelve los botones para que Oscar pueda repetir."""
        try:
            ok = fn()
        except Exception as exc:
            log.warning("%s: la decisión de Oscar falló: %s", rec.get("task_id"), exc)
            self._edit(rec, where, "blocked", "no se pudo aplicar la decisión; detalle en el log")
            ok = False
        if not ok:
            self.store.restore(token)
            try:
                self._edit(rec, where, None, None, markup=self.store.markup(token, rec), keep_text=True)
            except Exception as exc:
                log.warning("%s: no se pudieron devolver los botones: %s", rec.get("task_id"), exc)
        if after:
            try:
                after()
            except Exception:
                pass

    def _hermes(self, rec: dict):
        return self.hermes_for(rec["board"])

    def _lane(self, rec: dict):
        return self.lanes.get(rec["lane"])

    def _act(self, rec: dict, button: dict, where: dict) -> bool:
        action = button["action"]
        if action.startswith("int_"):  # 🔀 Fusionar / 🚀 Desplegar: solo con el Integrador activo
            return bool(self.integrator) and self.integrator.on_button(action, rec, where, self)
        if action == APPROVE:
            return self._approve(rec, where)
        if action == PARK:
            return self._park(rec, where)
        if action == RETRY:
            return self._retry(rec, where)
        if action == OPTION:
            q = rec.get("question") or {}
            opts = q.get("options") or []
            idx = button.get("index")
            if not isinstance(idx, int) or not 0 <= idx < len(opts):
                return False
            if (rec.get("n_questions") or 1) > 1:
                return self._answer_first_of_many(rec, where, q.get("question") or "", opts[idx])
            return self._answer(rec, where, q.get("question") or "", opts[idx])
        return False

    def _answer_first_of_many(self, rec: dict, where: dict, question: str, answer: str) -> bool:
        """Varias preguntas: la opción responde la 1ª SIN desbloquear (retomar con media respuesta haría que el
        worker volviera a bloquearse, y Hermes manda a triage los bloqueos repetidos). Queda ✍️ para el resto."""
        tid, h = rec["task_id"], self._hermes(rec)
        if not h.comment(tid, f"{ANSWER_PREFIX} {question} → {answer}"[:3000], author=OSCAR_AUTHOR):
            self._edit(rec, where, "blocked", "no se pudo guardar la respuesta en la tarjeta")
            return False
        rest = {**rec, "question": {"question": "resto de preguntas", "options": [], "recommended": None},
                "n_questions": 1, "other_label": "resto de preguntas"}
        spec = [[{"text": "✍️ Otra respuesta", "action": OTHER}, {"text": "🗄 Aparcar", "action": PARK}]]
        _, markup = self.store.issue(rest, spec)
        self._edit(rest, where, "needs_input", f"💬 1ª: {truncate(answer, 80)} · responde el resto con ✍️",
                   markup=markup)
        return True

    def _approve(self, rec: dict, where: dict) -> bool:
        lane, tid = self._lane(rec), rec["task_id"]
        slug = self.links.repo_slug(lane) if (self.links and lane) else None
        if not slug:
            self._edit(rec, where, "blocked", "no se pudo abrir el PR: el repo del carril no está en GitHub")
            return False
        pr = self._find_pr(slug, tid) or self._create_pr(slug, lane, rec)
        if not pr:
            self._edit(rec, where, "blocked", "no se pudo abrir el PR; detalle en el log")
            return False
        number, url = pr
        stamp = self._now().strftime("%Y-%m-%d %H:%M")
        if not self._hermes(rec).comment(tid, f"APROBADO-OSCAR {stamp} · PR {url}", author=OSCAR_AUTHOR):
            log.warning("%s: PR %s abierto pero no se pudo comentar la aprobación en la tarjeta", tid, url)
        # Nunca se fusiona aquí: el merge es del carril Integrador.
        self._edit(rec, where, "approved", f"✅ aprobada · PR #{number}", extra_links=[(f"PR #{number}", url)])
        return True

    def _gh(self, *args: str):
        return self._run([self.gh_exe, *args], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=120)

    def _find_pr(self, slug: str, tid: str) -> tuple[int, str] | None:
        cp = self._gh("pr", "list", "--repo", slug, "--head", f"lane/{tid}", "--state", "open",
                      "--json", "number,url")
        if cp.returncode != 0:
            log.warning("%s: gh pr list falló: %s", tid, (cp.stderr or "").strip()[:300])
            return None
        try:
            prs = json.loads(cp.stdout or "[]")
        except json.JSONDecodeError:
            return None
        return (int(prs[0]["number"]), prs[0]["url"]) if prs else None

    def _create_pr(self, slug: str, lane, rec: dict) -> tuple[int, str] | None:
        tid = rec["task_id"]
        card = card_url(getattr(self.links, "kanban_base_url", None), rec["board"], tid)
        body = "\n\n".join(p for p in (
            rec.get("summary") or f"Tarea {tid}.",
            "Aprobado por Oscar desde Telegram.",
            f"Tarjeta: {card}" if card else f"Tarjeta: {tid} (tablero {rec['board']})",
            PR_FOOTER) if p)
        cp = self._gh("pr", "create", "--repo", slug, "--head", f"lane/{tid}", "--base", lane.base,
                      "--title", rec.get("title") or tid, "--body", body)
        if cp.returncode != 0:
            log.warning("%s: gh pr create falló: %s", tid, (cp.stderr or "").strip()[:300])
            return None
        m = re.search(r"https://github\.com/\S+/pull/(\d+)", cp.stdout or "")
        return (int(m.group(1)), m.group(0)) if m else None

    def _park(self, rec: dict, where: dict) -> bool:
        tid, h = rec["task_id"], self._hermes(rec)
        self._edit(rec, where, "parked", "🗄 aparcada")
        if not h.assign(tid, PARK_PROFILE):
            self._edit(rec, where, "blocked", "no se pudo aparcar (¿la tarea está en curso?)")
            return False
        h.comment(tid, "Aparcada por Oscar", author=OSCAR_AUTHOR)
        return True

    def _retry(self, rec: dict, where: dict) -> bool:
        # Se edita ANTES de desbloquear: el runner puede cogerla enseguida y su "▶️ en curso" debe quedar encima.
        self._edit(rec, where, "requeued", "🔄 reencolada")
        if not self._hermes(rec).unblock(rec["task_id"]):
            self._edit(rec, where, "blocked", "no se pudo reencolar: la tarea ya no está bloqueada")
            return False
        return True

    def _answer(self, rec: dict, where: dict, question: str, answer: str) -> bool:
        tid, h = rec["task_id"], self._hermes(rec)
        if not h.comment(tid, f"{ANSWER_PREFIX} {question} → {answer}"[:3000], author=OSCAR_AUTHOR):
            self._edit(rec, where, "blocked", "no se pudo guardar la respuesta en la tarjeta")
            return False
        self._edit(rec, where, "answered", f"💬 respondida: {truncate(answer, 120)}")
        if not h.unblock(tid):
            self._edit(rec, where, "answered", "💬 respuesta anotada · no se pudo desbloquear (mira la tarjeta)")
        return True

    def _ask(self, token: str, rec: dict, button: dict, where: dict) -> None:
        tid = rec["task_id"]
        if button["action"] == CHANGES:
            prompt = f"¿Qué cambio pides para {tid}?"
        else:
            q = (rec.get("question") or {}).get("question") or rec.get("summary") or ""
            prompt = f"Tu respuesta para {tid}:" + (f"\n{truncate(q, 300)}" if q else "")
        try:
            sent = self.notifier.send_to(where["chat_id"], where["thread_id"], prompt, html=False,
                                         reply_to=where["message_id"],
                                         # selective=False: con True Telegram lo muestra al autor del mensaje
                                         # citado, que es el propio bot. Solo Oscar cuenta: se filtra por from.id.
                                         reply_markup={"force_reply": True, "selective": False,
                                                       "input_field_placeholder": "Escribe aquí"})
        except Exception as exc:
            log.warning("%s: no se pudo pedir la respuesta: %s", tid, exc)
            return
        if sent and sent.get("message_id"):
            self.store.put_reply(where["chat_id"], sent["message_id"],
                                 {"token": token, "rec": rec, "button": button, "where": where})

    def _apply_reply(self, rec: dict, button: dict, where: dict, text: str) -> bool:
        tid, h = rec["task_id"], self._hermes(rec)
        if button["action"] == OTHER:
            q = rec.get("other_label") or (rec.get("question") or {}).get("question") or "pregunta del worker"
            return self._answer(rec, where, q, text)
        # Pedir cambios: igual que el carril review (comentario CAMBIOS de REVIEW_AUTHOR + reopen-review).
        body = f"{CHANGES_PREFIX} pedidos por Oscar desde Telegram:\n- {text}"
        if not h.comment(tid, body[:3000], author=REVIEW_AUTHOR):
            self._edit(rec, where, "blocked", "no se pudo guardar el cambio pedido en la tarjeta")
            return False
        self._edit(rec, where, "changes", "🔁 cambios pedidos")
        if not h.reopen_review(tid):
            # Aprobada = done, y la CLI 0.21.4 no reabre tareas done: el cambio queda anotado para cuando se reabra.
            self._edit(rec, where, "changes", "🔁 cambios anotados · reábrela desde el panel para que vuelva al carril")
        return True

    # --- mensaje --------------------------------------------------------------------------------------

    def _edit(self, rec: dict, where: dict, state: str | None, status: str | None, *, extra_links=(),
              markup: dict | None = None, keep_text: bool = False) -> None:
        if keep_text:  # solo devolver los botones
            self.notifier.edit_markup(where["chat_id"], where["message_id"], markup)
            return
        lane = self._lane(rec)
        links = self.links(lane, rec["task_id"], changed_files=rec.get("changed_files")) if (self.links and lane) else []
        text = render(state, rec["task_id"], rec.get("title"), rec["lane"], status, [*links, *extra_links],
                      body=rec.get("body"))
        self.notifier.edit(where["chat_id"], where["message_id"], text, **({"reply_markup": markup} if markup else {}))

    def _cleanup_messages(self, chat_id, message_ids) -> None:
        """Borra la pregunta force_reply y la respuesta de Oscar: el hilo no acumula mensajes (requiere admin)."""
        for mid in message_ids:
            if mid:
                try:
                    self.notifier.delete(str(chat_id), mid)
                except Exception:
                    pass


class UpdatePoller:
    """Hilo de long-polling de getUpdates con offset persistido en .state/tg_offset."""

    def __init__(self, notifier, desk: DecisionDesk, offset_path: Path | str, *, timeout: int = 25,
                 sleep: Callable[[float], None] = time.sleep):
        self.notifier = notifier
        self.desk = desk
        self.offset_path = Path(offset_path)
        self.timeout = timeout
        self._sleep = sleep
        self._stop = threading.Event()

    def _offset(self) -> int | None:
        try:
            return int(self.offset_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _save(self, offset: int) -> None:
        self.offset_path.parent.mkdir(parents=True, exist_ok=True)
        self.offset_path.write_text(str(offset), encoding="utf-8")

    def poll_once(self) -> int:
        updates = self.notifier.get_updates(self._offset(), self.timeout)
        for update in sorted(updates, key=lambda u: u.get("update_id", 0)):
            # Offset antes de procesar: un update que rompe no se reintenta en bucle (los tokens son de un uso).
            self._save(int(update["update_id"]) + 1)
            try:
                self.desk.handle_update(update)
            except Exception as exc:
                log.warning("update %s de Telegram falló: %s", update.get("update_id"), exc)
        return len(updates)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # red, 409 de otro consumidor...: nunca tumba el runner; nunca con token
                log.warning("getUpdates falló: %s", exc)
                self._sleep(5)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="tg-updates", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
