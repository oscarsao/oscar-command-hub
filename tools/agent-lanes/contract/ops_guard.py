"""PreToolUse guard del carril ops (trabajo operativo sin repo).

Lista BLANCA, fail closed: herramienta, comando de Bash o herramienta MCP que no esté aquí se bloquea (exit 2).
No relaja nada del guard de código (`guard.py`), que sigue bloqueando todo `mcp__`.

Entorno que fija el runner (nunca un fichero que el worker pueda editar):
  AGENT_LANES_ROLE=ops · AGENT_LANES_TASK · AGENT_LANES_WORKSPACE · AGENT_LANES_OPS_DESTS (JSON, ya validados)
Docs: https://code.claude.com/docs/en/hooks (exit 2 bloquea en cualquier modo de permisos)
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_lanes.ops_paths import inside_any, is_secret, normalize, read_protected  # noqa: E402

# Herramientas internas inocuas (lista de tareas, carga diferida de herramientas, salida --json-schema).
INTERNAL_OK = {"TodoWrite", "ToolSearch", "StructuredOutput", "TaskCreate", "TaskGet", "TaskList", "TaskUpdate"}
READ_TOOLS = {"Read", "Grep", "Glob"}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit"}

# MCP de SOLO LECTURA (nombres exactos; nada de prefijos tipo get_*: hay get_auth_token en otros servidores).
MCP_READ_ALLOW = frozenset({
    "mcp__claude_ai_Google_Calendar__list_calendars",
    "mcp__claude_ai_Google_Calendar__list_events",
    "mcp__claude_ai_Google_Calendar__get_event",
    "mcp__claude_ai_Google_Calendar__search_events",
    "mcp__claude_ai_Google_Drive__search_files",
    "mcp__claude_ai_Google_Drive__read_file_content",
    "mcp__claude_ai_Google_Drive__get_file_metadata",
    "mcp__claude_ai_Google_Drive__get_file_permissions",
    "mcp__claude_ai_Google_Drive__list_recent_files",
    "mcp__claude_ai_ClickUp__clickup_get_task",
    "mcp__claude_ai_ClickUp__clickup_filter_tasks",
    "mcp__claude_ai_ClickUp__clickup_search",
    "mcp__claude_ai_ClickUp__clickup_get_workspace_hierarchy",
    "mcp__claude_ai_ClickUp__clickup_get_list",
    "mcp__claude_ai_ClickUp__clickup_get_folder",
    "mcp__claude_ai_ClickUp__clickup_get_task_comments",
    "mcp__claude_ai_ClickUp__clickup_get_workspace_members",
    "mcp__claude_ai_ClickUp__clickup_get_custom_fields",
})

SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||&|\n")
FD_DUP = re.compile(r"\d*>&\d+|&>\s*/dev/null|\d*>\s*/dev/null")
REDIRECT = re.compile(r"(?:\d*>>?|&>>?)\s*(\"[^\"]*\"|'[^']*'|[^\s;|&<>]+)")
EXECUTABLE_EXT = re.compile(r"\.(exe|cmd|bat|com|ps1)$", re.I)

# Motivos explícitos (el resto de comandos fuera de lista caen en el mensaje genérico).
DENIED = {
    **dict.fromkeys(("rm", "del", "rmdir", "rd", "erase", "remove-item", "ri", "shred", "unlink", "truncate", "dd"),
                    "borrar está prohibido en el carril ops"),
    **dict.fromkeys(("mv", "move", "move-item", "rename", "ren"),
                    "mover está prohibido (tocaría el original): copia con cp/robocopy y propón el resto"),
    **dict.fromkeys(("format", "diskpart", "bcdedit", "reg", "schtasks", "netsh", "setx", "sc", "icacls", "takeown",
                     "attrib", "mklink", "ln", "chmod", "chown", "shutdown", "taskkill", "kill"),
                    "comando de sistema prohibido en el carril ops"),
    **dict.fromkeys(("railway", "vercel", "alembic", "supabase"), "despliegues y bases de datos prohibidos"),
    **dict.fromkeys(("py", "python", "python3", "node", "deno", "bun", "powershell", "pwsh", "cmd", "bash", "sh",
                     "zsh", "eval", "exec", "source", ".", "xargs", "env", "printenv", "set", "export", "sudo",
                     "runas", "start", "call", "perl", "ruby", "awk", "sed"),
                    "intérpretes, shells y variables de entorno no se permiten en ops (el hook no puede verlos)"),
    **dict.fromkeys(("curl", "wget", "scp", "ssh", "rsync", "ftp", "sftp", "nc"),
                    "red y transferencias remotas prohibidas en ops (propón la acción a Oscar)"),
}

READ_CMDS = {"ls", "dir", "cat", "head", "tail", "wc", "du", "df", "stat", "file", "grep", "egrep", "fgrep", "rg",
             "sha256sum", "sha1sum", "md5sum", "sort", "cut", "tr", "jq", "echo", "printf", "pwd", "whoami", "tree",
             "realpath", "readlink", "basename", "dirname", "test", "[", "diff", "cmp", "comm", "true", "false",
             "tasklist", "where", "which", "cd", "date", "uniq", "find", "certutil",
             "git", "gh", "cloudflared", "hostname"}
WRITE_CMDS = {"cp", "copy", "mkdir", "md", "touch", "tee", "robocopy", "xcopy"}

FIND_BAD = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
ROBOCOPY_BAD = {"/mir", "/purge", "/mov", "/move", "/xx", "/xo", "/create", "/job", "/save"}  # /JOB carga opciones
GIT_READ = {"status", "log", "show", "diff", "ls-files", "rev-parse", "describe", "ls-remote", "blame", "shortlog"}
GH_READ = {("repo", "view"), ("repo", "list"), ("issue", "list"), ("issue", "view"), ("pr", "list"), ("pr", "view"),
           ("pr", "status"), ("pr", "diff"), ("pr", "checks"), ("release", "list"), ("release", "view"),
           ("run", "list"), ("run", "view"), ("org", "list"), ("auth", "status")}
GH_API_WRITE = ("-x", "--method", "-f", "--field", "--raw-field", "--input")  # prefijos, en minúsculas (-F = -f)
GIT_BAD_FLAGS = ("--output", "--ext-diff", "--textconv", "--exec", "--upload-pack", "--receive-pack")
PROTECTED_READ_MSG = "leer credenciales o configuración protegida (AppData, ~/.claude*, ~/.cloudflared, .env...) prohibido"


class Blocked(Exception):
    pass


def _roots(env) -> list[str]:
    roots = []
    ws = env.get("AGENT_LANES_WORKSPACE")
    if ws:
        roots.append(normalize(ws))
    try:
        roots += [normalize(d) for d in json.loads(env.get("AGENT_LANES_OPS_DESTS") or "[]")]
    except (ValueError, TypeError):
        raise Blocked("AGENT_LANES_OPS_DESTS ilegible; bloqueo por seguridad")
    return roots


def _check_write(path: str, cwd: str, roots: list[str]) -> None:
    if not path or any(c in path for c in "$`%*?"):
        raise Blocked(f"destino de escritura no resoluble '{path}': usa una ruta literal")
    if path in ("/dev/null", "nul", "NUL"):
        return
    if is_secret(path):
        raise Blocked("escribir ficheros de secretos está prohibido")
    target = normalize(path, cwd)
    if not roots or not inside_any(target, roots):
        raise Blocked(f"escritura fuera del workspace y de los destinos declarados: {path}")


def _tokens(segment: str) -> list[str]:
    try:
        raw = shlex.split(segment, posix="\\" not in segment)
    except ValueError:
        raise Blocked("comando con comillas desbalanceadas")
    return [t.strip("\"'") for t in raw]


def _cmd_name(token: str) -> str:
    return EXECUTABLE_EXT.sub("", re.split(r"[\\/]", token)[-1].lower())


def _positional(args: list[str]) -> list[str]:
    return [a for a in args if not a.startswith("-")]


def _check_segment(seg: str, cwd: str, roots: list[str]) -> str:
    """Valida un segmento; devuelve el cwd resultante (para `cd`)."""
    for m in REDIRECT.finditer(seg):
        _check_write(m.group(1).strip("\"'"), cwd, roots)
    seg_noredir = REDIRECT.sub(" ", seg)
    tokens = _tokens(seg_noredir)
    if not tokens:
        return cwd
    if "=" in tokens[0] and not tokens[0].startswith(("-", "/", ".")):
        raise Blocked("asignaciones de variables prohibidas en ops")
    name, args = _cmd_name(tokens[0]), tokens[1:]
    low = [a.lower() for a in args]
    recursive = name == "rg" or name in ("robocopy", "xcopy") or (
        name in ("grep", "egrep", "fgrep", "cp", "copy") and any(
            a in ("--recursive", "--archive", "--dereference-recursive") or
            (a.startswith("-") and not a.startswith("--") and set(a[1:]) & set("rRa")) for a in args))
    for a in args:  # cada argumento que pueda ser ruta (también el valor de --flag=valor)
        for cand in ([a] if not a.startswith("-") else []) + ([a.split("=", 1)[1]] if "=" in a else []):
            if cand and read_protected(cand, cwd, roots, recursive=recursive):
                raise Blocked(PROTECTED_READ_MSG)
    if name == "git" and "push" in low:
        raise Blocked("git push prohibido en ops")
    if name == "gh" and "pr" in low and "merge" in low:
        raise Blocked("gh pr merge prohibido en ops")
    if name in DENIED:
        raise Blocked(f"{name}: {DENIED[name]}")
    if name not in READ_CMDS | WRITE_CMDS:
        raise Blocked(f"'{name}' no está en la lista blanca del carril ops")

    if name == "cd":
        return normalize(args[0], cwd) if args else normalize("~")
    if name in ("cp", "copy") and any(a in ("--link", "--symbolic-link") or
                                      (a.startswith("-") and not a.startswith("--") and set(a[1:]) & set("ls"))
                                      for a in args):
        raise Blocked("cp -l/-s (enlaces) prohibido: un enlace al original permitiría modificarlo")
    if name == "find" and FIND_BAD & set(low):
        raise Blocked("find con -delete/-exec/-fprint prohibido")
    if name == "sort" and any(a == "-o" or a.startswith(("-o", "--output")) for a in args):
        raise Blocked("sort -o escribe ficheros: usa una redirección dentro del workspace")
    if name == "uniq" and len(_positional(args)) > 1:
        raise Blocked("uniq con fichero de salida prohibido")
    if (name == "hostname" and _positional(args)) or (name == "date" and ("-s" in low or "--set" in low)):
        raise Blocked(f"{name} solo en lectura")
    if name == "tree" and "-o" in low:
        raise Blocked("tree -o escribe ficheros: usa una redirección dentro del workspace")
    if name == "rg" and any(a.startswith("--pre") for a in low):
        raise Blocked("rg --pre ejecuta comandos")
    if name == "certutil" and (not low or low[0] != "-hashfile"):
        raise Blocked("certutil solo con -hashfile")
    if name == "git":
        rest = list(args)
        while rest and rest[0] in ("-C", "--no-pager"):
            rest = rest[2:] if rest[0] == "-C" else rest[1:]
        if not rest or rest[0].lower() not in GIT_READ or any(a.startswith(GIT_BAD_FLAGS) for a in low):
            raise Blocked("git solo en lectura (status/log/show/diff/ls-files/...)")
    if name == "gh":
        pos = _positional(args)
        if pos[:1] == ["api"]:
            if any(a.startswith(GH_API_WRITE) for a in low):
                raise Blocked("gh api solo GET (sin -X/-f/-F/--input)")
        elif tuple(p.lower() for p in pos[:2]) not in GH_READ or "--show-token" in low                 or (pos[:2] == ["auth", "status"] and "-t" in low):
            raise Blocked("gh solo en lectura (repo/issue/pr view|list, api GET, auth status)")
    if name == "cloudflared":
        pos = [p.lower() for p in _positional(args)]
        if not ("--version" in low or pos[:1] == ["version"] or pos[:2] in (["tunnel", "list"], ["tunnel", "info"])):
            raise Blocked("cloudflared solo en lectura (tunnel list/info): configurar un túnel se propone a Oscar")

    if name in ("cp", "copy"):
        pos = _positional(args)
        target = next((args[i + 1] for i, a in enumerate(args) if a in ("-t", "--target-directory") and i + 1 < len(args)),
                      None) or next((a.split("=", 1)[1] for a in args if a.startswith("--target-directory=")), None)
        if target is None:
            if len(pos) < 2:
                raise Blocked("cp necesita origen y destino explícitos")
            target = pos[-1]
        _check_write(target, cwd, roots)
    elif name in ("mkdir", "md", "touch", "tee"):
        pos = _positional(args)
        if not pos:
            raise Blocked(f"{name} sin ruta")
        for p in pos:
            _check_write(p, cwd, roots)
    elif name in ("robocopy", "xcopy"):
        bad = [a for a in low if a.split(":")[0] in ROBOCOPY_BAD]
        if bad:
            raise Blocked(f"{name} con {bad[0]} prohibido (borra o mueve en origen/destino)")
        pos = [a for a in args if not a.startswith(("/", "-"))]
        if len(pos) < 2:
            raise Blocked(f"{name} necesita origen y destino")
        _check_write(pos[1], cwd, roots)
    return cwd


def check_bash(command: str, cwd: str, env) -> str | None:
    if re.search(r"\$|`|<\(|>\(", command):
        return "variables, `...`, $(...) y sustitución de procesos prohibidos en ops: usa rutas y valores literales"
    if is_secret(command):
        return "leer o imprimir secretos (.env, tokens, claves, credenciales) está prohibido"
    try:
        roots = _roots(env)
        cur = cwd
        for segment in SEGMENT_SPLIT.split(FD_DUP.sub(" ", command)):
            if segment.strip():
                cur = _check_segment(segment.strip(), cur, roots)
    except Blocked as exc:
        return str(exc)
    return None


def check(payload: dict, env=None) -> str | None:
    env = os.environ if env is None else env
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or env.get("AGENT_LANES_WORKSPACE") or os.getcwd()
    if tool.startswith("mcp__"):
        return None if tool in MCP_READ_ALLOW else \
            f"herramienta MCP {tool} no permitida en ops (solo lectura de Calendar/Drive/ClickUp); propón la acción"
    if tool in INTERNAL_OK:
        return None
    if tool == "Bash":
        return check_bash(ti.get("command", ""), cwd, env)
    if tool in READ_TOOLS:
        if any(is_secret(ti.get(k) or "") for k in ("file_path", "path", "glob", "pattern")):
            return "leer ficheros de secretos (.env, tokens, claves, credenciales) está prohibido"
        try:
            roots = _roots(env)
        except Blocked as exc:
            return str(exc)
        target = ti.get("file_path") or ti.get("path")
        if target and read_protected(target, cwd, roots, recursive=tool == "Grep"):
            return PROTECTED_READ_MSG
        if tool == "Glob" and ti.get("pattern") and read_protected(ti["pattern"], target or cwd, roots):
            return PROTECTED_READ_MSG
        return None
    if tool in WRITE_TOOLS:
        try:
            _check_write(ti.get("file_path") or "", cwd, _roots(env))
        except Blocked as exc:
            return str(exc)
        return None
    return f"herramienta {tool} no permitida en el carril ops"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:  # entrada ilegible: fail closed
        print(f"ops_guard: entrada ilegible ({exc}); bloqueo por seguridad", file=sys.stderr)
        return 2
    try:
        reason = check(payload)
    except Exception as exc:  # cualquier fallo del propio guard bloquea
        reason = f"error interno del guard ({type(exc).__name__}); bloqueo por seguridad"
    if reason:
        print(f"BLOQUEADO por agent-lanes ops_guard: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
