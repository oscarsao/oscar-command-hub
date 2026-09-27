import subprocess

from agent_lanes.hermes import HermesCLI


def _capture():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return calls, run


def test_block_puts_kind_before_task_id():
    # hermes 0.21.4 rejects `block <id> --kind k <reason...>` (reason nargs='*' already consumed).
    calls, run = _capture()
    assert HermesCLI("oscarhq", exe="hermes", runner=run).block("t_1", "needs_input", "why")
    assert calls[0][calls[0].index("block"):] == ["block", "--kind", "needs_input", "t_1", "why"]


def test_block_reports_cli_failure():
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 2, "", "unrecognized arguments")

    assert HermesCLI("oscarhq", exe="hermes", runner=run).block("t_1", "transient", "x") is False
