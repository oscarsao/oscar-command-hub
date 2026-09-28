"""Hook PreToolUse del carril ops: lista blanca, fail closed."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contract"))
import ops_guard  # noqa: E402

GUARD = Path(__file__).resolve().parents[1] / "contract" / "ops_guard.py"


@pytest.fixture
def ctx(tmp_path):
    ws = tmp_path / "ops" / "t_1"
    dest = tmp_path / "destino"
    ws.mkdir(parents=True)
    dest.mkdir()
    env = {"AGENT_LANES_ROLE": "ops", "AGENT_LANES_TASK": "t_1", "AGENT_LANES_WORKSPACE": str(ws),
           "AGENT_LANES_OPS_DESTS": json.dumps([str(dest)])}
    return {"ws": ws, "dest": dest, "env": env, "tmp": tmp_path}


def bash(ctx, cmd):
    return ops_guard.check({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": str(ctx["ws"])}, env=ctx["env"])


def tool(ctx, name, **ti):
    return ops_guard.check({"tool_name": name, "tool_input": ti, "cwd": str(ctx["ws"])}, env=ctx["env"])


@pytest.mark.parametrize("cmd", [
    "rm -rf notas", "rm x.md", "del /s /q x", "rmdir /s x", "Remove-Item -Recurse x", "erase x", "rd /s x",
    "format E:", "diskpart", "reg add HKCU\\x /v y", "reg delete HKCU\\x", "schtasks /create /tn x",
    "sc config cloudflared start= auto", "netsh advfirewall set allprofiles state off", "setx PATH x",
    "bcdedit /set x", "git push origin main", "git -C C:/r push origin lane/t_1", "gh pr merge 3",
    "railway up", "vercel --prod", "alembic upgrade head", "supabase db push",
    "cat .env", "type C:/Users/oscar/oscar-command-hub/tools/agent-lanes/.env", "head -n 3 ../.env.local",
    "gh auth token", "printenv", "env", "echo $GITHUB_TOKEN", "echo `whoami`", "ls $(pwd)",
    "mv E:/vault ./vault", "move E:\\vault C:\\x", "find . -name '*.tmp' -delete", "find . -exec rm {} ;",
    "robocopy E:/vault C:/x /MIR", "robocopy E:/vault C:/x /MOVE", "py -c \"import shutil\"",
    "python borrar.py", "powershell -c Remove-Item x", "cmd /c del x", "bash -c 'rm x'", "node -e 1",
    "curl -X POST https://x", "git clean -fdx", "git rm x", "git checkout -- .", "ls && rm x",
    "cloudflared service install", "cloudflared tunnel create vault", "gh api -X PUT repos/o/r/collaborators/a",
    "gh api repos/o/r/collaborators/a -f permission=push", "gh repo edit o/r --visibility public",
    "sort a -o /c/Windows/x", "FOO=1 ls", "shred x", "xargs rm",
])
def test_blocks_dangerous(ctx, cmd):
    assert bash(ctx, cmd), cmd


@pytest.mark.parametrize("cmd", [
    "ls -la E:/vault", "cat E:/vault/nota.md", "find E:/vault -name '*.md'", "wc -l E:/vault/a.md",
    "du -sh E:/vault", "sha256sum E:/vault/a.md", "cp -r E:/vault/. ./vault", "cp E:/a.md vault/a.md",
    "mkdir -p informe", "ls E:/vault > inventario.txt", "sha256sum E:/vault/a.md >> hashes.txt",
    "robocopy E:/vault vault /E /COPY:DAT", "git -C C:/Users/oscar/dev/x log --oneline -5", "gh repo view o/r",
    "gh api repos/o/r/collaborators", "cloudflared tunnel list", "grep -r TODO E:/vault 2>&1 | head",
    "ls E:/vault 2>/dev/null", "cd vault && ls", "echo hola | tee notas.txt",
])
def test_allows_read_and_copy_into_workspace(ctx, cmd):
    assert bash(ctx, cmd) is None, bash(ctx, cmd)


def test_copy_into_declared_destination(ctx):
    assert bash(ctx, f"cp -r E:/vault/. {ctx['dest'].as_posix()}/vault") is None
    assert bash(ctx, f"robocopy E:/vault {ctx['dest'].as_posix()} /E") is None


def test_git_bash_drive_paths_resolved(ctx):
    posix = "/" + ctx["dest"].as_posix()[0].lower() + ctx["dest"].as_posix()[2:]
    assert bash(ctx, f"cp E:/a.md {posix}/a.md") is None
    assert bash(ctx, "cp E:/a.md /c/Windows/a.md")


@pytest.mark.parametrize("target", ["../fuera.txt", "C:/Users/oscar/otro/x.md", "E:/vault/copia.md", "~/x.md"])
def test_blocks_writes_outside(ctx, target):
    assert bash(ctx, f"cp a.md {target}")
    assert bash(ctx, f"echo x > {target}")
    assert bash(ctx, f"mkdir {target}")
    assert tool(ctx, "Write", file_path=target, content="x")


def test_sibling_workspace_is_outside(ctx):
    """ops/t_1 no cubre ops/t_10 (contención por ruta, no por prefijo de texto)."""
    sib = ctx["tmp"] / "ops" / "t_10"
    assert bash(ctx, f"cp a.md {sib.as_posix()}/a.md")
    assert tool(ctx, "Write", file_path=str(sib / "a.md"), content="x")


def test_cd_changes_resolution(ctx):
    assert bash(ctx, "cd C:/Users && echo x > y.txt")
    assert bash(ctx, f"cd {ctx['dest'].as_posix()} && echo x > y.txt") is None


def test_write_tools(ctx):
    assert tool(ctx, "Write", file_path=str(ctx["ws"] / "informe.md"), content="x") is None
    assert tool(ctx, "Edit", file_path=str(ctx["dest"] / "a.md"), old_string="a", new_string="b") is None
    assert tool(ctx, "Write", file_path=str(ctx["ws"] / ".env"), content="x")
    assert tool(ctx, "NotebookEdit", notebook_path=str(ctx["ws"] / "a.ipynb"))


def test_read_tools_block_secrets_only(ctx):
    assert tool(ctx, "Read", file_path="E:/vault/nota.md") is None
    assert tool(ctx, "Read", file_path="C:/Users/oscar/oscar-command-hub/tools/agent-lanes/.env")
    assert tool(ctx, "Grep", pattern="x", path="C:/x/.env")
    assert tool(ctx, "Glob", pattern="**/.env*")
    assert tool(ctx, "Glob", pattern="**/*.md", path="E:/vault") is None


def test_mcp_read_allowlist(ctx):
    for name in ("mcp__claude_ai_Google_Calendar__list_events", "mcp__claude_ai_Google_Drive__search_files",
                 "mcp__claude_ai_ClickUp__clickup_get_task"):
        assert tool(ctx, name) is None
    for name in ("mcp__claude_ai_Google_Calendar__create_event", "mcp__claude_ai_Google_Drive__share_file",
                 "mcp__claude_ai_ClickUp__clickup_create_task", "mcp__claude_ai_ClickUp__clickup_execute_operator",
                 "mcp__claude_ai_Supabase__execute_sql", "mcp__claude_ai_Vercel__get_auth_token",
                 "mcp__claude_ai_Gmail__send_message", "mcp__sentry__update_issue"):
        assert tool(ctx, name), name


def test_unknown_tools_fail_closed(ctx):
    for name in ("PowerShell", "Agent", "Task", "WebFetch", "WebSearch", "Skill", "Monitor", "EnterWorktree"):
        assert tool(ctx, name), name
    for name in ("TodoWrite", "ToolSearch", "StructuredOutput"):
        assert tool(ctx, name) is None


def test_bad_dests_env_fails_closed(ctx):
    env = {**ctx["env"], "AGENT_LANES_OPS_DESTS": "no-json"}
    assert ops_guard.check({"tool_name": "Bash", "tool_input": {"command": "mkdir x"}, "cwd": str(ctx["ws"])}, env=env)


def test_no_workspace_blocks_all_writes(ctx):
    env = {"AGENT_LANES_ROLE": "ops"}
    assert ops_guard.check({"tool_name": "Write", "tool_input": {"file_path": str(ctx["ws"] / "a")},
                            "cwd": str(ctx["ws"])}, env=env)


def test_hook_process_exit_codes(ctx):
    def run(payload):
        return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload), capture_output=True,
                              text=True, env={**__import__("os").environ, **ctx["env"]})
    blocked = run({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}, "cwd": str(ctx["ws"])})
    assert blocked.returncode == 2 and "ops_guard" in blocked.stderr
    ok = run({"tool_name": "Bash", "tool_input": {"command": "cp E:/a.md a.md"}, "cwd": str(ctx["ws"])})
    assert ok.returncode == 0, ok.stderr
    bad = subprocess.run([sys.executable, str(GUARD)], input="{no json", capture_output=True, text=True)
    assert bad.returncode == 2


# --- endurecimiento (flags con =, enlaces, credenciales reales) ------------------------------------------

@pytest.mark.parametrize("cmd", [
    "git log --output=C:/Windows/x", "git diff --ext-diff=x", "gh api repos/o/r/collaborators/a --input=body.json",
    "gh api repos/o/r -fpermission=push", "gh api x --field=a=b", "gh api x --raw-field=a=b", "gh api x --method=PUT",
    "gh api x -XPUT", "robocopy E:/v vault /JOB:x.rcj", "robocopy E:/v vault /SAVE:x", "cp -l E:/a.md a.md",
    "cp --link E:/a.md a.md", "cp -s E:/a.md a.md", "cp -rl E:/v v", "cp --symbolic-link E:/a a",
    'cat "C:/Users/oscar/AppData/Roaming/GitHub CLI/hosts.yml"', "cat ~/.cloudflared/abc.json", "cat ~/.claude.json",
    "cat ~/.npmrc", "ls ~/.docker", "grep -r TunnelSecret ~", "grep -rn x C:/Users/oscar", "rg TunnelSecret ~",
    "cp -r ~ backup", "robocopy C:/Users/oscar vault /E", "cd ~/.cloudflared && cat x.json",
    "cat C:/Users/oscar/oscar-command-hub/tools/agent-lanes/.state/tg_offset", "sha256sum ~/.ssh/known_hosts",
])
def test_blocks_hardened(ctx, cmd):
    assert bash(ctx, cmd), cmd


@pytest.mark.parametrize("cmd", ["ls ~", "cloudflared tunnel list", "grep TODO ~/notas.md", "ls C:/Users/oscar",
                                 "cp -r E:/vault/. vault", "find C:/Users/oscar/Documents -name '*.md'"])
def test_hardening_keeps_legit_reads(ctx, cmd):
    assert bash(ctx, cmd) is None, bash(ctx, cmd)


def test_read_tools_block_protected_roots(ctx):
    assert tool(ctx, "Read", file_path="C:/Users/oscar/.cloudflared/abc.json")
    assert tool(ctx, "Read", file_path="C:/Users/oscar/AppData/Roaming/GitHub CLI/hosts.yml")
    assert tool(ctx, "Read", file_path="C:/Users/oscar/.claude.json")
    assert tool(ctx, "Grep", pattern="TunnelSecret", path="C:/Users/oscar")  # recursivo: contiene raíces protegidas
    assert tool(ctx, "Glob", pattern="C:/Users/oscar/.cloudflared/*.json")
    assert tool(ctx, "Glob", pattern="*.json", path="C:/Users/oscar/.cloudflared")
    assert tool(ctx, "Read", file_path=str(ctx["ws"] / "informe.md")) is None  # el workspace siempre se lee
    assert tool(ctx, "Grep", pattern="x", path="E:/vault") is None
