import json
import subprocess
import urllib.error

import pytest

from agent_lanes import telegram
from agent_lanes.verify import verify
from agent_lanes.worker import parse_result
from tests.test_runner_state import GOOD, LANE


def test_parse_structured_output_success():
    out = parse_result(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                                   "structured_output": GOOD, "total_cost_usd": 0.2}))
    assert out.ok and out.structured["head_sha"] == GOOD["head_sha"] and out.cost_usd == 0.2


def test_parse_result_string_fallback():
    out = parse_result(json.dumps({"subtype": "success", "is_error": False, "result": json.dumps(GOOD)}))
    assert out.ok


@pytest.mark.parametrize("payload", [
    {"subtype": "error_max_budget_usd", "is_error": True},
    {"subtype": "success", "is_error": False, "result": "texto libre"},
])
def test_parse_not_ok(payload):
    assert not parse_result(json.dumps(payload)).ok


def test_parse_garbage():
    assert not parse_result("boom").ok and not parse_result("").ok


class FakeGitRun:
    def __init__(self, remote_sha, head_sha=None, ahead="1", test_rc=0):
        self.remote_sha, self.head, self.ahead, self.test_rc = remote_sha, head_sha or remote_sha or "", ahead, test_rc

    def __call__(self, args, **kw):
        if isinstance(args, str):  # test_cmd via shell
            return subprocess.CompletedProcess(args, self.test_rc, "", "")
        sub = args[3]
        out = {"ls-remote": f"{self.remote_sha}\trefs/heads/lane/t_1\n" if self.remote_sha else "",
               "rev-list": self.ahead + "\n", "rev-parse": self.head + "\n"}.get(sub, "")
        return subprocess.CompletedProcess(args, 0, out, "")


def test_verify_ok():
    assert verify(LANE, "t_1", "C:/wt", GOOD, runner=FakeGitRun("a" * 40)).ok


@pytest.mark.parametrize("fake,needle", [
    (FakeGitRun(None), "no existe"),
    (FakeGitRun("b" * 40), "!= remoto"),
    (FakeGitRun("a" * 40, ahead="0"), "no tiene commits"),
    (FakeGitRun("a" * 40, head_sha="c" * 40), "sin empujar"),
    (FakeGitRun("a" * 40, test_rc=1), "test_cmd falló"),
])
def test_verify_fails(fake, needle):
    r = verify(LANE, "t_1", "C:/wt", GOOD, runner=fake)
    assert not r.ok and any(needle in x for x in r.reasons)


def test_verify_rejects_wrong_branch():
    r = verify(LANE, "t_1", "C:/wt", {**GOOD, "branch": "master"}, runner=FakeGitRun("a" * 40))
    assert not r.ok


def test_telegram_error_never_contains_token(monkeypatch):
    secret = "123456:SECRET-TOKEN"

    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(telegram.urllib.request, "urlopen", boom)
    n = telegram.TelegramNotifier(secret, "-100", "5")
    with pytest.raises(RuntimeError) as exc:
        n("hola")
    assert secret not in str(exc.value) and secret not in repr(n)
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_telegram_disabled_without_token():
    telegram.TelegramNotifier(None, None)("no-op")


def test_worker_runs_without_any_mcp_server():
    from agent_lanes.worker import base_args

    args = base_args(LANE, ["--session-id", "x"])
    assert "--strict-mcp-config" in args
    cfg = json.loads(args[args.index("--mcp-config") + 1])
    assert cfg == {"mcpServers": {}}


def test_worker_settings_deny_mcp_and_hook_covers_mcp():
    from agent_lanes.worker import SETTINGS_TEMPLATE

    s = json.loads(SETTINGS_TEMPLATE.read_text(encoding="utf-8"))
    assert "mcp__*" in s["permissions"]["deny"]
    assert "mcp__" in s["hooks"]["PreToolUse"][0]["matcher"]
