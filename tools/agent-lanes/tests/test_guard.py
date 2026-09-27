import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "contract"))
import guard  # noqa: E402

GUARD = Path(__file__).resolve().parents[1] / "contract" / "guard.py"


def bash(cmd, db=None):
    return guard.check({"tool_name": "Bash", "tool_input": {"command": cmd}}, env={"DATABASE_URL": db} if db else {})


@pytest.mark.parametrize("cmd", [
    "git push origin master",
    "git push origin main",
    "git push origin HEAD:master",
    "git push origin lane/t_1:refs/heads/main",
    "git -C C:/repo push origin master",
    "git push --force origin lane/t_1",
    "git push -f origin lane/t_1",
    "git push --force-with-lease origin lane/t_1",
    "git push origin +lane/t_1",
    "git push",
    "git push origin",
    "git push origin feature/x",
    "git add . && git commit -m x && git push origin master",
    "gh pr merge 12 --squash",
    "railway up",
    "/c/x/railway.exe up --detach",
    "vercel --prod --yes",
    "node vercel.js --cwd x --prod",
    "vercel promote https://x.vercel.app",
    "alembic upgrade head",
    "DATABASE_URL=postgresql://u@db.supabase.co/x alembic upgrade head",
    "echo x > .github/workflows/ci.yml",
    "cp a.yml .github/workflows/a.yml",
])
def test_blocks(cmd):
    assert bash(cmd), cmd


@pytest.mark.parametrize("cmd", [
    "git push -u origin lane/t_1",
    "git push origin HEAD:lane/t_1",
    "git status && git log --oneline -3",
    "gh pr view 12",
    "vercel ls",
    "alembic heads",
    "DATABASE_URL=sqlite:///local.db alembic upgrade head",
    "cat .github/workflows/ci.yml",
    "py -3.12 -m pytest tests -q",
])
def test_allows(cmd):
    assert bash(cmd) is None, cmd


def test_alembic_env_local_allowed():
    assert bash("alembic upgrade head", db="postgresql://localhost:5432/x") is None


def test_edit_workflows_blocked_and_normal_edit_allowed():
    assert guard.check({"tool_name": "Write", "tool_input": {"file_path": "C:\\r\\.github\\workflows\\ci.yml"}})
    assert guard.check({"tool_name": "Edit", "tool_input": {"file_path": "C:/r/docs/a.md"}}) is None


def test_process_exit_codes():
    def run(payload):
        return subprocess.run([sys.executable, str(GUARD)], input=json.dumps(payload), capture_output=True, text=True)

    blocked = run({"tool_name": "Bash", "tool_input": {"command": "git push origin master"}})
    assert blocked.returncode == 2 and "BLOQUEADO" in blocked.stderr
    assert run({"tool_name": "Bash", "tool_input": {"command": "ls"}}).returncode == 0
    bad = subprocess.run([sys.executable, str(GUARD)], input="not json", capture_output=True, text=True)
    assert bad.returncode == 2


@pytest.mark.parametrize("tool", ["mcp__claude_ai_Supabase__execute_sql", "mcp__claude_ai_Supabase__apply_migration",
                                  "mcp__anything__x"])
def test_mcp_tools_blocked(tool):
    assert guard.check({"tool_name": tool, "tool_input": {"query": "select 1"}})
