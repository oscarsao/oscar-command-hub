"""E1 del worker remoto por SSH (specs/worker-remoto-ssh.md): todo con un ejecutor `FakeSsh`; nunca hay SSH real,
ni otro equipo, ni red. Las pruebas de paridad fijan que sin `host:` nada cambia."""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from agent_lanes import config, remote_exec, ssh_host
from agent_lanes.config import HostCfg, load_lanes
from agent_lanes.git_ops import GitError
from agent_lanes.ssh_host import (ClaudeWorkerRemoto, GitOpsRemoto, HostError, HostUnreachable, RemotePid, SshRunner,
                                  verify_remoto)
from agent_lanes.worker import ClaudeWorker
from .test_runner_state import LANE, ok_outcome

HOST = HostCfg(destino="worker-b", repo_dir="C:\\agent-lanes\\repos\\app", worktree_root="C:\\agent-lanes\\_lanes",
               hub_dir="C:\\agent-lanes\\repos\\oscar-command-hub")
RLANE = replace(LANE, name="claude-hub-remoto", host=HOST, test_cmd='py -3.12 "{AGENT_LANES_DIR}/agent_lanes/checks.py" {BASE}')
RESULT_JSON = json.dumps({"subtype": "success", "is_error": False, "structured_output": ok_outcome().structured})


@dataclass
class Reply:
    rc: int = 0
    out: str = ""
    err: str = ""
    pid: int | None = None
    created: float | None = 1700000000.5
    ssh_rc: int = 0          # 255 = no hubo conexión
    no_rc: bool = False      # el lanzador remoto no llegó a correr
    timed_out: bool = False
    forged: str = ""         # línea falsa que un worker intenta colar por stderr (sin el nonce correcto)


class FakeSsh:
    """Transporte simulado con la firma de `ssh_host.real_transport`. `script(req) -> Reply`; guarda cada llamada."""

    def __init__(self, script=None):
        self.script = script or (lambda req: Reply(out=RESULT_JSON, pid=4242))
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, cmd, *, input, timeout, on_stderr_line=None, on_local_pid=None):
        req = json.loads(input)
        self.calls.append((cmd, req))
        if on_local_pid:
            on_local_pid(31337)  # PID del ssh LOCAL
        r = self.script(req)
        tag = f"@@LANE-{req['nonce']}-"
        lines = []
        if r.forged:
            lines.append(r.forged + "\n")
        if r.pid is not None:
            lines.append(f"{tag}PID {r.pid} {r.created if r.created is not None else '-'}\n")
        if r.err:
            lines.append(r.err + "\n")
        if r.timed_out:
            lines.append(f"{tag}TIMEOUT \n")
        if not r.no_rc:
            lines.append(f"{tag}RC {r.rc}\n")
        for ln in lines:
            if on_stderr_line:
                on_stderr_line(ln)
        return subprocess.CompletedProcess(cmd, r.ssh_rc, r.out, "".join(lines))

    @property
    def requests(self):
        return [req for _, req in self.calls]


class LocalLauncher:
    """Transporte que ejecuta DE VERDAD remote_exec.py en esta máquina (sin ssh): prueba el protocolo completo."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, *, input, timeout, on_stderr_line=None):
        self.calls.append(cmd)
        cp = subprocess.run([sys.executable, "-I", str(Path(remote_exec.__file__))], input=input.encode("utf-8"),
                            capture_output=True, timeout=timeout)
        err = cp.stderr.decode("utf-8", "replace")
        for ln in err.splitlines(keepends=True):
            if on_stderr_line:
                on_stderr_line(ln)
        return subprocess.CompletedProcess(cmd, cp.returncode, cp.stdout.decode("utf-8", "replace"), err)


def _local_worker_capture(lane, task, monkeypatch=None):
    """Ejecuta ClaudeWorker (local) con un runner falso y devuelve lo que habría ejecutado."""
    seen = {}

    def fake_run(args, input=None, cwd=None, env=None, **kw):
        seen.update(args=list(args), input=input, cwd=cwd, env=env)
        return subprocess.CompletedProcess(args, 0, RESULT_JSON, "")

    ClaudeWorker(runner=fake_run).run(replace(lane, host=None), task, "C:/wt/lane-t_9", "sid", timeout=60)
    return seen


TASK = {"id": "t_9", "title": "Tarea", "body": "Cuerpo de la tarjeta con acentos: ñ é"}


# --- 1. el worker remoto ejecuta lo mismo que el local ----------------------------------------------------------------

def test_remote_prompt_and_args_equal_local(monkeypatch):
    monkeypatch.setenv("OHQ_API_KEY", "super-secreto-del-hub")
    monkeypatch.setenv("AGENT_LANES_LANE_EXTRA", "1")
    local = _local_worker_capture(RLANE, TASK)
    ssh = FakeSsh()
    out = ClaudeWorkerRemoto(transport=ssh).run(RLANE, TASK, "C:\\agent-lanes\\_lanes\\lane-t_9", "sid", timeout=60)
    assert out.ok
    (cmd, req), = ssh.calls
    assert req["args"] == local["args"] and req["input"] == local["input"]
    assert "--strict-mcp-config" in req["args"] and '{"mcpServers":{}}' in req["args"]  # sin MCP
    assert req["args"][req["args"].index("--max-budget-usd") + 1] == local["args"][local["args"].index("--max-budget-usd") + 1]
    assert req["args"][req["args"].index("--model") + 1] == RLANE.model
    assert req["cwd"] == "C:\\agent-lanes\\_lanes\\lane-t_9"
    # entorno: solo AGENT_LANES_*; ningún secreto del hub viaja
    assert req["env"] and all(k.startswith("AGENT_LANES_") for k in req["env"])
    assert req["env"]["AGENT_LANES_TASK"] == "t_9" and req["env"]["AGENT_LANES_WORKTREE"] == req["cwd"]
    assert "OHQ_API_KEY" not in json.dumps(req) and "super-secreto" not in json.dumps(req)
    # el comando remoto es fijo y ssh no interactivo, sin StrictHostKeyChecking=no
    assert cmd[0] == "ssh" and "BatchMode=yes" in cmd and "--" in cmd and "worker-b" in cmd
    assert cmd[-1] == 'py -3.12 "C:\\agent-lanes\\repos\\oscar-command-hub\\tools\\agent-lanes\\agent_lanes\\remote_exec.py"'
    assert "StrictHostKeyChecking=yes" in cmd and not any("StrictHostKeyChecking=no" in c for c in cmd)


def test_resume_remote_keeps_session_and_model():
    ssh = FakeSsh()
    w = ClaudeWorkerRemoto(transport=ssh)
    w.run(RLANE, TASK, "C:\\wt\\lane-t_9", "sid", timeout=60)
    w.resume(RLANE, "C:\\wt\\lane-t_9", "sid", timeout=60, task_id="t_9")
    first, second = ssh.requests
    assert "--session-id" in first["args"] and "--resume" in second["args"]
    assert second["args"][second["args"].index("--model") + 1] == first["args"][first["args"].index("--model") + 1]
    assert "--strict-mcp-config" in second["args"]


# --- 2. PID remoto en el estado ----------------------------------------------------------------------------------------

def test_remote_pid_goes_to_host_keys_not_worker_pid(tmp_path):
    """El PID remoto va a claves propias (nunca a worker_pid) y SE CONSERVA si el transporte cae (puede seguir vivo)."""
    (tmp_path / "t_9.json").write_text(json.dumps({"task_id": "t_9", "pid": 1}))
    ssh = FakeSsh(lambda req: Reply(pid=4242, created=1700000123.25, ssh_rc=255, no_rc=True))
    with pytest.raises(HostUnreachable):
        ClaudeWorkerRemoto(pid_dir=tmp_path, transport=ssh).run(RLANE, TASK, "C:\\wt\\lane-t_9", "sid", timeout=60)
    state = json.loads((tmp_path / "t_9.json").read_text())
    assert state["host"] == "worker-b" and state["host_worker_pid"] == 4242 and state["host_worker_created"] == 1700000123.25
    assert state["worker_pid"] == 31337  # el ssh LOCAL (reconcile lo mata); el PID remoto va aparte, nunca aquí
    assert state["pid"] == 1 and state["host_worker_started"] > 0


def test_remote_pid_is_cleared_when_the_remote_process_finished(tmp_path):
    (tmp_path / "t_9.json").write_text(json.dumps({"task_id": "t_9", "pid": 1}))
    ssh = FakeSsh(lambda req: Reply(out=RESULT_JSON, pid=4242, created=1700000123.25))
    ClaudeWorkerRemoto(pid_dir=tmp_path, transport=ssh).run(RLANE, TASK, "C:\\wt\\lane-t_9", "sid", timeout=60)
    state = json.loads((tmp_path / "t_9.json").read_text())
    assert "host_worker_pid" not in state and "host_worker_created" not in state and state["host"] == "worker-b"


def test_on_start_receives_remote_pid_object():
    seen = []
    SshRunner(HOST, FakeSsh())(["claude", "-p"], input="x", cwd="C:\\x", env={}, timeout=5, on_start=seen.append)
    assert seen == [4242] and isinstance(seen[0], RemotePid) and seen[0].host == "worker-b" and seen[0].created == 1700000000.5


# --- 3. fallos de transporte frente a fallos del worker -----------------------------------------------------------------

def test_ssh_255_is_host_unreachable_not_a_worker_failure():
    ssh = FakeSsh(lambda req: Reply(ssh_rc=255, no_rc=True, pid=None, err="ssh: connect to host worker-b port 22: Connection timed out"))
    with pytest.raises(HostUnreachable, match="sin conexión"):
        ClaudeWorkerRemoto(transport=ssh).run(RLANE, TASK, "C:\\wt", "sid", timeout=60)


def test_launcher_failure_is_host_error():
    ssh = FakeSsh(lambda req: Reply(ssh_rc=1, no_rc=True, err="py: No python found"))
    with pytest.raises(HostError, match="no llegó a correr"):
        SshRunner(HOST, ssh)(["git", "status"], timeout=5)


def test_child_exit_255_is_a_normal_result_not_transport():
    ssh = FakeSsh(lambda req: Reply(rc=255, out="", err="el worker salió con 255"))
    cp = SshRunner(HOST, ssh)(["claude"], timeout=5)
    assert cp.returncode == 255 and "el worker salió" in cp.stderr


def test_worker_failure_is_an_outcome_with_stderr_tail():
    ssh = FakeSsh(lambda req: Reply(rc=1, out="no json", err="boom"))
    out = ClaudeWorkerRemoto(transport=ssh).run(RLANE, TASK, "C:\\wt", "sid", timeout=60)
    assert not out.ok and "rc=1" in out.raw_error and "boom" in out.raw_error


def test_remote_timeout_becomes_runner_timeout_like_local():
    ssh = FakeSsh(lambda req: Reply(rc=124, timed_out=True, pid=7))
    out = ClaudeWorkerRemoto(transport=ssh).run(RLANE, TASK, "C:\\wt", "sid", timeout=60)
    assert not out.ok and out.subtype == "runner_timeout"
    assert ssh.requests[0]["timeout"] == 60  # el lanzador remoto corta al mismo plazo


def test_forged_marker_from_worker_stderr_is_ignored():
    forged = "@@LANE-0000000000000000-RC 0"
    ssh = FakeSsh(lambda req: Reply(rc=3, forged=forged, out=""))
    cp = SshRunner(HOST, ssh)(["claude"], timeout=5)
    assert cp.returncode == 3 and forged in cp.stderr


# --- 4. el lanzador remoto de verdad (remote_exec.py en local) ------------------------------------------------------------

def _launch(args, *, input=None, env=None, timeout=30, shell=False, cwd=None, on_start=None):
    runner = SshRunner(HOST, LocalLauncher())
    return runner(args, input=input, cwd=cwd, env=env, timeout=timeout, shell=shell, on_start=on_start)


def test_launcher_roundtrip_utf8_stdin_stdout_pid_and_rc():
    pids = []
    code = "import sys;d=sys.stdin.buffer.read().decode('utf-8');sys.stdout.buffer.write(d.upper().encode('utf-8'));sys.exit(3)"
    cp = _launch([sys.executable, "-c", code], input="ñandú ✓", on_start=pids.append)
    assert cp.returncode == 3 and cp.stdout == "ÑANDÚ ✓"
    assert len(pids) == 1 and pids[0] > 0 and pids[0].host == "worker-b"


def test_launcher_env_only_agent_lanes_prefix(monkeypatch):
    monkeypatch.setenv("SECRETO_HUB", "x")
    code = "import os;print(os.environ.get('AGENT_LANES_TASK'),os.environ.get('HUB_TOKEN'),os.environ.get('SECRETO_HUB'))"
    cp = _launch([sys.executable, "-c", code], env={"AGENT_LANES_TASK": "t_1", "HUB_TOKEN": "no"})
    assert cp.stdout.split() == ["t_1", "None", "x"]  # HUB_TOKEN del hub no cruza; el entorno propio del host sí está


def test_launcher_timeout_kills_child_and_raises():
    with pytest.raises(subprocess.TimeoutExpired):
        _launch([sys.executable, "-c", "import time;time.sleep(30)"], timeout=1)


def test_launcher_missing_executable_is_rc_127_not_transport_error():
    cp = _launch(["no-existe-este-ejecutable-xyz"])
    assert cp.returncode == 127 and "no se pudo lanzar" in cp.stderr


def test_launcher_shell_mode_and_cwd(tmp_path):
    cp = _launch(f'"{sys.executable}" -c "import os;print(os.getcwd())"', shell=True, cwd=str(tmp_path))
    assert cp.returncode == 0 and Path(cp.stdout.strip()).resolve() == tmp_path.resolve()


def test_launcher_translates_hub_paths_and_renders_settings(tmp_path, monkeypatch):
    (tmp_path / "contract").mkdir()
    (tmp_path / "contract" / "x-settings.json").write_text('{"hook": "{AGENT_LANES_DIR}/contract/guard.py"}', encoding="utf-8")
    monkeypatch.setattr(remote_exec, "ROOT", tmp_path)
    hub = "C:\\Users\\oscar\\oscar-command-hub\\tools\\agent-lanes"
    arg = f"{hub}\\roles\\implementador.md"
    assert remote_exec.translate(arg, [hub, hub.replace("\\", "/")]) == f"{tmp_path}\\roles\\implementador.md"
    out = tmp_path / ".state" / "x-settings.rendered.json"
    remote_exec.render_settings_if_needed(["claude", "--settings", str(out)])
    assert json.loads(out.read_text(encoding="utf-8"))["hook"] == f"{tmp_path.as_posix()}/contract/guard.py"


# --- 5. git en el host -----------------------------------------------------------------------------------------------------

def _git_script(branch_local=False, remote_branch=False, existing_wt=None, fail_fetch=False):
    def script(req):
        a = req["args"]
        assert a[0] == "git" and a[1] == "-C"
        sub = a[3:]
        if sub[:1] == ["fetch"] and len(sub) == 3 and sub[2].startswith("+refs/heads/lane/"):
            return Reply(rc=0 if remote_branch else 128)
        if sub[:1] == ["fetch"]:
            return Reply(rc=1 if fail_fetch else 0, err="fatal: sin red" if fail_fetch else "")
        if sub[:2] == ["branch", "--show-current"]:
            return Reply(rc=0, out=existing_wt + "\n") if existing_wt else Reply(rc=128, err="fatal: cannot change directory")
        if sub[:3] == ["rev-parse", "--verify", "--quiet"]:
            return Reply(rc=0 if branch_local else 1)
        return Reply(rc=0)
    return script


def _cmds(ssh):
    return [" ".join(r["args"][3:]) for r in ssh.requests]


def test_prepare_worktree_creates_branch_from_base_in_host():
    ssh = FakeSsh(_git_script())
    path = GitOpsRemoto(ssh).prepare_worktree(RLANE, "t_9")
    assert path == "C:\\agent-lanes\\_lanes\\lane-t_9"
    cmds = _cmds(ssh)
    assert cmds[0] == "fetch origin master"
    assert any(c == f"worktree add {path} -b lane/t_9 origin/master" for c in cmds)
    assert all(r["args"][2] in (HOST.repo_dir, path) for r in ssh.requests)  # solo toca el clon y el worktree del host


def test_prepare_worktree_reopened_task_starts_from_remote_branch():
    ssh = FakeSsh(_git_script(remote_branch=True))
    path = GitOpsRemoto(ssh).prepare_worktree(RLANE, "t_9")
    assert f"worktree add {path} -b lane/t_9 origin/lane/t_9" in _cmds(ssh)


def test_prepare_worktree_reuses_existing_and_rejects_wrong_branch():
    ssh = FakeSsh(_git_script(existing_wt="lane/t_9"))
    assert GitOpsRemoto(ssh).prepare_worktree(RLANE, "t_9").endswith("lane-t_9")
    assert not any(c.startswith("worktree add") for c in _cmds(ssh))
    with pytest.raises(GitError, match="no en lane/t_9"):
        GitOpsRemoto(FakeSsh(_git_script(existing_wt="lane/otra"))).prepare_worktree(RLANE, "t_9")


def test_prepare_worktree_git_failure_is_giterror_and_transport_failure_propagates():
    with pytest.raises(GitError, match="worker-b"):
        GitOpsRemoto(FakeSsh(_git_script(fail_fetch=True)), sleep=lambda s: None).prepare_worktree(RLANE, "t_9")
    down = FakeSsh(lambda req: Reply(ssh_rc=255, no_rc=True))
    with pytest.raises(HostUnreachable):
        GitOpsRemoto(down).prepare_worktree(RLANE, "t_9")


def test_sync_base_conflict_in_host():
    def script(req):
        sub = req["args"][3:]
        if sub[:1] == ["merge"]:
            return Reply(rc=1, out="CONFLICT (content): Merge conflict in a.txt")
        return Reply()
    ops = GitOpsRemoto(FakeSsh(script))
    assert "CONFLICT" in ops.sync_base(RLANE, "C:\\wt\\lane-t_9")


# --- 6. verify dividido: no se fía del host ---------------------------------------------------------------------------------

SHA = "a" * 40


class HubGit:
    """Dobles del git del HUB (lane.repo contra GitHub): guionizados por subcomando."""

    def __init__(self, remote_sha=SHA, ahead="3", diff="", changed="docs/x.md\n", fetch_rc=0, cat_rc=0):
        self.calls, self.cfg = [], dict(remote_sha=remote_sha, ahead=ahead, diff=diff, changed=changed, fetch_rc=fetch_rc,
                                       cat_rc=cat_rc)

    def __call__(self, cmd, timeout=None, **kw):
        assert cmd[:2] == ["git", "-C"] and cmd[2] == RLANE.repo, cmd  # nada se ejecuta en un worktree local inexistente
        sub = cmd[3:]
        if sub[:2] == ["-c", "core.quotePath=false"]:
            sub = sub[2:]
        self.calls.append(sub)
        c = self.cfg
        out, rc = "", 0
        if sub[0] == "ls-remote":
            out = f"{c['remote_sha']}\trefs/heads/lane/t_1\n" if c["remote_sha"] else ""
        elif sub[0] == "fetch":
            rc = c["fetch_rc"]
        elif sub[0] == "cat-file":
            rc = c["cat_rc"]
        elif sub[0] == "rev-list":
            out = c["ahead"] + "\n"
        elif sub[0] == "diff" and "--name-only" in sub:
            assert "-z" in sub  # rutas separadas por NUL
            out = c["changed"].replace(chr(10), chr(0))
        elif sub[0] == "diff":
            out = c["diff"]
        return subprocess.CompletedProcess(cmd, rc, out, "")


GOOD = {"branch": "lane/t_1", "head_sha": SHA}


def _verify(hub, host_script=None, lane=RLANE, result=GOOD):
    def default(req):
        a = req.get("args", [])
        return Reply(out=SHA + "\n") if a[-1:] == ["HEAD"] else Reply(out="") if "status" in a else Reply(out="ok")

    def limpio(script):  # el worktree del host está limpio salvo que el test diga otra cosa
        return lambda req: Reply(out="") if "status" in req.get("args", []) else script(req)
    ssh = FakeSsh(limpio(host_script) if host_script else default)
    return verify_remoto(lane, "t_1", "C:\\wt\\lane-t_1", result, runner=hub, transport=ssh), ssh


def test_verify_ok_runs_git_in_hub_and_tests_in_host():
    hub = HubGit()
    res, ssh = _verify(hub)
    assert res.ok and res.remote_sha == SHA and res.test_exit == 0
    assert [c[0] for c in hub.calls][:3] == ["ls-remote", "fetch", "cat-file"]  # se trae la rama a lane.repo antes de leerla
    head_req, _status_req, test_req = ssh.requests
    assert head_req["args"] == ["git", "-C", "C:\\wt\\lane-t_1", "rev-parse", "HEAD"]
    assert test_req["shell"] and test_req["cwd"] == "C:\\wt\\lane-t_1"
    assert "C:/agent-lanes/repos/oscar-command-hub/tools/agent-lanes/agent_lanes/checks.py" in test_req["cmd"]  # {AGENT_LANES_DIR} del HOST
    assert "origin/master" in test_req["cmd"] and "{" not in test_req["cmd"]


def test_verify_does_not_trust_declared_sha_or_host():
    res, ssh = _verify(HubGit(remote_sha="b" * 40))
    assert not res.ok and "head_sha declarado" in res.reasons[0] and res.test_exit is None
    assert not ssh.calls or all(r.get("shell") is not True for r in ssh.requests)  # test_cmd no corre en una rama dudosa


def test_verify_branch_missing_on_github_even_if_host_says_pushed():
    res, ssh = _verify(HubGit(remote_sha=None))
    assert not res.ok and "no existe en origin" in res.reasons[0] and not ssh.calls


def test_verify_host_head_differs_from_github_blocks_tests():
    res, ssh = _verify(HubGit(), lambda req: Reply(out=("c" * 40 + "\n") if req["args"][-1:] == ["HEAD"] else Reply()))
    assert not res.ok and any("HEAD del host" in r for r in res.reasons)
    assert not any(r.get("shell") for r in ssh.requests)


def test_verify_dirty_host_worktree_blocks_tests():
    def host(req):
        a = req.get("args", [])
        return Reply(out=SHA + "\n") if a[-1:] == ["HEAD"] else Reply(out="?? suelto.py\n")
    ssh = FakeSsh(host)
    res = verify_remoto(RLANE, "t_1", "C:\\wt\\lane-t_1", GOOD, runner=HubGit(), transport=ssh)
    assert not res.ok and "sin commitear" in res.reasons[0] and not any(r.get("shell") for r in ssh.requests)


def test_only_whitelisted_env_travels_even_with_agent_lanes_prefix():
    ssh = FakeSsh()
    SshRunner(HOST, ssh)(["x"], env={"AGENT_LANES_TASK": "t", "AGENT_LANES_API_TOKEN": "s", "OTRA": "o"}, timeout=5)
    assert ssh.requests[0]["env"] == {"AGENT_LANES_TASK": "t"}


def test_unreachable_after_pid_warns_worker_may_be_alive():
    ssh = FakeSsh(lambda req: Reply(ssh_rc=255, no_rc=True, pid=99))
    with pytest.raises(HostUnreachable, match="pudo seguir vivo"):
        SshRunner(HOST, ssh)(["claude"], timeout=5, on_start=lambda p: None)


def test_empty_host_block_and_host_with_repos_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="vacío"):
        load_lanes(_yaml(tmp_path, "    host: {}\n"))
    body = ("    repos: {a: {repo: C:/x}}\n    host: {destino: w, repo_dir: 'C:\\a', worktree_root: 'C:\\b', hub_dir: 'C:\\h'}\n")
    with pytest.raises(ValueError, match="repos"):
        load_lanes(_yaml(tmp_path, body))


def test_verify_test_cmd_exit_nonzero_fails_with_exit_code():
    def host(req):
        if req["args"][-1:] == ["HEAD"] if req.get("args") else False:
            return Reply(out=SHA + "\n")
        return Reply(rc=2, out="1 failed")
    res, _ = _verify(HubGit(), host)
    assert not res.ok and res.test_exit == 2 and "exit 2" in res.reasons[0]


def test_verify_transport_failure_is_not_reported_as_red_tests():
    def host(req):
        if req.get("args"):
            return Reply(out=SHA + "\n")
        return Reply(ssh_rc=255, no_rc=True)
    res, _ = _verify(HubGit(), host)
    assert not res.ok and res.test_exit is None and "no se pudo ejecutar test_cmd en el host" in res.reasons[0]
    res2, _ = _verify(HubGit(), lambda req: Reply(ssh_rc=255, no_rc=True))
    assert not res2.ok and "no se pudo comprobar el HEAD del host" in res2.reasons[0]


def test_verify_git_part_checks_forbidden_paths_no_commits_and_secrets():
    lane = replace(RLANE, forbidden_paths=(".github/workflows/",))
    res, _ = _verify(HubGit(changed=".github/workflows/x.yml\n"), lane=lane)
    assert not res.ok and "rutas vetadas" in " ".join(res.reasons)
    res, _ = _verify(HubGit(ahead="0"))
    assert not res.ok and "no tiene commits" in " ".join(res.reasons)
    secret = ("diff --git a/f.env b/f.env\n--- /dev/null\n+++ b/f.env\n@@ -0,0 +1 @@\n+" + "AKIA" + "ABCDEFGHIJKLMNOP\n")
    res, ssh = _verify(HubGit(diff=secret))
    assert not res.ok and "posible secreto" in res.reasons[0] and not ssh.calls
    res, _ = _verify(HubGit(fetch_rc=1))
    assert not res.ok and "no se pudo traer" in res.reasons[0]


# --- 7. config: opt-in y paridad --------------------------------------------------------------------------------------------

def _yaml(tmp_path, lane_body, defaults=""):
    p = tmp_path / "lanes.yaml"
    p.write_text(f"{defaults}lanes:\n  l1:\n    board: b\n    repo: C:/r\n    base: main\n{lane_body}", encoding="utf-8")
    return p


def test_lane_without_host_is_local_and_real_lanes_yaml_has_none():
    assert LANE.host is None and config.Lane(name="x").host is None
    lanes = load_lanes()
    assert lanes and all(l.host is None for l in lanes.values())  # el lanes.yaml real no activa ningún host


def test_host_parsed_clamps_max_parallel_and_ignores_future_keys(tmp_path):
    body = ("    max_parallel: 3\n    host_gasto_dia_usd: 15\n    host:\n      destino: worker-b\n"
            "      repo_dir: 'C:\\agent-lanes\\repos\\hub'\n      hub_dir: 'C:\\agent-lanes\\repos\\hub'\n      worktree_root: 'C:\\agent-lanes\\_lanes'\n"
            "      max_parallel: 1\n      latido_s: 60\n      disco_min_gb: 10\n")
    lane = load_lanes(_yaml(tmp_path, body))["l1"]
    assert lane.host.destino == "worker-b" and lane.host.conexion_timeout_s == 15
    assert lane.max_parallel == 1 and lane.host.agent_lanes_dir == "C:/agent-lanes/repos/hub/tools/agent-lanes"


@pytest.mark.parametrize("bad", [
    "destino: -oProxyCommand=x", "destino: 'a b'", "repo_dir: relativa", "repo_dir: 'C:\\a\"&calc'",
    "repo_dir: 'C:\\a\\..\\b'", "max_parallel: 0", "conexion_timeout_s: 0", "otra: 1"])
def test_host_rejects_unsafe_or_unknown_values(tmp_path, bad):
    base = {"destino": "w", "repo_dir": "C:\\a", "worktree_root": "C:\\b", "hub_dir": "C:\\h"}
    key, _, val = bad.partition(": ")
    lines = {**base, key: val.strip("'")} if key in base else {**base, key: val}
    body = "    host:\n" + "".join(f"      {k}: '{v}'\n" if isinstance(v, str) else f"      {k}: {v}\n" for k, v in lines.items())
    with pytest.raises(ValueError):
        load_lanes(_yaml(tmp_path, body))


def test_host_only_for_implement_lanes_and_never_in_defaults(tmp_path):
    host = "    host: {destino: w, repo_dir: 'C:\\a', worktree_root: 'C:\\b', hub_dir: 'C:\\h'}\n"
    with pytest.raises(ValueError, match="kind: implement"):
        load_lanes(_yaml(tmp_path, "    kind: ops\n" + host))
    with pytest.raises(ValueError, match="defaults"):
        load_lanes(_yaml(tmp_path, "", defaults="defaults:\n  host: {destino: w}\n"))


def test_local_worker_still_runs_locally_with_full_env_and_local_pid(tmp_path, monkeypatch):
    """Paridad: ClaudeWorker de siempre sigue usando su runner local, el entorno completo y `worker_pid`."""
    monkeypatch.setenv("UNA_VAR_DEL_ENTORNO", "valor")
    (tmp_path / "t_9.json").write_text(json.dumps({"task_id": "t_9"}))
    seen = {}

    def fake_run(args, on_start=None, env=None, **kw):
        seen["env"] = env
        on_start(4242)
        return subprocess.CompletedProcess(args, 0, RESULT_JSON, "")

    ClaudeWorker(runner=fake_run, pid_dir=tmp_path).run(replace(RLANE, host=None), TASK, "C:/wt", "sid", timeout=60)
    state = json.loads((tmp_path / "t_9.json").read_text())
    assert seen["env"]["UNA_VAR_DEL_ENTORNO"] == "valor" and state["worker_pid"] == 4242 and "host" not in state


def test_local_verify_and_gitops_do_not_know_about_ssh(monkeypatch):
    """Paridad: con host=None el camino local no importa ni usa nada de ssh_host."""
    from agent_lanes import git_ops, verify
    for mod in (git_ops, verify):
        assert "ssh_host" not in Path(mod.__file__).read_text(encoding="utf-8")


# --- 8. revisión 21-11: review/integrador, repos sensibles, límites y robustez ---------------------------------------------

def test_review_does_not_run_or_execute_a_branch_from_a_host_lane():
    from tests.test_phase2 import REVIEW, FakeReviewGit, FakeVerifier, ReviewHermes
    from tests.test_integrator import FakeTg
    from agent_lanes.review import ReviewRunner
    h = ReviewHermes([{"id": "t_1", "title": "T", "body": "B", "assignee": "claude-oscarhq", "status": "review"}])
    git, ver = FakeReviewGit(), FakeVerifier(True)
    llamadas, prepared = [], []

    class Rv:
        def run(self, *a, **k):
            llamadas.append("run")

        resume = run

    git.prepare_review_worktree = lambda lane, tid: prepared.append(tid)
    r = ReviewRunner(REVIEW, {"claude-oscarhq": replace(LANE, host=HOST)}, hermes_for=lambda b: h, git=git,
                     reviewer=Rv(), verifier=ver, notify=FakeTg())
    assert r.run_once() == {"t_1": "blocked:needs_input"}
    assert not prepared and not llamadas  # ni worktree en el hub, ni test_cmd, ni revisor con Bash
    assert any(c[0] == "block" and "aprobación humana" in c[3] for c in h.calls)


def test_integrator_never_runs_test_cmd_on_a_host_lane_branch(tmp_path, monkeypatch):
    from tests import test_integrator as ti
    orig = ti.lane
    monkeypatch.setattr(ti, "lane", lambda name="claude-oscarhq", tmp=None: replace(orig(name, tmp), host=HOST))
    integ, w, h, tg, _, _ = ti.make(tmp_path)
    assert integ.run_pass() == {"t_1": "gates_failed"}
    assert not [c for c in w.calls if c[1].get("shell")]  # test_cmd nunca se ejecuta en el hub


@pytest.mark.parametrize("repo,github", [("C:/Users/oscar/oscar-command-hub", ""), ("C:/x/y", "oscarsao/oscar-hq"),
                                          ("C:/x/OSCAR-HQ.git", "")])
def test_host_rejected_for_sensitive_repos(tmp_path, repo, github):
    p = tmp_path / "lanes.yaml"
    p.write_text(f"lanes:\n  l1:\n    board: b\n    base: main\n    repo: {repo}\n    github: '{github}'\n"
                 "    host: {destino: w, repo_dir: 'C:/a', worktree_root: 'C:/b', hub_dir: 'C:/h'}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="repo sensible"):
        load_lanes(p)


def test_sensitive_repo_list_is_united_not_replaced_and_hub_dir_is_required(tmp_path):
    host = "    host: {destino: w, repo_dir: 'C:/a', worktree_root: 'C:/b', hub_dir: 'C:/hub'}\n"
    p = tmp_path / "lanes.yaml"
    # La lista de lanes.yaml SE UNE a la de fábrica: [otro] o [] no dejan fuera hub ni oscar-hq.
    for lista in ("[otro]", "[]", "[migrateam]"):
        p.write_text(f"host_sensitive_repos: {lista}\nlanes:\n  l1:\n    board: b\n    base: main\n    repo: C:/x/oscar-hq\n"
                     + host, encoding="utf-8")
        with pytest.raises(ValueError, match="repo sensible"):
            load_lanes(p)
    p.write_text("host_sensitive_repos: [migrateam]\nlanes:\n  l1:\n    board: b\n    base: main\n    repo: C:/x/migrateam\n"
                 + host, encoding="utf-8")
    with pytest.raises(ValueError, match="migrateam"):  # y la lista propia suma
        load_lanes(p)
    p.write_text("lanes:\n  l1:\n    board: b\n    base: main\n    repo: C:/x/app\n"
                 "    host: {destino: w, repo_dir: 'C:/a', worktree_root: 'C:/b'}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hub_dir"):  # hub_dir obligatorio: no se cae en repo_dir
        load_lanes(p)
    p.write_text("lanes:\n  l1:\n    board: b\n    base: main\n    repo: C:/x/app\n" + host, encoding="utf-8")
    lane = load_lanes(p)["l1"]
    assert lane.host.agent_lanes_dir == "C:/hub/tools/agent-lanes"
    assert ssh_host.launcher_command(lane.host) == 'py -3.12 "C:/hub\\tools\\agent-lanes\\agent_lanes\\remote_exec.py"'


def test_sensitive_check_covers_host_repo_dir_and_dot_segments_and_hub_aliases(tmp_path):
    host = "    host: {destino: w, repo_dir: '%s', worktree_root: 'C:/b', hub_dir: 'C:/hub'}\n"
    p = tmp_path / "lanes.yaml"
    hub = config.ROOT.parents[1].as_posix()
    casos = [("C:/x/app", "C:/agent-lanes/repos/oscar-hq"), ("C:/x/oscar-hq/.", "C:/a"), ("C:/x/./app", "C:/a"),
             (hub, "C:/a"), (hub + "/tools/../..", "C:/a")]
    for repo, repo_dir in casos:
        p.write_text(f"lanes:\n  l1:\n    board: b\n    base: main\n    repo: '{repo}'\n" + host % repo_dir, encoding="utf-8")
        with pytest.raises(ValueError, match="sensible"):
            load_lanes(p)


def test_e2_host_keys_are_read_validated_and_inert_by_default(tmp_path):
    p = tmp_path / "lanes.yaml"
    p.write_text("lanes:\n  l1:\n    board: b\n    base: main\n    repo: C:/x\n    host_gasto_dia_usd: 15\n    host_review: true\n"
                 "    host: {destino: w, repo_dir: 'C:/a', worktree_root: 'C:/b', hub_dir: 'C:/h', hub_sha_minimo: pin,\n"
                 "           latido_s: 60, disco_min_gb: 10, contrato_hash: true}\n", encoding="utf-8")
    lane = load_lanes(p)["l1"]
    assert (lane.host_gasto_dia_usd, lane.host_review, lane.host_integracion) == (15.0, True, False)
    assert (lane.host.hub_sha_minimo, lane.host.latido_s, lane.host.disco_min_gb, lane.host.contrato_hash) == ("pin", 60, 10, True)
    defecto = replace(HOST)
    assert (defecto.hub_sha_minimo, defecto.latido_s, defecto.disco_min_gb, defecto.contrato_hash) == ("", 0, 0.0, False)
    assert (LANE.host_review, LANE.host_integracion, LANE.host_gasto_dia_usd) == (False, False, None)
    for malo in ("hub_sha_minimo: 'zz; rm'", "latido_s: -1", "disco_min_gb: -5", "contrato_hash: 'si'"):
        p.write_text("lanes:\n  l1:\n    board: b\n    base: main\n    repo: C:/x\n    host: {destino: w, repo_dir: 'C:/a',"
                     f" worktree_root: 'C:/b', hub_dir: 'C:/h', {malo}}}\n", encoding="utf-8")
        with pytest.raises(ValueError):
            load_lanes(p)
    p.write_text("lanes:\n  l1:\n    board: b\n    base: main\n    repo: C:/x\n    host_review: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="solo tiene sentido"):  # sin host: las claves de host no valen
        load_lanes(p)


def test_ssh_argv_forces_strict_host_key_and_identities_only():
    cmd = ssh_host.ssh_argv(HOST)
    assert "StrictHostKeyChecking=yes" in cmd and "IdentitiesOnly=yes" in cmd and "BatchMode=yes" in cmd
    assert not any("StrictHostKeyChecking=no" in c for c in cmd)


def test_unreachable_and_alive_messages_are_excluded_from_auto_relaunch():
    from agent_lanes import relanzar
    marcas = [relanzar._norm(m) for m in relanzar.EXCLUIR_DEFECTO]
    for msg in ("error del runner: host worker-b sin conexión (ssh rc=255): x",
                "ATENCIÓN: el proceso remoto PID 7 pudo seguir vivo en el host"):
        assert any(m in relanzar._norm(msg) for m in marcas)
    yaml_marcas = [relanzar._norm(m) for m in relanzar.load_ajustes(relanzar.ROOT / "relanzar.yaml").excluir_motivos]
    assert "(ssh rc=255)" in yaml_marcas and "pudo seguir vivo" in yaml_marcas


def test_remote_quota_text_blocks_card_without_global_pause(tmp_path):
    from tests.test_cuota_runner import limite_outcome, runner_cuota
    r, h, w, n = runner_cuota(tmp_path, [limite_outcome()], lane=replace(LANE, host=HOST))
    r.run_once()
    assert not (tmp_path / "cuota.json").exists()  # el texto del host no pausa a todos los carriles
    assert any(c[0] == "block" and "host worker-b" in str(c) for c in h.calls)
    assert w.runs == ["run"]  # sin resumes contra la misma cuota


def test_previous_host_pid_is_kept_when_task_restarts(tmp_path):
    from tests.test_cuota_runner import runner_cuota
    from agent_lanes.worker import WorkerOutcome
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "t_1.json").write_text(json.dumps({"host": "worker-b", "host_worker_pid": 77,
                                                             "host_worker_created": 5.0, "pid": 1}))
    r, h, w, n = runner_cuota(tmp_path, [WorkerOutcome(False, "x", None, None, "fallo")] * 3)
    r.run_once()
    state = json.loads((tmp_path / "tasks" / "t_1.json").read_text())
    assert state["host_worker_pid"] == 77 and state["host"] == "worker-b"


def test_remote_output_is_capped(monkeypatch):
    monkeypatch.setattr(ssh_host, "MAX_OUTPUT_BYTES", 1000)
    with pytest.raises(HostError, match="superior"):
        SshRunner(HOST, FakeSsh(lambda req: Reply(out="x" * 2000)))(["x"], timeout=5)


def test_real_transport_cuts_ssh_when_output_exceeds_cap():
    code = "import sys;sys.stdout.write('x'*100000);sys.stdout.flush()"
    with pytest.raises(HostError, match="superior"):
        ssh_host.real_transport([sys.executable, "-c", code], input="", timeout=30, max_bytes=1000)
    ok = ssh_host.real_transport([sys.executable, "-c", "print('hola')"], input="", timeout=30, max_bytes=1000)
    assert ok.returncode == 0 and ok.stdout.strip() == "hola"


def test_forbidden_paths_robust_to_spaces_nonascii_and_case():
    from agent_lanes.verify import split_paths, vetada
    out = chr(0).join([".GitHub/Workflows/ci.yml", "docs/ñandú café.md", "a b/c.txt"]) + chr(0)
    assert split_paths(out) == [".GitHub/Workflows/ci.yml", "docs/ñandú café.md", "a b/c.txt"]
    assert vetada(".GitHub/Workflows/ci.yml", (".github/workflows/",))
    assert vetada("DOCS/Ñandú café.md", ("docs/ñandú",))
    assert vetada("a b\\c.txt", ("a b/",)) and not vetada("docs/x.md", (".github/",))


def test_verify_remoto_forbidden_paths_with_unicode_and_case_and_marks_host():
    lane = replace(RLANE, forbidden_paths=(".github/workflows/", "docs/privado"))
    res, _ = _verify(HubGit(changed="Docs/Privado/ñandú café.md\n"), lane=lane)
    assert not res.ok and "rutas vetadas" in " ".join(res.reasons) and res.verificado_en == "host"
    res, _ = _verify(HubGit(changed="Docs/publico/a b.md\n"), lane=lane)
    assert res.ok and res.verificado_en == "host"


@pytest.mark.parametrize("declared,expected", [(-5, 0.0), (float("nan"), 0.0), ("abc", 0.0), (0.4, 0.4), (1e9, 6.0),
                                               (None, None)])
def test_cost_declared_by_host_is_sanitised(declared, expected):
    assert ssh_host.sanear_coste(declared, 3.0) == expected
