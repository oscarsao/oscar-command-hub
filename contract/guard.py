"""PreToolUse guard for lane workers.

Claude Code pipes the hook input JSON on stdin. Exit 0 = no objection,
exit 2 = block (stderr is shown to the model). Blocks in every permission mode.
Docs: https://code.claude.com/docs/en/hooks
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
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


def _check_git_push(tokens: list[str]) -> str | None:
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
    return None


def check_bash(command: str, env_db_url: str | None) -> str | None:
    for segment in SEGMENT_SPLIT.split(command):
        seg = segment.strip()
        if not seg:
            continue
        tokens = _tokens(seg)
        low = [t.lower() for t in tokens]
        reason = _check_git_push(low)
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
    if tool == "Bash":
        return check_bash(ti.get("command", ""), env.get("DATABASE_URL"))
    if tool in WRITE_TOOLS:
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        if WORKFLOWS.search(path):
            return "escritura en .github/workflows/ prohibida para workers"
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
