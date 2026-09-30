"""Carril ops (trabajo operativo sin repo): config, destinos, workspace, verificación por evidencias, runner que
siempre acaba en review, worker con su contrato propio, botones de validación y carriles de código sin cambios."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import jsonschema
import pytest

from agent_lanes import worker as W
from agent_lanes.config import ROOT, Lane, load_lanes
from agent_lanes.decisions import APPROVE, CHANGES, PARK, keyboard_spec
from agent_lanes.hermes import OSCAR_AUTHOR
from agent_lanes.ops import OpsTargetError, OpsWorkspace, task_targets, verify_ops
from agent_lanes import ops_paths
from agent_lanes.ops_paths import inside, normalize, parse_targets, validate_dests
from agent_lanes.runner import LaneRunner
from agent_lanes.worker import ClaudeWorker, WorkerOutcome
from tests.test_decisions import DeskHermes, FakeTG, _desk, _press
from tests.test_runner_state import LANE, FakeHermes, FakeNotifier, FakeWorker

sys.path.insert(0, str(ROOT / "contract"))
import ops_guard  # noqa: E402

DONE = {"status": "done", "summary": "vault copiado", "evidence": [], "proposed_actions": [], "questions": [],
        "next_steps": [], "risks": []}


@pytest.fixture(autouse=True)
def _tmp_not_protected(monkeypatch):
    """tmp_path cuelga de AppData (protegida como destino): en estos tests se quita solo esa entrada."""
    real = list(ops_paths._FORBIDDEN_DEST)
    monkeypatch.setattr(ops_paths, "_FORBIDDEN_DEST", [f for f in real if not f.endswith("AppData")])


def test_appdata_is_protected_for_real():
    assert str(Path.home() / "AppData") in ops_paths._FORBIDDEN_DEST_REAL


@pytest.fixture
def lane(tmp_path):
    return Lane(name="claude-ops", kind="ops", board="oscarhq", worktree_root=str(tmp_path / "ops"),
                dest_roots=(str(tmp_path / "home"),), role="roles/ops.md", max_runtime_seconds=3600)


# --- config ------------------------------------------------------------------------------------------

def test_lanes_yaml_ops_lane_overrides_code_defaults():
    lanes = load_lanes()
    ops = lanes["claude-ops"]
    assert (ops.kind, ops.board, ops.repo, ops.test_cmd, ops.role) == ("ops", "oscarhq", "", "", "roles/ops.md")
    assert ops.worktree_root == "C:/Users/oscar/dev/_lanes/ops" and ops.max_parallel == 1
    assert ops.forbidden_paths == () and ops.dest_roots == ("C:/Users/oscar", "E:/02_Negocio_Pildora/LEGAL-LLC")
    assert not any("git push" in t or "git commit" in t or "git add" in t or "pytest" in t for t in ops.allowed_tools)
    assert "claude-ops" not in lanes["review"].reviews  # el carril review crearía un worktree git con repo=""


def test_lanes_yaml_mcp_tools_are_read_only_and_known_to_the_guard():
    mcp = [t for t in load_lanes()["claude-ops"].allowed_tools if t.startswith("mcp__")]
    assert mcp and set(mcp) <= ops_guard.MCP_READ_ALLOW
    assert not any(w in t for t in mcp for w in ("create", "update", "delete", "share", "send", "execute", "trash"))


def test_code_lanes_unchanged():
    for name, l in load_lanes().items():
        if l.kind == "implement":
            # claude-hub (plan D) tiene rol propio (el sistema de carriles), mismas herramientas que el resto
            role = "roles/hub.md" if name == "claude-hub" else "roles/implementador.md"
            assert l.dest_roots == () and "Bash(git push:*)" in l.allowed_tools and l.role == role


def test_ops_contract_files_are_valid():
    schema = json.loads((ROOT / "contract" / "ops-result.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate({**DONE, "evidence": [{"type": "count", "path": "vault", "value": "3"}],
                         "proposed_actions": [{"id": "a1", "kind": "share", "description": "compartir", "tool": "x",
                                               "arguments": "{}"}]}, schema)
    settings = json.loads((ROOT / "contract" / "ops-worker-settings.json").read_text(encoding="utf-8"))
    hook = settings["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "*" and "ops_guard.py" in hook["hooks"][0]["args"][1]
    assert "mcp__*" not in settings["permissions"]["deny"]  # deny prevalece: taparía también la lectura


# --- destinos y rutas --------------------------------------------------------------------------------

def test_parse_targets_from_body():
    body = "Copia el vault.\nOrigen-Ops: E:/Obsidian/Vault\nDestino-Ops: C:/Users/oscar/Obsidian\nOrigen-Telegram: chat=1"
    assert parse_targets(body) == (["E:/Obsidian/Vault"], ["C:/Users/oscar/Obsidian"])
    assert parse_targets(None) == ([], [])


def test_inside_is_by_path_not_text_prefix(tmp_path):
    a, b = normalize(str(tmp_path / "ops" / "t_1")), normalize(str(tmp_path / "ops" / "t_10"))
    assert not inside(b, a) and inside(normalize(str(tmp_path / "ops" / "t_1" / "x")), a)
    assert normalize("/c/Users/x") == normalize("C:\\Users\\x")


def test_validate_dests(tmp_path):
    home, ws = tmp_path / "home", tmp_path / "ops"
    ok, bad = validate_dests([str(home / "Obsidian"), "relativa/x", str(tmp_path / "fuera"), str(home),
                              str(home / "orig" / "sub"), "C:/"], [str(home / "orig")], [str(home)], str(ws))
    assert ok == [normalize(str(home / "Obsidian"))]
    # relativa, fuera de dest_roots, home (contiene el origen), dentro del origen y raíz de unidad
    assert len(bad) == 5 and any("absoluta" in b for b in bad) and any("dest_roots" in b for b in bad)
    assert sum("solapa" in b for b in bad) == 2 and any("raíz" in b for b in bad)
    _, bad = validate_dests(["C:/Users/oscar/.claude/x", "C:/Users/oscar/.ssh/y"], [], ["C:/Users/oscar"], str(ws))
    assert len(bad) == 2 and all("protegida" in b for b in bad)
    _, bad = validate_dests([str(ws / "t_1")], [], [str(tmp_path)], str(ws))
    assert bad  # la raíz de workspaces no es un destino


@pytest.mark.parametrize("dest", [
    "C:/Users/oscar/.claude.json", "C:/Users/oscar/.local/bin", "C:/Users/oscar/.cloudflared",
    "C:/Users/oscar/dev/_lanes/lane-t_9", "C:/Users/oscar/dev/_lanes", "C:/Users/oscar/dev/oscar-hq",
    "C:/Users/oscar/dev/migrateam/backend", "C:/Users/oscar/Documents/WindowsPowerShell",
    "C:/Users/oscar/dev/_hub-wt/otro", "C:/Users/oscar/oscar-command-hub/x",
])
def test_protected_destinations(dest):
    lane = Lane(name="claude-ops", kind="ops", worktree_root="C:/Users/oscar/dev/_lanes/ops",
                dest_roots=("C:/Users/oscar",))
    _, ok, bad = task_targets(lane, {"body": f"Destino-Ops: {dest}"})
    assert ok == [] and bad and "protegida" in bad[0], bad


def test_legit_destination_under_home():
    lane = Lane(name="claude-ops", kind="ops", worktree_root="C:/Users/oscar/dev/_lanes/ops",
                dest_roots=("C:/Users/oscar",))
    body = "Origen-Ops: E:/Obsidian\nDestino-Ops: C:/Users/oscar/Obsidian"
    _, ok, bad = task_targets(lane, {"body": body})
    assert bad == [] and len(ok) == 1


def test_workspace_created_and_reused(lane):
    ws = OpsWorkspace()
    p = ws.prepare_worktree(lane, "t_1", {"id": "t_1", "body": ""})
    assert Path(p).is_dir() and Path(p) == Path(lane.worktree_root) / "t_1"
    (Path(p) / "a.md").write_text("x", encoding="utf-8")
    assert ws.prepare_worktree(lane, "t_1", {"id": "t_1"}) == p and (Path(p) / "a.md").exists()


def test_workspace_refuses_bad_destination(lane):
    with pytest.raises(OpsTargetError):
        OpsWorkspace().prepare_worktree(lane, "t_1", {"id": "t_1", "body": "Destino-Ops: C:/Windows/x"})


# --- verificación por evidencias ---------------------------------------------------------------------

def _ws(lane, tid="t_1", body=""):
    return OpsWorkspace().prepare_worktree(lane, tid, {"id": tid, "body": body})


def test_verify_path_hash_count(lane):
    cwd = _ws(lane)
    (Path(cwd) / "vault").mkdir()
    for n in ("a.md", "b.md"):
        (Path(cwd) / "vault" / n).write_text(n, encoding="utf-8")
    digest = hashlib.sha256(b"a.md").hexdigest()
    ev = [{"type": "path", "value": "vault"}, {"type": "count", "path": "vault", "value": "2"},
          {"type": "hash", "path": "vault/a.md", "value": digest}, {"type": "link", "value": "https://x"}]
    res = verify_ops(lane, "t_1", cwd, {**DONE, "evidence": ev}, task={"id": "t_1"})
    assert res.ok, res.reasons


@pytest.mark.parametrize("ev,why", [
    ([{"type": "path", "value": "no-existe.md"}], "no existe"),
    ([{"type": "path", "value": "C:/Windows/win.ini"}], "fuera"),
    ([{"type": "path", "value": "../t_10/x.md"}], "fuera"),
    ([{"type": "count", "path": ".", "value": "99"}], "archivo"),
    ([{"type": "hash", "path": "informe.md", "value": "0" * 64}], "sha256"),
    ([{"type": "link", "value": "https://x"}, {"type": "note", "value": "hecho"}], "sin evidencias verificables"),
    ([], "sin evidencias verificables"),
])
def test_verify_rejects(lane, ev, why):
    cwd = _ws(lane)
    (Path(cwd) / "informe.md").write_text("x", encoding="utf-8")
    res = verify_ops(lane, "t_1", cwd, {**DONE, "evidence": ev}, task={"id": "t_1"})
    assert not res.ok and any(why in r for r in res.reasons), res.reasons


def test_verify_accepts_declared_destination(lane, tmp_path):
    dest = tmp_path / "home" / "Obsidian"
    dest.mkdir(parents=True)
    (dest / "n.md").write_text("n", encoding="utf-8")
    task = {"id": "t_1", "body": f"Destino-Ops: {dest.as_posix()}"}
    cwd = _ws(lane, body=task["body"])
    res = verify_ops(lane, "t_1", cwd, {**DONE, "evidence": [{"type": "count", "path": str(dest), "value": "1"}]},
                     task=task)
    assert res.ok, res.reasons
    res = verify_ops(lane, "t_1", cwd, {**DONE, "evidence": [{"type": "count", "path": str(dest), "value": "1"}]},
                     task={"id": "t_1"})  # sin la declaración, el destino no vale
    assert not res.ok


# --- runner ------------------------------------------------------------------------------------------

class OpsHermes(FakeHermes):
    def complete(self, *a, **k):  # el runner NUNCA debe llamarlo en ops
        self.calls.append(("complete",) + a)
        return True, ""


def _run_ops(lane, structured, *, body="B"):
    h = OpsHermes(tasks=[{"id": "t_1", "title": "Vault", "body": body}])
    n = FakeNotifier()
    out = WorkerOutcome(ok=True, subtype="success", structured=structured, cost_usd=0.2, raw_error=None)
    r = LaneRunner(lane, hermes=h, git=OpsWorkspace(), worker=FakeWorker([out]), verifier=verify_ops, notify=n)
    return r.run_once(), h, n


def test_runner_claims_creates_workspace_verifies_and_goes_to_review(lane):
    ws = Path(lane.worktree_root) / "t_1"
    ws.mkdir(parents=True)
    (ws / "informe.md").write_text("inventario", encoding="utf-8")
    res, h, n = _run_ops(lane, {**DONE, "evidence": [{"type": "path", "value": "informe.md"}]})
    assert res == {"t_1": "review"}
    assert ("claim", "t_1", lane.claim_ttl_seconds) in h.calls
    assert "complete" not in h.kinds()
    review = [c for c in h.calls if c[0] == "review"][0]
    meta = review[3]
    assert meta["kind"] == "ops" and meta["workspace"] == str(ws) and meta["verified"] is True
    assert meta["evidence"] == [{"type": "path", "value": "informe.md"}] and "head_sha" not in meta
    assert any("workspace ops" in c[2] for c in h.calls if c[0] == "comment")
    assert "pendiente de tu validación" in (n.msgs + [e[1] for e in n.edits])[-1]  # alerta: mensaje nuevo


def test_runner_ops_bad_evidence_blocks_never_review(lane):
    res, h, _ = _run_ops(lane, {**DONE, "evidence": [{"type": "path", "value": "C:/Windows/win.ini"}]})
    assert res == {"t_1": "blocked:transient"}
    assert "review" not in h.kinds() and "complete" not in h.kinds()


def test_runner_ops_needs_input_carries_proposed_action(lane):
    q = {"question": "¿Comparto la carpeta con Andrea (lectura)?", "options": ["Aprobar", "No"], "recommended": 0}
    act = {"id": "share-drive", "kind": "share", "description": "Drive: compartir 'Clientes' con andrea (lector)",
           "tool": "mcp__claude_ai_Google_Drive__share_file", "arguments": '{"file_id": "abc", "role": "reader"}'}
    res, h, _ = _run_ops(lane, {**DONE, "status": "needs_input", "questions": [q], "proposed_actions": [act]})
    assert res == {"t_1": "blocked:needs_input"}
    block = [c for c in h.calls if c[0] == "block"][0]
    assert "share-drive" in block[3] and "no ejecutadas" in block[3] and "reader" in block[3]


def test_runner_ops_bad_destination_blocks_before_worker(lane):
    res, h, _ = _run_ops(lane, DONE, body="Destino-Ops: C:/Windows/System32")
    assert res == {"t_1": "blocked:transient"}
    assert "Destino-Ops no permitido" in [c for c in h.calls if c[0] == "block"][0][3]


# --- worker ------------------------------------------------------------------------------------------

def test_code_lane_args_unchanged():
    args = W.base_args(LANE, ["--session-id", "s"])
    assert args[args.index("--mcp-config") + 1] == '{"mcpServers":{}}' and "--strict-mcp-config" in args
    assert Path(args[args.index("--settings") + 1]).name == "worker-settings.rendered.json"
    assert json.loads(args[args.index("--json-schema") + 1])["title"] == "LaneWorkerResult"
    assert "--add-dir" not in args


class Capture:
    def __init__(self):
        self.calls = []

    def __call__(self, args, **kw):
        import subprocess
        self.calls.append((args, kw))
        return subprocess.CompletedProcess(args, 0, json.dumps({"subtype": "success", "structured_output": DONE}), "")


def test_ops_worker_contract_and_env(lane, tmp_path):
    dest = tmp_path / "home" / "Obsidian"
    task = {"id": "t_1", "title": "Vault", "body": f"Origen-Ops: E:/Vault\nDestino-Ops: {dest.as_posix()}"}
    cwd = _ws(lane, body=task["body"])
    cap = Capture()
    w = ClaudeWorker(runner=cap)
    assert w.run(lane, task, cwd, "sess-1", timeout=60).ok
    args, kw = cap.calls[0]
    assert "--strict-mcp-config" not in args and "--mcp-config" not in args
    assert Path(args[args.index("--settings") + 1]).name == "ops-worker-settings.rendered.json"
    assert json.loads(args[args.index("--json-schema") + 1])["title"] == "OpsWorkerResult"
    assert args[args.index("--append-system-prompt-file") + 1].endswith("ops.md")
    dirs = [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]
    assert dirs == [normalize(str(dest)), "E:/Vault"]
    env = kw["env"]
    assert env["AGENT_LANES_ROLE"] == "ops" and env["AGENT_LANES_WORKSPACE"] == cwd and env["AGENT_LANES_TASK"] == "t_1"
    assert json.loads(env["AGENT_LANES_OPS_DESTS"]) == [normalize(str(dest))]
    assert "Workspace de la tarea" in kw["input"] and "E:/Vault" in kw["input"]
    w.resume(lane, cwd, "sess-1", timeout=60, task_id="t_1")
    rargs, rkw = cap.calls[1]
    assert "--resume" in rargs and rkw["env"]["AGENT_LANES_WORKSPACE"] == cwd
    assert "--strict-mcp-config" not in rargs


def test_task_targets_drops_invalid(lane, tmp_path):
    o, d, bad = task_targets(lane, {"body": "Destino-Ops: C:/Windows\nDestino-Ops: " + (tmp_path / "home" / "x").as_posix()})
    assert len(d) == 1 and len(bad) == 1 and o == []


# --- botones ------------------------------------------------------------------------------------------

def test_review_buttons_only_for_ops():
    assert keyboard_spec("review") is None
    spec = keyboard_spec("review", ops=True)
    assert [b["action"] for b in spec[0]] == [APPROVE, CHANGES, PARK]


class OpsDeskHermes(DeskHermes):
    def complete(self, tid, result, metadata):
        self.calls.append(("complete", tid, result, metadata))
        return self.ok, ""


def test_validate_ops_completes_without_pr(tmp_path, lane):
    gh_calls = []
    h = OpsDeskHermes()
    desk, tg, _ = _desk(tmp_path, hermes=h, gh=lambda a, **k: gh_calls.append(a), lanes={lane.name: lane})
    markup = desk.markup("review", task={"id": "t_1", "title": "Vault", "body": ""}, lane=lane)
    _press(desk, markup, 0)
    assert gh_calls == []  # sin PR ni gh
    assert h.kinds() == ["complete", "comment"]
    assert h.calls[1][2].startswith("VALIDADO-OSCAR 2026-09-28 10:30") and h.calls[1][3] == OSCAR_AUTHOR
    assert "✅ validada" in tg.edits[-1]["text"]


def test_validate_ops_failure_keeps_task(tmp_path, lane):
    h = OpsDeskHermes(ok=False)
    desk, tg, _ = _desk(tmp_path, hermes=h, gh=lambda a, **k: None, lanes={lane.name: lane})
    _press(desk, desk.markup("review", task={"id": "t_1"}, lane=lane), 0)
    assert h.kinds() == ["complete"] and "no se pudo validar" in tg.edits[-1]["text"]


# --- recordatorios / bandeja (/decisiones) -------------------------------------------------------------

def test_renotify_collects_ops_blocks_but_never_ops_done(tmp_path):
    from agent_lanes import renotify
    from agent_lanes.decisions import CallbackStore, DecisionDesk
    from agent_lanes.notices import MessageStore
    from tests import test_renotify as T

    ops = Lane(name="claude-ops", kind="ops", board="oscarhq")
    lanes = {**T.LANES, ops.name: ops}
    shows = {"t_o1": T.blocked_show("t_o1", "needs_input", "El worker necesita decisión:\n- ¿Comparto con Andrea?",
                                    assignee="claude-ops"),
             "t_o2": T.done_show("t_o2", assignee="claude-ops")}
    h = T.BoardHermes(shows)
    bot = T.Bot()
    desk = DecisionDesk(bot, CallbackStore(tmp_path / "cb"), lanes=lanes, hermes_for=lambda b: h, links=None)

    def no_git(*a):
        raise AssertionError("ops no consulta ramas git")
    r = renotify.Renotifier(lanes, hermes_for=lambda b: h, notifier=bot, messages=MessageStore(tmp_path / "m"),
                            links=None, desk=desk, branch_state=no_git, out=lambda *_: None)
    found = r.collect()
    assert [(p.tid, p.state) for p in found] == [("t_o1", "needs_input")]
