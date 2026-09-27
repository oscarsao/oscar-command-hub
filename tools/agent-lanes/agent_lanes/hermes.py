"""Thin wrapper over the `hermes kanban` CLI (pinned to 0.21.4) plus its contract check."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERMES_EXE = Path.home() / "AppData/Local/hermes/bin/hermes.exe"
PINNED_VERSION = "0.21.4"

# subcommand -> flags the runner relies on
CONTRACT = {
    "list": ["--assignee", "--status", "--json"],
    "claim": ["--ttl"],
    "heartbeat": ["--note"],
    "comment": ["--author"],
    "block": ["--kind", "needs_input", "transient"],
    "request-review": ["--summary", "--metadata"],
    "show": ["--json"],
    "complete": ["--result", "--summary", "--metadata"],
    "reopen-review": ["--reason"],
    "reassign": ["profile"],
}

REVIEW_AUTHOR = "lane-review"  # author of the review lane's change requests (read back by the implementer)


class HermesError(RuntimeError):
    pass


class HermesCLI:
    def __init__(self, board: str, exe: Path = HERMES_EXE, runner=subprocess.run, author: str = "agent-lanes"):
        self.board = board
        self.exe = str(exe)
        self._run = runner
        self.author = author

    def _call(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return self._run([self.exe, "kanban", "--board", self.board, *args],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)

    def list_status(self, assignee: str, status: str, sort: str = "priority") -> list[dict]:
        cp = self._call("list", "--assignee", assignee, "--status", status, "--json", "--sort", sort)
        if cp.returncode != 0:
            raise HermesError(f"list failed: {cp.stderr.strip()[:300]}")
        return json.loads(cp.stdout or "[]")

    def list_ready(self, assignee: str) -> list[dict]:
        return self.list_status(assignee, "ready")

    def claim(self, task_id: str, ttl: int) -> bool:
        return self._call("claim", task_id, "--ttl", str(ttl)).returncode == 0

    def heartbeat(self, task_id: str, note: str | None = None) -> bool:
        args = ["heartbeat", task_id] + (["--note", note] if note else [])
        return self._call(*args).returncode == 0

    def comment(self, task_id: str, text: str, author: str | None = None) -> bool:
        return self._call("comment", task_id, "--author", author or self.author, text).returncode == 0

    def show(self, task_id: str) -> dict:
        cp = self._call("show", task_id, "--json")
        if cp.returncode != 0:
            raise HermesError(f"show {task_id} failed: {cp.stderr.strip()[:300]}")
        return json.loads(cp.stdout)

    def review_feedback(self, task_id: str) -> list[str]:
        """Change requests left by the review lane, oldest first (empty for a first run)."""
        comments = self.show(task_id).get("comments") or []
        return [c["body"] for c in comments if c.get("author") == REVIEW_AUTHOR]

    def complete(self, task_id: str, result: str, metadata: dict) -> tuple[bool, str]:
        cp = self._call("complete", task_id, "--result", result, "--metadata", json.dumps(metadata))
        return cp.returncode == 0, cp.stderr.strip()[:500]

    def reopen_review(self, task_id: str) -> bool:
        return self._call("reopen-review", task_id).returncode == 0

    def reassign(self, task_id: str, profile: str, reason: str) -> bool:
        return self._call("reassign", task_id, profile, "--reason", reason).returncode == 0

    def block(self, task_id: str, kind: str, reason: str) -> bool:
        # --kind must precede the positionals: hermes rejects `block <id> --kind k <reason>`.
        return self._call("block", "--kind", kind, task_id, reason).returncode == 0

    def request_review(self, task_id: str, summary: str, metadata: dict) -> tuple[bool, str]:
        cp = self._call("request-review", task_id, "--summary", summary, "--metadata", json.dumps(metadata))
        return cp.returncode == 0, cp.stderr.strip()[:500]

    def check_contract(self) -> list[str]:
        """Return a list of problems; empty means the CLI matches what the runner expects."""
        problems: list[str] = []
        try:
            cp = self._run([self.exe, "--version"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return [f"hermes no ejecutable ({exc})"]
        if PINNED_VERSION not in cp.stdout:
            problems.append(f"versión de hermes distinta de {PINNED_VERSION}: {cp.stdout.strip().splitlines()[:1]}")
        for sub, flags in CONTRACT.items():
            help_cp = self._call(sub, "--help", timeout=60)
            if help_cp.returncode != 0:
                problems.append(f"falta subcomando `kanban {sub}`")
                continue
            problems += [f"`kanban {sub}` sin {f}" for f in flags if f not in help_cp.stdout]
        cp = self._call("list", "--status", "ready", "--json")
        try:
            data = json.loads(cp.stdout or "null")
            if not isinstance(data, list) or (data and not {"id", "title", "body"} <= set(data[0])):
                problems.append("`kanban list --json` no devuelve [{id,title,body,...}]")
        except json.JSONDecodeError:
            problems.append("`kanban list --json` no devuelve JSON")
        return problems
