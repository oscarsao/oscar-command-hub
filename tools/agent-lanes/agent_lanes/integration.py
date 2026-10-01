"""Tema de Integración (28-09): fichas de PR/merge/deploy con cabecera por riesgo y resumen fijado.

Todo aviso de integración (tarjeta done con ✅ Aprobar, "Listo para integrar", gates fallidos, en espera, fusionado,
desplegado, fallo de deploy) va al tema `integration_telegram` de lanes.yaml y, como copia espejo sincronizada, al DM
de Oscar. En el tema del carril solo queda una línea con enlace ("🚦 … → ver en Integración").

Ficha (HTML de Telegram, sin imágenes), primera línea en negrita y mayúsculas con el emoji de riesgo:

    🟠 OSCAR HQ · LISTO PARA INTEGRAR · PR #7
    t_1 · Arreglar login
    oscar-hq · lane/t_1 → master
    Para ti: <for_oscar o el título>
    Gates: ✔ sin conflictos con master · ✔ sin secretos · ✔ tests OK
    Riesgos: ninguno
    Deploy: fusionar NO despliega; el deploy es otro botón
    🚦 Listo para integrar PR #7 · pulsa para integrar
    🗂 Tarjeta · PR #7

Riesgo por política del integrador (`risk` en lanes.yaml): 🟢 staging · 🟠 deploy manual con botón · 🔴 producción ·
⚙️ sistema (claude-hub: el propio agent-lanes; fusionar no despliega, [🔁 Aplicar] hace el reinicio ordenado).
Sin `risk` explícito se asume lo peor: `on_merge` (el merge despliega) = 🔴 PRODUCCIÓN. ⏸ y ⛔ sustituyen al emoji.
"""
from __future__ import annotations

import html
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .notices import TEXT_MAX, truncate

log = logging.getLogger("agent_lanes")

CHANNEL = "integration"  # marca del registro de MessageStore cuyo aviso principal vive en el tema de Integración
FOR_OSCAR_MAX = 300
SUMMARY_TITLE_MAX = 40
SUMMARY_EMPTY = "✅ Nada pendiente de integrar"


@dataclass(frozen=True)
class Risk:
    emoji: str
    label: str


RISKS = {"staging": Risk("🟢", "STAGING"), "manual": Risk("🟠", "DEPLOY MANUAL"),
         "production": Risk("🔴", "PRODUCCIÓN"), "none": Risk("🟢", "SIN DEPLOY"),
         # claude-hub (plan D): el merge no despliega, pero cambia el código que ejecuta a los propios workers.
         "system": Risk("⚙️", "SISTEMA")}
WAITING, FAILED = "⏸", "⛔"


@dataclass(frozen=True)
class IntegrationRoute:
    """Destino de los avisos de integración (config de confianza) y políticas del integrador por carril."""
    target: tuple[str, str]
    policies: dict = field(default_factory=dict)


def risk_of(policy) -> Risk:
    """Riesgo de una política del integrador (duck typing: `risk`, `risk_label`, `deploy`). Sin política: SIN DEPLOY."""
    if policy is None:
        return RISKS["none"]
    level = str(getattr(policy, "risk", "") or "").strip().lower()
    if level not in RISKS:
        deploy = getattr(policy, "deploy", "none")
        level = {"on_merge": "production", "railway_up": "manual"}.get(deploy, "none")
    base = RISKS[level]
    label = str(getattr(policy, "risk_label", "") or "").strip()
    return Risk(base.emoji, label.upper() if label else base.label)


# Fase de la cabecera tras pulsar un botón, deducida de la línea de estado del integrador (textos estables).
_PHASES = (("🚀 desplegado", "DESPLEGADO"), ("🚀 desplegando", "DESPLEGANDO"), ("🔀 fusionando", "FUSIONANDO"),
           ("✅ fusionado", "FUSIONADO"), ("🚀 fusionado", "FUSIONADO"))
_STATE_PHASES = {"running": "EN CURSO", "done": "LISTO", "blocked": "REVISAR", "approved": "APROBADA"}


def phase_for(state: str, status: str | None) -> str:
    s = (status or "").strip()
    for prefix, phase in _PHASES:
        if s.startswith(prefix):
            return phase
    return _STATE_PHASES.get(state, str(state or "").upper())


def header(risk: Risk, phase: str, *, pr: int | None = None, override: str | None = None) -> str:
    """Primera línea: emoji (⏸/⛔ sustituyen al de riesgo) + ENTORNO · FASE · PR #N, en mayúsculas."""
    parts = [f"{override or risk.emoji} {risk.label}", phase.upper()]
    if pr:
        parts.append(f"PR #{int(pr)}")
    return " · ".join(parts)


def risks_from_files(policy, files) -> list[str]:
    """Riesgos que se ven en los archivos cambiados (antes de los gates: tarjeta done de review)."""
    files = [f for f in files or () if isinstance(f, str)]
    out = []
    alembic = str(getattr(policy, "alembic_versions", "") or "")
    if alembic and any(f.startswith(alembic) for f in files):
        out.append("⚠️ migración de Alembic")
    manual = tuple(getattr(policy, "manual_migrations", ()) or ())
    if manual and any(f.startswith(p) for p in manual for f in files):
        out.append("⚠️ migración manual")
    sensitive = tuple(getattr(policy, "sensitive_paths", ()) or ())
    infra = [f for f in files if any(f.startswith(p) or Path(f).name == p for p in sensitive)]
    if infra:
        out.append("⚠️ infraestructura de deploy: " + ", ".join(infra[:3]))
    return out


def render_ficha(*, risk: Risk, phase: str, tid: str, title: str | None, repo: str, base: str, status: str,
                 pr: int | None = None, override: str | None = None, for_oscar: str | None = None,
                 gates=(), risks=(), deploy: str | None = None, deps=(),
                 links: list[tuple[str, str]] | None = None, tree=()) -> str:
    """Ficha compacta en HTML. Todo el texto variable se escapa; si no cabe, se recortan listas, nunca el HTML.
    `tree`: líneas 🔗/↳ de deps.tree_lines (sin ⏸: los padres pendientes ya van en "Depende de")."""
    e = html.escape
    plain = truncate(for_oscar, FOR_OSCAR_MAX) if for_oscar else truncate(title, FOR_OSCAR_MAX)
    head = [f"<b>{e(header(risk, phase, pr=pr, override=override))}</b>",
            f"{e(tid)} · {e(truncate(title, 60))}",
            f"{e(repo or '?')} · lane/{e(tid)} → {e(base or '?')}"]
    if plain:
        head.append("Para ti: " + e(plain))
    gates, risks, deps = list(gates or ()), list(risks or ()), list(deps or ())
    tail = [e(status)] if status else []
    if links:
        tail.append(" · ".join(f'<a href="{e(url, quote=True)}">{e(label)}</a>' for label, url in links))

    def body() -> list[str]:
        out = []
        if gates:
            out.append("Gates: " + e(" · ".join(gates)))
        out.append("Riesgos: " + (e(" · ".join(risks)) if risks else "ninguno"))
        if deploy:
            out.append("Deploy: " + e(deploy))
        if deps:
            out.append("Depende de: " + e(" · ".join(deps)))
        out += [e(truncate(line, 300)) for line in (tree or ())[:3]]
        return out

    while len("\n".join(head + body() + tail)) > TEXT_MAX and (gates or risks or deps):
        for lst in (deps, gates, risks):  # recorta listas (lo menos crítico primero), nunca el HTML
            if lst:
                lst.pop()
                break
    return "\n".join(head + body() + tail)


def topic_link(chat_id, thread_id, message_id) -> str | None:
    """Enlace t.me a un mensaje de un tema de supergrupo (-100…). None en un DM o sin message_id."""
    chat = str(chat_id or "")
    if not chat.startswith("-100") or not message_id:
        return None
    thread = str(thread_id or "0")
    mid = int(message_id)
    return (f"https://t.me/c/{chat[4:]}/{thread}/{mid}" if thread not in ("", "0")
            else f"https://t.me/c/{chat[4:]}/{mid}")


def link_line(prefix: str, sent: dict | None) -> str:
    """Línea corta que queda en el tema del carril, con enlace a la ficha del tema de Integración."""
    url = topic_link((sent or {}).get("chat_id"), (sent or {}).get("thread_id"), (sent or {}).get("message_id"))
    return f'{html.escape(prefix)} → <a href="{html.escape(url, quote=True)}">ver en Integración</a>' if url \
        else f"{html.escape(prefix)} → ver en Integración"


# --- resumen fijado ------------------------------------------------------------------------------------

def age_label(since: float | None, now: float) -> str:
    """Antigüedad en unidades gruesas (h/d): el texto no cambia en cada pasada de 5 min."""
    if not since:
        return "?"
    hours = max(0, int((now - float(since)) // 3600))
    if hours < 1:
        return "<1 h"
    return f"{hours} h" if hours < 48 else f"{hours // 24} d"


def summary_text(entries: list[dict], now: float) -> str:
    """entries: [{emoji, pr, title, state, since}]. Una línea por PR, la más antigua primero."""
    if not entries:
        return SUMMARY_EMPTY
    e = html.escape
    rows = sorted(entries, key=lambda x: (float(x.get("since") or now), int(x.get("pr") or 0)))
    lines = [f"<b>🚦 PENDIENTE DE INTEGRAR ({len(rows)})</b>"]
    for x in rows:
        line = (f"{x.get('emoji') or '•'} PR #{int(x.get('pr') or 0)} · {e(truncate(x.get('title'), SUMMARY_TITLE_MAX))}"
                f" · {e(x.get('state') or '?')} · {e(age_label(x.get('since'), now))}")  # "<1 h" rompería el HTML
        if len("\n".join(lines + [line])) > TEXT_MAX - 40:
            lines.append(f"… y {len(rows) - len(lines) + 1} más")
            break
        lines.append(line)
    return "\n".join(lines)


class PinnedSummary:
    """Un solo mensaje fijado en el tema de Integración: se crea (y fija, sin notificar) una vez y luego se EDITA
    solo cuando cambia su texto. Estado en `path` ({chat_id, thread_id, message_id, text})."""

    def __init__(self, notifier, target: tuple[str, str], path: Path | str):
        self.notifier = notifier
        self.chat, self.thread = str(target[0]), str(target[1])
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _store(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def update(self, text: str, markup_factory=None) -> str:
        """'created' | 'edited' | 'unchanged' | 'error'. Un error de red no recrea (evita fijados duplicados).
        `markup_factory`: teclado (p. ej. [🚀 Promover a producción]) que se pide solo cuando hay que editar o crear."""
        with self._lock:
            st = self._load()
            same_place = str(st.get("chat_id")) == self.chat and str(st.get("thread_id")) == self.thread
            if st.get("message_id") and same_place:
                if st.get("text") == text:
                    return "unchanged"
                try:
                    markup = markup_factory() if markup_factory else None
                    ok = self.notifier.edit(self.chat, st["message_id"], text,
                                            **({"reply_markup": markup} if markup else {}))
                except Exception as exc:
                    log.info("resumen de integración: no se pudo editar (%s); se reintenta en la próxima pasada", exc)
                    return "error"
                if ok:
                    self._store({**st, "text": text, "updated": time.time()})
                    return "edited"
                log.info("resumen de integración: el mensaje fijado ya no se puede editar; creo otro")
            try:
                markup = markup_factory() if markup_factory else None
                sent = self.notifier.send_to(self.chat, self.thread, text, silent=True,
                                             **({"reply_markup": markup} if markup else {}))
            except Exception as exc:
                log.warning("resumen de integración: no se pudo enviar: %s", exc)
                return "error"
            if not sent or not sent.get("message_id"):
                return "error"
            self._store({"chat_id": self.chat, "thread_id": self.thread, "message_id": sent["message_id"],
                         "text": text, "updated": time.time()})
            try:
                if not self.notifier.pin(self.chat, sent["message_id"]):
                    log.warning("resumen de integración: no se pudo fijar (¿el bot es admin del grupo?)")
            except Exception as exc:
                log.warning("resumen de integración: no se pudo fijar: %s", exc)
            return "created"


def summary_state(status: str, *, deploy: str = "none", migration: str | None = None,
                  problem: str | None = None, apply: str = "") -> str | None:
    """Estado legible de una entrada del integrador; None = ya no está pendiente (integrada, desplegada, aplicada...)."""
    if status == "applied":
        return None
    if status == "merged" and apply:  # claude-hub: fusionado en main, falta el reinicio ordenado
        return "🔁 fusionado, falta aplicar"
    if status == "offered":
        return "⏸ migración pendiente" if migration and deploy == "on_merge" else "🚦 listo, pulsa Fusionar"
    if status == "failed":
        return "⛔ gates fallidos"
    if status == "waiting_deps":
        return "⏸ espera dependencias"
    if status == "merging":
        return "🔀 fusionando"
    if status == "stale":
        return "🔄 se revisa de nuevo"
    if status == "pr_problem":
        if (problem or "").startswith("el PR está "):  # fusionado/cerrado a mano en GitHub: ya no está pendiente
            return None
        return "⚠️ " + truncate(problem or "problema con el PR", 60)
    if status == "merged":
        if migration:
            return "⏸ fusionado, migración pendiente"
        return "🚀 fusionado, falta desplegar" if deploy == "railway_up" else "🚀 fusionado, deploy sin confirmar"
    return None


def repo_name(lane) -> str:
    return Path(str(getattr(lane, "repo", "") or "")).name or getattr(lane, "name", "?")
