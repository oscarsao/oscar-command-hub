"""lanes.yaml loader."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CLAIM_TTL_MARGIN_SECONDS = 300


@dataclass(frozen=True)
class Lane:
    name: str
    board: str
    repo: str
    base: str
    tool: str
    model: str
    effort: str
    max_budget_usd: float
    max_parallel: int
    role: str
    test_cmd: str
    max_runtime_seconds: int
    worktree_root: str
    max_resumes: int = 2
    heartbeat_seconds: float = 240
    remote: str = "origin"
    # -p mode denies any Bash not pre-approved; acceptEdits only covers file edits.
    allowed_tools: tuple[str, ...] = ()

    @property
    def claim_ttl_seconds(self) -> int:
        # hermes 0.21.4: the CLI heartbeat does not extend claim_expires, so the lease is requested up front
        # and the runner stops at max_runtime, before the sweep could reclaim the task.
        return self.max_runtime_seconds + CLAIM_TTL_MARGIN_SECONDS

    @property
    def role_path(self) -> Path:
        return ROOT / self.role


def load_lanes(path: Path | None = None) -> dict[str, Lane]:
    data = yaml.safe_load((path or ROOT / "lanes.yaml").read_text(encoding="utf-8"))
    defaults = data.get("defaults", {})
    lanes = {}
    for name, cfg in data["lanes"].items():
        merged = {**defaults, **cfg}
        merged["allowed_tools"] = tuple(merged.get("allowed_tools") or ())
        lanes[name] = Lane(name=name, **merged)
    return lanes


def load_env(path: Path | None = None) -> dict[str, str]:
    """Minimal KEY=VALUE parser for agent-lanes/.env (never logged)."""
    env: dict[str, str] = {}
    p = path or ROOT / ".env"
    if not p.exists():
        return env
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip("\"'")
    return env
