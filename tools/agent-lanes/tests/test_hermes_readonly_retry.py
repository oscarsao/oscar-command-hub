import logging
import subprocess

from agent_lanes.hermes import HermesCLI, HermesError

RO = "sqlite3.OperationalError: kanban.db-shm is read-only for this user"


def _cli(results):
    calls, sleeps = [], []

    def run(cmd, **kw):
        calls.append(cmd)
        rc, out, err = results[min(len(calls) - 1, len(results) - 1)]
        return subprocess.CompletedProcess(cmd, rc, out, err)

    return HermesCLI("oscarhq", exe="hermes", runner=run, sleep=sleeps.append), calls, sleeps


def test_retries_readonly_then_succeeds(caplog):
    h, calls, sleeps = _cli([(1, "", RO), (1, "", RO), (0, "", "")])
    with caplog.at_level(logging.WARNING, logger="agent_lanes"):
        assert h.heartbeat("t_1") is True
    assert len(calls) == 3 and sleeps == [1.0, 2.0]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


def test_readonly_in_stdout_also_retries():
    h, calls, _ = _cli([(1, RO, ""), (0, "", "")])
    assert h.heartbeat("t_1") is True and len(calls) == 2


def test_exhausted_retries_propagate_original_error():
    h, calls, sleeps = _cli([(1, "", RO)])
    try:
        h.show("t_1")
        raise AssertionError("debía fallar")
    except HermesError as exc:
        assert "read-only" in str(exc)
    assert len(calls) == 3 and sleeps == [1.0, 2.0]


def test_winerror_4551_not_retried():
    h, calls, sleeps = _cli([(1, "", f"{RO} WinError 4551")])
    assert h.heartbeat("t_1") is False
    assert len(calls) == 1 and sleeps == []


def test_other_failures_not_retried():
    h, calls, sleeps = _cli([(2, "", "unrecognized arguments")])
    assert h.heartbeat("t_1") is False
    assert len(calls) == 1 and sleeps == []
