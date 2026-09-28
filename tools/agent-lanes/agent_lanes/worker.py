"""Claude Code headless worker: builds the `claude -p` invocation and parses its JSON result."""
from __future__ import annotations

import json
import os
import subprocess

from . import proc as _proc
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT, Lane

SCHEMA_PATH = ROOT / "contract" / "result.schema.json"
SETTINGS_TEMPLATE = ROOT / "contract" / "worker-settings.json"
OPS_SCHEMA_PATH = ROOT / "contract" / "ops-result.schema.json"
OPS_SETTINGS_TEMPLATE = ROOT / "contract" / "ops-worker-settings.json"
STATE_DIR = ROOT / ".state"

RESUME_PROMPT = ("Continúa donde lo dejaste y termina la tarea. Recuerda: commit y push SOLO de tu rama lane/<task_id>. "
                 "Devuelve el JSON del schema.")
OPS_RESUME_PROMPT = ("Continúa donde lo dejaste y termina la tarea. Recuerda: sin borrar ni mover, escritura solo en el "
                     "workspace y los destinos declarados, acciones externas como propuesta. Devuelve el JSON del schema.")


@dataclass
class WorkerOutcome:
    ok: bool                      # claude finished normally AND returned schema-shaped output
    subtype: str | None           # claude result subtype (success, error_max_budget_usd, ...)
    structured: dict | None
    cost_usd: float | None
    raw_error: str | None


def render_settings(template: Path = SETTINGS_TEMPLATE) -> Path:
    """Settings template with the absolute agent-lanes path substituted (hook command needs it)."""
    STATE_DIR.mkdir(exist_ok=True)
    text = template.read_text(encoding="utf-8").replace("{AGENT_LANES_DIR}", ROOT.as_posix())
    out = STATE_DIR / f"{template.stem}.rendered.json"
    out.write_text(text, encoding="utf-8")
    return out


def build_prompt(task: dict, lane: Lane) -> str:
    return (
        f"Tarea {task['id']} del kanban de Hermes (board {lane.board}, carril {lane.name}).\n"
        f"Estás en un worktree dedicado en la rama lane/{task['id']} creada desde {lane.remote}/{lane.base}.\n"
        f"Al terminar: commit, `git push -u {lane.remote} lane/{task['id']}` y devuelve el JSON del schema.\n\n"
        f"# {task.get('title', '')}\n\n{task.get('body') or ''}\n"
        + _answers_section(task.get("oscar_answers"))
        + _feedback_section(task.get("review_feedback"))
    )


def build_ops_prompt(task: dict, lane: Lane, cwd: str, origins: list[str], dests: list[str]) -> str:
    def listed(xs):
        return "\n".join(f"- {x}" for x in xs) if xs else "- (ninguno)"
    return (
        f"Tarea {task['id']} del kanban de Hermes (board {lane.board}, carril {lane.name}): trabajo OPERATIVO.\n"
        f"Workspace de la tarea (carpeta de trabajo, no es git): {cwd}\n"
        f"Orígenes declarados (solo lectura):\n{listed(origins)}\n"
        f"Destinos declarados (se puede escribir además del workspace):\n{listed(dests)}\n"
        "Al terminar devuelve el JSON del schema con evidencias verificables. Las acciones externas o irreversibles "
        "van en proposed_actions + needs_input, nunca se ejecutan.\n\n"
        f"# {task.get('title', '')}\n\n{task.get('body') or ''}\n"
        + _answers_section(task.get("oscar_answers"), ops=True)
        + _feedback_section(task.get("review_feedback"), ops=True)
    )


def _answers_section(answers: list[str] | None, ops: bool = False) -> str:
    if not answers:
        return ""
    items = "\n".join(f"- {a}" for a in answers[-5:])
    return ("\n## Decisiones de Oscar (respuestas a tus preguntas; obligatorio respetarlas)\n"
            "Esta tarea ya se bloqueó antes pidiendo decisión y Oscar ha respondido. Tu "
            + ("workspace" if ops else "worktree") + " conserva el trabajo "
            f"anterior: continúa desde ahí aplicando estas decisiones.\n\n{items}\n")


def _feedback_section(feedback: list[str] | None, ops: bool = False) -> str:
    if not feedback:
        return ""
    items = "\n\n".join(feedback[-2:])
    how = ("Tu workspace ya tiene el trabajo anterior: parte de ahí y aplica SOLO estos cambios." if ops else
           "Tu rama ya tiene el trabajo anterior: parte de ahí, aplica SOLO estos "
           "cambios, commit y push de la misma rama.")
    return ("\n## Cambios pedidos por la revisión (obligatorio atenderlos)\n"
            f"Esta tarea vuelve de review. {how}\n\n{items}\n")


def base_args(lane: Lane, session_flag: list[str]) -> list[str]:
    return [
        "claude", "-p", *session_flag,
        "--settings", str(render_settings()),
        # No MCP at all: user settings allow e.g. Supabase execute_sql/apply_migration, which the Bash guard
        # cannot see. strict + empty config ignores every other MCP source.
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--permission-mode", "acceptEdits",
        *(["--allowedTools", *lane.allowed_tools] if lane.allowed_tools else []),
        "--append-system-prompt-file", str(lane.role_path),
        "--json-schema", SCHEMA_PATH.read_text(encoding="utf-8"),
        "--max-budget-usd", str(lane.max_budget_usd),
        "--model", lane.model,
        "--effort", lane.effort,
        "--output-format", "json",
    ]


def ops_args(lane: Lane, session_flag: list[str], add_dirs: list[str]) -> list[str]:
    """Worker ops. Sin --strict-mcp-config: así cargan los conectores de claude.ai (en -p se cargan salvo con
    --strict-mcp-config/--bare). El filtro real es contract/ops_guard.py (lista blanca de herramientas MCP de lectura;
    exit 2 bloquea en cualquier modo); ops-worker-settings.json añade deny explícitos y --allowedTools las de lectura."""
    dirs = [a for d in add_dirs for a in ("--add-dir", d)]
    return [
        "claude", "-p", *session_flag,
        "--settings", str(render_settings(OPS_SETTINGS_TEMPLATE)),
        "--permission-mode", "acceptEdits",
        *(["--allowedTools", *lane.allowed_tools] if lane.allowed_tools else []),
        *dirs,
        "--append-system-prompt-file", str(lane.role_path),
        "--json-schema", OPS_SCHEMA_PATH.read_text(encoding="utf-8"),
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


def run_claude(runner, args: list[str], prompt: str, cwd: str, timeout: float, env_extra: dict) -> WorkerOutcome:
    # AGENT_LANES_TASK switches on the "MODO WORKER" section of ~/.claude/CLAUDE.md.
    env = {**os.environ, **env_extra}
    try:
        cp = runner(args, input=prompt, cwd=cwd, env=env, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return WorkerOutcome(False, "runner_timeout", None, None, f"claude superó {int(timeout)}s")
    out = parse_result(cp.stdout)
    if not out.ok and out.raw_error and cp.returncode != 0 and not cp.stdout.strip():
        out.raw_error += f" rc={cp.returncode} stderr={cp.stderr.strip()[-300:]}"
    return out


class ClaudeWorker:
    def __init__(self, runner=_proc.run):
        self._run = runner
        self._ops: dict[str, tuple[dict, list[str]]] = {}  # session_id -> (env, add_dirs) del run() ops, para resume

    def _exec(self, args: list[str], prompt: str, cwd: str, timeout: float, lane: Lane, task_id: str,
              extra_env: dict | None = None) -> WorkerOutcome:
        return run_claude(self._run, args, prompt, cwd, timeout,
                          {"AGENT_LANES_TASK": task_id, "AGENT_LANES_LANE": lane.name, **(extra_env or {})})

    def run(self, lane: Lane, task: dict, cwd: str, session_id: str, timeout: float) -> WorkerOutcome:
        flag = ["--session-id", session_id, "--name", f"task-{task['id']}"]
        if lane.kind == "ops":
            from .ops import task_targets, worker_env
            origins, dests, _ = task_targets(lane, task)
            env, dirs = worker_env(lane, task, cwd), [*dests, *origins]
            self._ops[session_id] = (env, dirs)
            return self._exec(ops_args(lane, flag, dirs), build_ops_prompt(task, lane, cwd, origins, dests), cwd,
                              timeout, lane, task["id"], env)
        args = base_args(lane, flag)
        return self._exec(args, build_prompt(task, lane), cwd, timeout, lane, task["id"])

    def resume(self, lane: Lane, cwd: str, session_id: str, timeout: float, task_id: str) -> WorkerOutcome:
        if lane.kind == "ops":
            # Sin el entorno del run() (proceso reiniciado) el hook no tiene workspace: bloquea toda escritura.
            env, dirs = self._ops.get(session_id, ({"AGENT_LANES_ROLE": "ops"}, []))
            return self._exec(ops_args(lane, ["--resume", session_id], dirs), OPS_RESUME_PROMPT, cwd, timeout, lane,
                              task_id, env)
        return self._exec(base_args(lane, ["--resume", session_id]), RESUME_PROMPT, cwd, timeout, lane, task_id)
