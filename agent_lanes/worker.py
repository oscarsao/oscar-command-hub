"""Claude Code headless worker: builds the `claude -p` invocation and parses its JSON result."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT, Lane

SCHEMA_PATH = ROOT / "contract" / "result.schema.json"
SETTINGS_TEMPLATE = ROOT / "contract" / "worker-settings.json"
STATE_DIR = ROOT / ".state"

RESUME_PROMPT = ("Continúa donde lo dejaste y termina la tarea. Recuerda: commit y push SOLO de tu rama lane/<task_id>. "
                 "Devuelve el JSON del schema.")


@dataclass
class WorkerOutcome:
    ok: bool                      # claude finished normally AND returned schema-shaped output
    subtype: str | None           # claude result subtype (success, error_max_budget_usd, ...)
    structured: dict | None
    cost_usd: float | None
    raw_error: str | None


def render_settings() -> Path:
    """worker-settings.json with the absolute agent-lanes path substituted (hook command needs it)."""
    STATE_DIR.mkdir(exist_ok=True)
    text = SETTINGS_TEMPLATE.read_text(encoding="utf-8").replace("{AGENT_LANES_DIR}", ROOT.as_posix())
    out = STATE_DIR / "worker-settings.rendered.json"
    out.write_text(text, encoding="utf-8")
    return out


def build_prompt(task: dict, lane: Lane) -> str:
    return (
        f"Tarea {task['id']} del kanban de Hermes (board {lane.board}, carril {lane.name}).\n"
        f"Estás en un worktree dedicado en la rama lane/{task['id']} creada desde {lane.remote}/{lane.base}.\n"
        f"Al terminar: commit, `git push -u {lane.remote} lane/{task['id']}` y devuelve el JSON del schema.\n\n"
        f"# {task.get('title', '')}\n\n{task.get('body') or ''}\n"
    )


def base_args(lane: Lane, session_flag: list[str]) -> list[str]:
    return [
        "claude", "-p", *session_flag,
        "--settings", str(render_settings()),
        "--permission-mode", "acceptEdits",
        *(["--allowedTools", *lane.allowed_tools] if lane.allowed_tools else []),
        "--append-system-prompt-file", str(lane.role_path),
        "--json-schema", SCHEMA_PATH.read_text(encoding="utf-8"),
        "--max-budget-usd", str(lane.max_budget_usd),
        "--model", lane.model,
        "--effort", lane.effort,
        "--output-format", "json",
    ]


def parse_result(stdout: str) -> WorkerOutcome:
    try:
        data = json.loads(stdout.strip().splitlines()[-1] if stdout.strip() else "")
    except (json.JSONDecodeError, IndexError):
        return WorkerOutcome(False, None, None, None, "salida de claude no es JSON")
    if isinstance(data, list):  # defensive: stream-like array
        data = next((d for d in reversed(data) if isinstance(d, dict) and d.get("type") == "result"), {})
    subtype = data.get("subtype")
    structured = data.get("structured_output")
    if structured is None and isinstance(data.get("result"), str):
        try:
            structured = json.loads(data["result"])
        except json.JSONDecodeError:
            structured = None
    if not isinstance(structured, dict) or "status" not in structured:
        structured = None
    ok = subtype == "success" and not data.get("is_error") and structured is not None
    return WorkerOutcome(ok, subtype, structured, data.get("total_cost_usd"),
                         None if ok else f"subtype={subtype} is_error={data.get('is_error')}")


class ClaudeWorker:
    def __init__(self, runner=subprocess.run):
        self._run = runner

    def _exec(self, args: list[str], prompt: str, cwd: str, timeout: float) -> WorkerOutcome:
        try:
            cp = self._run(args, input=prompt, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            return WorkerOutcome(False, "runner_timeout", None, None, f"claude superó {int(timeout)}s")
        out = parse_result(cp.stdout)
        if not out.ok and out.raw_error and cp.returncode != 0 and not cp.stdout.strip():
            out.raw_error += f" rc={cp.returncode} stderr={cp.stderr.strip()[-300:]}"
        return out

    def run(self, lane: Lane, task: dict, cwd: str, session_id: str, timeout: float) -> WorkerOutcome:
        args = base_args(lane, ["--session-id", session_id, "--name", f"task-{task['id']}"])
        return self._exec(args, build_prompt(task, lane), cwd, timeout)

    def resume(self, lane: Lane, cwd: str, session_id: str, timeout: float) -> WorkerOutcome:
        return self._exec(base_args(lane, ["--resume", session_id]), RESUME_PROMPT, cwd, timeout)
