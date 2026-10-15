"""Claude Code headless worker: builds the `claude -p` invocation and parses its JSON result."""
from __future__ import annotations

import json
import os
import re
import subprocess

from . import preferencias
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


ORIGIN_BRANCH_LINE = re.compile(r"^\s*Rama-origen:\s*(\S+)\s*$", re.M | re.I)
_SAFE_REF = re.compile(r"^[\w][\w./-]*$")


def merge_refs(lane: Lane, task: dict) -> list[str]:
    """Refs que el guard deja traer con `git merge`: `<remote>/<base>` y las ramas de líneas `Rama-origen: <rama>` del
    cuerpo (línea de confianza, como Origen-Ops). Cualquier otra rama arrastraría trabajo sin revisar."""
    refs = [f"{lane.remote}/{lane.base}"]
    for b in ORIGIN_BRANCH_LINE.findall(task.get("body") or ""):
        b = b.removeprefix(f"{lane.remote}/")
        if _SAFE_REF.match(b) and ".." not in b:
            refs.append(f"{lane.remote}/{b}")
    return refs


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
        + _coordinator_section(task.get("coordinator_notes"))
        + _feedback_section(task.get("review_feedback"))
        + _conflict_section(task.get("merge_conflict"), lane)
        + preferencias.section_for("workers")
    )


def _conflict_section(conflict: str | None, lane: Lane) -> str:
    if not conflict:
        return ""
    return (f"\n## Conflicto con {lane.remote}/{lane.base} (el runner intentó ponerte al día y hay conflicto)\n"
            "Tu worktree está en medio de esa fusión. Resuelve los conflictos, `git add`, `git merge --continue`, "
            f"comprueba los tests y haz push de tu rama. Salida de git:\n\n{conflict}\n")


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
        + _coordinator_section(task.get("coordinator_notes"))
        + _feedback_section(task.get("review_feedback"), ops=True)
    )


def _answers_section(answers: list[str] | None, ops: bool = False) -> str:
    if not answers:
        return ""
    # TODAS las respuestas de la ronda (t_a2972b36, t_5fbf77bc): con solo las 5 últimas el worker volvía a preguntar
    # lo ya decidido. `oscar_answers_from` ya deja una por pregunta; el tope solo evita un prompt desmesurado.
    items = "\n".join(f"- {a}" for a in answers[-40:])
    return ("\n## Decisiones de Oscar (respuestas a tus preguntas; obligatorio respetarlas)\n"
            "Esta tarea ya se bloqueó antes pidiendo decisión y Oscar ha respondido a TODAS las de abajo: no vuelvas a "
            "preguntar nada de lo ya decidido, aunque tu pregunta tenga otras palabras. Si dos respuestas se "
            "contradicen, vale la ÚLTIMA (la de más abajo). Tu "
            + ("workspace" if ops else "worktree") + " conserva el trabajo "
            f"anterior: continúa desde ahí aplicando estas decisiones.\n\n{items}\n")


def _coordinator_section(notes: list[str] | None) -> str:
    if not notes:
        return ""
    items = "\n".join(f"- {n}" for n in notes[-6:])
    return ("\n## Notas del coordinador (leer antes de preguntar)\n"
            "Comentarios del coordinador en la tarjeta. Si una nota ya contesta o descarta algo, NO vuelvas a "
            "preguntarlo; si es posterior a una respuesta de Oscar y la contradice, prevalece la más reciente.\n\n"
            f"{items}\n")


def _feedback_section(feedback: list[str] | None, ops: bool = False) -> str:
    if not feedback:
        return ""
    items = "\n\n".join(feedback[-2:])
    how = ("Tu workspace ya tiene el trabajo anterior: parte de ahí y aplica SOLO estos cambios." if ops else
           "Tu rama ya tiene el trabajo anterior: parte de ahí, aplica SOLO estos "
           "cambios, commit y push de la misma rama.")
    return ("\n## Cambios pedidos por la revisión (obligatorio atenderlos)\n"
            f"Esta tarea vuelve de review. {how}\n\n{items}\n")


# Presupuesto POR TAREA (t_ad46ae34): `max_budget_usd` del carril es el tope de toda la tarea, no de cada invocación
# (t_5ef35468 gastó 3,19 USD con tope 3: run + resumes sumaban más). La 1ª llamada se queda con FIRST_CALL_SHARE y
# el resto se reparte entre los resumes que quedan; con menos de MIN_CALL_BUDGET_USD no se llama a claude.
FIRST_CALL_SHARE = 0.6
MIN_CALL_BUDGET_USD = 0.10


def base_args(lane: Lane, session_flag: list[str], budget: float | None = None) -> list[str]:
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
        "--max-budget-usd", str(round(lane.max_budget_usd if budget is None else budget, 4)),
        "--model", lane.model,
        "--effort", lane.effort,
        "--output-format", "json",
    ]


def ops_args(lane: Lane, session_flag: list[str], add_dirs: list[str], budget: float | None = None) -> list[str]:
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
        "--max-budget-usd", str(round(lane.max_budget_usd if budget is None else budget, 4)),
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


def run_claude(runner, args: list[str], prompt: str, cwd: str, timeout: float, env_extra: dict,
               on_start=None) -> WorkerOutcome:
    # AGENT_LANES_TASK switches on the "MODO WORKER" section of ~/.claude/CLAUDE.md.
    env = {**os.environ, **env_extra}
    # `on_start(pid)` solo se pasa si hay quien lo recoja (los dobles de los tests no lo conocen).
    extra = {"on_start": on_start} if on_start else {}
    try:
        cp = runner(args, input=prompt, cwd=cwd, env=env, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout, **extra)
    except subprocess.TimeoutExpired:
        return WorkerOutcome(False, "runner_timeout", None, None, f"claude superó {int(timeout)}s")
    out = parse_result(cp.stdout)
    if not out.ok and out.raw_error and cp.returncode != 0 and not cp.stdout.strip():
        out.raw_error += f" rc={cp.returncode} stderr={cp.stderr.strip()[-300:]}"
    return out


def record_worker_pid(state_path: Path, pid: int) -> None:
    """Apunta en el estado de la tarea (.state/tasks/<id>.json) el PID del `claude` y su hora de creación. Si el
    runner muere (schtasks /End no mata a los hijos), el siguiente arranque sabe qué proceso es suyo y lo mata en vez
    de dejarlo huérfano o lanzar un segundo worker sobre el mismo worktree (incidente 27-09, t_bdee05fe)."""
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return  # sin estado de la tarea (p. ej. consola interactiva): nada que actualizar
    if not isinstance(state, dict):
        return
    state["worker_pid"] = pid
    state["worker_created"] = _proc.created_at(pid)
    tmp = Path(state_path).with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, state_path)


class ClaudeWorker:
    def __init__(self, runner=_proc.run, pid_dir: Path | None = None):
        self._run = runner
        self._pid_dir = pid_dir  # carpeta de los estados de tarea; None = no se apunta el PID
        self._ops: dict[str, tuple[dict, list[str]]] = {}  # session_id -> (env, add_dirs) del run() ops, para resume
        self._spent: dict[str, float] = {}   # session_id -> USD gastados en la tarea (run + resumes)
        self._resumes: dict[str, int] = {}   # session_id -> resumes ya lanzados

    def _budget_for(self, lane: Lane, session_id: str, first: bool) -> float:
        remaining = max(0.0, lane.max_budget_usd - self._spent.get(session_id, 0.0))
        if first:
            return remaining * FIRST_CALL_SHARE if lane.max_resumes > 0 else remaining
        return remaining / max(1, lane.max_resumes - self._resumes.get(session_id, 0))

    def _account(self, session_id: str, out: WorkerOutcome) -> WorkerOutcome:
        """Suma el coste de la llamada al de la tarea; `cost_usd` del resultado pasa a ser el total acumulado."""
        total = self._spent.get(session_id, 0.0) + (out.cost_usd or 0.0)
        self._spent[session_id] = total
        out.cost_usd = total if (out.cost_usd is not None or total) else None
        return out

    def _exhausted(self, lane: Lane, session_id: str) -> WorkerOutcome:
        spent = self._spent.get(session_id, 0.0)
        return WorkerOutcome(False, "error_max_budget_usd", None, spent or None,
                             f"presupuesto de la tarea agotado ({spent:.2f} de {lane.max_budget_usd:.2f} USD)")

    def _exec(self, args: list[str], prompt: str, cwd: str, timeout: float, lane: Lane, task_id: str,
              extra_env: dict | None = None) -> WorkerOutcome:
        on_start = None
        if self._pid_dir:
            state_path = Path(self._pid_dir) / f"{task_id}.json"
            on_start = lambda pid: record_worker_pid(state_path, pid)  # noqa: E731
        return run_claude(self._run, args, prompt, cwd, timeout,
                          {"AGENT_LANES_TASK": task_id, "AGENT_LANES_LANE": lane.name, **(extra_env or {})},
                          on_start=on_start)

    def run(self, lane: Lane, task: dict, cwd: str, session_id: str, timeout: float) -> WorkerOutcome:
        flag = ["--session-id", session_id, "--name", f"task-{task['id']}"]
        budget = self._budget_for(lane, session_id, first=True)
        if budget < MIN_CALL_BUDGET_USD:
            return self._exhausted(lane, session_id)
        if lane.kind == "ops":
            from .ops import task_targets, worker_env
            origins, dests, _ = task_targets(lane, task)
            env, dirs = worker_env(lane, task, cwd), [*dests, *origins]
            self._ops[session_id] = (env, dirs)
            return self._account(session_id, self._exec(
                ops_args(lane, flag, dirs, budget), build_ops_prompt(task, lane, cwd, origins, dests), cwd,
                timeout, lane, task["id"], env))
        args = base_args(lane, flag, budget)
        env = {"AGENT_LANES_MERGE_OK": json.dumps(merge_refs(lane, task))}
        self._ops[session_id] = (env, [])
        return self._account(session_id, self._exec(args, build_prompt(task, lane), cwd, timeout, lane, task["id"], env))

    def resume(self, lane: Lane, cwd: str, session_id: str, timeout: float, task_id: str,
               prompt: str | None = None) -> WorkerOutcome:
        """`prompt`: mensaje de reanudación a medida (p. ej. "Oscar ya respondió esto: …"); por defecto el genérico."""
        budget = self._budget_for(lane, session_id, first=False)
        if budget < MIN_CALL_BUDGET_USD:
            return self._exhausted(lane, session_id)
        self._resumes[session_id] = self._resumes.get(session_id, 0) + 1
        if lane.kind == "ops":
            # Sin el entorno del run() (proceso reiniciado) el hook no tiene workspace: bloquea toda escritura.
            env, dirs = self._ops.get(session_id, ({"AGENT_LANES_ROLE": "ops"}, []))
            return self._account(session_id, self._exec(
                ops_args(lane, ["--resume", session_id], dirs, budget), prompt or OPS_RESUME_PROMPT, cwd, timeout, lane,
                task_id, env))
        env = self._ops.get(session_id, ({"AGENT_LANES_MERGE_OK": json.dumps([f"{lane.remote}/{lane.base}".lower()])}, []))[0]
        return self._account(session_id, self._exec(
            base_args(lane, ["--resume", session_id], budget), prompt or RESUME_PROMPT, cwd, timeout, lane, task_id, env))
