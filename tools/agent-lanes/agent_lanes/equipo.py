"""Tareas del equipo por Telegram (tablero «equipo» del kanban de Hermes).

Comandos: /asignar @persona Título | detalle [!alta] [para:dd-mm], /tareas_equipo, /mis_tareas, /hecha t_xxx, más los
botones [✅ Hecha] [💬 Comentar] [↩️ Reasignar] y comentar respondiendo al mensaje de la tarea.

Garantías:
- Solo las personas de people.yaml (más Oscar) con `tareas_equipo` distinto de false las usan; el resto cae al
  comportamiento de siempre. María queda fuera (decisión de Oscar 22-10): ni asignable ni con avisos.
- El texto de la tarea es DATO: se guarda en una tarjeta del tablero «equipo»; nunca crea tareas de carril ni ejecuta nada.
- Ningún carril ni worker lee ese tablero: si `tablero:` coincidiera con el de un carril, la función se apaga (falla cerrado).
- Todo se configura en lanes.yaml (`tareas_equipo:`): tablero, grupos (chat/tema) y recordatorio. Sin el bloque, apagado.
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .resumen_carriles import MADRID

log = logging.getLogger("agent_lanes")

COMANDOS = ("asignar", "tareas_equipo", "mis_tareas", "hecha")
CB = "eq"                       # callback_data: eq:ok|cm|re:<t_id>
ALTA = 10                       # prioridad de `!alta` (kanban: más alto = antes)
CERRADAS = ("done", "archived")
TID_RE = re.compile(r"^t_[0-9a-f]{8}$")
_PARA_RE = re.compile(r"(?<!\S)para:\s*(\d{1,2})[-/](\d{1,2})(?!\d)", re.I)
_ALTA_RE = re.compile(r"(?<!\S)!alta(?!\S)", re.I)
_USER_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
TITLE_MAX = 200
MAX_MSGS = 600                  # mensajes de tarea recordados para «comentar respondiendo»
USO = ("Uso: /asignar @persona Título | detalle opcional  (opcional: !alta, para:dd-mm)\n"
       "Ejemplo: /asignar @ingerber Crear la app de LinkedIn para Postiz | necesito el acceso !alta para:28-10")


def fold(text) -> str:
    base = "".join(c for c in unicodedata.normalize("NFD", str(text or "")) if not unicodedata.combining(c))
    return base.strip().lstrip("@").casefold()


@dataclass(frozen=True)
class Miembro:
    key: str
    name: str
    user_id: str
    username: str = ""
    grupo: str = ""
    owner: bool = False


@dataclass
class Config:
    activo: bool = False
    tablero: str = "equipo"
    grupos: dict = None          # nombre -> (chat, thread)
    recordatorio: bool = True
    hora: int = 9


def load_config(data: dict | None, lane_boards=()) -> Config:
    """Bloque `tareas_equipo:` de lanes.yaml. Sin bloque, `activo: false` o tablero de un carril -> apagado."""
    cfg = (data or {}).get("tareas_equipo") if isinstance(data, dict) else None
    if not isinstance(cfg, dict) or cfg.get("activo") is False:
        return Config(grupos={})
    board = str(cfg.get("tablero") or "equipo").strip()
    if not re.match(r"^[A-Za-z0-9_-]+$", board) or board in {b for b in lane_boards if b} | {"default"}:
        log.warning("tareas_equipo: el tablero %r es de un carril o no es válido; función apagada", board)
        return Config(grupos={})
    grupos = {}
    for name, g in (cfg.get("grupos") or {}).items():
        if isinstance(g, dict) and g.get("chat") is not None:
            grupos[fold(name)] = (str(g["chat"]), str(g.get("thread") or 0))
    rec = cfg.get("recordatorio") if isinstance(cfg.get("recordatorio"), dict) else {}
    try:
        hora = max(0, min(23, int(rec.get("hora", 9))))
    except (TypeError, ValueError):
        hora = 9
    return Config(True, board, grupos, rec.get("activo") is not False, hora)


def mencion(m: Miembro) -> str:
    """@username si lo hay; si no, enlace tg://user?id= (notifica en los grupos donde es miembro). HTML."""
    if m.username:
        return f"@{m.username}"
    return f'<a href="tg://user?id={html.escape(m.user_id)}">{html.escape(m.name)}</a>'


def meta_of(body: str | None) -> dict:
    """Líneas `clave: valor` tras el separador `---` del cuerpo de la tarjeta."""
    _, sep, tail = (body or "").rpartition("\n---\n")
    out = {}
    for line in (tail if sep else "").splitlines():
        k, _, v = line.partition(":")
        if v:
            out[k.strip().lower()] = v.strip()
    return out


def detalle_of(body: str | None) -> str:
    head, sep, _ = (body or "").rpartition("\n---\n")
    return (head if sep else (body or "")).strip()


def load_equipo_config(path: Path | None = None, lane_boards=()) -> Config:
    from .config import _load
    try:
        return load_config(_load(path), lane_boards)
    except Exception as exc:  # lanes.yaml ilegible: las tareas del equipo no arrancan
        log.warning("tareas_equipo: no pude leer lanes.yaml (%s); apagado", type(exc).__name__)
        return Config(grupos={})


class Equipo:
    def __init__(self, notifier, hermes, people, cfg: Config, *, owner_id, state_path: Path | None = None,
                 now=time.time, bot_username: str | None = None):
        self.notifier = notifier
        self.hermes = hermes
        self.people = people
        self.cfg = cfg
        self.owner_id = str(owner_id)
        self.state_path = Path(state_path) if state_path else None
        self._now = now
        self.bot_username = bot_username

    # --- miembros -------------------------------------------------------------------------------------

    def miembros(self) -> dict[str, Miembro]:
        out = {"oscar": Miembro("oscar", "Oscar", self.owner_id, owner=True)}
        for p in getattr(self.people, "personas", lambda: [])():
            if p.tareas_equipo:
                out[p.key] = Miembro(p.key, p.name, p.user_id, p.username, fold(p.grupo_tareas))
        return out

    def por_uid(self, uid) -> Miembro | None:
        return next((m for m in self.miembros().values() if m.user_id == str(uid)), None)

    def resolver(self, token: str, msg: dict | None = None) -> Miembro | None:
        want = fold(token)
        for m in self.miembros().values():
            if want and want in {fold(m.key), fold(m.name), fold(m.username)}:
                return m
        for ent in (msg or {}).get("entities") or ():  # mención de alguien sin @username (text_mention)
            uid = (ent.get("user") or {}).get("id")
            if ent.get("type") == "text_mention" and uid and fold((msg.get("text") or "")[ent["offset"]:ent["offset"] + ent["length"]]) == want:
                return self.por_uid(uid)
        return None

    # --- estado ---------------------------------------------------------------------------------------

    def _state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path else {}
        except (OSError, ValueError):
            data = {}
        data.setdefault("msgs", {})
        data.setdefault("asks", {})
        return data

    def _save(self, data: dict) -> None:
        if not self.state_path:
            return
        data["msgs"] = dict(list(data["msgs"].items())[-MAX_MSGS:])
        data["asks"] = dict(list(data["asks"].items())[-100:])
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(data), encoding="utf-8")
        except OSError as exc:
            log.warning("equipo: no pude guardar el estado: %s", exc)

    # --- entrada --------------------------------------------------------------------------------------

    @staticmethod
    def _where(msg: dict) -> tuple[str, str]:
        chat = str((msg.get("chat") or {}).get("id"))
        return chat, (str(msg.get("message_thread_id") or 0) if msg.get("is_topic_message") else "0")

    def _comando(self, msg: dict) -> tuple[str, str] | None:
        m = re.match(r"^/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?:\s+(.*))?$", (msg.get("text") or "").strip(), re.S)
        if not m or m.group(1).lower() not in COMANDOS:
            return None
        if m.group(2) and (not self.bot_username or m.group(2).lower() != self.bot_username.lower()):
            return None  # dirigido a otro bot
        return m.group(1).lower(), (m.group(3) or "").strip()

    def wants(self, msg: dict) -> bool:
        """True si el mensaje es de Equipo: comando nuestro o respuesta a un mensaje de tarea / a una pregunta nuestra,
        siempre de un miembro. Cualquier otro (o de un desconocido) sigue el camino de siempre."""
        if not self.cfg.activo or not self.por_uid((msg.get("from") or {}).get("id")) or (msg.get("from") or {}).get("is_bot"):
            return False
        if self._comando(msg):
            return True
        replied = (msg.get("reply_to_message") or {}).get("message_id")
        if not replied or not (msg.get("text") or "").strip() or (msg.get("text") or "").lstrip().startswith("/"):
            return False
        key = f"{(msg.get('chat') or {}).get('id')}:{replied}"
        st = self._state()
        if key in st["asks"]:  # la pregunta del botón solo la contesta quien pulsó
            return st["asks"][key].get("uid") == str((msg.get("from") or {}).get("id"))
        return key in st["msgs"]

    def handle(self, msg: dict) -> None:
        actor = self.por_uid((msg.get("from") or {}).get("id"))
        where = self._where(msg)
        cmd = self._comando(msg)
        if cmd is None:
            return self._respuesta(msg, actor, where)
        name, args = cmd
        if name == "asignar":
            self._asignar(msg, actor, where, args)
        elif name == "tareas_equipo":
            self._send(where, self._listado(None))
        elif name == "mis_tareas":
            self._send(where, self._listado(actor))
        else:
            self._hecha(actor, where, args.split()[0] if args.split() else "")

    # --- /asignar -------------------------------------------------------------------------------------

    def parse_asignar(self, args: str, msg: dict | None = None) -> dict | str:
        """-> {persona, titulo, detalle, alta, para} o un texto de error."""
        args = (args or "").strip()
        first, _, rest = args.partition(" ")
        if not first:
            return USO
        persona = self.resolver(first, msg)
        if persona is None:
            return f"No conozco a «{first.lstrip('@')[:40]}» entre el equipo. Pruebas con: " + ", ".join(
                m.name for m in self.miembros().values())
        alta = bool(_ALTA_RE.search(rest))
        rest = _ALTA_RE.sub("", rest)
        para = None
        m = _PARA_RE.search(rest)
        if m:
            try:
                datetime(2000, int(m.group(2)), int(m.group(1)))  # año bisiesto: admite 29-02
            except ValueError:
                return f"La fecha «{m.group(0)[:20]}» no es válida (usa para:dd-mm)."
            para = f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
            rest = _PARA_RE.sub("", rest)
        titulo, _, detalle = rest.partition("|")
        titulo = " ".join(titulo.split())[:TITLE_MAX]
        if not titulo:
            return "Falta el título de la tarea.\n" + USO
        return {"persona": persona, "titulo": titulo, "detalle": detalle.strip(), "alta": alta, "para": para}

    def _asignar(self, msg: dict, actor: Miembro, where, args: str) -> None:
        parsed = self.parse_asignar(args, msg)
        if isinstance(parsed, str):
            return self._send(where, parsed, html_=False)
        persona = parsed["persona"]
        hoy = datetime.fromtimestamp(self._now(), MADRID).strftime("%Y-%m-%d")
        lines = [f"creador: {actor.key}", f"creado: {hoy}", f"origen: {where[0]}:{where[1]}"]
        if parsed["para"]:
            lines.append(f"para: {parsed['para']}")
        body = (parsed["detalle"] + "\n---\n" if parsed["detalle"] else "\n---\n") + "\n".join(lines) + "\n"
        try:
            tid = self.hermes.create(parsed["titulo"], body, persona.key, priority=ALTA if parsed["alta"] else None,
                                     created_by=actor.key)
        except Exception as exc:
            log.warning("equipo: no pude crear la tarjeta: %s", exc)
            return self._send(where, "No pude crear la tarea (¿existe el tablero «%s»?). Avísale a Oscar." % self.cfg.tablero,
                              html_=False)
        task = {"id": tid, "title": parsed["titulo"], "body": body, "priority": ALTA if parsed["alta"] else 0}
        self._publicar(task, persona, actor, where, nueva=True)

    def _destino(self, persona: Miembro, where) -> tuple[str, str]:
        return self.cfg.grupos.get(persona.grupo) or where

    def _texto(self, task: dict, persona: Miembro, actor_name: str, cabecera: str) -> str:
        meta = meta_of(task.get("body"))
        det = detalle_of(task.get("body"))
        extra = [f"🆔 {task['id']}", f"de {html.escape(actor_name)}"]
        if meta.get("para"):
            extra.append(f"para {html.escape(meta['para'])}")
        if int(task.get("priority") or 0) >= ALTA:
            extra.append("❗alta")
        return (f"{cabecera} {mencion(persona)}: <b>{html.escape(task['title'])}</b>"
                + (f"\n{html.escape(det[:600])}" if det else "") + "\n" + " · ".join(extra))

    @staticmethod
    def _botones(tid: str) -> dict:
        return {"inline_keyboard": [[{"text": "✅ Hecha", "callback_data": f"{CB}:ok:{tid}"},
                                     {"text": "💬 Comentar", "callback_data": f"{CB}:cm:{tid}"},
                                     {"text": "↩️ Reasignar", "callback_data": f"{CB}:re:{tid}"}]]}

    def _publicar(self, task: dict, persona: Miembro, actor: Miembro, where, *, nueva: bool, cabecera: str | None = None) -> None:
        """Aviso en el grupo del asignado (o donde se pidió), confirmación breve donde se pidió si es otro sitio y DM."""
        texto = self._texto(task, persona, actor.name, cabecera or "📌 Nueva tarea para")
        destino = self._destino(persona, where)
        sent = self._send(destino, texto, markup=self._botones(task["id"]))
        if sent is None and destino != tuple(where):
            destino = tuple(where)  # el bot no pudo escribir en el grupo del asignado: se queda donde se pidió
            sent = self._send(destino, texto + "\n(No pude publicarla en su grupo.)", markup=self._botones(task["id"]))
        if sent and sent.get("message_id"):
            st = self._state()
            st["msgs"][f"{sent['chat_id']}:{sent['message_id']}"] = task["id"]
            self._save(st)
        if tuple(destino) != tuple(where) and sent:
            self._send(where, f"✅ {task['id']} creada para {html.escape(persona.name)} y publicada en su grupo.")
        if persona.user_id != actor.user_id and persona.user_id != str(where[0]):
            try:  # solo llega si ha hecho /start; si no, Telegram lo rechaza y vale con el grupo
                self.notifier.send_to(persona.user_id, 0, texto, html=True, reply_markup=self._botones(task["id"]),
                                      trusted=True)
            except Exception as exc:
                log.info("equipo: DM a %s no entregado (¿sin /start?): %s", persona.key, type(exc).__name__)

    # --- listados -------------------------------------------------------------------------------------

    def _abiertas(self) -> list[dict]:
        try:
            tasks = self.hermes.list_board()
        except Exception as exc:
            log.warning("equipo: no pude leer el tablero: %s", exc)
            return []
        return [t for t in tasks if t.get("status") not in CERRADAS]

    def _asignado(self, task: dict, miembros: dict) -> Miembro:
        """María (o quien ya no participa) pasa a Oscar."""
        return miembros.get(str(task.get("assignee") or "").lower()) or miembros["oscar"]

    def _listado(self, solo: Miembro | None) -> str:
        miembros = self.miembros()
        grupos: dict[str, list] = {}
        for t in self._abiertas():
            who = self._asignado(t, miembros)
            if solo is None or who.key == solo.key:
                grupos.setdefault(who.key, []).append(t)
        if not grupos:
            return "📭 No hay tareas abiertas." if solo is None else "📭 No tienes tareas abiertas."
        out = ["📋 <b>Tareas del equipo</b>" if solo is None else "📋 <b>Tus tareas</b>"]
        for key in [k for k in miembros if k in grupos]:
            if solo is None:
                out.append(f"\n<b>{html.escape(miembros[key].name)}</b> ({len(grupos[key])})")
            for t in sorted(grupos[key], key=lambda x: -int(x.get("priority") or 0)):
                meta = meta_of(t.get("body"))
                tail = (f" · para {html.escape(meta['para'])}" if meta.get("para") else "") + (
                    " · ❗" if int(t.get("priority") or 0) >= ALTA else "")
                out.append(f"• {t['id']} {html.escape(str(t.get('title') or '')[:90])}{tail}")
        return "\n".join(out)

    # --- cerrar / comentar / reasignar ----------------------------------------------------------------

    def puede_cerrar(self, actor: Miembro, task: dict) -> bool:
        """Asignado, creador u Oscar (María ya no participa)."""
        miembros = self.miembros()
        creador = meta_of(task.get("body")).get("creador")
        return actor.owner or actor.key in {self._asignado(task, miembros).key, creador}

    def _tarea(self, tid: str) -> dict | None:
        try:
            return (self.hermes.show(tid).get("task") or None)
        except Exception:
            return None

    def _hecha(self, actor: Miembro, where, tid: str, *, edit=None) -> str:
        tid = tid.strip().lower()
        task = self._tarea(tid) if TID_RE.match(tid) else None
        if not task:
            msg = "Dime una tarea del equipo: /hecha t_xxxxxxxx"
            self._send(where, msg, html_=False)
            return msg
        if task.get("status") in CERRADAS:
            self._send(where, f"{tid} ya estaba cerrada.", html_=False)
            return "ya cerrada"
        if not self.puede_cerrar(actor, task):
            msg = "Solo el asignado, quien la creó u Oscar pueden cerrarla."
            self._send(where, msg, html_=False)
            return msg
        ok, err = self.hermes.complete(tid, f"Hecha por {actor.name} desde Telegram", {"via": "telegram", "por": actor.key})
        if not ok:
            log.warning("equipo: complete %s falló: %s", tid, err)
            self._send(where, f"No pude cerrar {tid}.", html_=False)
            return "error"
        miembros = self.miembros()
        creador = miembros.get(meta_of(task.get("body")).get("creador") or "")
        aviso = f"✅ {tid} hecha por {html.escape(actor.name)}: {html.escape(str(task.get('title') or '')[:120])}"
        if creador and creador.key != actor.key:
            aviso += f"\n{mencion(creador)}, ya está."
        self._send(where, aviso)
        if creador and creador.key != actor.key and creador.user_id != str(where[0]):
            try:
                self.notifier.send_to(creador.user_id, 0, aviso, html=True, trusted=True)
            except Exception as exc:
                log.info("equipo: DM a %s no entregado: %s", creador.key, type(exc).__name__)
        return "hecha"

    def _comentar(self, actor: Miembro, where, tid: str, texto: str) -> None:
        task = self._tarea(tid)
        if not task:
            return self._send(where, "No encuentro esa tarea.", html_=False)
        if not self.hermes.comment(tid, f"{actor.name}: {texto.strip()[:1500]}", author=actor.key):
            return self._send(where, f"No pude guardar el comentario en {tid}.", html_=False)
        miembros = self.miembros()
        otros = {self._asignado(task, miembros).key, meta_of(task.get("body")).get("creador")} - {actor.key, None}
        avisa = " ".join(mencion(miembros[k]) for k in otros if k in miembros)
        self._send(where, f"💬 Comentario de {html.escape(actor.name)} guardado en {tid}." + (f" {avisa}" if avisa else ""))

    def _reasignar(self, actor: Miembro, where, tid: str, destino_txt: str, msg: dict | None = None) -> None:
        task = self._tarea(tid)
        if not task or task.get("status") in CERRADAS:
            return self._send(where, "No encuentro esa tarea abierta.", html_=False)
        if not self.puede_cerrar(actor, task):
            return self._send(where, "Solo el asignado, quien la creó u Oscar pueden reasignarla.", html_=False)
        nuevo = self.resolver(destino_txt.split()[0] if destino_txt.split() else "", msg)
        if nuevo is None:
            return self._send(where, "No conozco a esa persona. Responde con un nombre del equipo.", html_=False)
        if not self.hermes.assign(tid, nuevo.key):
            return self._send(where, f"No pude reasignar {tid}.", html_=False)
        self.hermes.comment(tid, f"Reasignada a {nuevo.name} por {actor.name}", author=actor.key)
        self._publicar(task, nuevo, actor, where, nueva=False, cabecera="↩️ Tarea reasignada a")

    def _respuesta(self, msg: dict, actor: Miembro, where) -> None:
        replied = (msg.get("reply_to_message") or {}).get("message_id")
        key = f"{(msg.get('chat') or {}).get('id')}:{replied}"
        st = self._state()
        texto = (msg.get("text") or "").strip()
        ask = st["asks"].get(key)
        if ask:
            if ask.get("uid") != actor.user_id:
                return
            del st["asks"][key]
            self._save(st)
            if ask["kind"] == "re":
                return self._reasignar(actor, where, ask["tid"], texto, msg)
            return self._comentar(actor, where, ask["tid"], texto)
        tid = st["msgs"].get(key)
        if tid:
            self._comentar(actor, where, tid, texto)

    # --- botones --------------------------------------------------------------------------------------

    def handle_callback(self, cq: dict) -> None:
        answer = self.notifier.answer_callback
        actor = self.por_uid((cq.get("from") or {}).get("id")) if self.cfg.activo else None
        parts = (cq.get("data") or "").split(":")
        if actor is None:
            return answer(cq.get("id"), "Estos botones son del equipo.", alert=True)
        if len(parts) != 3 or parts[1] not in ("ok", "cm", "re") or not TID_RE.match(parts[2]):
            return answer(cq.get("id"), "Este botón ya no vale")
        _, accion, tid = parts
        msg = cq.get("message") or {}
        where = self._where(msg)
        if accion == "ok":
            task = self._tarea(tid)
            if task and task.get("status") not in CERRADAS and not self.puede_cerrar(actor, task):
                return answer(cq.get("id"), "Solo el asignado, quien la creó u Oscar pueden cerrarla.", alert=True)
            answer(cq.get("id"), "Marcada como hecha")
            if self._hecha(actor, where, tid) == "hecha":
                self.notifier.edit_markup(str((msg.get("chat") or {}).get("id")), msg.get("message_id"), None)
            return
        answer(cq.get("id"), "Responde al mensaje que te envío")
        pide = "tu comentario" if accion == "cm" else "el nombre de la persona (ingerber, andrea, angel, oscar…)"
        sent = self._send(where, f"{'💬' if accion == 'cm' else '↩️'} {tid}: responde a ESTE mensaje con {pide}.",
                          markup={"force_reply": True, "selective": True}, html_=False,
                          reply_to=msg.get("message_id"))
        if sent and sent.get("message_id"):
            st = self._state()
            st["asks"][f"{sent['chat_id']}:{sent['message_id']}"] = {"kind": accion, "tid": tid, "uid": actor.user_id}
            self._save(st)

    # --- recordatorio diario --------------------------------------------------------------------------

    def tick(self) -> bool:
        """Una vez al día a partir de las `hora` (Madrid): tareas abiertas por persona, en el grupo de cada una; las de
        Oscar (y las de quien no tiene grupo) al DM de Oscar. True si envió algo."""
        if not self.cfg.activo or not self.cfg.recordatorio:
            return False
        ahora = datetime.fromtimestamp(self._now(), MADRID)
        hoy = ahora.strftime("%Y-%m-%d")
        st = self._state()
        if ahora.hour < self.cfg.hora or st.get("recordatorio") == hoy:
            return False
        st["recordatorio"] = hoy
        self._save(st)  # se marca antes: un fallo de envío no repite el mensaje cada minuto
        miembros = self.miembros()
        por: dict[str, list] = {}
        for t in self._abiertas():
            por.setdefault(self._asignado(t, miembros).key, []).append(t)
        por_destino: dict[tuple, list] = {}
        for key, ts in por.items():
            m = miembros[key]
            destino = self.cfg.grupos.get(m.grupo) or (self.owner_id, "0")
            por_destino.setdefault(destino, []).append((m, ts))
        enviado = False
        for destino, bloques in por_destino.items():
            out = ["⏰ <b>Tareas abiertas del equipo</b>"]
            for m, ts in bloques:
                out.append(f"\n{mencion(m) if destino[0] != self.owner_id else html.escape(m.name)} ({len(ts)})")
                out += [f"• {t['id']} {html.escape(str(t.get('title') or '')[:80])}"
                        + (f" · para {html.escape(meta_of(t.get('body'))['para'])}" if meta_of(t.get("body")).get("para") else "")
                        for t in ts]
            enviado = bool(self._send(destino, "\n".join(out), trusted=destino[0] == self.owner_id)) or enviado
        return enviado

    # --- envío ----------------------------------------------------------------------------------------

    def _send(self, where, text: str, *, html_: bool = True, markup: dict | None = None, reply_to=None,
              trusted: bool = False):
        kw = {"reply_markup": markup} if markup else {}
        if reply_to:
            kw["reply_to"] = reply_to
        if trusted:
            kw["trusted"] = True
        try:
            return self.notifier.send_to(where[0], where[1], text, html=html_, **kw)
        except Exception as exc:
            log.warning("equipo: no pude enviar a %s: %s", type(exc).__name__, exc)
            return None
