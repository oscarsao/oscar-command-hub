"""lanes.yaml loader."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CLAIM_TTL_MARGIN_SECONDS = 300


HOST_DESTINO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")  # fullmatch
HOST_RUTA_RE = re.compile(r"[A-Za-z]:[\\/][A-Za-z0-9 _.\\/-]*")  # lista blanca (fullmatch): sin $ ` ; ( ) ! ' " % & | < > ^
HOST_CLAVES_FUTURAS: tuple = ()  # E2 lee todas las claves de host: (hub_sha_minimo, latido_s, disco_min_gb, ...)
HOST_SHA_RE = re.compile(r"[0-9a-fA-F]{7,40}")


@dataclass(frozen=True)
class HostCfg:
    """Destino remoto de un carril (opt-in; spec specs/worker-remoto-ssh.md §3). Sin `host:` el carril es local.
    `destino` es un alias de ~/.ssh/config del hub (usuario, IP e IdentityFile viven ahí, nunca aquí). `repo_dir` y
    `worktree_root` son rutas del HOST (Windows). `Lane.repo` sigue siendo el clon del HUB (verify, revisión, candado)."""
    destino: str
    repo_dir: str
    worktree_root: str
    max_parallel: int = 1
    conexion_timeout_s: int = 15
    # Clon de oscar-command-hub EN EL HOST (lanzador remote_exec.py y contract/), fijado al SHA del hub. Aparte de
    # `repo_dir` (el repo del carril, que NO puede ser un repo sensible). Vacío = igual que repo_dir.
    hub_dir: str = ""
    # E2 (todo nace APAGADO: estos valores no activan nada). Comprobaciones previas al despacho, vía sondeo (host_ops).
    hub_sha_minimo: str = ""        # "" = sin comprobar; "pin" = el HEAD del hub; o un SHA hex (7-40) que el clon del host contiene
    latido_s: int = 0               # 0 = sin latido ni «sin señal»; >0 = cada cuántos s se sondea el host
    latido_parado_s: int = 300      # sin señal tras este tiempo sin un sondeo correcto (solo con latido_s > 0)
    disco_min_gb: float = 0.0       # 0 = no se comprueba; si el host informa de menos GB libres, no se lanza
    contrato_hash: bool = False     # True: el hash de contract/ del host debe coincidir con el del hub

    def __post_init__(self):
        if not isinstance(self.destino, str) or not HOST_DESTINO_RE.fullmatch(self.destino):
            raise ValueError(f"host.destino {self.destino!r}: solo letras, cifras, . _ - y sin guion inicial")
        for nombre in ("repo_dir", "worktree_root", "hub_dir"):
            valor = getattr(self, nombre)
            if (not isinstance(valor, str) or not HOST_RUTA_RE.fullmatch(valor)
                    or ".." in re.split(r"[\\/]", valor)):
                raise ValueError(f"host.{nombre} {valor!r}: ruta absoluta de Windows sin comillas, % & | < > ^ ni '..'")
        if not isinstance(self.max_parallel, int) or self.max_parallel < 1:
            raise ValueError("host.max_parallel debe ser un entero >= 1")
        if not isinstance(self.conexion_timeout_s, int) or not 1 <= self.conexion_timeout_s <= 120:
            raise ValueError("host.conexion_timeout_s debe estar entre 1 y 120")
        if self.hub_sha_minimo and self.hub_sha_minimo != "pin" and not HOST_SHA_RE.fullmatch(str(self.hub_sha_minimo)):
            raise ValueError("host.hub_sha_minimo debe ser 'pin' o un SHA hexadecimal de 7 a 40 caracteres")
        for nombre, tope in (("latido_s", 3600), ("latido_parado_s", 86400)):
            v = getattr(self, nombre)
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= tope:
                raise ValueError(f"host.{nombre} debe ser un entero entre 0 y {tope}")
        d = self.disco_min_gb
        if isinstance(d, bool) or not isinstance(d, (int, float)) or not 0 <= d <= 100000:
            raise ValueError("host.disco_min_gb debe ser un número >= 0")
        if not isinstance(self.contrato_hash, bool):
            raise ValueError("host.contrato_hash debe ser true o false")

    @property
    def agent_lanes_dir(self) -> str:
        """{AGENT_LANES_DIR} en el host: tools/agent-lanes del clon de oscar-command-hub, con barras normales."""
        return self.hub_dir.rstrip("\\/").replace("\\", "/") + "/tools/agent-lanes"


HOST_REPOS_SENSIBLES = ("oscar-command-hub", "oscar-hq")  # repos donde NO se admite `host:` (configurable)


def _segmentos(valor) -> list[str]:
    return [x for x in re.split(r"[\\/]", str(valor or "").strip()) if x != ""]


def _repo_apunta_al_hub(valor: str) -> str | None:
    """Motivo si una ruta de repo del hub es el propio hub por otra vía: segmentos `.`/`..`, junction o enlace que
    resuelve dentro del clon del hub, o un worktree del hub (su `.git` apunta al `.git` del hub). Solo si la ruta existe."""
    if any(x in (".", "..") for x in _segmentos(valor)):
        return "la ruta lleva segmentos '.' o '..'"
    try:
        p = Path(str(valor))
        if not p.exists():
            return None
        raiz = ROOT.parents[1].resolve()
        real = p.resolve()
        if real == raiz or raiz in real.parents:
            return "la ruta resuelve (enlace/junction) dentro del clon del hub"
        git = p / ".git"
        if git.is_file():
            gitdir = git.read_text(encoding="utf-8", errors="replace").replace("\\", "/").lower()
            if (raiz / ".git").as_posix().lower() in gitdir:
                return "la ruta es un worktree del hub"
    except OSError:
        return None
    return None


def host_repo_sensible(name: str, merged: dict, lista=None, host=None) -> str | None:
    """Nombre del repo sensible al que apunta el carril (por `github:`, por la carpeta de `repo:` o por `host.repo_dir`),
    o None. La lista de lanes.yaml SE UNE a la de fábrica (nunca la sustituye). Un `repo:` que apunta al hub por `.`,
    junction o worktree también es sensible."""
    extra = lista if isinstance(lista, (list, tuple)) else ()
    sensibles = {str(x).strip().casefold() for x in (*HOST_REPOS_SENSIBLES, *extra)}
    for valor in (merged.get("github"), merged.get("repo"), getattr(host, "repo_dir", "")):
        partes = _segmentos(valor)
        ultimo = (partes[-1] if partes else "").casefold().removesuffix(".git")
        if ultimo in sensibles:
            return ultimo
    if merged.get("repo"):
        motivo = _repo_apunta_al_hub(merged["repo"])
        if motivo:
            return f"oscar-command-hub ({motivo})"
    return None


def host_from_yaml(name: str, cfg) -> HostCfg | None:
    if cfg is None:
        return None
    if not cfg:  # `host: {}` o `host: ""` no degradan en silencio a carril local
        raise ValueError(f"{name}: `host` vacío (quítalo para un carril local)")
    if not isinstance(cfg, dict):
        raise ValueError(f"{name}: `host` debe ser un bloque (destino, repo_dir, worktree_root, ...)")
    propios = {"destino", "repo_dir", "worktree_root", "max_parallel", "conexion_timeout_s", "hub_dir", "hub_sha_minimo",
               "latido_s", "latido_parado_s", "disco_min_gb", "contrato_hash"}
    for k in cfg:
        if k in HOST_CLAVES_FUTURAS:
            logging.getLogger("agent_lanes").warning("%s: host.%s es de una entrega posterior (E2/E3); se IGNORA: no "
                                                     "hay protección ni límite por esa clave todavía", name, k)
        elif k not in propios:
            raise ValueError(f"{name}: clave desconocida en host: {k!r}")
    faltan = [k for k in ("destino", "repo_dir", "worktree_root", "hub_dir") if not cfg.get(k)]
    if faltan:
        raise ValueError(f"{name}: host sin {', '.join(faltan)}")
    try:
        return HostCfg(**{k: cfg[k] for k in propios if k in cfg})
    except ValueError as exc:
        raise ValueError(f"{name}: {exc}") from None


@dataclass(frozen=True)
class Lane:
    name: str
    board: str = ""
    repo: str = ""
    base: str = ""
    tool: str = "claude"
    model: str = "sonnet"
    effort: str = "medium"
    model_small: str = ""  # modelo barato para tarjetas `Tamaño: S` (grandes.es_pequena); "" = siempre `model`
    # Lista blanca de alias que una tarjeta puede fijar con `model_override` (worker.choose_model); otro valor se ignora.
    model_permitidos: tuple[str, ...] = ("sonnet", "haiku")
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
    # Hueco «grande» (23-10): tarjetas grandes (agent_lanes/grandes.py) de ESTE carril que corren a la vez, fuera de
    # max_parallel (el carril normal sigue con lo pequeño). 0 = sin hueco grande (todo va por el carril normal).
    # `big_minutes`: runtime estimado a partir del cual una tarjeta es grande. El tope global es runner.max_big_total.
    max_big: int = 1
    big_minutes: int = 45
    # Rutas que, citadas en dos tarjetas del mismo carril, impiden que corran a la vez (p. ej. supabase/migrations).
    serialize_paths: tuple[str, ...] = ()
    # Lista blanca de repos por tarjeta (`Repo: <alias>` en el cuerpo): alias -> {repo, github, base, test_cmd, ...}.
    # Ver telegram_lane.py. El primero es el de por defecto.
    repos: dict = field(default_factory=dict, hash=False, compare=False)
    # Nombres antiguos del carril (23-10, carriles por proyecto): se aceptan en entrada (tarjetas abiertas, .env,
    # hilos de Telegram) y resuelven al carril canónico; nunca se emiten.
    aliases: tuple[str, ...] = ()
    # E2: revisión del carril review EN el host (False = se mantiene el bloqueo «aprobación humana»); tras aprobación
    # explícita, repetir la batería en el hub con safe_env (False = el integrador sigue fallando cerrado); tope diario
    # de gasto del host en USD (None = sin tope). Nada de esto se activa solo.
    host_review: bool = False
    host_integracion: bool = False
    host_gasto_dia_usd: float | None = None
    # Segundo ordenador (E1 de specs/worker-remoto-ssh.md): None = carril local, todo como siempre. Con `host:` el
    # worker, la preparación del worktree y los tests corren en ese equipo por SSH; el kanban y verify (parte git)
    # siguen en el hub. `max_parallel` del carril queda acotado por el del host (load_lanes).
    host: HostCfg | None = None

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
    if "host" in defaults:
        raise ValueError("`host` no puede ir en defaults: es por carril (opt-in)")
    lanes = {}
    for name, cfg in data["lanes"].items():
        merged = {**defaults, **cfg}
        merged["host"] = host_from_yaml(name, merged.get("host"))
        for clave in ("host_review", "host_integracion", "host_gasto_dia_usd"):
            if merged.get(clave) is not None and not merged["host"]:
                raise ValueError(f"{name}: `{clave}` solo tiene sentido en un carril con `host:`")
        if merged.get("host_gasto_dia_usd") is not None:
            g = merged["host_gasto_dia_usd"]
            if isinstance(g, bool) or not isinstance(g, (int, float)) or g <= 0:
                raise ValueError(f"{name}: host_gasto_dia_usd debe ser un número > 0")
            merged["host_gasto_dia_usd"] = float(g)
        for clave in ("host_review", "host_integracion"):
            if merged.get(clave) is not None and not isinstance(merged[clave], bool):
                raise ValueError(f"{name}: {clave} debe ser true o false")
        if merged["host"]:
            if merged.get("kind", "implement") != "implement":
                raise ValueError(f"{name}: `host` solo se admite en carriles kind: implement")
            sensible = host_repo_sensible(name, merged, data.get("host_sensitive_repos"), merged["host"])
            if sensible:
                raise ValueError(f"{name}: `host` no se admite en el repo sensible {sensible!r} (código del host ejecutándose "
                                 f"junto al hub). Lista en `host_sensitive_repos` de lanes.yaml (por defecto "
                                 f"{', '.join(HOST_REPOS_SENSIBLES)})")
            if merged.get("repos"):
                raise ValueError(f"{name}: `host` no se combina con `repos:` (un solo clon remoto por carril)")
            if int(merged.get("reserved_slots") or 0) > 0:
                raise ValueError(f"{name}: `host` no se combina con `reserved_slots` (el tope del host es compartido)")
            merged["max_parallel"] = min(int(merged.get("max_parallel", 1)), merged["host"].max_parallel)
        for f in TUPLE_FIELDS:
            merged[f] = tuple(merged.get(f) or ())
        if merged.get("model_permitidos"):
            merged["model_permitidos"] = tuple(str(m).strip().lower() for m in merged["model_permitidos"])
        else:
            merged.pop("model_permitidos", None)  # ausente o vacía: la de fábrica (nunca sin lista blanca)
        merged["telegram"] = _chat_thread(merged.get("telegram"))
        merged["serialize_paths"] = tuple(merged.get("serialize_paths") or ())
        lanes[name] = Lane(name=name, **merged)
    return LaneMap(lanes)


def _chat_thread(cfg: dict | None, any_thread: str = "0") -> tuple[str, str] | None:
    """{chat, thread} de YAML (ints) -> (str, str), mismo formato que telegram_target()."""
    if not cfg:
        return None
    thread = cfg.get("thread")
    return str(cfg["chat"]), any_thread if thread is None else str(thread)


def _destino(cfg, name: str) -> tuple[str, str] | None:
    """{chat, thread} de un destino que NO admite valor por defecto: sin `thread` (o con 0) caería en el General del
    grupo, así que falta cualquiera de los dos = destino apagado (y se avisa en el log)."""
    if not cfg:
        return None
    chat, thread = (cfg.get("chat"), cfg.get("thread")) if isinstance(cfg, dict) else (None, None)
    if not chat or not thread or str(thread) == "0":
        logging.getLogger("agent_lanes").warning("%s necesita chat y thread (distinto de 0); queda apagado", name)
        return None
    return str(chat), str(thread)


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
            # Tema «🎯 Decisiones» (telegram.destinos.decisiones {chat, thread}); None = todo al DM de Oscar como antes.
            "decisiones": _destino((cfg.get("destinos") or {}).get("decisiones"), "telegram.destinos.decisiones"),
            # Tema de Integración (fichas de PR/merge/deploy + resumen fijado); None = tema del carril, como antes.
            "integration": _chat_thread(data.get("integration_telegram")),
            # Tema «Atención» (avisos accionables con @mención) {chat, thread, mention, remind_hours}; None = apagado.
            "attention": dict(data["attention_telegram"]) if (data.get("attention_telegram") or {}).get("chat") else None}


def load_runner_settings(path: Path | None = None) -> dict:
    """Top-level `runner:` block (global worker cap, poll interval)."""
    from .ajustes_hub import aplicar_runner  # lo editado con /ajustes manda sobre lanes.yaml (rige al arrancar el runner)
    return aplicar_runner({"max_workers": 1, "interval_seconds": 60, "ciego_minutes": 5, "max_big_total": 2,
                           "latido_parado_segundos": 300, "reinicio_auto_colgado": False, "worker_ram_mb": 0,
                           "worker_ram_intervalo_segundos": 15,
                           **(_load(path).get("runner") or {})})


def kanban_backend(env: dict | None = None, path: Path | None = None, env_file: Path | None = None) -> str:
    """Backend del kanban (F2-02): `KANBAN_BACKEND` del entorno (o de `env`) > el de `.env` del hub >
    `runner.kanban_backend` de lanes.yaml > `hermes` (la CLI de Hermes de siempre). Lo valida y fija
    kanban_api.backend_name (una vez por proceso)."""
    import os
    value = str((env if env is not None else os.environ).get("KANBAN_BACKEND") or "").strip().lower()
    if not value:
        value = str(load_env(env_file).get("KANBAN_BACKEND") or "").strip().lower()
    if not value:
        try:
            value = str((_load(path).get("runner") or {}).get("kanban_backend") or "").strip().lower()
        except (OSError, AttributeError):
            value = ""
    return value or "hermes"


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


def ohq_api_url(env: dict | None = None, path: Path | None = None) -> str:
    """URL de la API de Oscar HQ (F1-02): la de `.env`/entorno (OHQ_API_URL) manda; sin ella, la de `ohq.api_url` de
    lanes.yaml (config de confianza, no es un secreto). Vacío = sin URL (sync_usuarios y la vigilancia del equipo
    quedan apagados). Sin OHQ_API_KEY siguen apagados igual: la clave nunca tiene valor por defecto."""
    value = str((env or {}).get("OHQ_API_URL") or "").strip()
    if value:
        return value
    try:
        cfg = _load(path).get("ohq") or {}
    except Exception:
        return ""
    return str(cfg.get("api_url") or "").strip() if isinstance(cfg, dict) else ""


def _chats_de(cfg) -> set[str]:
    """Chats de un bloque {chat, ...} o de una lista de ellos (los thread no cuentan para la allowlist)."""
    items = cfg if isinstance(cfg, list) else [cfg]
    return {str(c["chat"]) for c in items if isinstance(c, dict) and c.get("chat")}


def chats_de_confianza(path: Path | None = None) -> set[str]:
    """Chats que lanes.yaml ya usa como destino (temas de carril, final_only, Integración, 🎯 Decisiones, panel del hub,
    👥 Equipo, Atención). Derivan la allowlist de Origen-Telegram (F1-02) para no repetirlos a mano en
    TELEGRAM_ALLOWED_CHATS. Un lanes.yaml ilegible da el conjunto vacío (se queda lo del .env)."""
    try:
        data = _load(path)
    except Exception:
        return set()
    tg = data.get("telegram") or {}
    out = set()
    for lane in (data.get("lanes") or {}).values():
        if isinstance(lane, dict):
            out |= _chats_de(lane.get("telegram"))
    out |= _chats_de(tg.get("final_only") or [])
    out |= _chats_de((tg.get("destinos") or {}).get("decisiones"))
    for key in ("integration_telegram", "hub_panel", "equipo_telegram", "attention_telegram"):
        out |= _chats_de(data.get(key))
    return out
