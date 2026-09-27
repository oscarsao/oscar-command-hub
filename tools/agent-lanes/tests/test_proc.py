import subprocess
import sys

from agent_lanes import proc


def test_run_adds_no_window_flag_on_windows(monkeypatch):
    seen = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(proc.subprocess, "run", fake_run)
    monkeypatch.setattr(proc.sys, "platform", "win32")
    proc.run(["git", "status"], capture_output=True)
    assert seen["creationflags"] & proc.CREATE_NO_WINDOW
    assert seen["capture_output"] is True


def test_run_leaves_flags_alone_elsewhere(monkeypatch):
    seen = {}
    monkeypatch.setattr(proc.subprocess, "run", lambda *a, **k: seen.update(k))
    monkeypatch.setattr(proc.sys, "platform", "linux")
    proc.run(["git"])
    assert "creationflags" not in seen
