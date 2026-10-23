"""lanes.yaml loader."""
from __future__ import annotations

from dataclasses import dataclass, field
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
    kind: str = "implement"           # implement | review | ops
    reviews: tuple[str, ...] = ()     # review lane: implementer lanes it reviews
    max_review_rounds: int = 2        # review lane: change requests before escalating to Oscar
    telegram: tuple[str, str] | None = None  # (chat, thread) de los avisos del carril; ver lanes.yaml
    # ops: raíces bajo las que una tarea puede declarar `Destino-Ops:` (config de confianza, no del cuerpo).
    dest_roots: tuple[str, ...] = ()
    # Candado de repos (28-09): owner/repo de GitHub que DEBE tener `remote` en `repo`. Un carril de código sin él,
    # o con otro remote, no reclama tareas (runner: guard_github_remote).
    github: str = ""
    # Plaza reservada (22-10): workers de ESTE carril que corren aunque max_workers esté lleno; no cuentan para él.
    reserved_slots: int = 0
    # Lista blanca de repos por tarjeta (`Repo: <alias>` en el cuerpo): alias -> {repo, github, base, test_cmd, ...}.
    # Ver telegram_lane.py. El primero es el de por defecto.
    repos: dict = field(default_factory=dict, hash=False, compare=False)
    # Nombres antiguos del carril (23-10, carriles por proyecto): se aceptan en entrada (tarjetas abiertas, .env,
    # hilos de Telegram) y resuelven al carril canónico; nunca se emiten.
    aliases: tuple[str, ...] = ()

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


TUPLE_FIELDS = ("allowed_tools", "forbidden_paths", "reviews", "dest_roots", "aliases")


def _load(path: Path | None) -> dict:
    return yaml.safe_load((path or ROOT / "lanes.yaml").read_text(encoding="utf-8"))


class LaneMap(dict):
    """dict nombre -> Lane que además acepta los alias del carril en `[]`, `.get` e `in`. Iterar (keys/items/values)
    solo da los nombres canónicos: un alias nunca se emite ni cuenta como carril aparte."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.aliases = {a: n for n, lane in self.items() for a in getattr(lane, "aliases", ())}

    def resolve(self, name):
        return name if dict.__contains__(self, name) else self.aliases.get(name, name)

    def __getitem__(self, name):
        return super().__getitem__(self.resolve(name))

    def __contains__(self, name):
        return super().__contains__(self.resolve(name))

    def get(self, name, default=None):
        return super().get(self.resolve(name), default)


def resolve_lane(lanes, name):
    """Nombre canónico de un carril (acepta alias); un nombre desconocido se devuelve tal cual."""
    return lanes.resolve(name) if isinstance(lanes, LaneMap) else name


def load_lanes(path: Path | None = None) -> dict[str, Lane]:
    data = _load(path)
    defaults = dict(data.get("defaults", {}))
    from .ajustes_hub import valor  # max_parallel por defecto editado con /ajustes (rige al arrancar el runner)
    if valor("carriles.max_parallel") is not None:
        defaults["max_parallel"] = valor("carriles.max_parallel")
    lanes = {}
    for name, cfg in data["lanes"].items():
        merged = {**defaults, **cfg}
        for f in TUPLE_FIELDS:
            merged[f] = tuple(merged.get(f) or ())
        merged["telegram"] = _chat_thread(merged.get("telegram"))
        lanes[name] = Lane(name=name, **merged)
    return LaneMap(lanes)


def _chat_thread(cfg: dict | None, any_thread: str = "0") -> tuple[str, str] | None:
    """{chat, thread} de YAML (ints) -> (str, str), mismo formato que telegram_target()."""
    if not cfg:
        return None
    thread = cfg.get("thread")
    return str(cfg["chat"]), any_thread if thread is None else str(thread)


def load_telegram_settings(path: Path | None = None) -> dict:
    """Top-level `telegram:` block (+ `integration_telegram`). generic_origins: orígenes sin marca; thread omitido =
    cualquier hilo ("*").
    kanban_base_url: base del enlace a la tarjeta (KANBAN_BASE_URL del .env tiene prioridad); sin ella, no hay enlace."""
    data = _load(path)
    cfg = data.get("telegram") or {}
    return {"generic_origins": {_chat_thread(o, "*") for o in cfg.get("generic_origins") or ()},
            "kanban_base_url": cfg.get("kanban_base_url") or None,
            # Chats «solo resultado final» (grupo de desarrollo de Angel): el ruido de proceso va al DM de Oscar.
            "final_only": {_chat_thread(o, "*") for o in cfg.get("final_only") or ()},
            # Aviso al equipo en grupos (decisions._team_notice): {enabled, chats, text}; sin la clave = apagado.
            "team_notice": cfg.get("team_notice") or {},
            # Texto libre como respuesta a la decisión pendiente del tema ({window_hours, max_choices}); None = apagado.
            "free_text": dict(cfg["free_text"]) if cfg.get("free_text") else None,
            # Tema de Integración (fichas de PR/merge/deploy + resumen fijado); None = tema del carril, como antes.
            "integration": _chat_thread(data.get("integration_telegram")),
            # Tema «Atención» (avisos accionables con @mención) {chat, thread, mention, remind_hours}; None = apagado.
            "attention": dict(data["attention_telegram"]) if (data.get("attention_telegram") or {}).get("chat") else None}


def load_runner_settings(path: Path | None = None) -> dict:
    """Top-level `runner:` block (global worker cap, poll interval)."""
    from .ajustes_hub import aplicar_runner  # lo editado con /ajustes manda sobre lanes.yaml (rige al arrancar el runner)
    return aplicar_runner({"max_workers": 1, "interval_seconds": 60, "ciego_minutes": 5,
                           **(_load(path).get("runner") or {})})


def load_notice_limits(path: Path | None = None) -> dict:
    """Bloque `notices:` de lanes.yaml: topes de los avisos de Telegram (ver notices.LIMITS). Vacío = de fábrica."""
    from .ajustes_hub import aplicar_topes
    return aplicar_topes(dict(_load(path).get("notices") or {}))


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
