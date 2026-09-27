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
    board: str = ""
    repo: str = ""
    base: str = ""
    tool: str = "claude"
    model: str = "sonnet"
    effort: str = "medium"
    max_budget_usd: float = 3.0
    max_parallel: int = 1
    role: str = "roles/implementador.md"
    test_cmd: str = ""
    max_runtime_seconds: int = 7200
    worktree_root: str = ""
    max_resumes: int = 2
    heartbeat_seconds: float = 240
    remote: str = "origin"
    # -p mode denies any Bash not pre-approved; acceptEdits only covers file edits.
    allowed_tools: tuple[str, ...] = ()
    # Paths a worker may never change in this repo (checked mechanically on the pushed diff).
    forbidden_paths: tuple[str, ...] = ()
    kind: str = "implement"           # implement | review
    reviews: tuple[str, ...] = ()     # review lane: implementer lanes it reviews
    max_review_rounds: int = 2        # review lane: change requests before escalating to Oscar
    telegram: tuple[str, str] | None = None  # (chat, thread) de los avisos del carril; ver lanes.yaml

    @property
    def claim_ttl_seconds(self) -> int:
        # hermes 0.21.4: the CLI heartbeat does not extend claim_expires, so the lease is requested up front
        # and the runner stops at max_runtime, before the sweep could reclaim the task.
        return self.max_runtime_seconds + CLAIM_TTL_MARGIN_SECONDS

    @property
    def role_path(self) -> Path:
        return ROOT / self.role

    @property
    def base_ref(self) -> str:
        return f"{self.remote}/{self.base}"


TUPLE_FIELDS = ("allowed_tools", "forbidden_paths", "reviews")


def _load(path: Path | None) -> dict:
    return yaml.safe_load((path or ROOT / "lanes.yaml").read_text(encoding="utf-8"))


def load_lanes(path: Path | None = None) -> dict[str, Lane]:
    data = _load(path)
    defaults = data.get("defaults", {})
    lanes = {}
    for name, cfg in data["lanes"].items():
        merged = {**defaults, **cfg}
        for f in TUPLE_FIELDS:
            merged[f] = tuple(merged.get(f) or ())
        merged["telegram"] = _chat_thread(merged.get("telegram"))
        lanes[name] = Lane(name=name, **merged)
    return lanes


def _chat_thread(cfg: dict | None, any_thread: str = "0") -> tuple[str, str] | None:
    """{chat, thread} de YAML (ints) -> (str, str), mismo formato que telegram_target()."""
    if not cfg:
        return None
    thread = cfg.get("thread")
    return str(cfg["chat"]), any_thread if thread is None else str(thread)


def load_telegram_settings(path: Path | None = None) -> dict:
    """Top-level `telegram:` block. generic_origins: orígenes sin marca; thread omitido = cualquier hilo ("*").
    kanban_base_url: base del enlace a la tarjeta (KANBAN_BASE_URL del .env tiene prioridad); sin ella, no hay enlace."""
    cfg = _load(path).get("telegram") or {}
    return {"generic_origins": {_chat_thread(o, "*") for o in cfg.get("generic_origins") or ()},
            "kanban_base_url": cfg.get("kanban_base_url") or None}


def load_runner_settings(path: Path | None = None) -> dict:
    """Top-level `runner:` block (global worker cap, poll interval)."""
    return {"max_workers": 1, "interval_seconds": 60, **(_load(path).get("runner") or {})}


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
