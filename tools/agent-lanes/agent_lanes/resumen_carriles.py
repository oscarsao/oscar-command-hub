"""Resumen diario de las 9:00 por carril (t_f8f58acb): una línea por carril (qué hace, cola, bloqueos) y debajo las
decisiones pendientes de Oscar con @mención y botón. Complementa al resumen de las 8:00 por marca (brief/brief_diario).

Se envía al tema Atención (con @mención) y, sin él, al DM de Oscar. Lo dispara `CommandCenter.veto_tick`; la fecha
enviada se guarda en un JSON (un reinicio no lo repite) y, si el runner arranca tarde, se recupera hasta las 11:00.
Solo lee el kanban: no cambia nada.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import yaml

from .ajustes_hub import valor
from .config import ROOT
from .formato_aviso import PIE_TEXTO_LIBRE
from .notices import hours_ago, truncate

log = logging.getLogger("agent_lanes")

MADRID = ZoneInfo("Europe/Madrid")
# Valores por defecto; los vigentes se editan con /ajustes (ajustes_hub: avisos.*).
SLOT_HOUR = 9
WINDOW_HOURS = 2     # si el runner estaba parado a las 9:00, se envía hasta las 11:00
DECISIONS_SHOWN = 5


def _hour(hour: int | None) -> int:
    v = valor("avisos.hora_parte") if hour is None else hour
    return SLOT_HOUR if v is None else v


def lane_row(name: str, h, kind: str = "implement") -> dict:
    """Estado de un carril leyendo su tablero: lo que hace, cola (ready) y bloqueos (blocked + triage)."""
    def titles(status):
        return [t.get("title") or t.get("id") for t in h.list_status(name, status)]
    running, ready = titles("running"), titles("ready")
    return {"name": name, "kind": kind, "doing": running, "queue": len(ready),
            "blocked": len(titles("blocked")) + len(titles("triage"))}


def collect_rows(lanes: dict, hermes_for: Callable) -> list[dict]:
    rows = []
    for name, lane in lanes.items():
        try:
            rows.append(lane_row(name, hermes_for(lane.board), getattr(lane, "kind", "implement")))
        except Exception as exc:  # un carril ilegible no tumba el resumen
            log.warning("resumen 9:00: carril %s no leído: %s", name, exc)
            rows.append({"name": name, "error": True})
    return rows


def render_lane(row: dict) -> str:
    if row.get("error"):
        return f"⚪ {row['name']}: sin datos (no pude leer su tablero)"
    doing = row["doing"]
    what = truncate(" + ".join(truncate(t, 40) for t in doing[:2]), 90) + (f" (+{len(doing) - 2})" if len(doing) > 2 else "") \
        if doing else "libre"
    icon = "🔵" if doing else ("🟡" if row["blocked"] else "🟢")
    return f"{icon} {row['name']}: {what} · cola {row['queue']} · bloqueos {row['blocked']}"


def render_summary(rows: list[dict], decisions: list, guard_n: int, now: float, who: str = "", extra: str = "",
                   hour: int | None = None) -> str:
    """El mensaje: una línea por carril (también los vacíos) y las decisiones de Oscar con su próximo paso.
    `extra`: líneas del integrador (estado de sus consolas y PR de sesiones interactivas), ya compuestas."""
    hour, shown = _hour(hour), valor("avisos.decisiones_en_resumen")
    lines = [f"☀️ Resumen de las {hour}:00 por carril"]
    lines += [render_lane(r) for r in rows] or ["(no hay carriles configurados)"]
    if guard_n:
        lines.append(f"🧑‍💻 {guard_n} en la cola del integrador (/integrador)")
    if extra:
        lines.append(extra)
    lines.append("")
    if not decisions:
        lines.append("Decisiones pendientes de Oscar: ninguna. ➡️ Próximo paso: ninguno · Lo da: nadie")
        return "\n".join(lines)
    lines.append(f"{who + ' ' if who else ''}Decisiones pendientes de Oscar ({len(decisions)}):")
    for p in decisions[:shown]:
        lines.append(f"• {p.tid} · {truncate((p.task or {}).get('title'), 60)} · hace {hours_ago(p.since, now)} h")
    if len(decisions) > shown:
        lines.append(f"… y {len(decisions) - shown} más")
    lines.append("➡️ Próximo paso: pulsa «Ver decisiones» y responde con los botones · Lo da: Oscar")
    lines.append(PIE_TEXTO_LIBRE)  # formato único de aviso (t_211c9902): botones + texto libre
    return "\n".join(lines)


def due_date(now: datetime, sent: set[str], hour: int | None = None) -> str | None:
    """Clave AAAA-MM-DD si toca enviar (desde `hour`:00 hasta el margen configurado, hora de Madrid, todos los días) y no
    está enviada."""
    now, hour = now.astimezone(MADRID), _hour(hour)
    key = f"{now:%Y-%m-%d}"
    return key if hour <= now.hour < hour + valor("avisos.resumen_ventana_horas") and key not in sent else None


class DailyLaneSummary:
    """`rows()` -> filas por carril; `decisions()` -> Pending de Oscar; `guard_n()` -> tamaño de la cola del
    integrador; `send(text, with_button)` -> True si salió. Cualquier fallo se reintenta en la siguiente pasada."""

    def __init__(self, rows: Callable[[], list], decisions: Callable[[], list], guard_n: Callable[[], int],
                 send: Callable[[str, bool], bool], state_path: Path | str, *, who: str = "",
                 now: Callable[[], datetime] = lambda: datetime.now(MADRID), hour: int | None = None,
                 extra: Callable[[], str] = lambda: ""):
        self.rows, self.decisions, self.guard_n, self.send = rows, decisions, guard_n, send
        self.state_path, self.who, self._now = Path(state_path), who, now
        self.hour, self.extra = hour, extra

    def _sent(self) -> set[str]:
        try:
            return set(json.loads(self.state_path.read_text(encoding="utf-8")).get("sent") or ())
        except (OSError, ValueError, AttributeError):
            return set()

    def tick(self) -> str | None:
        """Una comprobación. Devuelve el texto enviado (o None)."""
        now = self._now()
        key = due_date(now, self._sent(), self.hour)
        if not key:
            return None
        decisions = self.decisions()
        text = render_summary(self.rows(), decisions, self.guard_n(), now.timestamp(), self.who if decisions else "",
                              self.extra(), self.hour)
        if not self.send(text, bool(decisions)):
            return None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"sent": sorted(self._sent() | {key})[-14:]}), encoding="utf-8")
        return text


# --- resumen diario de avisos INFO (F1-04, 3c) ------------------------------------------------------------------------
# Los avisos informativos que hoy llegan sueltos o repetidos (vigilancias apagadas, tareas relanzadas, «limpieza
# pendiente», workflows sin `origen`, batería) se apuntan aquí y salen en UN mensaje al día. ALTA/URGENTE no pasan por
# aquí: siguen saliendo al momento. El almacén es un JSON en .state (sobrevive a reinicios y lo puede escribir otro
# proceso, p. ej. el monitor en F1-06); un evento con la misma `clave` solo cuenta una vez al día.

TIPOS_RESUMEN = {"vigilancia": "🔌 Vigilancias apagadas", "relanzada": "🔁 Tareas programadas relanzadas",
                 "limpieza": "🧹 Limpieza pendiente", "workflow_sin_origen": "🧩 Workflows sin origen",
                 "bateria": "🔋 Batería del PC", "hermes_config": "🧭 Config de Hermes fuera de la plantilla",
                 "pr_sin_cierra": "🔗 PR de consola sin «Cierra: t_x»"}
RESUMEN_INFO_HOUR = 9
RESUMEN_INFO_MAX = 40   # líneas por resumen (el resto se cuenta)


class DiarioInfo:
    """`anotar(tipo, texto, clave)` guarda; `tick()` envía el resumen del día una vez, desde `hour` (Madrid), por
    `send(text) -> bool`. Un envío fallido se reintenta en la siguiente pasada. Sin eventos no se envía nada."""

    def __init__(self, send: Callable[[str], bool], state_path: Path | str, *, hour: int = RESUMEN_INFO_HOUR,
                 now: Callable[[], datetime] = lambda: datetime.now(MADRID)):
        self.send, self.path, self.hour, self._now = send, Path(state_path), hour, now

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("eventos", [])
        data.setdefault("vistas", {})
        data.setdefault("sent", [])
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def anotar(self, tipo: str, texto: str, clave: str | None = None) -> bool:
        """True si queda para el resumen; False si el tipo no es de resumen (quien llama lo envía como siempre)."""
        if tipo not in TIPOS_RESUMEN:
            return False
        dia = f"{self._now().astimezone(MADRID):%Y-%m-%d}"
        data = self._load()
        key = f"{tipo}:{clave or texto}"
        if data["vistas"].get(key) == dia:
            return True  # ya apuntado hoy: una vez al día
        data["vistas"][key] = dia
        data["vistas"] = dict(list(data["vistas"].items())[-500:])
        data["eventos"].append({"tipo": tipo, "texto": truncate(texto, 300)})
        self._save(data)
        return True

    def render(self, eventos: list[dict]) -> str:
        lines = [f"🗞 Resumen diario de avisos informativos ({len(eventos)})"]
        shown = 0
        for tipo, titulo in TIPOS_RESUMEN.items():
            grupo = [e for e in eventos if e.get("tipo") == tipo]
            if not grupo:
                continue
            lines.append(f"\n{titulo} ({len(grupo)})")
            for e in grupo:
                if shown >= RESUMEN_INFO_MAX:
                    break
                lines.append(f"• {e.get('texto')}")
                shown += 1
        if len(eventos) > shown:
            lines.append(f"\n… y {len(eventos) - shown} más")
        lines.append("\nLo urgente (caídas, producción) no espera a este resumen: llega al momento.")
        return "\n".join(lines)

    def tick(self) -> str | None:
        now = self._now().astimezone(MADRID)
        key = f"{now:%Y-%m-%d}"
        data = self._load()
        if now.hour < self.hour or key in data["sent"] or not data["eventos"]:
            return None
        enviados = len(data["eventos"])
        text = self.render(data["eventos"])
        try:
            ok = self.send(text)
        except Exception as exc:
            log.warning("resumen INFO: envío falló: %s", exc)
            ok = False
        if not ok:
            return None
        data = self._load()  # pudo apuntarse algo mientras se enviaba: solo se quitan los enviados
        data["eventos"] = data["eventos"][enviados:]
        data["sent"] = sorted(set(data["sent"]) | {key})[-14:]
        self._save(data)
        return text


ACTIVO: "DiarioInfo | None" = None  # lo instala el runner; sin él `anotar` no hace nada (tests, consolas, --once)


def instalar(instancia: "DiarioInfo | None") -> None:
    global ACTIVO
    ACTIVO = instancia


def anotar(tipo: str, texto: str, clave: str | None = None) -> bool:
    """Apunta un aviso INFO para el resumen del día. False sin resumen instalado o tipo ajeno (nunca lanza)."""
    try:
        return bool(ACTIVO) and ACTIVO.anotar(tipo, texto, clave)
    except Exception as exc:
        log.warning("resumen INFO: no se pudo apuntar %s: %s", tipo, exc)
        return False


# --- resumen de preguntas de carriles a las 9, 14 y 20 h (t_a37ab14b) ---------------------------------------------------
# Un mensaje corto (máx. MAX_LINEAS líneas) con las preguntas que esperan a Oscar y, debajo, las más antiguas con sus
# botones de siempre (⭐ la recomendada). Solo sale si algo cambió desde el último parte enviado: cambian las preguntas
# abiertas (alguna nueva o resuelta), los bloqueos por tipo o el estado del runner. «En marcha» y «hechas» informan pero no
# disparan el envío por sí solas. Ajustes en integrador_visibilidad.yaml, sección `resumen_preguntas` (se relee en cada
# pasada). Solo lee el kanban; no cambia nada.

MAX_LINEAS = 12
LINEA_MAX = 200
SETTINGS_PATH = ROOT / "integrador_visibilidad.yaml"
SECCIONES = ("preguntas", "nuevas", "en_marcha", "bloqueadas", "runner")  # tipos de línea con interruptor
RESUMEN_DEFAULTS = {"activo": True, "horas": [9, 14, 20], "preguntas_max": 5, "antiguas_horas": 24,
                    "tipos": {s: True for s in SECCIONES}}


def load_resumen_config(path: Path | str | None = None) -> dict:
    """Ajustes de `resumen_preguntas` en el YAML; lo que falte o no sea válido vale el default."""
    out = {**RESUMEN_DEFAULTS, "tipos": dict(RESUMEN_DEFAULTS["tipos"]), "horas": list(RESUMEN_DEFAULTS["horas"])}
    try:
        raw = (yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}).get("resumen_preguntas")
    except (OSError, yaml.YAMLError, AttributeError):
        return out
    if not isinstance(raw, dict):
        return out
    es_int = lambda v: isinstance(v, int) and not isinstance(v, bool)
    if isinstance(raw.get("activo"), bool):
        out["activo"] = raw["activo"]
    horas = raw.get("horas")
    if isinstance(horas, list):
        validas = sorted({h for h in horas if es_int(h) and 0 <= h <= 23})
        if validas:
            out["horas"] = validas
    if es_int(raw.get("preguntas_max")) and 0 <= raw["preguntas_max"] <= 20:
        out["preguntas_max"] = raw["preguntas_max"]
    if isinstance(raw.get("antiguas_horas"), (int, float)) and not isinstance(raw["antiguas_horas"], bool) \
            and raw["antiguas_horas"] > 0:
        out["antiguas_horas"] = float(raw["antiguas_horas"])
    if isinstance(raw.get("tipos"), dict):
        for s in SECCIONES:
            if isinstance(raw["tipos"].get(s), bool):
                out["tipos"][s] = raw["tipos"][s]
    return out


def tipo_bloqueo(p) -> str:
    """Tipo de una tarjeta bloqueada: guard | atascada | pregunta | el `block_kind` del carril | otro."""
    if getattr(p, "guard", False):
        return "guard"
    state = getattr(p, "state", "")
    if state == "stuck":
        return "atascada"
    if state == "needs_input":
        return "pregunta"
    return str(getattr(p, "block_kind", None) or "otro")


def contar_bloqueadas(bloqueadas: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in bloqueadas:
        t = tipo_bloqueo(p)
        out[t] = out.get(t, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def contar_antiguas(preguntas: list, now: float, horas: float) -> int:
    """Preguntas que llevan más de `horas` h esperando (las que no tienen fecha no cuentan como antiguas)."""
    return sum(1 for p in preguntas if getattr(p, "since", None) and now - p.since > horas * 3600)


def snapshot(preguntas: list, bloqueadas: list, runner: str) -> dict:
    """Lo que, si cambia, justifica un parte nuevo."""
    return {"preguntas": sorted(p.tid for p in preguntas), "bloqueadas": contar_bloqueadas(bloqueadas), "runner": runner}


def render_resumen(preguntas: list, bloqueadas: list, rows: list[dict], runner: str, *, now: float, hora: int,
                   nuevas: int, hechas: int, cfg: dict, con_botones: int = 0) -> str:
    """El parte, de MAX_LINEAS líneas como mucho. Las secciones apagadas en `cfg['tipos']` no salen."""
    tipos = cfg["tipos"]
    lines = [f"🗓 Resumen de las {hora}:00 · preguntas de carriles"]
    if tipos.get("preguntas"):
        if preguntas:
            viejas = contar_antiguas(preguntas, now, cfg["antiguas_horas"])
            mas_antigua = min(preguntas, key=lambda p: (p.since or now, p.tid))
            lines.append(f"❓ {len(preguntas)} pregunta{'s' if len(preguntas) != 1 else ''} esperan · {viejas} con más de "
                         f"{int(cfg['antiguas_horas'])} h · la más antigua hace {hours_ago(mas_antigua.since, now)} h "
                         f"({mas_antigua.tid})")
        else:
            lines.append("❓ Ninguna pregunta espera tu respuesta")
    if tipos.get("nuevas"):
        lines.append(f"🆕 Desde el último parte: {nuevas} pregunta{'s' if nuevas != 1 else ''} nueva"
                     f"{'s' if nuevas != 1 else ''} · {hechas} tarjeta{'s' if hechas != 1 else ''} hecha"
                     f"{'s' if hechas != 1 else ''}")
    if tipos.get("en_marcha"):
        activos = [f"{r['name']} {len(r['doing'])}" for r in rows if r.get("doing")]
        total = sum(len(r["doing"]) for r in rows if r.get("doing"))
        lines.append(f"🔵 En marcha: {total}" + (f" ({', '.join(activos[:4])}{'…' if len(activos) > 4 else ''})"
                                                  if activos else ""))
    if tipos.get("bloqueadas"):
        por_tipo = contar_bloqueadas(bloqueadas)
        lines.append(f"🚧 Bloqueadas: {len(bloqueadas)}" + (" (" + " · ".join(f"{t} {n}" for t, n in por_tipo.items()) + ")"
                                                              if por_tipo else ""))
    if tipos.get("runner"):
        lines.append(f"⚙️ Runner: {runner}")
    if con_botones:
        lines.append(f"⬇️ Debajo, {con_botones} con sus botones (⭐ = la recomendada)")
    return "\n".join(truncate(l, LINEA_MAX) for l in lines[:MAX_LINEAS])


def due_slot(now: datetime, sent: set[str], horas: list[int]) -> tuple[str, int] | None:
    """(clave AAAA-MM-DD HH, hora) del primer parte que toca y no está enviado: desde su hora hasta el margen
    `avisos.resumen_ventana_horas` (hora de Madrid)."""
    now = now.astimezone(MADRID)
    ventana = valor("avisos.resumen_ventana_horas")
    ventana = 2 if ventana is None else ventana
    for h in horas:
        key = f"{now:%Y-%m-%d} {h:02d}"
        if h <= now.hour < h + ventana and key not in sent:
            return key, h
    return None


class ResumenPreguntas:
    """`decisions()` -> Pending en needs_input (más antigua primero); `blocked()` -> Pending bloqueadas (cualquier
    tipo); `rows()` -> filas por carril; `done_since(ts)` -> tarjetas hechas desde `ts`; `runner()` -> texto de estado;
    `send(text)` -> True si salió; `send_cards(pendings)` pinta las preguntas con sus botones. Un fallo se reintenta en
    la pasada siguiente y no marca el parte como enviado."""

    def __init__(self, decisions: Callable[[], list], blocked: Callable[[], list], rows: Callable[[], list],
                 done_since: Callable[[float], int], runner: Callable[[], str], send: Callable[[str], bool],
                 send_cards: Callable[[list], None], state_path: Path | str, *,
                 now: Callable[[], datetime] = lambda: datetime.now(MADRID),
                 config: Callable[[], dict] = load_resumen_config):
        self.decisions, self.blocked, self.rows, self.done_since, self.runner = decisions, blocked, rows, done_since, runner
        self.send, self.send_cards, self.state_path, self._now, self.config = send, send_cards, Path(state_path), now, config

    def _load(self) -> dict:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"sent": [], "last": None}
        raw = raw if isinstance(raw, dict) else {}
        last = raw.get("last") if isinstance(raw.get("last"), dict) else None
        return {"sent": [s for s in raw.get("sent") or () if isinstance(s, str)], "last": last}

    def _save(self, data: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {**data, "sent": sorted(set(data["sent"]))[-30:]}
        self.state_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def tick(self) -> str | None:
        """Una comprobación. Devuelve el texto enviado, o None (no toca, sin cambios o falló el envío)."""
        cfg = self.config()
        if not cfg["activo"]:
            return None
        now = self._now()
        data = self._load()
        slot = due_slot(now, set(data["sent"]), cfg["horas"])
        if not slot:
            return None
        key, hora = slot
        preguntas, bloqueadas, runner = self.decisions(), self.blocked(), self.runner()
        snap, last = snapshot(preguntas, bloqueadas, runner), data["last"]
        previo = (last or {}).get("snap")
        cambios = bool(preguntas or bloqueadas) if previo is None else snap != previo
        if not cambios:  # sin novedades: el hueco queda consumido y no se vuelve a evaluar
            self._save({**data, "sent": [*data["sent"], key]})
            return None
        ts = now.timestamp()
        desde = float((last or {}).get("at") or ts - 24 * 3600)
        nuevas = len(set(snap["preguntas"]) - set((previo or {}).get("preguntas") or ()))
        grupos = preguntas[:cfg["preguntas_max"]] if cfg["tipos"].get("preguntas") else []
        text = render_resumen(preguntas, bloqueadas, self.rows(), runner, now=ts, hora=hora, nuevas=nuevas,
                              hechas=self.done_since(desde), cfg=cfg, con_botones=len(grupos))
        if not self.send(text):
            return None
        self._save({"sent": [*data["sent"], key], "last": {"at": ts, "snap": snap}})
        if grupos:
            try:
                self.send_cards(grupos)
            except Exception as exc:  # el parte ya salió; las fichas siguen en /decisiones
                log.warning("resumen de preguntas: fichas no enviadas: %s", exc)
        return text
