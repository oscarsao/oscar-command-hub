"""Vigilante del equipo (t_04fe996e): tema «👥 Equipo» del grupo Gestión.

Dos cosas en el mismo tema, ambas solo para Oscar (nada llega a una persona del equipo sin su botón):

1. **Solicitudes** de Ángel, María, Andrea, Ingerber (y quien entre en people.yaml/ohq_people), vengan del bot que
   vengan: [IDEA]/peticiones del bot de Avisos y capacidades pendientes (vía OHQ), [IDEA de …] del bot de carriles y
   tarjetas de Hermes pedidas por ellos (`Pidió:` / `Origen-Telegram` de su DM / `created_by tg-dm-…`). Solo cuentan
   artefactos explícitos (tarjetas, peticiones): la charla no es una solicitud.
   - Tabla única: cada solicitud es una petición `req_<n>` del registro de `peticiones.py` (t_211c9902). Lo que el
     contrato de ese registro no tiene (quién pidió, ficha publicada, decisión de Oscar) va en `.state/vigilancia.json`
     con la clave `req_<n>`; el registro no se amplía.
   - Ficha al momento con [✅ Aprobar → tarea] [🙋 Asignar a…] [💬 Responder] [❓ Pedir detalle] [❌ Rechazar]
     [⏸ Luego]; la ficha se edita al pasar por aprobada → en curso → hecha → entregada.
   - Deduplicación: misma persona + título parecido (`triaje.similitud`, umbral de triaje.yaml) en la ventana → una
     sola ficha con varias fuentes.
2. **Fallos** que calcula OHQ (`GET /api/equipo/vigilancia`): «dijo hecho sin hacerlo» y respuesta cortada al momento
   (máx. `equipo.urgentes_hora`, el resto agrupado); el resto, en los resúmenes de `equipo.horas` (9:00 y 17:00
   Madrid) agrupado por persona, sin repetir lo ya avisado y sin mensaje si no hay nada. Botones por elemento:
   [📌 Crear tarea] [💬 Responder a <persona>] [✅ Resuelto] [🔕 Ignorar 7 días].

El primer resumen del día añade las solicitudes abiertas por persona y antigüedad, y la revisión de permisos
(people.yaml frente a ohq_people). «Responder», «Pedir detalle», «Rechazar» y «Avisar» solo preparan un BORRADOR que
Oscar envía con [📤 Enviar]. Sin OHQ_API_URL/OHQ_API_KEY se vigila solo lo del hub.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import yaml

from .ajustes_hub import valor
from .config import ROOT
from .notices import truncate

log = logging.getLogger("agent_lanes")

MADRID = ZoneInfo("Europe/Madrid")
KIND = "equipo"
GESTION = "-1003530490339"
TEMA_POR_DEFECTO = 5          # Gestión · Operaciones General (si no se puede crear «👥 Equipo»)
TEMA_NOMBRE = "👥 Equipo"
IGNORAR_DIAS = 7
VENTANA_H = 72                # solicitudes y deduplicación: solo lo de las últimas N horas
INTERVALO_S = 600             # una pasada completa (OHQ + kanban) cada 10 min como mucho
BOTONES_POR_MENSAJE = 8
FINALES = ("entregada", "rechazada", "archivada")
ESTADOS_ABIERTOS_HERMES = ("todo", "ready", "running", "blocked", "triage", "review")
PROYECTO_DE_MARCA = {"migrateam": "MigraTeam", "pildora": "Pildora", "pildora_digital_inbound": "Pildora",
                     "píldora": "Pildora", "oscarhq": "OHQ", "signal": "Signal", "nextjobs": "NextJobs",
                     "scraper": "Scraper"}
CARRIL_DE_PROYECTO = {"MigraTeam": "claude-migrateam", "OHQ": "claude-oscarhq", "Hub": "claude-hub",
                      "Signal": "claude-scraper", "Scraper": "claude-scraper", "NextJobs": "claude-nextjobs"}
ICONO = {"nueva": "🆕", "aprobada": "✅", "en curso": "🔧", "hecha": "🏁", "entregada": "📦", "rechazada": "❌",
         "atascada": "⛔", "luego": "⏸", "asignada": "🙋", "archivada": "🗄"}
TITULO_TIPO = {"hecho_sin_hacer": "🚨 Dijo hecho sin hacerlo", "respuesta_cortada": "🚨 Respuesta cortada o con error",
               "negativa_sin_aviso": "🙅 Dijo «no puedo» sin avisarte", "promesa_sin_suscripcion": "⏰ Prometió avisar",
               "sin_marca": "🏷 Petición sin marca", "sin_carril": "🛤 Petición sin carril",
               "sin_tocar": "💤 Petición sin tocar", "asignada_parada": "🐢 Tarea parada", "sin_fecha": "📅 Sin fecha",
               "origen_cruzado": "🔀 Tarjeta con origen de otra persona"}
_ALTA = re.compile(r"\b(urgente|urge|hoy|ya mismo|cuanto antes|ca[ií]d[oa]|no funciona|bloquead[oa]|cliente)\b", re.I)
_PIDIO = re.compile(r"^\s*Pidió:\s*(.+?)\s*$", re.M)
_ORIGEN = re.compile(r"^\s*origen-telegram:\s*chat=(-?\d+)\s+thread=(\d+)\s*$", re.I | re.M)
_PREFIJO = re.compile(r"^\s*\[[^\]]*\]\s*")

# Acciones de botón (prefijo eq_: las enruta decisions._act a CommandCenter.equipo_choice).
APROBAR, ASIGNAR, ASIGNAR_A, RESPONDER, DETALLE, RECHAZAR, LUEGO = (
    "eq_ok", "eq_asig", "eq_asig_p", "eq_resp", "eq_det", "eq_rech", "eq_luego")
TAREA, RESUELTO, IGNORAR, AVISAR, ENVIAR, CANCELAR = ("eq_tarea", "eq_hecho", "eq_ign", "eq_avisar", "eq_env",
                                                     "eq_cancel")
PIDEN_TEXTO = (RESPONDER, RECHAZAR)  # force_reply (decisions.REPLY_ACTIONS)


def corto(tid: str | None) -> str:
    """Id corto acordado para las fichas: t_0a1b2c3d → t_0a1b(2c3d); req_12 queda igual."""
    m = re.fullmatch(r"t_([0-9a-f]{4})([0-9a-f]{4})", tid or "")
    return f"t_{m.group(1)}({m.group(2)})" if m else (tid or "")


def hace(desde: float | None, ahora: float) -> str:
    if not desde:
        return "sin fecha"
    m = max(0, int((ahora - desde) // 60))
    if m < 60:
        return f"hace {m} min"
    if m < 48 * 60:
        return f"hace {m // 60} h"
    return f"hace {m // 1440} d"


def _epoch(valor_) -> float | None:
    if isinstance(valor_, (int, float)) and not isinstance(valor_, bool):
        return float(valor_)
    try:
        return datetime.fromisoformat(str(valor_).replace("Z", "+00:00")).timestamp() if valor_ else None
    except ValueError:
        return None


def _mm(text: str | None) -> str:
    return " ".join(str(text or "").split())


def prioridad_estimada(texto: str, prioridad: str | None = None) -> str:
    p = (prioridad or "").lower()
    if p in ("high", "urgent", "alta") or _ALTA.search(texto or ""):
        return "alta"
    return "baja" if p in ("low", "baja") else "media"


def proyecto_de(marca: str | None, por_defecto: str = "Pildora") -> str:
    from .people import norm_marca
    return PROYECTO_DE_MARCA.get(norm_marca(marca), por_defecto) if marca else por_defecto


@dataclass
class Config:
    chat: str = GESTION
    tema: int = TEMA_POR_DEFECTO
    crear_tema: bool = True
    tema_nombre: str = TEMA_NOMBRE
    carriles: dict = field(default_factory=lambda: dict(CARRIL_DE_PROYECTO))
    proyecto_por_defecto: str = "Pildora"


def load_config(path: Path | str | None = None) -> Config:
    """lanes.yaml `equipo_telegram: {chat, thread, crear_tema, nombre, carriles}`. Sin bloque: los valores de fábrica."""
    try:
        data = (yaml.safe_load(Path(path or ROOT / "lanes.yaml").read_text(encoding="utf-8")) or {})
        data = data.get("equipo_telegram") or {}
    except (OSError, yaml.YAMLError, AttributeError):
        data = {}
    cfg = Config()
    if data.get("chat"):
        cfg.chat = str(data["chat"])
    if isinstance(data.get("thread"), int):
        cfg.tema = data["thread"]
    if isinstance(data.get("crear_tema"), bool):
        cfg.crear_tema = data["crear_tema"]
    if isinstance(data.get("nombre"), str) and data["nombre"].strip():
        cfg.tema_nombre = data["nombre"].strip()
    if isinstance(data.get("carriles"), dict):
        cfg.carriles.update({str(k): (str(v) if v else None) for k, v in data["carriles"].items()})
    return cfg


def horas_resumen() -> list[int]:
    out = []
    for h in valor("equipo.horas") or ():
        try:
            if 0 <= int(h) <= 23:
                out.append(int(h))
        except (TypeError, ValueError):
            continue
    return sorted(set(out)) or [9, 17]


# --- estado ------------------------------------------------------------------------------------------------------

class Estado:
    """`.state/vigilancia.json`: avisos ya dados, fichas por req, topes por hora, partes enviados. Escritura atómica."""

    def __init__(self, path: Path | str):
        self.path, self._lock = Path(path), threading.RLock()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        for k in ("items", "fichas", "fuentes", "refs", "tema"):
            data.setdefault(k, {})
        for k in ("envios", "envios_fichas", "cola", "cola_fichas", "slots"):
            data.setdefault(k, [])
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


# --- fuentes del hub (kanban de Hermes) -----------------------------------------------------------------------------

def persona_de_tarjeta(task: dict, people) -> object | None:
    """Persona del equipo que pidió la tarjeta (people.yaml, mismo id de Telegram que ohq_people), o None.
    Orden: chat del `Origen-Telegram` si es el DM de alguien > `created_by tg-dm-<clave>` > `Pidió: <nombre>` (lo
    escribe la vía única de tarjetas a partir de people.yaml, no es texto libre de la persona)."""
    body = task.get("body") or ""
    personas = [p for p in getattr(people, "_by_id", {}).values()]
    m = _ORIGEN.search(body)
    if m and not m.group(1).startswith("-"):
        p = people.lookup(m.group(1))
        if p is not None and p.key != "oscar":
            return p
    cb = str(task.get("created_by") or "")
    if cb.startswith("tg-dm-"):
        p = next((x for x in personas if x.key == cb[6:]), None)
        if p:
            return p
    m = _PIDIO.search(body)
    if m:
        nombre = m.group(1).strip().casefold()
        return next((x for x in personas if nombre in (x.key.casefold(), x.name.casefold())), None)
    return None


def solicitudes_hermes(tasks: list[dict], people, ahora: float) -> tuple[list[dict], list[dict]]:
    """(solicitudes, elementos) de las tarjetas de Hermes pedidas por el equipo en la ventana."""
    sols, items = [], []
    for t in tasks:
        creada = _epoch(t.get("created_at"))
        if creada and ahora - creada > VENTANA_H * 3600:
            continue
        p = persona_de_tarjeta(t, people)
        if p is None:
            continue
        persona = {"telegram_id": int(p.user_id), "nombre": p.name, "rol": p.role}
        title = t.get("title") or t.get("id")
        idea = (title or "").startswith("[IDEA de ")
        cita = "\n".join(line[2:] for line in (t.get("body") or "").splitlines() if line.startswith("> "))
        sols.append({"id": f"hermes:{t['id']}", "persona": persona, "titulo": truncate(_PREFIJO.sub("", title), 90),
                     "cita": truncate(_mm(cita), 200), "marca": (p.brands or (None,))[0],
                     "bot": "carriles" if idea else "hermes", "tipo": "idea" if idea else "tarjeta",
                     "prioridad": "medium", "creado": creada, "tarjeta": t["id"], "board": t.get("board") or ""})
        m = _ORIGEN.search(t.get("body") or "")
        pidio = _PIDIO.search(t.get("body") or "")
        if m and pidio and not m.group(1).startswith("-") and m.group(1) != p.user_id:
            items.append({"id": f"cruzado:{t['id']}", "tipo": "origen_cruzado", "gravedad": "normal",
                          "persona": persona, "desde": creada, "marca": None, "acciones": ["resuelto", "ignorar"],
                          "resumen": f"La tarjeta {corto(t['id'])} dice que la pidió {p.name} pero vuelve al chat "
                                     "de otra persona", "detalle": truncate(title, 90),
                          "enlace": {"tipo": "tarjeta", "id": t["id"], "board": t.get("board") or ""}})
    return sols, items


# --- textos ------------------------------------------------------------------------------------------------------

def texto_ficha(f: dict, ahora: float) -> str:
    estado = f.get("estado") or "nueva"
    persona = (f.get("persona") or {}).get("nombre") or "sin identificar"
    head = f"🙋 Solicitud {f['req']} · {persona} · {hace(f.get('creado'), ahora)}"
    lines = [head, f"Pide: {truncate(f.get('titulo'), 120)}"]
    if f.get("cita") and _mm(f["cita"]) != _mm(f.get("titulo")):
        lines.append(f"«{truncate(f['cita'], 200)}»")
    bots = " + ".join(dict.fromkeys(f.get("bots") or ())) or "¿?"
    lines.append(f"Proyecto: {f.get('proyecto')}" + (f" · Marca: {f['marca']}" if f.get("marca") else "")
                 + f" · Desde: {bots}")
    lines.append(f"Prioridad estimada: {f.get('prioridad') or 'media'}")
    extra = f" · {corto(f['tid'])}" if f.get("tid") else ""
    if f.get("asignada"):
        extra += f" · asignada a {f['asignada']}"
    if f.get("motivo"):
        extra += f" · motivo: {truncate(f['motivo'], 120)}"
    lines.append(f"Estado: {ICONO.get(estado, '•')} {estado}{extra}")
    if estado in FINALES or f.get("cerrada"):
        lines.append("Aviso a quien lo pidió: " + ("le llega por su suscripción" if f.get("suscrito")
                                                     else "pulsa «📣 Avisar» para prepararlo"))
    return "\n".join(lines)


def botones_ficha(f: dict) -> list[list[dict]]:
    estado, nombre = f.get("estado") or "nueva", (f.get("persona") or {}).get("nombre") or "?"
    if estado in FINALES or f.get("cerrada"):
        return [] if f.get("suscrito") or f.get("avisado") else [[{"text": f"📣 Avisar a {nombre}", "action": AVISAR}]]
    if estado in ("nueva", "luego", "asignada"):
        return [[{"text": "✅ Aprobar → tarea", "action": APROBAR}, {"text": "🙋 Asignar a…", "action": ASIGNAR}],
                [{"text": "💬 Responder", "action": RESPONDER}, {"text": "❓ Pedir detalle", "action": DETALLE}],
                [{"text": "❌ Rechazar", "action": RECHAZAR}, {"text": "⏸ Luego", "action": LUEGO}]]
    return [[{"text": "💬 Responder", "action": RESPONDER}]]


def texto_elemento(el: dict, ahora: float) -> str:
    persona = (el.get("persona") or {}).get("nombre") or "sin identificar"
    lines = [f"{TITULO_TIPO.get(el['tipo'], '⚠️ ' + el['tipo'])} · {persona} · {hace(_epoch(el.get('desde')), ahora)}",
             truncate(el.get("resumen"), 300)]
    if el.get("detalle"):
        lines.append(truncate(el["detalle"], 300))
    enl = el.get("enlace") or {}
    ref = corto(enl.get("id")) if enl.get("tipo") == "tarjeta" else str(enl.get("id") or "")[:8]
    lines.append(f"Ref: {enl.get('tipo', '?')} {ref}" + (f" · Marca: {el['marca']}" if el.get("marca") else ""))
    return "\n".join(lines)


def botones_elemento(el: dict, n: int | None = None) -> list[list[dict]]:
    sufijo = f" {n}" if n is not None else ""
    persona = (el.get("persona") or {}).get("nombre")
    fila = [{"text": f"📌 Crear tarea{sufijo}", "action": TAREA, "index": el["id"]}]
    if persona and persona != "sin identificar" and n is None:
        fila.append({"text": f"💬 Responder a {persona}", "action": RESPONDER, "index": el["id"]})
    return [fila, [{"text": f"✅ Resuelto{sufijo}", "action": RESUELTO, "index": el["id"]},
                   {"text": f"🔕 Ignorar 7 días{sufijo}", "action": IGNORAR, "index": el["id"]}]]


# --- motor -----------------------------------------------------------------------------------------------------

class Vigilancia:
    """Una pasada (`tick`) cada pocos minutos desde CommandCenter.veto_tick. Todo inyectable:

    fetch_ohq() -> dict | None · hermes_tasks() -> [task con 'board'] · show_card(tid) -> show | None ·
    send(text, spec) -> {chat_id, message_id} | None · edit(chat, msg, text, spec) -> bool ·
    create_topic(chat, name) -> int | None · registro = Peticiones · people = People."""

    def __init__(self, *, estado: Estado, registro, people, fetch_ohq: Callable[[], dict | None],
                 hermes_tasks: Callable[[], list], show_card: Callable[[str], dict | None],
                 send: Callable[[str, list, int], dict | None], edit: Callable[[str, object, str, list], bool],
                 create_topic: Callable[[str, str], int | None] | None = None, cfg: Config | None = None,
                 now: Callable[[], float] = time.time, triaje_cfg: dict | None = None):
        self.estado, self.registro, self.people = estado, registro, people
        self.fetch_ohq, self.hermes_tasks, self.show_card = fetch_ohq, hermes_tasks, show_card
        self.send, self.edit, self.create_topic = send, edit, create_topic
        self.cfg, self._now = cfg or Config(), now
        if triaje_cfg is None:
            from . import triaje
            triaje_cfg = triaje.load_config()
        self.umbral = float(triaje_cfg.get("duplicado_umbral", 0.6))
        self.ultimo: dict = {"elementos": [], "solicitudes": [], "personas": []}

    # tema ----------------------------------------------------------------------------------------------------
    def tema(self, data: dict) -> int:
        t = data["tema"]
        if t.get("thread"):
            return int(t["thread"])
        if self.cfg.crear_tema and self.create_topic and not t.get("intentado"):
            t["intentado"] = True
            nuevo = None
            try:
                nuevo = self.create_topic(self.cfg.chat, self.cfg.tema_nombre)
            except Exception as exc:
                log.warning("vigilancia: no se pudo crear el tema %s: %s", self.cfg.tema_nombre, exc)
            if nuevo:
                t.update(thread=int(nuevo), creado=True)
                return int(nuevo)
            log.warning("vigilancia: el bot no puede crear «%s»; uso el tema %s (lanes.yaml equipo_telegram.thread)",
                        self.cfg.tema_nombre, self.cfg.tema)
        return int(self.cfg.tema)

    def _enviar(self, data: dict, text: str, spec: list) -> dict | None:
        try:
            return self.send(text, spec, self.tema(data))
        except Exception as exc:
            log.warning("vigilancia: envío fallido: %s", exc)
            return None

    @staticmethod
    def _cupo(lista: list, tope: int, ahora: float) -> bool:
        lista[:] = [t for t in lista if ahora - t < 3600]
        return len(lista) < tope

    # pasada --------------------------------------------------------------------------------------------------
    def tick(self) -> dict:
        """Una pasada. Devuelve un resumen (para tests y el log). Nunca lanza por una fuente caída."""
        ahora = self._now()
        out = {"fichas": [], "urgentes": [], "editadas": [], "resumen": None}
        with self.estado._lock:
            data = self.estado.load()
            primera = not data.get("iniciado")
            ohq = None
            try:
                ohq = self.fetch_ohq()
            except Exception as exc:
                log.warning("vigilancia: OHQ no responde: %s", exc)
            try:
                tasks = self.hermes_tasks()
            except Exception as exc:
                log.warning("vigilancia: kanban no leído: %s", exc)
                tasks = []
            h_sols, h_items = solicitudes_hermes(tasks, self.people, ahora)
            sols = [self._normalizar(s) for s in ((ohq or {}).get("solicitudes") or [])] + h_sols
            items = [dict(e, desde=_epoch(e.get("desde"))) for e in ((ohq or {}).get("elementos") or [])]
            items += h_items + self._refs(data, (ohq or {}).get("refs_hermes") or [])
            self.ultimo = {"elementos": items, "solicitudes": sols, "personas": (ohq or {}).get("personas") or []}
            for s in sols:
                rid = self._registrar(data, s, ahora)
                if rid and not primera:
                    data["cola_fichas"].append(rid)
            out["fichas"] = self._publicar_fichas(data, ahora)
            out["editadas"] = self._seguimiento(data, ohq is not None, ahora)
            if not primera:
                out["urgentes"] = self._urgentes(data, items, ahora)
            out["resumen"] = self._resumen(data, items, ahora)
            data["iniciado"] = True
            self.estado.save(data)
        return out

    def _normalizar(self, s: dict) -> dict:
        return dict(s, creado=_epoch(s.get("creado")))

    def _refs(self, data: dict, refs: list) -> list[dict]:
        """Tarjetas de Hermes citadas por el bot: si no existen, es un «dijo hecho sin hacerlo»."""
        out = []
        for r in refs:
            tid = r.get("tarjeta")
            if not tid:
                continue
            if tid not in data["refs"]:
                try:
                    data["refs"][tid] = bool((self.show_card(tid) or {}).get("task"))
                except Exception:
                    continue
            if not data["refs"][tid]:
                nombre = (r.get("persona") or {}).get("nombre") or "alguien"
                out.append({"id": r["id"], "tipo": "hecho_sin_hacer", "gravedad": "urgente", "persona": r.get("persona"),
                            "desde": _epoch(r.get("desde")), "marca": r.get("marca"), "enlace": r.get("enlace") or {},
                            "resumen": f"El bot le dio a {nombre} la tarjeta {corto(tid)}, que no existe",
                            "detalle": r.get("detalle") or "", "acciones": ["crear_tarea", "responder", "resuelto",
                                                                            "ignorar"]})
        return out

    # solicitudes ---------------------------------------------------------------------------------------------
    def _duplicada(self, data: dict, s: dict, ahora: float) -> str | None:
        from .triaje import similitud
        tg = (s.get("persona") or {}).get("telegram_id")
        best, score = None, 0.0
        for rid, f in data["fichas"].items():
            if (f.get("persona") or {}).get("telegram_id") != tg or ahora - (f.get("creado") or ahora) > VENTANA_H * 3600:
                continue
            sc = max(similitud(s.get("titulo") or "", f.get("titulo") or ""),
                     similitud(s.get("cita") or s.get("titulo") or "", f.get("cita") or f.get("titulo") or ""))
            if sc >= self.umbral and sc > score:
                best, score = rid, sc
        return best

    def _registrar(self, data: dict, s: dict, ahora: float) -> str | None:
        """Devuelve el req nuevo si hace falta ficha; None si la fuente ya estaba (o se juntó a otra ficha)."""
        key = s["id"]
        if key in data["fuentes"]:
            f = data["fichas"].get(data["fuentes"][key])
            if f is not None:
                f.setdefault("ohq", {})[key] = s.get("estado")
            return None
        if s.get("creado") and ahora - s["creado"] > VENTANA_H * 3600:
            return None
        dup = self._duplicada(data, s, ahora) if (s.get("persona") or {}).get("telegram_id") else None
        if dup:
            f = data["fichas"][dup]
            data["fuentes"][key] = dup
            f["fuentes"] = list(dict.fromkeys((f.get("fuentes") or []) + [key]))
            f["bots"] = list(dict.fromkeys((f.get("bots") or []) + [self._bot(s)]))
            if s.get("tarjeta") and not f.get("tid"):
                f["tid"], f["board"] = s["tarjeta"], s.get("board") or ""
                self._vincular(dup, s["tarjeta"])
            f["editar"] = True
            return None
        proyecto = proyecto_de(s.get("marca"), self.cfg.proyecto_por_defecto)
        try:
            rec = self.registro.crear(proyecto, s.get("titulo") or "solicitud", "telegram",
                                      tarjetas=[s["tarjeta"]] if s.get("tarjeta") else ())
        except Exception as exc:
            log.warning("vigilancia: no pude registrar la solicitud %s: %s", key, exc)
            return None
        rid = rec["id"]
        data["fuentes"][key] = rid
        data["fichas"][rid] = {
            "req": rid, "persona": s.get("persona") or {}, "titulo": s.get("titulo"), "cita": s.get("cita"),
            "marca": s.get("marca"), "proyecto": proyecto, "bots": [self._bot(s)], "fuentes": [key],
            "prioridad": prioridad_estimada(f"{s.get('titulo')} {s.get('cita')}", s.get("prioridad")),
            "creado": s.get("creado") or ahora, "estado": "nueva", "decision": "nueva",
            "tid": s.get("tarjeta"), "board": s.get("board") or "", "ohq": {key: s.get("estado")}}
        return rid

    @staticmethod
    def _bot(s: dict) -> str:
        return {"avisos": "Avisos", "carriles": "Carriles", "hermes": "Hermes"}.get(s.get("bot"), s.get("bot") or "?")

    def _vincular(self, rid: str, tid: str) -> None:
        try:
            self.registro.vincular(rid, tid)
        except Exception as exc:
            log.info("vigilancia: %s no vinculada a %s: %s", tid, rid, exc)

    def _publicar_fichas(self, data: dict, ahora: float) -> list[str]:
        cola, enviadas = list(dict.fromkeys(data["cola_fichas"])), []
        tope = int(valor("equipo.fichas_hora") or 10)
        resto = []
        for rid in cola:
            f = data["fichas"].get(rid)
            if f is None or f.get("msg"):
                continue
            if not self._cupo(data["envios_fichas"], tope, ahora):
                resto.append(rid)
                continue
            sent = self._enviar(data, texto_ficha(f, ahora), self._spec(f, botones_ficha(f)))
            if sent:
                f["msg"] = {"chat": str(sent.get("chat_id")), "id": sent.get("message_id")}
                data["envios_fichas"].append(ahora)
                enviadas.append(rid)
            else:
                resto.append(rid)
        if len(resto) > 1 and self._cupo(data["envios_fichas"], tope + 1, ahora):  # el resto, en un solo mensaje
            lines = [f"🙋 {len(resto)} solicitudes más (tope de {tope}/h; las verás una a una en el resumen):"]
            lines += [f"• {r} · {(data['fichas'][r].get('persona') or {}).get('nombre')} · "
                      f"{truncate(data['fichas'][r].get('titulo'), 70)}" for r in resto[:15]]
            if self._enviar(data, "\n".join(lines), []):
                data["envios_fichas"].append(ahora)
                for r in resto:
                    data["fichas"][r]["agrupada"] = True
                resto = []
        data["cola_fichas"] = resto
        return enviadas

    @staticmethod
    def _spec(obj: dict, rows: list) -> list:
        """Botones con su referencia (`index`): el req de la ficha o el id del elemento."""
        ref = obj.get("req") or obj.get("id")
        extra = {"proyecto": obj["proyecto"]} if obj.get("req") and obj.get("proyecto") else {}
        return [[dict(b, index=b.get("index") or ref, **extra) for b in row] for row in rows]

    def estado_derivado(self, f: dict, ohq_ok: bool) -> str:
        if f.get("decision") == "rechazada":
            return "rechazada"
        if f.get("tid"):
            try:
                show = self.show_card(f["tid"]) or {}
            except Exception:
                show = {}
            task = show.get("task") or {}
            st = task.get("status")
            decidiendo = f.get("decision") in (None, "nueva", "luego", "asignada")
            if st and decidiendo and task.get("assignee") == "oscar" and st in ("todo", "ready", "blocked"):
                return f.get("decision") or "nueva"   # [IDEA] esperando a Oscar: aún no está aprobada
            if st:
                from .peticiones import integrada
                if st in ("done", "review", "archived") and integrada(show):
                    return "entregada"
                if st == "archived":
                    return "archivada"
                if st in ("done", "review"):
                    return "hecha"
                if st == "running":
                    return "en curso"
                if st in ("blocked", "triage"):
                    return "atascada"
                return "aprobada"
            return f.get("estado") or "aprobada"
        vals = [v for v in (f.get("ohq") or {}).values() if v]
        if ohq_ok and vals:
            orden = ("hecha", "en curso", "atascada", "aprobada")
            v = next((x for x in orden if x in vals), None)
            if v == "hecha":
                return "entregada"  # el kanban de equipo no distingue hecha de entregada
            if v:
                return v
        if f.get("decision") in ("luego", "asignada", "aprobada"):
            return f["decision"]
        return f.get("estado") or "nueva"

    def _seguimiento(self, data: dict, ohq_ok: bool, ahora: float) -> list[str]:
        editadas = []
        for rid, f in data["fichas"].items():
            if f.get("estado") in FINALES and not f.get("editar"):
                continue
            nuevo = self.estado_derivado(f, ohq_ok)
            if nuevo == f.get("estado") and not f.get("editar"):
                continue
            f["estado"], f["editar"] = nuevo, False
            if nuevo in FINALES:
                f["cerrada"] = True
            msg = f.get("msg") or {}
            if msg.get("id"):
                try:
                    if self.edit(msg["chat"], msg["id"], texto_ficha(f, ahora), self._spec(f, botones_ficha(f))):
                        editadas.append(rid)
                except Exception as exc:
                    log.info("vigilancia: ficha %s sin editar: %s", rid, exc)
        return editadas

    # fallos ----------------------------------------------------------------------------------------------------
    def _visto(self, data: dict, el_id: str, ahora: float) -> bool:
        it = data["items"].get(el_id)
        if not it:
            return False
        if it.get("estado") == "ignorado" and ahora > (it.get("hasta") or 0):
            return False
        return True

    def _urgentes(self, data: dict, items: list[dict], ahora: float) -> list[str]:
        tope, enviados = int(valor("equipo.urgentes_hora") or 5), []
        nuevos = [e for e in items if e.get("gravedad") == "urgente" and not self._visto(data, e["id"], ahora)]
        cola = [e for e in data["cola"] if not self._visto(data, e["id"], ahora)]
        pend = list({e["id"]: e for e in cola + nuevos}.values())
        resto = []
        for el in pend:
            if not self._cupo(data["envios"], tope, ahora):
                resto.append(el)
                continue
            if self._enviar(data, texto_elemento(el, ahora), self._spec(el, botones_elemento(el))):
                data["envios"].append(ahora)
                data["items"][el["id"]] = {"estado": "avisado", "visto": ahora, "el": el}
                enviados.append(el["id"])
            else:
                resto.append(el)
        if len(resto) > 1 and len(data["envios"]) <= tope:  # tope agotado: un solo mensaje agrupado (una vez)
            if not data.get("agrupado_en") or ahora - data["agrupado_en"] > 3600:
                lines = [f"🚨 {len(resto)} avisos urgentes más esta hora (tope {tope}/h):"]
                lines += [f"• {(e.get('persona') or {}).get('nombre')}: {truncate(e.get('resumen'), 90)}"
                          for e in resto[:15]]
                if self._enviar(data, "\n".join(lines), []):
                    data["agrupado_en"] = ahora
                    for e in resto:
                        data["items"][e["id"]] = {"estado": "avisado", "visto": ahora, "el": e}
                    resto = []
        data["cola"] = resto
        return enviados

    def _due_slot(self, data: dict, ahora: float) -> tuple[str, int] | None:
        now = datetime.fromtimestamp(ahora, MADRID)
        ventana = int(valor("avisos.resumen_ventana_horas") or 2)
        for h in horas_resumen():
            key = f"{now:%Y-%m-%d}T{h:02d}"
            if h <= now.hour < h + ventana and key not in data["slots"]:
                return key, h
        return None

    def _resumen(self, data: dict, items: list[dict], ahora: float) -> str | None:
        slot = self._due_slot(data, ahora)
        if slot is None:
            return None
        key, hora = slot
        primera = hora == horas_resumen()[0]
        pend = [e for e in items if not self._visto(data, e["id"], ahora)]
        por_persona: dict[str, list] = {}
        for e in pend:
            por_persona.setdefault((e.get("persona") or {}).get("nombre") or "sin identificar", []).append(e)
        abiertas: dict[str, list] = {}
        if primera:
            for f in data["fichas"].values():
                if f.get("estado") not in FINALES:
                    abiertas.setdefault((f.get("persona") or {}).get("nombre") or "sin identificar", []).append(f)
        permisos = self.permisos() if primera else []
        if not por_persona and not abiertas and not permisos:
            data["slots"] = (data["slots"] + [key])[-30:]
            return None  # sin nada que contar no sale mensaje
        textos = []
        cabecera = [f"👥 Resumen del equipo de las {hora}:00"]
        if abiertas:
            cabecera.append("\nSolicitudes abiertas por persona:")
            for nombre, fs in sorted(abiertas.items()):
                fs.sort(key=lambda f: f.get("creado") or ahora)
                cabecera.append(f"• {nombre} ({len(fs)}): " + "; ".join(
                    f"{f['req']} {truncate(f.get('titulo'), 40)} · {f.get('estado')} · {hace(f.get('creado'), ahora)}"
                    for f in fs[:6]) + (f" … y {len(fs) - 6} más" if len(fs) > 6 else ""))
        if permisos:
            cabecera.append("\n🔐 Permisos que no cuadran:")
            cabecera += [f"• {p}" for p in permisos]
        if not por_persona:
            cabecera.append("\nSin fallos nuevos del equipo.")
        sent_head = self._enviar(data, "\n".join(cabecera), [])
        if sent_head is None:
            return None
        textos.append("\n".join(cabecera))
        for nombre, els in sorted(por_persona.items()):
            lines = [f"{nombre} · {len(els)} aviso(s):"]
            spec = []
            for n, el in enumerate(els, 1):
                lines.append(f"{n}. {TITULO_TIPO.get(el['tipo'], el['tipo'])} · {hace(el.get('desde'), ahora)}: "
                             f"{truncate(el.get('resumen'), 140)}")
                if n <= BOTONES_POR_MENSAJE:
                    spec += botones_elemento(el, n)
            if len(els) > BOTONES_POR_MENSAJE:
                lines.append(f"(botones para los {BOTONES_POR_MENSAJE} primeros)")
            persona = els[0].get("persona") or {}
            if persona.get("telegram_id"):
                spec.append([{"text": f"💬 Responder a {nombre}", "action": RESPONDER, "index": els[0]["id"]}])
            if self._enviar(data, "\n".join(lines), spec):
                for el in els:
                    data["items"][el["id"]] = {"estado": "avisado", "visto": ahora, "el": el}
                textos.append("\n".join(lines))
        data["slots"] = (data["slots"] + [key])[-30:]
        return "\n\n".join(textos)

    # permisos ----------------------------------------------------------------------------------------------------
    def permisos(self) -> list[str]:
        """people.yaml (bot de carriles) frente a ohq_people (OHQ). Solo con datos de OHQ; sin datos personales."""
        ohq = {str(p.get("telegram_id")): p for p in self.ultimo.get("personas") or [] if p.get("telegram_id")}
        if not ohq:
            return []
        out = []
        yaml_ = getattr(self.people, "_by_id", {})
        owner = str(getattr(self.people, "owner_id", ""))
        for uid, p in yaml_.items():
            o = ohq.get(uid)
            if o is None:
                out.append(f"{p.name}: en people.yaml (bot de carriles) pero no en ohq_people")
            elif not o.get("activo", True):
                out.append(f"{p.name}: desactivada en ohq_people pero con acceso al bot de carriles")
        for uid, o in ohq.items():
            if uid != owner and o.get("activo", True) and uid not in yaml_:
                out.append(f"{o.get('nombre')}: en ohq_people pero sin acceso al bot de carriles (people.yaml)")
        return out

    # botones ----------------------------------------------------------------------------------------------------
    def ficha(self, rid: str) -> dict | None:
        return self.estado.load()["fichas"].get(rid)

    def elemento(self, el_id: str) -> dict | None:
        data = self.estado.load()
        it = (data["items"].get(el_id) or {}).get("el")
        return it or next((e for e in self.ultimo.get("elementos") or [] if e.get("id") == el_id), None)

    def actualizar_ficha(self, rid: str, **cambios) -> dict | None:
        with self.estado._lock:
            data = self.estado.load()
            f = data["fichas"].get(rid)
            if f is None:
                return None
            f.update(cambios)
            f["estado"] = self.estado_derivado(f, True) if "estado" not in cambios else cambios["estado"]
            if f["estado"] in FINALES:
                f["cerrada"] = True
            self.estado.save(data)
            return dict(f)

    def marcar_elemento(self, el_id: str, estado: str) -> None:
        with self.estado._lock:
            data = self.estado.load()
            it = data["items"].setdefault(el_id, {})
            it.update(estado=estado, visto=self._now())
            if estado == "ignorado":
                it["hasta"] = self._now() + IGNORAR_DIAS * 86400
            it.setdefault("el", self.elemento(el_id))
            self.estado.save(data)

    def vincular_tarjeta(self, rid: str, tid: str, board: str, suscrito: bool) -> dict | None:
        self._vincular(rid, tid)
        with self.estado._lock:
            data = self.estado.load()
            data["fuentes"][f"hermes:{tid}"] = rid
            self.estado.save(data)
        return self.actualizar_ficha(rid, tid=tid, board=board, suscrito=suscrito, decision="aprobada",
                                     estado="aprobada")
