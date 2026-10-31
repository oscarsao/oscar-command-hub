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

from .ajustes_hub import valor
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


def _opciones(p) -> str:
    """Opciones de la primera pregunta pendiente de la tarjeta como A) B) C) (la recomendada, marcada)."""
    try:
        q = next(iter(p.pending_questions()))
    except (AttributeError, StopIteration):
        return ""
    opts = [str(o) for o in (q.get("options") or [])][:4]
    rec = q.get("recommended")
    return " ".join(f"{chr(65 + i)}) {truncate(o, 40)}{' (recomendada)' if i == rec else ''}" for i, o in enumerate(opts))


def render_summary(rows: list[dict], decisions: list, guard_n: int, now: float, who: str = "", extra: str = "",
                   hour: int | None = None, tuyas: list[dict] | None = None) -> str:
    """El mensaje: una línea por carril (también los vacíos) y la sección «Decides tú» con lo que solo decide Oscar:
    las preguntas de los carriles (needs_input, con sus opciones A/B/C) y sus tarjetas «tuyo/decidir» (`tuyas`, ítems
    de lo_tuyo). Sin solapes, choques, «aprobar» ni cola del integrador (esa va en su propia línea de arriba).
    `extra`: líneas del integrador (estado de sus consolas y PR de sesiones interactivas), ya compuestas."""
    hour, shown = _hour(hour), valor("avisos.decisiones_en_resumen")
    tuyas = tuyas or []
    total = len(decisions) + len(tuyas)
    lines = [f"☀️ Resumen de las {hour}:00 por carril"]
    lines += [render_lane(r) for r in rows] or ["(no hay carriles configurados)"]
    if guard_n:
        lines.append(f"🧑‍💻 {guard_n} en la cola del integrador (/integrador)")
    if extra:
        lines.append(extra)
    lines.append("")
    if not total:
        lines.append("Decides tú: nada. ➡️ Próximo paso: ninguno · Lo da: nadie")
        return "\n".join(lines)
    lines.append(f"{who + ' ' if who else ''}Decides tú ({total}):")
    for p in decisions[:shown]:
        lines.append(f"• {p.tid} · {truncate((p.task or {}).get('title'), 60)} · hace {hours_ago(p.since, now)} h")
        if opciones := _opciones(p):
            lines.append(f"   {opciones}")
    for it in tuyas[:max(0, shown - len(decisions))]:
        lines.append(f"• {it['tid']} · {truncate(it.get('title') or '', 60)} · tuya"
                     + (f" · hace {hours_ago(it['since'], now)} h" if it.get("since") else ""))
    if total > shown:
        lines.append(f"… y {total - shown} más")
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
                 extra: Callable[[], str] = lambda: "", tuyas: Callable[[], list] = lambda: []):
        self.rows, self.decisions, self.guard_n, self.send = rows, decisions, guard_n, send
        self.tuyas = tuyas
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
        decisions, tuyas = self.decisions(), self.tuyas()
        text = render_summary(self.rows(), decisions, self.guard_n(), now.timestamp(),
                              self.who if decisions or tuyas else "", self.extra(), self.hour, tuyas)
        if not self.send(text, bool(decisions or tuyas)):
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
