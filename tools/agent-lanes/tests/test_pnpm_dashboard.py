"""25-10: workers de claude-oscarhq y pnpm (guard, permisos renderizados, instalación en el worktree)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "contract"))
import guard  # noqa: E402

from agent_lanes import pnpm_setup, worker  # noqa: E402
from agent_lanes.config import load_lanes  # noqa: E402


def bash(cmd):
    return guard.check({"tool_name": "Bash", "tool_input": {"command": cmd}}, env={"AGENT_LANES_TASK": "t_1"})


@pytest.mark.parametrize("cmd", [
    "npx pnpm@10 install --frozen-lockfile",
    "pnpm install --frozen-lockfile",
    "corepack pnpm@10.18.3 install --frozen-lockfile --store-dir C:/Users/oscar/dev/_lanes/.pnpm-store",
    "npx pnpm@10 exec tsc --noEmit",
    "pnpm exec vitest run",
    "pnpm lint",
    "pnpm exec next lint",
    "npx pnpm@10 exec vitest run -c vitest.config.ts",
    "npm ci",
])
def test_pnpm_permitido(cmd):
    assert bash(cmd) is None, cmd


@pytest.mark.parametrize("cmd", [
    "pnpm add left-pad",
    "npx pnpm@10 add left-pad",
    "corepack pnpm@10.18.3 add -D x",
    "pnpm install left-pad",
    "pnpm dlx create-foo",
    "pnpm install -g typescript",
    "pnpm add -g x",
    "npm i -g typescript",
    "npm install --global x",
    "pnpm install --frozen-lockfile --dir C:/otro",
    "pnpm install --dir=../otro",
    "pnpm -C ../x install --frozen-lockfile",
    "npm install --prefix /tmp/x",
])
def test_pnpm_denegado(cmd):
    assert bash(cmd), cmd


def test_settings_renderizados_permiten_y_deniegan(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "STATE_DIR", tmp_path)
    out = json.loads(worker.render_settings().read_text(encoding="utf-8"))
    deny = out["permissions"]["deny"]
    assert "mcp__*" in deny
    for d in ("Bash(pnpm add:*)", "Bash(npm install -g:*)", "Bash(npm i -g:*)", "Bash(pnpm dlx:*)"):
        assert d in deny
    assert not any("install --frozen-lockfile" in d for d in deny)
    # la plantilla sigue enganchando el guard
    assert out["hooks"]["PreToolUse"][0]["hooks"][0]["args"][-1].endswith("contract/guard.py")


def test_lanes_yaml_permite_pnpm_del_worker():
    lanes = load_lanes()
    lane = lanes["claude-oscarhq"]
    for rule in ("Bash(npx pnpm@10 install:*)", "Bash(npx pnpm@10 exec:*)", "Bash(pnpm lint:*)",
                 "Bash(git add:*)", "Bash(git commit:*)"):
        assert rule in lane.allowed_tools, rule
    assert not any(" add:" in r and "pnpm" in r for r in lane.allowed_tools)


def test_rol_dice_pnpm_y_un_comando_por_llamada():
    text = (ROOT / "roles" / "implementador.md").read_text(encoding="utf-8")
    assert "pnpm" in text and "Un comando por llamada" in text and "nunca `npm ci`" in text


class _Cp:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _repo(tmp_path, *, nm=False):
    d = tmp_path / "wt" / "dashboard"
    d.mkdir(parents=True)
    (d / "package.json").write_text("{}")
    (d / "pnpm-lock.yaml").write_text("lockfileVersion: 9")
    if nm:
        (d / "node_modules").mkdir()
    return tmp_path / "wt"


def test_instala_con_proceso_falso_y_store_compartido(tmp_path):
    wt = _repo(tmp_path)
    calls = []

    def fake(cmd, **kw):
        calls.append((cmd, kw))
        return _Cp(0)

    res = pnpm_setup.ensure_pnpm_deps(str(wt), str(tmp_path / "root"), runner=fake)
    assert res == ["dashboard: ok"]
    cmd, kw = calls[0]
    assert "install" in cmd and "--frozen-lockfile" in cmd and "add" not in cmd
    assert cmd[cmd.index("--store-dir") + 1] == str(tmp_path / "root" / ".pnpm-store")
    assert Path(kw["cwd"]) == wt / "dashboard" and kw["timeout"] == pnpm_setup.INSTALL_TIMEOUT_SECONDS


def test_no_instala_si_ya_hay_node_modules_o_no_hay_lockfile(tmp_path):
    def boom(*a, **k):
        raise AssertionError("no debía ejecutarse")

    assert pnpm_setup.ensure_pnpm_deps(str(_repo(tmp_path, nm=True)), str(tmp_path), runner=boom) == []
    assert pnpm_setup.ensure_pnpm_deps(str(tmp_path / "nada"), str(tmp_path), runner=boom) == []


def test_fallo_y_timeout_no_lanzan(tmp_path):
    wt = _repo(tmp_path)
    res = pnpm_setup.ensure_pnpm_deps(str(wt), str(tmp_path), runner=lambda *a, **k: _Cp(1, "", "ERR_PNPM_X"))
    assert len(res) == 1 and "FALLÓ" in res[0] and "ERR_PNPM_X" in res[0]

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)

    res = pnpm_setup.ensure_pnpm_deps(str(wt), str(tmp_path), runner=slow)
    assert "timeout" in res[0]

    def gone(*a, **k):
        raise FileNotFoundError("corepack")

    assert "FALLÓ" in pnpm_setup.ensure_pnpm_deps(str(wt), str(tmp_path), runner=gone)[0]


def test_entorno_de_instalacion_sin_secretos(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("SUPABASE_API_KEY", "y")
    env = pnpm_setup.install_env(tmp_path)
    assert "GITHUB_TOKEN" not in env and "SUPABASE_API_KEY" not in env
    assert env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] == "0"
