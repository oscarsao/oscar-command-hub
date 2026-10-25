"""PreToolUse guard for lane workers.

Claude Code pipes the hook input JSON on stdin. Exit 0 = no objection,
exit 2 = block (stderr is shown to the model). Blocks in every permission mode.
Docs: https://code.claude.com/docs/en/hooks

F1-07 (3f de la auditoría del 25-10): `npm install <paquete>` vetado (y `npm i|add|update|uninstall…`); la herramienta
PowerShell pasa por las mismas reglas que Bash (push a master, merges, despliegues); Write/Edit fuera del worktree de
la tarea vetado; y antes de un `git push` de lane/* se pasa el secret-scan del integrador a lo añadido frente a la
base (AGENT_LANES_BASE): con un posible secreto no se empuja.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}
ALEMBIC_DB_CMDS = {"upgrade", "downgrade", "stamp", "current"}
LOCAL_DB = re.compile(r"(localhost|127\.0\.0\.1|::1|sqlite)", re.I)
SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
WORKFLOWS = re.compile(r"\.github[\\/]+workflows", re.I)
WRITE_HINT = re.compile(r"(>|\btee\b|\bcp\b|\bmv\b|\brm\b|\bsed\s+-i|\bOut-File\b|\bSet-Content\b|\bNew-Item\b)")


EXECUTABLES = {"git", "gh", "railway", "vercel", "alembic"}


def _basename(token: str) -> str:
    """'/c/x/railway.exe' -> 'railway', 'C:\\Git\\bin\\git.exe' -> 'git'; other tokens unchanged."""
    name = re.split(r"[\\/]", token)[-1].lower()
    name = re.sub(r"\.(exe|cmd|bat|js)$", "", name)
    return name if name in EXECUTABLES else token


def _tokens(segment: str) -> list[str]:
    try:
        raw = shlex.split(segment, posix=False if "\\" in segment else True)
    except ValueError:
        raw = segment.split()
    return [_basename(t.strip("\"'")) for t in raw]


# Reviewer (AGENT_LANES_ROLE=revisor): only these read-only git commands, with no shell metacharacters, so a prompt
# injected through the task body cannot chain writes (`git status && ...`, `git diff > f`, `$(...)`).
REVIEWER_BASH = re.compile(r'^git(\s+--no-pager|\s+-C\s+("[^"]*"|\S+))*\s+(diff|log|show|status)(\s[^;&|<>`$\n\r]*)?$')


def _check_reviewer_bash(command: str) -> str | None:
    cmd = command.strip()
    if not REVIEWER_BASH.match(cmd) or "--output" in cmd or "--ext-diff" in cmd:
        return "el revisor es de solo lectura: solo `git diff|log|show|status` sin operadores de shell"
    return None


PUSH_OK_RE = re.compile(r"^lane/t_[0-9a-z]{1,40}$")


def _check_git_push(tokens: list[str], task: str | None = None, push_ok: list[str] | None = None) -> str | None:
    """`push_ok` (AGENT_LANES_PUSH_OK, de las líneas `Rama-push:` de la tarjeta): ramas lane/t_<id> EXTRA que esta tarea
    puede empujar (la rama de su PR original). Nunca main/master ni otra forma; el force sigue prohibido."""
    if "git" not in tokens or "push" not in tokens:
        return None
    args = tokens[tokens.index("push") + 1:]
    for a in args:
        if a in ("-f", "--force", "--force-with-lease", "--force-if-includes", "--mirror", "--all", "--delete", "-d") \
                or a.startswith("--force"):
            return f"git push con {a} está prohibido para workers"
    positional = [a for a in args if not a.startswith("-")]
    refspecs = positional[1:]  # first positional is the remote
    if not refspecs:
        return "git push sin refspec explícito está prohibido: usa `git push -u origin lane/<task_id>`"
    for ref in refspecs:
        if ref.startswith("+"):
            return f"refspec forzado {ref} prohibido"
        dst = ref.split(":", 1)[-1]
        dst = dst.removeprefix("refs/heads/")
        if dst in ("master", "main") or not dst.startswith("lane/"):
            return f"push a '{dst}' prohibido: los workers solo empujan ramas lane/<task_id>"
        if task and dst != f"lane/{task.lower()}" and not (dst in (push_ok or ()) and PUSH_OK_RE.match(dst)):
            return f"push a '{dst}' prohibido: esta tarea solo puede empujar lane/{task}"
    return None


def _check_git_merge(tokens: list[str], allowed: list[str] | None = None) -> str | None:
    """30-09 (OK de Oscar): el worker puede traer a su lane `origin/<base>` y las ramas que la tarea declara con
    `Rama-origen: <rama>` (el runner las pasa en AGENT_LANES_MERGE_OK), abortar o continuar. Nada de otras ramas (un PR no
    arrastra a otro sin revisar: t_5fbf77bc trajo feat/signal-core-v2), ni -s/-X ours/theirs. `-m <msg>` se permite."""
    if "git" not in tokens or "merge" not in tokens:
        return None
    args = tokens[tokens.index("merge") + 1:]
    if any(a in ("--abort", "--continue", "--quit") for a in args):
        return None
    if any(a in ("-s", "-x") or a.startswith(("--strategy", "--allow-unrelated")) for a in args):
        return "git merge con estrategia forzada prohibido para workers"
    targets, skip = [], False
    for a in args:
        if skip:  # mensaje de -m / -F
            skip = False
        elif a in ("-m", "--message", "-f"):
            skip = a != "-f"
        elif not a.startswith("-"):
            targets.append(a)
    ok = allowed or []
    if not targets or any(t not in ok for t in targets):
        return ("git merge solo de origin/<base> o de una rama declarada en la tarea con `Rama-origen: <rama>`"
                + (f" (permitidas: {', '.join(ok)})" if ok else ""))
    return None


PKG_DIR_FLAGS = {"-c", "--dir", "--prefix", "--workspace-root", "--cwd"}
PKG_BAD_CMDS = {"add", "dlx", "link", "unlink", "publish", "remove", "rm", "uninstall", "update", "up", "upgrade"}
PKG_INSTALL = {"install", "i"}
NPM_BAD_CMDS = {"add", "i", "in", "ins", "inst", "insta", "instal", "install", "isntall", "uninstall", "un", "unlink",
                "remove", "rm", "r", "update", "up", "upgrade", "link", "ln", "publish", "exec", "x"}


def _outside_worktree(val: str) -> bool:
    val = val.strip("\"'")
    return val.startswith(("/", "\\", "~")) or bool(re.match(r"^[a-z]:", val)) or ".." in re.split(r"[\\/]", val)


def _check_pkg_manager(low: list[str]) -> str | None:
    """pnpm (directo, `npx pnpm@10`, `corepack pnpm@10.x`): solo `install` desde el lockfile y exec/run/lint dentro del
    worktree. Nada de `pnpm add|dlx|link|publish...`, instalaciones globales (también npm -g) ni rutas fuera del
    worktree (25-10)."""
    idx = next((i for i, t in enumerate(low) if t == "pnpm" or t.startswith("pnpm@")), None)
    is_npm = bool(low) and low[0] in ("npm", "npm.cmd")
    if idx is None and not is_npm:
        return None
    args = low[(idx + 1 if idx is not None else 1):]
    name = "pnpm" if idx is not None else "npm"
    if any(a in ("-g", "--global", "--location=global") for a in args):
        return f"{name} global (-g) prohibido para workers: solo dependencias del worktree"
    for i, a in enumerate(args):
        flag, _, inline = a.partition("=")
        if flag in PKG_DIR_FLAGS:
            val = inline or (args[i + 1] if i + 1 < len(args) else "")
            if _outside_worktree(val):
                return f"{name} {flag} fuera del worktree prohibido"
    if idx is None:
        return _check_npm(args)
    positional, skip = [], False
    for a in args:
        if skip:
            skip = False
        elif a in PKG_DIR_FLAGS | {"--store-dir", "--filter", "-f"}:  # flags que llevan su valor aparte
            skip = True
        elif not a.startswith("-"):
            positional.append(a)
    if positional and positional[0] in PKG_BAD_CMDS:
        return f"pnpm {positional[0]} prohibido para workers: solo `pnpm install --frozen-lockfile`, exec, run y lint"
    if positional and positional[0] in PKG_INSTALL and len(positional) > 1:
        return "pnpm install con paquetes prohibido (añadir dependencias): usa `pnpm install --frozen-lockfile`"
    return None


def _check_npm(args: list[str]) -> str | None:
    """npm: `npm ci`, `npm install` sin paquetes (desde el lockfile), run/test. Añadir o quitar dependencias
    (`npm install <pkg>`, `npm i x`, `npm add`, `update`, `uninstall`, `link`, `publish`, `exec`) está vetado (S-06)."""
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        return None
    cmd = positional[0]
    if cmd in PKG_INSTALL | {"in", "ins", "inst", "insta", "instal", "isntall", "add"} and len(positional) > 1:
        return "npm install con paquetes prohibido (añadir dependencias): usa `npm ci` o `npm install` a secas"
    if cmd in NPM_BAD_CMDS - PKG_INSTALL - {"in", "ins", "inst", "insta", "instal", "isntall"}:
        return f"npm {cmd} prohibido para workers: solo `npm ci`, `npm install` sin paquetes, run y test"
    return None


def inside(path: str, root: str) -> bool:
    """¿`path` (absoluto o relativo a `root`) queda dentro de `root`? Sin distinguir mayúsculas (Windows)."""
    full = os.path.normcase(os.path.realpath(os.path.join(root, path)))
    base = os.path.normcase(os.path.realpath(root))
    try:
        return os.path.commonpath([full, base]) == base
    except ValueError:  # otra unidad
        return False


def _check_write_path(path: str, env: dict, cwd: str | None) -> str | None:
    """Worker de implementación: solo escribe dentro de su worktree (AGENT_LANES_WORKTREE o el cwd del hook)."""
    if not env.get("AGENT_LANES_TASK") or not path:
        return None
    root = env.get("AGENT_LANES_WORKTREE") or cwd
    if not root:
        return "no sé cuál es tu worktree: escritura bloqueada por seguridad"
    if not inside(path, root):
        return f"escritura fuera del worktree prohibida ({os.path.basename(path)}): solo dentro de {root}"
    return None


def _is_push(command: str) -> bool:
    return any("git" in (low := [t.lower() for t in _tokens(seg.strip())]) and "push" in low
               for seg in SEGMENT_SPLIT.split(command) if seg.strip())


def secret_scan_push(cwd: str | None, base: str | None, run=None) -> str | None:
    """Secret-scan del integrador sobre lo añadido en la rama frente a la base, antes del push. Falla cerrado: si no
    se puede revisar, no se empuja. Solo nombra archivo:línea y la regla, nunca el valor."""
    import subprocess
    if not base:
        return None  # sin base declarada (consola, tests antiguos): no hay contra qué comparar
    run = run or subprocess.run
    try:
        cp = run(["git", "-C", cwd or ".", "diff", "--no-color", "--no-ext-diff", "--no-renames", f"{base}...HEAD"],
                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except Exception as exc:
        return f"no pude revisar secretos antes del push ({type(exc).__name__}); avisa en needs_input"
    if cp.returncode != 0:
        return "no pude revisar secretos antes del push (git diff falló); avisa en needs_input"
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from agent_lanes.integrator import scan_secrets  # import tardío: solo al empujar
    hits = scan_secrets(cp.stdout or "")
    if hits:
        where = ", ".join(f"{p}:{n} ({r})" for p, n, r in hits[:5])
        return (f"posible secreto en lo que vas a empujar: {where}. Quítalo (o usa un valor de ejemplo evidente) y "
                "vuelve a hacer commit; si es un falso positivo, explícalo en needs_input")
    return None


def check_bash(command: str, env_db_url: str | None, task: str | None = None,
               merge_ok: list[str] | None = None, push_ok: list[str] | None = None) -> str | None:
    for segment in SEGMENT_SPLIT.split(command):
        seg = segment.strip()
        if not seg:
            continue
        tokens = _tokens(seg)
        low = [t.lower() for t in tokens]
        reason = _check_git_push(low, task, push_ok) or _check_git_merge(low, merge_ok)
        if reason:
            return reason
        reason = _check_pkg_manager(low)
        if reason:
            return reason
        if "gh" in low and "pr" in low and "merge" in low:
            return "gh pr merge prohibido: el merge lo hace Oscar o el Integrador"
        if "railway" in low and "up" in low:
            return "railway up prohibido: los workers no despliegan"
        if "vercel" in low \
                and ("--prod" in low or "promote" in low or "--prod=true" in low):
            return "vercel --prod / promote prohibido: los workers no despliegan"
        if "alembic" in low \
                and any(t in ALEMBIC_DB_CMDS for t in low):
            inline = re.search(r"DATABASE_URL=(\S+)", seg)
            url = inline.group(1) if inline else env_db_url
            if not url or not LOCAL_DB.search(url):
                return "alembic con DATABASE_URL no local (o no definida) prohibido; Alembic es del Integrador"
    if WORKFLOWS.search(command) and WRITE_HINT.search(command):
        return "escritura en .github/workflows/ prohibida para workers"
    return None


def check(payload: dict, env: dict | None = None) -> str | None:
    env = os.environ if env is None else env
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    if tool.startswith("mcp__"):
        return f"herramienta MCP {tool} prohibida para workers (los workers corren sin MCP)"
    reviewer = env.get("AGENT_LANES_ROLE") == "revisor"
    if tool in SHELL_TOOLS:
        if reviewer:
            return _check_reviewer_bash(ti.get("command", ""))
        try:
            merge_ok = [str(r).lower() for r in json.loads(env.get("AGENT_LANES_MERGE_OK") or "[]")]
        except (ValueError, TypeError):
            merge_ok = []  # ilegible: ningún merge (falla cerrado)
        try:
            push_ok = [str(r).lower() for r in json.loads(env.get("AGENT_LANES_PUSH_OK") or "[]")]
        except (ValueError, TypeError):
            push_ok = []  # ilegible: ninguna rama extra (falla cerrado)
        command = ti.get("command", "")
        reason = check_bash(command, env.get("DATABASE_URL"), env.get("AGENT_LANES_TASK"), merge_ok, push_ok)
        if reason or not (env.get("AGENT_LANES_TASK") and _is_push(command)):
            return reason
        return secret_scan_push(env.get("AGENT_LANES_WORKTREE") or payload.get("cwd"), env.get("AGENT_LANES_BASE"))
    if tool in WRITE_TOOLS and reviewer:
        return f"el revisor es de solo lectura: {tool} prohibido"
    if tool in WRITE_TOOLS:
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if WORKFLOWS.search(path):
            return "escritura en .github/workflows/ prohibida para workers"
        return _check_write_path(path, env, payload.get("cwd"))
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # malformed input: fail closed
        print(f"guard: entrada ilegible ({exc}); bloqueo por seguridad", file=sys.stderr)
        return 2
    reason = check(payload)
    if reason:
        print(f"BLOQUEADO por agent-lanes guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
