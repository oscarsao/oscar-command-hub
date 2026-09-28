"""[💬 Explícame más] en los avisos de decisión: una explicación corta en lenguaje llano de lo que se decide.

Una llamada barata y aislada: `claude -p --model haiku`, sin herramientas (`--tools ""`), sin MCP
(`--strict-mcp-config` con config vacía), sin sesión guardada, con tope de coste y timeout. Recibe solo el título, el
cuerpo de la tarea (recortado), las preguntas y el último resumen del worker; nunca rutas locales ni secretos.
Cualquier fallo devuelve None y quien llama responde "no pude generar la explicación".
"""
from __future__ import annotations

import json
import logging
import subprocess

from . import proc as _proc
from .notices import normalize_questions, truncate

log = logging.getLogger("agent_lanes")

MODEL = "haiku"
MAX_BUDGET_USD = 0.05
TIMEOUT_SECONDS = 90
BODY_MAX = 3000
SUMMARY_MAX = 1500
ANSWER_MAX = 1500  # la respuesta a Telegram: corta, se lee en el móvil

SYSTEM = ("Eres un asistente que explica a Oscar, dueño de un negocio y no técnico, una decisión que le pide un "
          "agente de programación. Responde en español de España, en texto plano (sin markdown ni encabezados), "
          "en 4-8 líneas: qué se decide, por qué importa, qué pasa con cada opción y qué recomienda el agente. "
          "Sin jerga; si un término técnico es imprescindible, explícalo en pocas palabras. No inventes datos que "
          "no estén en el contexto.")


def build_prompt(rec: dict) -> str:
    """Contexto de la decisión desde el registro del teclado (el mismo que usan los demás botones)."""
    qs = normalize_questions(rec.get("questions") or ([rec["question"]] if rec.get("question") else []))
    lines = [f"Tarea {rec.get('task_id', '')}: {truncate(rec.get('title'), 200)}"]
    if rec.get("for_oscar"):
        lines.append(f"Resumen del agente para Oscar: {truncate(rec['for_oscar'], 400)}")
    if qs:
        lines.append("Preguntas del agente:")
        for q in qs:
            opts = [f"{i + 1}) {o}" + (" (recomendada)" if i == q.get("recommended") else "")
                    for i, o in enumerate(q.get("options") or ())]
            lines.append(f"- {q['question']}" + (f" Opciones: {' / '.join(opts)}" if opts else ""))
    if rec.get("summary"):
        lines.append(f"Último resumen del agente: {(rec['summary'] or '')[:SUMMARY_MAX]}")
    if rec.get("body"):
        lines.append(f"Descripción de la tarea:\n{(rec['body'] or '')[:BODY_MAX]}")
    lines.append("\nExplícale a Oscar esta decisión.")
    return "\n".join(lines)


def claude_args() -> list[str]:
    return ["claude", "-p", "--model", MODEL, "--tools", "", "--strict-mcp-config", "--mcp-config",
            '{"mcpServers":{}}', "--no-session-persistence", "--max-budget-usd", str(MAX_BUDGET_USD),
            "--system-prompt", SYSTEM, "--output-format", "json"]


def explain(rec: dict, *, runner=_proc.run, cwd: str | None = None) -> str | None:
    """Texto plano para Telegram, o None si la llamada falla (timeout, coste, salida no JSON)."""
    try:
        cp = runner(claude_args(), input=build_prompt(rec), cwd=cwd, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("%s: explicación no generada: %s", rec.get("task_id"), exc)
        return None
    try:
        data = json.loads((cp.stdout or "").strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        log.warning("%s: explicación no generada (salida no JSON, rc=%s)", rec.get("task_id"), cp.returncode)
        return None
    text = data.get("result") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip() or data.get("is_error"):
        log.warning("%s: explicación no generada (subtype=%s)", rec.get("task_id"), (data or {}).get("subtype"))
        return None
    text = text.strip()
    return text if len(text) <= ANSWER_MAX else text[: ANSWER_MAX - 1].rstrip() + "…"
