"""Carril Integrador: fusión DETERMINISTA (código, no un LLM) de los PR que Oscar aprobó desde Telegram.

Detrás de INTEGRATOR_ENABLED (.env o entorno; por defecto apagado). El runner llama a `run_pass()` en cada vuelta.

Por cada tarjeta `done` de un carril con política en lanes.yaml (`integrator.lanes`) que tenga comentario
`APROBADO-OSCAR … PR <url>` de Oscar (autor oscar-telegram), el PR abierto y ningún `INTEGRADO`:

  1. Gates en un worktree temporal `int-<id>`: PR fusionado (--no-ff, sin empujar) sobre <remote>/<base>.
     (a) sin conflictos · (b) test_cmd del carril exit 0 · (c) secret-scan de las líneas añadidas
     (d) toca backend/alembic/versions/ -> un solo head (análisis estático con ast: importar las migraciones sería
         ejecutar código del PR) + "requiere migración"; nunca se aplica
     (e) toca supabase/migrations/ -> "requiere migración manual".
  2. Gate fallido -> comentario en la tarjeta + aviso ⛔ en lenguaje llano. Fin.
  3. Todo OK -> NO fusiona: aviso "🚦 Listo para integrar" con botón. Oscar HQ: [🔀 Fusionar] y después
     [🚀 Desplegar]. MigraTeam (el merge ES el deploy): [🚀 Fusionar y desplegar a producción].
     Con migración pendiente no hay botón de deploy: "⏸ requiere aplicar migración antes (manual, con OK)".
  4. Al pulsar (solo Oscar; lo filtra DecisionDesk): `gh pr merge --squash --match-head-commit <sha>` ->
     comentario `INTEGRADO <sha>`. Deploy de Oscar HQ: `railway up --detach` de un worktree limpio del commit
     fusionado, con ids explícitos, y sondeo de ESE deployment hasta SUCCESS/FAILED (15 min). MigraTeam: sondeo
     de GET /health hasta que `commit_sha` sea el del merge (su CLI de Railway no está enlazada).

Nunca: --force, --admin, --auto, push a la rama base, variables de entorno, migraciones.
Nada técnico ni ningún secreto encontrado llega a Telegram: solo archivo:línea y el nombre de la regla.

Prueba en seco (solo lectura: fetch + worktree temporal, sin comentarios, sin Telegram, sin merge):
    py -3.12 -m agent_lanes.integrator --dry-run                         # tarjetas aprobadas de todos los carriles
    py -3.12 -m agent_lanes.integrator --dry-run --lane claude-oscarhq --pr 12   # gates de un PR concreto
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):  # `py agent_lanes/integrator.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "agent_lanes"

from . import proc as _proc  # noqa: E402
from .config import ROOT, _chat_thread, _load  # noqa: E402
from .deps import is_integration_comment, pending_parents, waiting_line  # noqa: E402
from .hermes import OSCAR_AUTHOR  # noqa: E402
from .integration import (CHANNEL, FAILED, WAITING, PinnedSummary, link_line, phase_for, render_ficha,  # noqa: E402
                          repo_name, risk_of, summary_state, summary_text)
from .notices import MessageStore, TaskNotices, render, status_line, truncate  # noqa: E402
from .telegram import telegram_target  # noqa: E402
from .verify import render_test_cmd  # noqa: E402

log = logging.getLogger("agent_lanes")

GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"
RAILWAY_EXE = str(Path.home() / "AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe")
STATE_DIR = ROOT / ".state" / "integrator"

INTEGRATOR_AUTHOR = "lane-integrator"
APPROVED_PREFIX = "APROBADO-OSCAR"
INTEGRATED_PREFIX = "INTEGRADO"
INT_MERGE, INT_DEPLOY, INT_MERGE_DEPLOY = "int_merge", "int_deploy", "int_merge_deploy"
INT_MIGRATION_APPLIED = "int_mig_applied"  # ✅ Migración aplicada (ficha fusionada con migración pendiente)
INT_APPLY = "int_apply"  # claude-hub: [🔁 Aplicar (reinicio ordenado)] tras fusionar
INT_ACTIONS = (INT_MERGE, INT_DEPLOY, INT_MERGE_DEPLOY, INT_MIGRATION_APPLIED, INT_APPLY)
MIGRATION_APPLIED_PREFIX = "MIGRACION-APLICADA"
MIGRATION_KINDS = ("supabase", "alembic")  # "infra" no es una migración: no se da por aplicada con un botón
MIGRATION_BUTTON = {"text": "✅ Migración aplicada", "action": INT_MIGRATION_APPLIED}


def pending_migration(st: dict) -> str | None:
    """Migración (o infraestructura) que aún bloquea el deploy de una ficha: la de su estado salvo que Oscar o el
    coordinador la hayan marcado como aplicada (`migration_applied`)."""
    mig = st.get("migration")
    return None if (mig in MIGRATION_KINDS and st.get("migration_applied")) else (mig or None)
DEPLOY_RAILWAY_UP, DEPLOY_ON_MERGE, DEPLOY_NONE = "railway_up", "on_merge", "none"
# `apply` de una política con deploy none: qué hace [🔁 Aplicar] tras fusionar. Solo restart_drain (claude-hub):
# `py -3.12 lanes.py restart --drain` en el checkout VIVO (ROOT), lanzado sin esperar, porque reinicia el propio
# proceso que atiende el botón. El subcomando lo aporta feat/plan-d-runtime; mientras lanes.py no lo tenga, no hay
# botón. El fast-forward del checkout raíz a main es cosa de ese restart (deploy de HEAD), nunca del integrador.
APPLY_RESTART_DRAIN = "restart_drain"
APPLY_ARGV = {APPLY_RESTART_DRAIN: ["py", "-3.12", "lanes.py", "restart", "--drain"]}
APPLY_LABEL = "🔁 Aplicar (reinicio ordenado)"
APPLIED_PREFIX = "APLICADO"
NEEDS_MIGRATION = "⏸ requiere aplicar migración antes (manual, con OK)"
MIGRATEAM_WARNING = "⚠️ Fusionar DESPLIEGA a producción solo (Railway, en minutos)"
RAILWAY_OK = {"SUCCESS"}
RAILWAY_BAD = {"FAILED", "CRASHED", "REMOVED", "SKIPPED"}
FORBIDDEN_FLAGS = ("--force", "--admin", "--auto")  # en gh; git push está vetado entero

PR_URL_RE = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# (c) Reglas del secret-scan. Solo se informa del NOMBRE de la regla y archivo:línea, nunca de la coincidencia.
SECRET_RULES: tuple[tuple[str, re.Pattern], ...] = (
    ("clave sk-", re.compile(r"(?<![\w-])sk-(?:[A-Za-z]+-)*[A-Za-z0-9_-]{20,}")),
    ("token de GitHub", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("JWT largo (eyJ…)", re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}")),
    ("clave privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("clave de Stripe live", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}")),
    ("clave de AWS", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("token de bot de Telegram", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b")),
    ("token de Slack", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("clave de Google", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("clave secreta de Supabase", re.compile(r"\bsb_secret_[A-Za-z0-9_-]{20,}")),
    ("URL de base de datos con contraseña", re.compile(
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)(?:\+\w+)?://[^\s:/@'\"]+:[^\s@/'\"]+@")),
    ("service_role con valor", re.compile(
        r"service[_-]?role[\w-]*[\"']?\s*[:=]\s*[\"']?(?!\s*(?:os\.|process\.|\$\{|<|None|null|\"\"|''))"
        r"[A-Za-z0-9._-]{20,}", re.I)),
)

# Ejecutables en el diff: con cwd = worktree, cmd.exe y CreateProcess los encuentran antes que el PATH
# (py.bat, git.exe...): ejecutarían código del PR con las credenciales de Oscar. Gate que falla, sin excepción.
EXEC_SUFFIXES = (".bat", ".cmd", ".exe", ".com", ".ps1", ".vbs", ".js", ".dll", ".scr", ".msi")
# Un .py con nombre de módulo de la stdlib (scripts/argparse.py) suplanta el import de un script de la base.
STDLIB_NAMES = frozenset(getattr(sys, "stdlib_module_names", ()))
# Entorno mínimo del test_cmd: sin tokens del runner y sin buscar ejecutables en el directorio actual.
SAFE_ENV_KEYS = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE",
                 "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PROGRAMFILES", "HOMEDRIVE", "HOMEPATH")
TID_RE = re.compile(r"t_[0-9A-Za-z]{1,40}")
APPROVALS_DIR = STATE_DIR / "approvals"


# --- configuración -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Policy:
    lane: str
    deploy: str = DEPLOY_NONE                # railway_up | on_merge | none
    alembic_versions: str = ""               # p. ej. backend/alembic/versions/
    manual_migrations: tuple[str, ...] = ()  # p. ej. supabase/migrations/
    railway_dir: str = ""                    # railway_up: directorio enlazado con la CLI (cwd del `up`)
    railway_project: str = ""
    railway_service: str = ""
    railway_environment: str = ""
    health_url: str = ""
    health_commit_key: str = ""              # clave del JSON de health con el sha desplegado (MigraTeam: commit_sha)
    # Infraestructura de arranque/deploy (Procfile, railway.*, alembic/env.py...): se fusiona, pero sin botón de
    # deploy (MigraTeam: sin botón) y con aviso, igual que una migración.
    sensitive_paths: tuple[str, ...] = ()
    # on_merge: texto del botón y aviso. MigraTeam (28-09) fusiona en develop = staging, nunca en master.
    merge_label: str = "🚀 Fusionar y desplegar a producción"
    merge_warning: str = ""
    # Cabecera de la ficha en el tema de Integración: staging (🟢) | manual (🟠, deploy con botón) | production (🔴).
    # Vacío = se deduce del deploy asumiendo lo peor (on_merge = 🔴 PRODUCCIÓN). risk_label cambia el texto (OSCAR HQ).
    risk: str = ""
    risk_label: str = ""
    # Solo con deploy none: acción de [🔁 Aplicar] tras fusionar (restart_drain = reinicio ordenado del runner).
    apply: str = ""


@dataclass(frozen=True)
class IntegratorSettings:
    enabled: bool = False
    interval_seconds: int = 300
    worktree_root: str = ""
    deploy_timeout_seconds: int = 900
    poll_seconds: int = 20
    policies: dict = field(default_factory=dict)
    # Tema de Integración (`integration_telegram` de lanes.yaml): fichas + resumen fijado + copia en el DM de Oscar.
    # None = los avisos siguen en el tema del carril, con el formato de siempre.
    integration_telegram: tuple[str, str] | None = None


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def load_integrator_settings(path: Path | None = None, env: dict | None = None, *,
                             lane_filter: bool = True) -> IntegratorSettings:
    """`integrator:` de lanes.yaml + INTEGRATOR_ENABLED (entorno del proceso o .env). Sin sección: apagado.
    `lane_filter=False`: todas las políticas aunque INTEGRATOR_LANES limite los carriles activos (la cabecera de
    riesgo de las fichas no puede caer a 🟢 SIN DEPLOY en un carril cuyo merge despliega)."""
    data = _load(path) or {}
    cfg = data.get("integrator") or {}
    env = env or {}
    enabled = _truthy(os.environ.get("INTEGRATOR_ENABLED", env.get("INTEGRATOR_ENABLED")))
    # INTEGRATOR_LANES=claude-oscarhq,... limita los carriles activos sin tocar sus políticas (vacío = todos).
    only = {x.strip() for x in str(os.environ.get("INTEGRATOR_LANES", env.get("INTEGRATOR_LANES")) or "").split(",")
            if x.strip()}
    policies = {}
    for name, p in (cfg.get("lanes") or {}).items():
        if lane_filter and only and name not in only:
            continue
        p = dict(p or {})
        p["manual_migrations"] = tuple(p.get("manual_migrations") or ())
        p["sensitive_paths"] = tuple(p.get("sensitive_paths") or ())
        if p.get("health_url") and not str(p["health_url"]).startswith("https://"):
            raise ValueError(f"integrator.lanes.{name}.health_url debe ser https://")
        policies[name] = Policy(lane=name, **{k: ("" if v is None else v) for k, v in p.items()})
        if policies[name].deploy not in (DEPLOY_RAILWAY_UP, DEPLOY_ON_MERGE, DEPLOY_NONE):
            raise ValueError(f"integrator.lanes.{name}.deploy desconocido: {policies[name].deploy}")
        if policies[name].apply and (policies[name].apply not in APPLY_ARGV or policies[name].deploy != DEPLOY_NONE):
            raise ValueError(f"integrator.lanes.{name}.apply: solo {sorted(APPLY_ARGV)} y con deploy none")
    return IntegratorSettings(enabled=enabled, interval_seconds=int(cfg.get("interval_seconds") or 300),
                              worktree_root=cfg.get("worktree_root") or str(ROOT / ".state" / "integrator" / "wt"),
                              deploy_timeout_seconds=int(cfg.get("deploy_timeout_seconds") or 900),
                              poll_seconds=int(cfg.get("poll_seconds") or 20), policies=policies,
                              integration_telegram=_chat_thread(data.get("integration_telegram")))


# --- gates puros ---------------------------------------------------------------------------------------

def scan_secrets(diff: str) -> list[tuple[str, int, str]]:
    """Líneas AÑADIDAS de un diff unificado -> [(archivo, línea, regla)]. Nunca devuelve el valor.
    Las cabeceras `---`/`+++` solo cuentan justo tras `diff --git` (una línea añadida `++ x` no es cabecera)."""
    hits, path, line, header = [], None, 0, False
    for raw in (diff or "").splitlines():
        if raw.startswith("diff --git "):
            header, path = True, None
            continue
        if header:
            if raw.startswith("+++ "):
                p = raw[4:].strip()
                path = None if p == "/dev/null" else (p[2:] if p.startswith("b/") else p)
            elif raw.startswith("Binary files"):
                hits.append((raw[13:].split(" and ")[-1].split(" differ")[0].removeprefix("b/"), 0,
                             "archivo binario sin revisar"))
            if not raw.startswith("@@"):
                continue
            header = False
        if raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
            continue
        if raw.startswith("+") and path:
            for name, rx in SECRET_RULES:
                if rx.search(raw[1:]):
                    hits.append((path, line, name))
                    break
            line += 1
        elif raw.startswith(" "):
            line += 1
    return hits


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def parse_revision(source: str) -> tuple[str | None, tuple[str, ...]]:
    """(revision, down_revisions) de una migración, sin ejecutarla. Acepta `x = ...` y `x: str = ...`."""
    rev, down = None, ()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            name, value = node.target.id, node.value
        else:
            continue
        if name == "revision":
            v = _literal(value)
            rev = v if isinstance(v, str) else None
        elif name == "down_revision":
            v = _literal(value)
            down = (v,) if isinstance(v, str) else tuple(x for x in (v or ()) if isinstance(x, str))
    return rev, down


def alembic_heads(versions_dir: Path) -> tuple[set[str], list[str]]:
    """Heads del grafo de Alembic por análisis estático (= `alembic heads` sin importar el código del PR).
    Devuelve (heads, problemas): ids duplicados, archivos ilegibles o down_revision a una revisión inexistente."""
    revs: dict[str, str] = {}
    downs: set[str] = set()
    problems: list[str] = []
    for f in sorted(Path(versions_dir).glob("*.py")):
        if f.name.startswith("__"):
            continue
        try:
            rev, down = parse_revision(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError) as exc:
            problems.append(f"{f.name}: no se puede leer ({type(exc).__name__})")
            continue
        if not rev:
            problems.append(f"{f.name}: sin `revision`")
            continue
        if rev in revs:
            problems.append(f"revision {rev} duplicada ({revs[rev]} y {f.name})")
        revs[rev] = f.name
        downs.update(down)
    problems += [f"down_revision {d} no existe" for d in sorted(downs - set(revs))]
    return set(revs) - downs, problems


# --- resultado de los gates ----------------------------------------------------------------------------

@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)   # públicas (lenguaje llano; sin secretos ni rutas locales)
    detail: list[str] = field(default_factory=list)    # técnicas: tarjeta y log
    passed: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    migration: str | None = None                       # "alembic" | "supabase" | None
    sensitive: list[str] = field(default_factory=list)  # infraestructura de deploy tocada
    base_sha: str = ""
    head_sha: str = ""
    test_exit: int | None = None


class Integrator:
    def __init__(self, settings: IntegratorSettings, lanes: dict, *, hermes_for: Callable[[str], object],
                 links=None, notifier=None, messages: MessageStore | None = None, desk=None,
                 runner=_proc.run, gh_exe: str = GH_EXE, railway_exe: str = RAILWAY_EXE,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 http_get: Callable[[str], tuple[int, str]] | None = None, state_dir: Path = STATE_DIR,
                 dry_run: bool = False, out: Callable[[str], None] = print,
                 now: Callable[[], float] = time.time, launcher: Callable[[list[str], str], None] | None = None,
                 apply_probe: Callable[[str], bool] | None = None):
        self.settings = settings
        self._launch = launcher or launch_detached      # [🔁 Aplicar]: lanza sin esperar (reinicia este proceso)
        self._apply_probe = apply_probe or apply_available  # ¿existe ya el subcomando? (lectura estática)
        self.lanes = lanes
        self.hermes_for = hermes_for
        self.links = links
        self.notifier = notifier
        self._notices = TaskNotices(notifier, messages) if notifier else None
        self.desk = desk
        self._run = runner
        self.gh_exe = gh_exe
        self.railway_exe = railway_exe
        self._clock = clock
        self._sleep = sleep
        self._http_get = http_get or _http_get
        self.state_dir = Path(state_dir)
        self.dry_run = dry_run
        self._out = out
        self._lock = threading.Lock()  # worktrees temporales y fetch: una operación git a la vez
        self._deploying = threading.Lock()  # un deploy a la vez (dos 🚀 seguidos no encadenan dos `railway up`)
        self._inflight = 0  # botones de Oscar en marcha (merge, deploy, verificación de /health)
        self._inflight_lock = threading.Lock()
        self._last_pass: float | None = None
        self._now = now  # reloj de pared: antigüedad del resumen fijado (self._clock es monotónico)
        # Resumen fijado del tema de Integración: se edita en cada pasada (y tras cada botón) si cambia.
        target = settings.integration_telegram
        self.pinned = (PinnedSummary(notifier, target, self.state_dir / "pinned.json")
                       if target and notifier and not dry_run else None)
        self._seen: list[tuple[object, Policy, str]] = []  # (carril, política, tarea) vistos en la última pasada

    # --- utilidades ----------------------------------------------------------------------------------

    def _exec(self, args: list[str], *, cwd: str | None = None, timeout: int = 300, shell: bool = False,
              env: dict | None = None) -> subprocess.CompletedProcess:
        if not shell:
            # El integrador nunca empuja (ni a la base ni a nada): el merge lo hace GitHub con --match-head-commit.
            if args[:1] == ["git"] and "push" in args:
                raise PermissionError("el integrador nunca hace git push")
            if args[:1] == [self.gh_exe] and any(a in FORBIDDEN_FLAGS for a in args):
                raise PermissionError(f"flag prohibido en gh: {[a for a in args if a in FORBIDDEN_FLAGS]}")
        kw = {"cwd": cwd} if cwd else {}
        if env:
            kw["env"] = env
        # stdin cerrado: `railway up` con la sesión caducada no puede quedarse esperando un login.
        return self._run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=timeout, shell=shell, stdin=subprocess.DEVNULL, **kw)

    def _git(self, repo: str, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
        return self._exec(["git", "-C", repo, *args], timeout=timeout)

    def _gh(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        # cwd fuera de cualquier clon: `gh pr merge --delete-branch` dentro de un checkout cambia de rama y borra
        # la local, y en el checkout raíz eso está prohibido.
        return self._exec([self.gh_exe, *args], cwd=tempfile.gettempdir(), timeout=timeout)

    def _railway(self, policy: Policy, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return self._exec([self.railway_exe, *args], cwd=policy.railway_dir, timeout=timeout)

    def _ids(self, policy: Policy) -> list[str]:
        return ["-p", policy.railway_project, "-s", policy.railway_service, "-e", policy.railway_environment]

    def _state_path(self, tid: str) -> Path:
        return self.state_dir / f"{tid}.json"

    def _state(self, tid: str) -> dict:
        try:
            return json.loads(self._state_path(tid).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, tid: str, **data) -> None:
        if self.dry_run:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_path(tid)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        cur = self._state(tid)
        since = {} if cur.get("since") else {"since": self._now()}  # pendiente desde (antigüedad del resumen)
        tmp.write_text(json.dumps({**cur, **since, **data, "updated": time.time()}), encoding="utf-8")
        os.replace(tmp, path)

    def _comment(self, lane, tid: str, text: str) -> None:
        if self.dry_run:
            self._out(f"[dry-run] comentaría en {tid}: {text[:300]}")
            return
        if not self.hermes_for(lane.board).comment(tid, text[:3000], author=INTEGRATOR_AUTHOR):
            log.warning("%s: no se pudo comentar en la tarjeta: %s", tid, text[:120])

    # --- pasada ---------------------------------------------------------------------------------------

    def run_pass(self, *, force: bool = False) -> dict[str, str]:
        """Una vuelta sobre las tarjetas aprobadas. Con throttle (interval_seconds) salvo `force`."""
        if not (self.settings.enabled or self.dry_run):
            return {}
        now = self._clock()
        if not force and self._last_pass is not None and now - self._last_pass < self.settings.interval_seconds:
            return {}
        self._last_pass = now
        results = {}
        seen = []
        for name, policy in self.settings.policies.items():
            lane = self.lanes.get(name)
            if not lane:
                continue
            try:
                tasks = self.hermes_for(lane.board).list_status(lane.name, "done")
            except Exception as exc:
                log.warning("integrador %s: no se pudieron listar tareas: %s", name, exc)
                seen += [x for x in self._seen if x[0] is lane]  # sin listado: se conserva lo de la pasada anterior
                continue
            for task in tasks:
                if TID_RE.fullmatch(task.get("id") or ""):
                    seen.append((lane, policy, task["id"]))
                try:
                    out = self.consider(lane, policy, task)
                except Exception as exc:  # una tarjeta rota no para las demás
                    log.warning("integrador %s: %s", task.get("id"), exc)
                    out = "error"
                if out:
                    results[task["id"]] = out
        self._seen = seen
        self.refresh_summary()
        return results

    def refresh_summary(self) -> str | None:
        """Edita el mensaje fijado del tema de Integración con lo pendiente (solo si cambia). Nunca lanza."""
        # Sin una pasada hecha (_last_pass None: arranque, o la CLI del coordinador) _seen está vacío y el fijado
        # quedaría en "nada pendiente": no se toca.
        if not self.pinned or self.dry_run or self._last_pass is None:
            return None
        try:
            entries = []
            for lane, policy, tid in list(self._seen):
                st = self._state(tid)
                state = summary_state(st.get("status") or "", deploy=policy.deploy, migration=pending_migration(st),
                                      problem=st.get("pr_problem"), apply=policy.apply)
                if not state or not st.get("pr"):
                    continue
                entries.append({"emoji": risk_of(policy).emoji, "pr": st["pr"], "title": st.get("title") or tid,
                                "state": state, "since": st.get("since")})
            return self.pinned.update(summary_text(entries, self._now()))
        except Exception as exc:
            log.warning("resumen de integración falló: %s", exc)
            return None

    @property
    def approvals_dir(self) -> Path:
        return self.state_dir / "approvals"

    def record_approval(self, lane, tid: str, number: int) -> bool:
        """Lo llama DecisionDesk._approve tras el ✅ de Oscar: registro LOCAL (no un comentario del kanban, que
        cualquiera con la CLI de hermes puede escribir con --author oscar-telegram) con el PR y su commit de cabeza.
        El integrador solo actúa sobre ese commit exacto."""
        slug = self.links.repo_slug(lane) if self.links else None
        pr = self._pr_view(slug, number) if slug else None
        sha = (pr or {}).get("headRefOid") or ""
        if not TID_RE.fullmatch(tid or "") or not re.fullmatch(r"[0-9a-f]{40}", sha):
            log.warning("%s: no se pudo registrar la aprobación del PR #%s", tid, number)
            return False
        self.approvals_dir.mkdir(parents=True, exist_ok=True)
        path = self.approvals_dir / f"{tid}.json"
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"task_id": tid, "lane": lane.name, "slug": slug, "pr": int(number),
                                   "head_sha": sha, "at": time.time()}), encoding="utf-8")
        os.replace(tmp, path)
        return True

    def _approved_record(self, tid: str) -> dict:
        try:
            return json.loads((self.approvals_dir / f"{tid}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _approval(self, lane, tid: str, comments: list[dict]) -> tuple[int, str] | None:
        """(número, url) del último APROBADO-OSCAR de Oscar cuyo PR es del repo del carril."""
        slug = self.links.repo_slug(lane) if self.links else None
        for c in reversed(comments):
            body = c.get("body") or ""
            if c.get("author") != OSCAR_AUTHOR or not body.startswith(APPROVED_PREFIX):
                continue
            m = PR_URL_RE.search(body)
            if not m:
                continue
            if not slug or m.group(1).lower() != slug.lower():
                log.warning("%s: el PR aprobado (%s) no es del repo del carril (%s)", tid, m.group(1), slug)
                return None
            return int(m.group(2)), m.group(0)
        return None

    def consider(self, lane, policy: Policy, task: dict) -> str | None:
        tid = task["id"]
        if not TID_RE.fullmatch(tid or ""):
            return None
        st = self._state(tid)
        if st.get("status") in ("integrated", "merged", "deployed"):
            return None
        show = self.hermes_for(lane.board).show(tid)
        comments = show.get("comments") or []
        if any(is_integration_comment(c) for c in comments):
            self._save(tid, status="integrated")
            return None
        approval = self._approval(lane, tid, comments)
        if not approval:
            return None
        number, url = approval
        task = {**(show.get("task") or {}), **task}
        from .review import implementation_metadata  # import tardío: review no depende del integrador
        for_oscar = (implementation_metadata(show) or {}).get("for_oscar")
        if for_oscar:
            task["for_oscar"] = str(for_oscar)[:600]
        about = {"lane": lane.name, "title": (task.get("title") or "")[:200]}  # para el resumen fijado
        pr = self._pr_view(self.links.repo_slug(lane), number)
        problem = self._pr_problem(lane, tid, pr) or self._approval_problem(tid, number, pr, show)
        if problem:
            if st.get("status") != "pr_problem" or st.get("pr_problem") != problem:
                log.info("%s: PR #%s no integrable: %s", tid, number, problem)
                self._save(tid, status="pr_problem", pr_problem=problem, pr=number, **about)
            return None
        # Dependencias lógicas (kanban link padre -> hijo): los gates solo ven conflictos, no que este PR necesite
        # el código de otro todavía sin integrar. Sin enlace declarado no hay forma de saberlo.
        waiting = pending_parents(self.hermes_for(lane.board), tid, show)
        if waiting:
            key = ",".join(w["id"] for w in waiting)
            if st.get("status") != "waiting_deps" or st.get("waiting") != key:
                self._publish_waiting(lane, task, number, url, waiting)
                self._save(tid, status="waiting_deps", pr=number, pr_url=url, waiting=key, **about)
            return "waiting_deps"
        if st.get("head_sha") == pr["headRefOid"] and st.get("status") in ("failed", "offered", "merging"):
            return None  # gates ya hechos sobre esta cabeza; se repiten al pulsar si la base se movió
        result = self.run_gates(lane, policy, tid, number, pr["headRefOid"])
        if self.dry_run:
            self._report(tid, number, result, policy)
            return "dry-run:" + ("ok" if result.ok else "failed")
        if not result.ok:
            self._publish_failed(lane, task, number, url, result)
            self._save(tid, status="failed", pr=number, pr_url=url, head_sha=result.head_sha, base_sha=result.base_sha,
                       **about)
            return "gates_failed"
        self._offer(lane, policy, task, number, url, result)
        self._save(tid, status="offered", pr=number, pr_url=url, head_sha=result.head_sha, base_sha=result.base_sha,
                   migration=result.migration, **about)
        return "offered"

    def _pr_view(self, slug: str, number: int) -> dict | None:
        cp = self._gh("pr", "view", str(number), "--repo", slug, "--json",
                      "number,url,state,isDraft,headRefName,headRefOid,baseRefName,isCrossRepository,mergeCommit")
        if cp.returncode != 0:
            log.warning("gh pr view %s falló: %s", number, (cp.stderr or "").strip()[:300])
            return None
        try:
            return json.loads(cp.stdout or "null")
        except json.JSONDecodeError:
            return None

    def _approval_problem(self, tid: str, number: int, pr: dict, show: dict) -> str | None:
        """El commit a integrar debe ser EXACTAMENTE el que aprobó Oscar (registro local) y el que revisó el carril
        review (metadata del handoff). Commits empujados después no se integran sin volver a pasar por Oscar."""
        rec = self._approved_record(tid)
        if not rec or int(rec.get("pr") or 0) != number:
            return "sin registro local de tu aprobación para este PR"
        if rec.get("head_sha") != pr.get("headRefOid"):
            return "hay commits después de tu aprobación"
        from .review import implementation_metadata  # import tardío: review no depende del integrador
        reviewed = str((implementation_metadata(show) or {}).get("head_sha") or "").lower()
        if reviewed and not pr["headRefOid"].startswith(reviewed):
            return "el commit del PR no es el que revisó el carril review"
        return None

    @staticmethod
    def _pr_problem(lane, tid: str, pr: dict | None) -> str | None:
        if not pr:
            return "no se pudo leer el PR"
        if pr.get("state") != "OPEN":
            return f"el PR está {str(pr.get('state')).lower()}"
        if pr.get("isDraft"):
            return "el PR es un borrador"
        if pr.get("isCrossRepository"):
            return "el PR viene de un fork"
        if pr.get("headRefName") != f"lane/{tid}":
            return f"la rama del PR no es lane/{tid}"
        if pr.get("baseRefName") != lane.base:
            return f"la base del PR no es {lane.base}"
        if not re.fullmatch(r"[0-9a-f]{40}", pr.get("headRefOid") or ""):
            return "sin commit de cabeza"
        return None

    # --- gates ----------------------------------------------------------------------------------------

    def _nohooks(self) -> str:
        path = self.state_dir / "nohooks"
        path.mkdir(parents=True, exist_ok=True)
        return path.as_posix()

    def run_gates(self, lane, policy: Policy, tid: str, number: int, head_sha: str) -> GateResult:
        with self._lock:
            return self._gates(lane, policy, tid, number, head_sha)

    def _gates(self, lane, policy: Policy, tid: str, number: int, head_sha: str) -> GateResult:
        repo, remote, base = lane.repo, lane.remote, lane.base
        res = GateResult(ok=False, head_sha=head_sha)
        pr_ref = f"refs/integrator/pr-{number}"
        cp = self._git(repo, "fetch", remote, f"+refs/heads/{base}:refs/remotes/{remote}/{base}",
                       f"+refs/pull/{number}/head:{pr_ref}")
        if cp.returncode != 0:
            res.reasons.append("no se pudo descargar el PR de GitHub")
            res.detail.append(f"git fetch: {(cp.stderr or '').strip()[:300]}")
            return res
        fetched = (self._git(repo, "rev-parse", pr_ref).stdout or "").strip()
        if fetched != head_sha:
            res.reasons.append("el PR cambió mientras se revisaba; se repetirá")
            res.detail.append(f"head esperado {head_sha[:12]}, descargado {fetched[:12]}")
            return res
        res.base_sha = (self._git(repo, "rev-parse", f"{remote}/{base}").stdout or "").strip()
        wt = Path(self.settings.worktree_root) / f"int-{tid}"
        self._remove_worktree(repo, wt)
        cp = self._git(repo, "-c", f"core.hooksPath={self._nohooks()}", "worktree", "add", "--detach", str(wt),
                       res.base_sha)
        if cp.returncode != 0:
            res.reasons.append("no se pudo preparar la copia de prueba")
            res.detail.append(f"git worktree add: {(cp.stderr or '').strip()[:300]}")
            return res
        try:
            self._gates_in(wt, lane, policy, res)
        finally:
            self._remove_worktree(repo, wt)
        res.ok = not res.reasons
        return res

    def _gates_in(self, wt: Path, lane, policy: Policy, res: GateResult) -> None:
        w = str(wt)
        # Hooks neutralizados: MigraTeam apunta core.hooksPath a .githooks (versionado), y el merge ejecutaría
        # código del PR. Identidad por -c: nunca se toca la config del repo.
        merge = self._git(w, "-c", f"core.hooksPath={self._nohooks()}", "-c", "user.name=agent-lanes-integrator",
                          "-c", "user.email=agent-lanes@localhost", "-c", "commit.gpgsign=false",
                          "merge", "--no-ff", "--no-edit", res.head_sha)
        if merge.returncode != 0:
            conflicts = (self._git(w, "diff", "--name-only", "--diff-filter=U").stdout or "").split()
            self._git(w, "-c", f"core.hooksPath={self._nohooks()}", "merge", "--abort")
            res.reasons.append(f"tiene conflictos con {lane.base}" +
                               (f" en {len(conflicts)} archivo(s)" if conflicts else ""))
            res.detail.append("conflictos: " + (", ".join(conflicts[:20]) or (merge.stderr or "").strip()[:300]))
            return
        res.passed.append(f"sin conflictos con {lane.base}")
        rng = f"{res.base_sha}...{res.head_sha}"
        res.changed_files = (self._git(w, "diff", "--no-renames", "--name-only", rng).stdout or "").split()

        forbidden = [f for f in res.changed_files if any(f.startswith(p) for p in lane.forbidden_paths)]
        if forbidden:
            res.reasons.append(f"toca rutas vetadas del carril: {', '.join(forbidden[:5])}")
        execs = [f for f in res.changed_files if f.lower().endswith(EXEC_SUFFIXES)]
        # Solo archivos NUEVOS: modificar un módulo que ya existía (p. ej. src/api/routes/signal.py en oscar-hq) no
        # introduce ninguna suplantación nueva (falso positivo del PR #55, 29-09).
        added = set((self._git(w, "diff", "--no-renames", "--name-only", "--diff-filter=A", rng).stdout or "").split())
        shadow = [f for f in res.changed_files
                  if f in added and f.endswith(".py") and Path(f).stem in STDLIB_NAMES]
        if execs or shadow:
            res.reasons.append("añade ejecutables o módulos que suplantan la librería estándar: "
                               + ", ".join((execs + shadow)[:5]))
        if any(Path(f).name == ".gitattributes" or (Path(f).name.startswith(".env") and not f.endswith(
                (".example", ".sample", ".template"))) for f in res.changed_files):
            res.reasons.append("toca .gitattributes o un .env (revísalo a mano)")
        res.sensitive = [f for f in res.changed_files
                         if any(f.startswith(p) or Path(f).name == p for p in policy.sensitive_paths)]

        # --text/--no-textconv/--no-ext-diff/--no-color: ni .gitattributes, ni binarios, ni la config del repo
        # pueden dejar ciego al scan.
        hits = scan_secrets(self._git(w, "diff", "--no-renames", "--no-color", "--no-ext-diff", "--no-textconv",
                                      "--text", "-U0", rng).stdout or "")
        if hits:
            where = ", ".join(f"{p}:{n} ({rule})" for p, n, rule in hits[:10])
            res.reasons.append(f"posible secreto en el código: {where}")
            res.detail.append(f"secret-scan: {len(hits)} coincidencia(s): {where}")
        else:
            res.passed.append("sin secretos")

        if policy.alembic_versions and any(f.startswith(policy.alembic_versions) for f in res.changed_files):
            heads, problems = alembic_heads(wt / policy.alembic_versions)
            if len(heads) != 1 or problems:
                res.reasons.append(f"migraciones de Alembic inconsistentes ({len(heads)} heads)")
                res.detail.append(f"alembic heads={sorted(heads)} problemas={problems[:10]}")
            else:
                res.passed.append("Alembic con un solo head")
            res.migration = "alembic"
        if any(f.startswith(p) for p in policy.manual_migrations for f in res.changed_files):
            res.migration = res.migration or "supabase"
        if res.sensitive:  # infraestructura de arranque/deploy: el deploy queda en manual, como una migración
            res.migration = res.migration or "infra"
            res.detail.append("toca infraestructura de deploy: " + ", ".join(res.sensitive[:10]))

        if res.reasons or not lane.test_cmd:
            return  # nunca se ejecuta test_cmd sobre un diff que ya falló
        try:
            t = self._exec(render_test_cmd(lane), cwd=w, timeout=1800, shell=True, env=safe_env())
            res.test_exit = t.returncode
            if t.returncode != 0:
                res.reasons.append("los tests del carril fallan con el PR fusionado")
                # A la tarjeta solo el exit: la salida puede llevar una línea con un secreto. Completa, al log.
                res.detail.append(f"test_cmd exit {t.returncode} (salida en el log del runner)")
                log.warning("integrador test_cmd exit %s: %s", t.returncode, (t.stdout + t.stderr).strip()[-1500:])
            else:
                res.passed.append("tests OK")
        except subprocess.TimeoutExpired:
            res.reasons.append("los tests del carril tardaron más de 30 min")

    def _remove_worktree(self, repo: str, wt: Path) -> None:
        """Solo worktrees propios del integrador (int-*/intdep-*), nunca lane-<id> ni otros."""
        if not wt.name.startswith(("int-", "intdep-")) or Path(self.settings.worktree_root) not in wt.parents:
            raise PermissionError(f"worktree ajeno: {wt}")
        if wt.exists():
            self._git(repo, "worktree", "remove", "--force", str(wt))
        self._git(repo, "worktree", "prune")

    # --- avisos ---------------------------------------------------------------------------------------

    def _links(self, lane, tid: str, number: int, url: str) -> list[tuple[str, str]]:
        base = self.links(lane, tid, branch=False) if callable(self.links) else []
        return [*base, (f"PR #{number}", url)]

    def _text(self, state: str, lane, rec: dict, status: str, bullets=None, *, phase: str | None = None,
              override: str | None = None) -> str:
        """Sin tema de Integración: el aviso de siempre. Con él: ficha con cabecera por riesgo (integration.py)."""
        links = self._links(lane, rec["task_id"], rec["pr_number"], rec["pr_url"])
        tree_of = getattr(self, "tree", None)  # deps.TreeReader que pone runner.py; None = sin árbol
        if not self.settings.integration_telegram:
            return render(state, rec["task_id"], rec.get("title"), lane.name, status, links, bullets,
                          body=rec.get("body"), tree=tree_of(lane.board, rec["task_id"]) if tree_of else None)
        phase = phase or phase_for(state, status)
        if override is None and state == "blocked":
            override = FAILED
        # Tras fusionar/desplegar, la nota "fusionar NO despliega…" ya no aporta.
        deploy = None if phase in ("FUSIONADO", "DESPLEGANDO", "DESPLEGADO") else rec.get("deploy_note")
        return render_ficha(risk=risk_of(self.settings.policies.get(lane.name)), phase=phase, override=override,
                            tid=rec["task_id"], title=rec.get("title"), repo=repo_name(lane), base=lane.base,
                            status=status, pr=rec.get("pr_number"), for_oscar=rec.get("for_oscar"),
                            gates=rec.get("gates") or (), risks=rec.get("risks") or (), deploy=deploy,
                            deps=rec.get("deps") or (), links=links,
                            tree=tree_of(lane.board, rec["task_id"], with_waiting=False) if tree_of else ())

    def _rec(self, lane, task: dict, number: int, url: str, result: GateResult | None = None,
             policy: Policy | None = None) -> dict:
        rec = {"kind": "integrate", "task_id": task["id"], "board": lane.board, "lane": lane.name,
               "title": task.get("title") or "", "body": (task.get("body") or "")[:4000], "pr_number": number,
               "pr_url": url, **({"for_oscar": task["for_oscar"]} if task.get("for_oscar") else {})}
        if result is not None:
            policy = policy or self.settings.policies.get(lane.name) or Policy(lane=lane.name)
            rec.update({"head_sha": result.head_sha, "base_sha": result.base_sha, "migration": result.migration,
                        "changed_files": result.changed_files[:50],
                        # campos de la ficha: también sirven para redibujarla tras pulsar un botón
                        "gates": [f"✔ {p}" for p in result.passed] + [f"⛔ {r}" for r in result.reasons],
                        "risks": self._risk_lines(result), "deploy_note": self._deploy_note(policy, result)})
        return rec

    def _publish(self, lane, task: dict, text: str, markup: dict | None = None, *, link: str | None = None) -> None:
        """Con tema de Integración: aviso allí (nunca en el origen de la tarea) + copia espejo en el DM de Oscar; en el
        tema del carril queda solo `link` (línea corta con enlace). Sin él: el tema del carril, como siempre."""
        if not self._notices:
            return
        target = self.settings.integration_telegram
        try:
            if target:
                owner = str(getattr(self.desk, "owner_id", "") or "") if self.desk else ""
                prefix = link or f"🚦 {task['id']} · {truncate(task.get('title'), 40)}"
                self._notices.publish(task["id"], text, None, target, alert=True, reply_markup=markup,
                                      channel=CHANNEL, leave_behind=lambda sent: link_line(prefix, sent),
                                      **({"mirror_to": owner} if owner else {}))
            else:
                self._notices.publish(task["id"], text, telegram_target(task.get("body")), lane.telegram,
                                      alert=True, reply_markup=markup)
        except Exception as exc:
            log.warning("%s: aviso del integrador falló: %s", task["id"], exc)

    def _publish_failed(self, lane, task: dict, number: int, url: str, result: GateResult) -> None:
        rec = self._rec(lane, task, number, url, result)
        self._comment(lane, task["id"], f"INTEGRADOR: gates fallidos · PR #{number} @ {result.head_sha[:12]} sobre "
                      f"{lane.base} @ {result.base_sha[:12]}\n" + "\n".join(f"- {d}" for d in result.detail or result.reasons))
        if self.settings.integration_telegram:  # la ficha ya lista cada gate con ✔/⛔
            status = status_line(f"⛔ no se puede integrar PR #{number}", "detalle en la tarjeta")
        else:
            status = status_line(f"⛔ no se puede integrar PR #{number}", "; ".join(result.reasons))
        self._publish(lane, task, self._text("blocked", lane, rec, status, [f"✔ {p}" for p in result.passed],
                                             phase="NO SE PUEDE INTEGRAR"),
                      link=f"⛔ PR #{number} no se puede integrar")

    def _publish_waiting(self, lane, task: dict, number: int, url: str, waiting: list[dict]) -> None:
        self._comment(lane, task["id"], f"INTEGRADOR: PR #{number} en espera · " + waiting_line(waiting, limit=10))
        rec = {**self._rec(lane, task, number, url),
               "deps": [f"{w['id']} · {truncate(w['title'], 40)} · {w['reason']}" for w in waiting[:5]]}
        self._publish(lane, task, self._text("blocked", lane, rec, status_line(
            f"⏸ PR #{number} aprobado, pero depende de otra tarea", waiting_line(waiting)),
            [f"• {w['id']} · {w['title'][:60]} · {w['reason']}" for w in waiting[:5]]
            + ["se ofrecerá Fusionar en cuanto estén integradas"], phase="EN ESPERA", override=WAITING),
            link=f"⏸ PR #{number} en espera de dependencias")

    def _spec(self, policy: Policy, migration: str | None) -> list[list[dict]] | None:
        if policy.deploy == DEPLOY_ON_MERGE:
            # MigraTeam: el merge ES el deploy. Siempre botón explícito; con migración pendiente, ninguno.
            return None if migration else [[{"text": policy.merge_label, "action": INT_MERGE_DEPLOY}]]
        return [[{"text": "🔀 Fusionar", "action": INT_MERGE}]]

    @staticmethod
    def _risk_lines(result: GateResult) -> list[str]:
        if result.migration == "alembic":
            return ["⚠️ requiere migración de Alembic (no se aplica sola desde aquí)"]
        if result.migration == "infra":
            return ["⚠️ toca infraestructura de deploy: " + ", ".join(result.sensitive[:3]) + " (revísalo tú)"]
        if result.migration:
            return ["⚠️ requiere migración manual (supabase/migrations)"]
        return []

    @staticmethod
    def _deploy_note(policy: Policy, result: GateResult) -> str | None:
        if policy.deploy == DEPLOY_ON_MERGE:
            return NEEDS_MIGRATION if result.migration else (policy.merge_warning or MIGRATEAM_WARNING)
        if policy.deploy == DEPLOY_RAILWAY_UP:
            return ("fusionar NO despliega; el deploy es otro botón" if not result.migration else
                    "fusionar NO despliega; " + NEEDS_MIGRATION)
        if policy.apply:
            return "fusionar NO despliega; después, 🔁 Aplicar hace el reinicio ordenado del runner"
        return None

    def _bullets(self, policy: Policy, result: GateResult) -> list[str]:
        note = self._deploy_note(policy, result)
        return [f"✔ {p}" for p in result.passed] + self._risk_lines(result) + ([note] if note else [])

    def _offer(self, lane, policy: Policy, task: dict, number: int, url: str, result: GateResult) -> None:
        rec = self._rec(lane, task, number, url, result, policy)
        spec = self._spec(policy, result.migration)
        markup = None
        if spec and self.desk:
            markup = self.desk.store.issue(rec, spec)[1]
        hint = "pulsa para integrar" if markup else ("sin botón: " + NEEDS_MIGRATION if spec is None
                                                    else "sin bot de carriles: fusiona a mano")
        self._comment(lane, task["id"], f"INTEGRADOR: gates OK · PR #{number} @ {result.head_sha[:12]} sobre "
                      f"{lane.base} @ {result.base_sha[:12]} · " + "; ".join(result.passed) +
                      (f" · requiere migración ({result.migration})" if result.migration else "") +
                      " · esperando a Oscar")
        self._publish(lane, task, self._text("done", lane, rec, status_line(
            f"🚦 Listo para integrar PR #{number}", hint), self._bullets(policy, result),
            phase="LISTO PARA INTEGRAR"), markup, link=f"🚦 PR #{number} listo para integrar")

    def _report(self, tid: str, number: int, result: GateResult, policy: Policy) -> None:
        self._out(f"[dry-run] {tid} · PR #{number} @ {result.head_sha[:12]} sobre {result.base_sha[:12]}: "
                  + ("GATES OK" if result.ok else "GATES FALLIDOS"))
        for p in result.passed:
            self._out(f"  ✔ {p}")
        for r in result.reasons:
            self._out(f"  ⛔ {r}")
        for d in result.detail:
            self._out(f"  · {d}")
        if result.ok:
            spec = self._spec(policy, result.migration)
            self._out("  botones: " + (" | ".join(b["text"] for row in spec for b in row) if spec else "ninguno")
                      + " · " + " · ".join(self._bullets(policy, result)))

    # --- botones (llamado por DecisionDesk tras comprobar que es Oscar y consumir el token) ------------

    def on_button(self, action: str, rec: dict, where: dict, desk) -> bool:
        """True = acción terminada (bien o mal, con su aviso). False = no se hizo nada: vuelven los botones."""
        with self._inflight_lock:  # busy(): un reinicio ordenado no corta un merge ni la verificación del deploy
            self._inflight += 1
        try:
            return self._on_button(action, rec, where, desk)
        finally:
            with self._inflight_lock:
                self._inflight -= 1
            self.refresh_summary()  # fusionado/desplegado: el fijado no espera a la próxima pasada

    def _on_button(self, action: str, rec: dict, where: dict, desk) -> bool:
        if not self.settings.enabled or rec.get("kind") != "integrate":
            return False
        policy = self.settings.policies.get(rec.get("lane"))
        lane = self.lanes.get(rec.get("lane"))
        if not policy or not lane:
            return False
        if action == INT_MERGE and policy.deploy != DEPLOY_ON_MERGE:
            return self._merge(lane, policy, rec, where, desk)
        if action == INT_MERGE_DEPLOY and policy.deploy == DEPLOY_ON_MERGE and not rec.get("migration"):
            return self._merge(lane, policy, rec, where, desk)
        if action == INT_DEPLOY and policy.deploy == DEPLOY_RAILWAY_UP and not rec.get("migration") \
                and rec.get("merge_sha"):
            return self._deploy_railway(lane, policy, rec, where, desk)
        if action == INT_MIGRATION_APPLIED:
            ok, _ = self.mark_migration_applied(rec.get("task_id") or "", OSCAR_AUTHOR, desk=desk, where=where)
            return ok
        if action == INT_APPLY and policy.deploy == DEPLOY_NONE and policy.apply and rec.get("merge_sha"):
            return self._apply(lane, policy, rec, where, desk)
        log.warning("%s: botón %s no válido para %s", rec.get("task_id"), action, policy.lane)
        return False

    def mark_migration_applied(self, tid: str, author: str, *, desk=None, where: dict | None = None) -> tuple[bool, str]:
        """Oscar (botón) o el coordinador (`lanes.py integrator migration-applied`) confirman que la migración de una
        ficha fusionada YA está aplicada: `migration_applied` en el estado, comentario MIGRACION-APLICADA, ficha sin el
        aviso de migración (con [🚀 Desplegar] si el carril despliega con botón) y deja de bloquear el deploy de lo
        último de la base. Nunca aplica nada: solo registra lo que ya se hizo fuera. (ok, mensaje)."""
        if not TID_RE.fullmatch(tid or ""):
            return False, f"id de tarea no válido: {tid!r}"
        st = self._state(tid)
        lane = self.lanes.get(st.get("lane") or "")
        if not st or lane is None:
            return False, f"{tid}: sin estado del integrador (¿no la fusionó el integrador?)"
        if st.get("status") != "merged":
            return False, f"{tid}: no está fusionada pendiente de deploy (estado {st.get('status') or '?'})"
        if st.get("migration") not in MIGRATION_KINDS:
            return False, f"{tid}: no tiene migración pendiente" + (
                " (toca infraestructura: eso no se marca como aplicado)" if st.get("migration") else "")
        if st.get("migration_applied"):
            return True, f"{tid}: la migración ya constaba como aplicada ({st['migration_applied'].get('by')})"
        stamp = time.strftime("%Y-%m-%d %H:%M")
        self._save(tid, migration_applied={"by": author, "at": self._now()})
        if not self.dry_run and not self.hermes_for(lane.board).comment(
                tid, f"{MIGRATION_APPLIED_PREFIX} {stamp} · {st['migration']} · PR #{st.get('pr', '?')} · "
                     f"marcada por {author}", author=author):
            log.warning("%s: no se pudo comentar %s en la tarjeta", tid, MIGRATION_APPLIED_PREFIX)
        sha = st.get("merge_sha") or ""
        rec = {"kind": "integrate", "task_id": tid, "board": lane.board, "lane": lane.name,
               "title": st.get("title") or "", "pr_number": st.get("pr"), "pr_url": st.get("pr_url") or "",
               "merge_sha": sha, "migration": None}
        policy = self.settings.policies.get(lane.name)
        if desk is not None:
            for msg in (self._notices.store.all_messages(tid) if self._notices else ()):
                if msg.get("token"):
                    desk.store.consume(msg["token"])  # el ✅ de las demás copias ya no vale
            markup = None
            if policy and policy.deploy == DEPLOY_RAILWAY_UP and sha:
                markup = desk.store.issue(rec, [[{"text": "🚀 Desplegar", "action": INT_DEPLOY}]])[1]
            self._edit(desk, lane, rec, where, "done", status_line(
                f"✅ fusionado · {sha[:7] or '?'}", "migración aplicada", "sin desplegar" if markup else None),
                markup=markup)
        self.refresh_summary()
        return True, f"{tid}: migración marcada como aplicada por {author}"

    def _edit(self, desk, lane, rec: dict, where: dict, state: str, status: str, bullets=None,
              markup: dict | None = None) -> None:
        """Edita el mensaje pulsado y TODAS las copias del aviso (tema de Integración, DM): siempre sincronizadas."""
        text = self._text(state, lane, rec, status, bullets)
        extra = {"reply_markup": markup} if markup else {}
        targets = [where] if where and where.get("message_id") else []
        if self._notices:
            targets += self._notices.store.all_messages(rec.get("task_id") or "")
        seen = set()
        for msg in targets:
            key = (str(msg.get("chat_id")), str(msg.get("message_id")))
            if key in seen:
                continue
            seen.add(key)
            try:
                desk.notifier.edit(msg["chat_id"], msg["message_id"], text, **extra)
            except Exception as exc:
                log.warning("%s: no se pudo editar el aviso %s: %s", rec.get("task_id"), msg.get("message_id"), exc)

    def _merge(self, lane, policy: Policy, rec: dict, where: dict, desk) -> bool:
        tid, number = rec["task_id"], int(rec["pr_number"])
        slug = self.links.repo_slug(lane) if self.links else None
        pr = self._pr_view(slug, number) if slug else None
        problem = self._pr_problem(lane, tid, pr)
        if problem or pr["headRefOid"] != rec["head_sha"]:
            why = problem or "el PR tiene commits nuevos desde los gates"
            self._save(tid, status="stale")  # la próxima pasada repite los gates
            self._edit(desk, lane, rec, where, "blocked", status_line(f"⛔ no fusionado PR #{number}", why,
                                                                      "se revisará de nuevo"))
            return True
        waiting = pending_parents(self.hermes_for(lane.board), tid)
        if waiting:  # un padre se reabrió o dejó de estar integrado desde que se ofreció el botón
            self._save(tid, status="waiting_deps", waiting=",".join(w["id"] for w in waiting))
            self._edit(desk, lane, rec, where, "blocked", status_line(f"⛔ no fusionado PR #{number}",
                                                                      waiting_line(waiting)))
            return True
        self._edit(desk, lane, rec, where, "running", status_line(f"🔀 fusionando PR #{number}…"))
        base_now = self._remote_sha(lane, lane.base)
        if base_now != rec["base_sha"]:
            # La base se movió desde los gates: lo probado ya no es lo que se fusionaría.
            result = self.run_gates(lane, policy, tid, number, rec["head_sha"])
            if not result.ok:
                self._save(tid, status="failed", head_sha=result.head_sha, base_sha=result.base_sha)
                self._comment(lane, tid, f"INTEGRADOR: gates fallidos al fusionar (base movida) · PR #{number}\n"
                              + "\n".join(f"- {d}" for d in result.detail or result.reasons))
                self._edit(desk, lane, rec, where, "blocked", status_line(
                    f"⛔ no fusionado PR #{number}", "; ".join(result.reasons)))
                return True
            if result.migration != rec.get("migration") and policy.deploy == DEPLOY_ON_MERGE:
                self._save(tid, status="stale")
                self._edit(desk, lane, rec, where, "blocked", status_line(
                    f"⛔ no fusionado PR #{number}", "cambió la necesidad de migración", "se revisará de nuevo"))
                return True
            rec = {**rec, "base_sha": result.base_sha, "migration": result.migration}
        if self._remote_sha(lane, lane.base) != rec["base_sha"]:  # se movió otra vez durante los gates repetidos
            self._save(tid, status="stale")
            self._edit(desk, lane, rec, where, "blocked", status_line(
                f"⛔ no fusionado PR #{number}", f"{lane.base} cambió durante la comprobación", "se revisará de nuevo"))
            return True
        self._save(tid, status="merging", head_sha=rec["head_sha"])
        args = ["pr", "merge", str(number), "--repo", slug, "--squash", "--match-head-commit", rec["head_sha"]]
        if pr["headRefName"] == f"lane/{tid}" and not pr.get("isCrossRepository"):
            args.append("--delete-branch")  # rama del runner, del mismo repo, y gh corre fuera de cualquier clon
        cp = self._gh(*args, timeout=180)
        if cp.returncode != 0:
            log.warning("%s: gh pr merge falló: %s", tid, (cp.stderr or "").strip()[:300])
            after = self._pr_view(slug, number) or {}
            outside = True
            if after.get("state") != "MERGED":
                self._save(tid, status="offered")
                self._edit(desk, lane, rec, where, "blocked", status_line(
                    f"⛔ GitHub no aceptó la fusión del PR #{number}", "detalle en el log", "puedes reintentar"))
                return False
        else:
            outside = False
        merged = self._pr_view(slug, number) or {}
        sha = ((merged.get("mergeCommit") or {}).get("oid") or "").strip()
        self._comment(lane, tid, f"{INTEGRATED_PREFIX} {sha or '?'} · PR {rec['pr_url']}"
                      + (" (fusionado fuera del integrador)" if outside else ""))
        self._save(tid, status="merged", merge_sha=sha)
        rec = {**rec, "merge_sha": sha}
        short = sha[:7] or "?"
        if policy.deploy == DEPLOY_ON_MERGE:
            self._edit(desk, lane, rec, where, "running", status_line(
                f"🚀 fusionado en {lane.base} · {short}", "Railway despliega solo; comprobando…"))
            return self._verify_on_merge(lane, policy, rec, where, desk)
        if policy.deploy == DEPLOY_RAILWAY_UP:
            if rec.get("migration"):
                # Oscar aplica la migración a mano y lo dice con [✅ Migración aplicada]: desbloquea el 🚀.
                markup = (desk.store.issue(rec, [[dict(MIGRATION_BUTTON)]])[1]
                          if rec["migration"] in MIGRATION_KINDS else None)
                self._edit(desk, lane, rec, where, "done", status_line(f"✅ fusionado · {short}", NEEDS_MIGRATION),
                           markup=markup)
                return True
            markup = desk.store.issue(rec, [[{"text": "🚀 Desplegar", "action": INT_DEPLOY}]])[1]
            self._edit(desk, lane, rec, where, "done", status_line(f"✅ fusionado · {short}", "sin desplegar"),
                       markup=markup)
            return True
        if policy.apply:
            argv = APPLY_ARGV[policy.apply]
            if self._apply_probe(policy.apply):
                markup = desk.store.issue(rec, [[{"text": APPLY_LABEL, "action": INT_APPLY}]])[1]
                self._edit(desk, lane, rec, where, "done", status_line(
                    f"✅ fusionado en {lane.base} · {short}", "falta aplicar (reinicio ordenado)"), markup=markup)
            else:  # el subcomando aún no existe en el checkout vivo (feat/plan-d-runtime sin integrar)
                self._edit(desk, lane, rec, where, "done", status_line(
                    f"✅ fusionado en {lane.base} · {short}",
                    f"para aplicarlo reinicia el runner (`{' '.join(argv[2:])}` aún no existe)"))
            return True
        self._edit(desk, lane, rec, where, "done", status_line(f"✅ fusionado · {short}"))
        return True

    def _apply(self, lane, policy: Policy, rec: dict, where: dict, desk) -> bool:
        """[🔁 Aplicar] (claude-hub): reinicio ordenado del runner en el checkout vivo. Se lanza SIN esperar: el
        comando termina reiniciando este mismo proceso. Nunca toca git aquí (el avance a main es del restart)."""
        tid = rec["task_id"]
        st = self._state(tid)
        short = (rec.get("merge_sha") or "")[:7] or "?"
        if st.get("status") == "applied":
            self._edit(desk, lane, rec, where, "done", status_line(f"🔁 ya aplicado · {short}"))
            return True
        if st.get("merge_sha") and st.get("merge_sha") != rec.get("merge_sha"):
            log.warning("%s: aplicar con un merge distinto del registrado", tid)
            return False
        if not self._apply_probe(policy.apply):
            self._edit(desk, lane, rec, where, "done", status_line(
                f"✅ fusionado · {short}", "no se puede aplicar desde aquí: falta `lanes.py restart --drain`"))
            return True
        argv = APPLY_ARGV[policy.apply]
        # Todo el registro ANTES de lanzar: el reinicio puede parar este proceso en cuanto arranca, y lo que quede
        # después (estado, comentario, aviso, resumen fijado) no llegaría a hacerse.
        self._save(tid, status="applied")
        self._comment(lane, tid, f"{APPLIED_PREFIX} {rec.get('merge_sha') or '?'} · reinicio ordenado lanzado "
                      f"(`{' '.join(argv)}`)")
        self._edit(desk, lane, rec, where, "done", status_line(
            f"🔁 reinicio ordenado lanzado · {short}", "el runner se reinicia al terminar lo que tenga en curso"))
        self.refresh_summary()
        try:
            self._launch(argv, str(ROOT))
        except Exception as exc:
            log.warning("%s: no se pudo lanzar el reinicio ordenado: %s", tid, exc)
            self._save(tid, status="merged")
            self._comment(lane, tid, f"{APPLIED_PREFIX}: FALLÓ el lanzamiento del reinicio ordenado; sigue sin aplicar")
            self._edit(desk, lane, rec, where, "blocked", status_line(
                f"⛔ no se pudo aplicar · {short}", "detalle en el log", "puedes reintentar"))
            return False
        return True

    def _remote_sha(self, lane, branch: str) -> str | None:
        cp = self._git(lane.repo, "ls-remote", lane.remote, f"refs/heads/{branch}", timeout=120)
        parts = (cp.stdout or "").split() if cp.returncode == 0 else []
        return parts[0] if parts else None

    # --- deploy ---------------------------------------------------------------------------------------

    def _deployments(self, policy: Policy) -> list[dict] | None:
        cp = self._railway(policy, "deployment", "list", "--json", "--limit", "20", *self._ids(policy))
        if cp.returncode != 0:
            return None
        try:
            data = json.loads(cp.stdout or "[]")
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, list) else None

    def _deploy_railway(self, lane, policy: Policy, rec: dict, where: dict, desk) -> bool:
        if not self._deploying.acquire(blocking=False):
            self._edit(desk, lane, rec, where, "blocked", "⛔ ya hay un deploy en curso; vuelve a pulsar cuando acabe")
            return False
        try:
            return self._deploy_railway_locked(lane, policy, rec, where, desk)
        finally:
            self._deploying.release()

    def _deploy_railway_locked(self, lane, policy: Policy, rec: dict, where: dict, desk) -> bool:
        tid, sha = rec["task_id"], rec["merge_sha"]
        if not (policy.railway_dir and policy.railway_project and policy.railway_service and policy.railway_environment):
            self._edit(desk, lane, rec, where, "blocked", "⛔ deploy no configurado (ids de Railway en lanes.yaml)")
            return True
        st = self._railway(policy, "status", "--json")
        try:
            linked = json.loads(st.stdout or "{}").get("id") if st.returncode == 0 else None
        except json.JSONDecodeError:
            linked = None
        if linked != policy.railway_project:
            self._edit(desk, lane, rec, where, "blocked",
                       "⛔ no se despliega: la CLI de Railway no está enlazada al proyecto esperado")
            return False
        with self._lock:
            self._git(lane.repo, "fetch", lane.remote, f"+refs/heads/{lane.base}:refs/remotes/{lane.remote}/{lane.base}")
            # Se despliega la PUNTA de la base, no el commit de esta ficha (28-09: 🚀 de a50b290 dejó fuera la
            # fusión posterior 6f46b0d). Candado: la punta contiene esta fusión y nada pendiente de migración/infra.
            tip = (self._git(lane.repo, "rev-parse", f"{lane.remote}/{lane.base}").stdout or "").strip()
            problem = self._tip_problem(lane, policy, tid, sha, tip)
            if problem:
                self._edit(desk, lane, rec, where, "blocked", status_line("⛔ no se despliega", problem,
                                                                          "despliega a mano"))
                return True
            extra = self._count(lane, f"{sha}..{tip}") if tip != sha else 0
            before = self._deployments(policy)
            if before is None:
                self._edit(desk, lane, rec, where, "blocked", "⛔ no se despliega: Railway no responde")
                return False
            self._edit(desk, lane, rec, where, "running", status_line(
                f"🚀 desplegando {tip[:7]}…", f"lo último de {lane.base}, {extra} commit(s) después de esta fusión"
                if extra else None))
            wt = Path(self.settings.worktree_root) / f"intdep-{tid}"
            self._remove_worktree(lane.repo, wt)
            add = self._git(lane.repo, "-c", f"core.hooksPath={self._nohooks()}", "worktree", "add", "--detach",
                            str(wt), tip)
            if add.returncode != 0:
                self._edit(desk, lane, rec, where, "blocked", "⛔ no se desplegó: no se pudo preparar la copia")
                return False
            try:
                # Worktree limpio del commit fusionado: nada sin commitear del checkout raíz llega a producción.
                # cwd = el worktree + ids explícitos (sin -y, `up` nunca crea un proyecto nuevo). `railway up <ruta>`
                # desde otro cwd falla con "prefix not found" (CLI 5.59, visto en el deploy de W4 el 28-09).
                up = self._exec([self.railway_exe, "up", "--detach", *self._ids(policy)], cwd=str(wt), timeout=900)
            finally:
                self._remove_worktree(lane.repo, wt)
        if up.returncode != 0:
            log.warning("%s: railway up falló: %s", tid, (up.stderr or up.stdout or "").strip()[-300:])
            self._edit(desk, lane, rec, where, "blocked", "⛔ deploy falló al subir (detalle en el log)")
            self._comment(lane, tid, f"DEPLOY-FALLIDO {tip} · railway up exit {up.returncode}")
            return True
        dep_id = self._new_deployment_id(policy, up.stdout + up.stderr, {d.get("id") for d in before})
        if not dep_id:
            self._edit(desk, lane, rec, where, "blocked",
                       "⛔ deploy lanzado pero sin identificar; compruébalo en Railway")
            return True
        status = self._poll_deployment(policy, dep_id)
        if status in RAILWAY_OK:
            health = self._health(policy)
            self._comment(lane, tid, f"DESPLEGADO {tip} · railway deployment {dep_id} SUCCESS"
                          + (f" · health {health}" if health else "")
                          + (f" · incluye la fusión {sha[:12]} de esta tarea" if tip != sha else ""))
            self._save(tid, status="deployed", deployment=dep_id, deployed_sha=tip)
            also = self._mark_included(lane, tid, tip, dep_id, desk)
            self._edit(desk, lane, rec, where, "done", status_line(
                f"🚀 desplegado · {tip[:7]}", f"health {health}" if health else None,
                ("también PR " + ", ".join(f"#{n}" for n in also)) if also else None))
        else:
            why = "sin terminar en 15 min" if status is None else status
            self._comment(lane, tid, f"DEPLOY-FALLIDO {tip} · railway deployment {dep_id} {why}")
            self._edit(desk, lane, rec, where, "blocked", status_line("⛔ deploy falló", why, "revisa Railway"))
        return True

    def busy(self) -> list[str]:
        """Lo que un reinicio ordenado (drain) debe esperar fuera del pool de workers: deploy o gates en marcha."""
        out = []
        if self._inflight:
            out.append("acción de Oscar en marcha (fusión/deploy)")
        if self._deploying.locked():
            out.append("deploy en curso")
        if self._lock.locked():
            out.append("gates/fusión del integrador")
        return out

    def _is_ancestor(self, lane, old: str, new: str) -> bool:
        return self._git(lane.repo, "merge-base", "--is-ancestor", old, new).returncode == 0

    def _count(self, lane, rng: str) -> int:
        try:
            return int((self._git(lane.repo, "rev-list", "--count", rng).stdout or "0").strip() or 0)
        except ValueError:
            return 0

    def _merged_states(self, lane, exclude: str) -> list[tuple[str, dict]]:
        """Fichas de este carril fusionadas y sin desplegar (estado local del integrador), salvo `exclude`."""
        out = []
        for path in sorted(self.state_dir.glob("t_*.json")):
            tid = path.stem
            if tid == exclude or not TID_RE.fullmatch(tid):
                continue
            st = self._state(tid)
            if st.get("lane") == lane.name and st.get("status") == "merged" \
                    and re.fullmatch(r"[0-9a-f]{40}", st.get("merge_sha") or ""):
                out.append((tid, st))
        return out

    def _tip_problem(self, lane, policy: Policy, tid: str, sha: str, tip: str) -> str | None:
        """Por qué NO desplegar la punta de la base (o None). Antes el candado era "punta == esta fusión"; ahora se
        despliega lo último, así que se comprueba que no se cuele nada que tampoco se habría desplegado solo."""
        if not re.fullmatch(r"[0-9a-f]{40}", tip or ""):
            return f"no se pudo leer {lane.base}"
        if tip != sha and not self._is_ancestor(lane, sha, tip):
            return f"{lane.base} ya no contiene esta fusión"
        for other, st in self._merged_states(lane, tid):
            if pending_migration(st) and self._is_ancestor(lane, st["merge_sha"], tip):
                return f"{lane.base} incluye el PR #{st.get('pr', '?')} con migración o infraestructura pendiente"
        if tip != sha:
            changed = (self._git(lane.repo, "diff", "--no-renames", "--name-only", sha, tip).stdout or "").split()
            risky = [f for f in changed if any(f.startswith(p) for p in policy.manual_migrations)
                     or any(f.startswith(p) or Path(f).name == p for p in policy.sensitive_paths)]
            if risky:
                return (f"después de esta fusión {lane.base} trae migración o infraestructura: "
                        + ", ".join(risky[:3]))
        return None

    def _mark_included(self, lane, tid: str, tip: str, dep_id: str, desk) -> list:
        """Tras un deploy correcto de `tip`: toda ficha fusionada cuyo merge_sha está en lo desplegado queda como
        desplegada (comentario, estado, avisos editados y su 🚀 Desplegar retirado). Devuelve sus números de PR."""
        done = []
        for other, st in self._merged_states(lane, tid):
            if pending_migration(st) or not self._is_ancestor(lane, st["merge_sha"], tip):
                continue
            self._comment(lane, other, f"DESPLEGADO {tip} · railway deployment {dep_id} SUCCESS · incluido en el "
                                       f"deploy pedido desde {tid} (fusión {st['merge_sha'][:12]})")
            self._save(other, status="deployed", deployment=dep_id, deployed_sha=tip)
            rec = {"kind": "integrate", "task_id": other, "board": lane.board, "lane": lane.name,
                   "title": st.get("title") or "", "pr_number": st.get("pr"), "pr_url": st.get("pr_url") or "",
                   "merge_sha": st["merge_sha"]}
            for msg in (self._notices.store.all_messages(other) if self._notices else ()):
                if msg.get("token") and desk is not None:
                    desk.store.consume(msg["token"])  # su 🚀 Desplegar ya no tiene sentido
            self._edit(desk, lane, rec, None, "done", status_line(f"🚀 desplegado · {tip[:7]}",
                                                                   f"con el deploy de {tid}"))
            done.append(st.get("pr") or other)
        return done

    def _new_deployment_id(self, policy: Policy, output: str, before: set) -> str | None:
        """Id del deployment que acaba de crear `up`: el de la URL de logs (?id=) o el único nuevo de la lista.
        Nunca "el primero de la lista": sería el SUCCESS de ayer (falso positivo)."""
        m = re.search(r"[?&]id=(" + UUID_RE.pattern + ")", output or "")
        if m:
            return m.group(1)
        for _ in range(6):
            after = self._deployments(policy) or []
            new = [d.get("id") for d in after if d.get("id") and d.get("id") not in before]
            if len(new) == 1:
                return new[0]
            if len(new) > 1:
                return None
            self._sleep(self.settings.poll_seconds)
        return None

    def _poll_deployment(self, policy: Policy, dep_id: str) -> str | None:
        deadline = self._clock() + self.settings.deploy_timeout_seconds
        while self._clock() < deadline:
            for d in self._deployments(policy) or []:
                if d.get("id") == dep_id:
                    status = str(d.get("status") or "").upper()
                    if status in RAILWAY_OK or status in RAILWAY_BAD:
                        return status
            self._sleep(self.settings.poll_seconds)
        return None

    def _health(self, policy: Policy) -> str | None:
        if not policy.health_url:
            return None
        try:
            code, _ = self._http_get(policy.health_url)
        except Exception:
            return "no responde"
        return "OK" if code == 200 else f"HTTP {code}"

    def _verify_on_merge(self, lane, policy: Policy, rec: dict, where: dict, desk) -> bool:
        """MigraTeam: su CLI de Railway no está enlazada (y enlazarla es config): se sondea GET /health hasta que
        `commit_sha` sea el del merge. Un 200 con otro sha es el contenedor anterior, no el deploy nuevo."""
        tid, sha = rec["task_id"], rec.get("merge_sha") or ""
        if not policy.health_url or not policy.health_commit_key or not sha:
            self._edit(desk, lane, rec, where, "done", status_line(
                f"🚀 fusionado · {sha[:7]}", "Railway despliega solo", "compruébalo en Railway y GET /health"))
            return True
        deadline = self._clock() + self.settings.deploy_timeout_seconds
        last = "sin respuesta"
        while self._clock() < deadline:
            try:
                code, body = self._http_get(policy.health_url)
                data = json.loads(body or "{}") if code == 200 else {}
                if str(data.get(policy.health_commit_key) or "").lower() == sha.lower() \
                        and data.get("status") == "healthy":
                    self._comment(lane, tid, f"DESPLEGADO {sha} · /health healthy con commit_sha del merge")
                    self._save(tid, status="deployed")
                    self._edit(desk, lane, rec, where, "done", status_line(f"🚀 desplegado · {sha[:7]}", "health OK"))
                    return True
                last = f"HTTP {code}" if code != 200 else f"sirve {str(data.get(policy.health_commit_key))[:7]}"
            except Exception as exc:
                last = type(exc).__name__
            self._sleep(self.settings.poll_seconds)
        self._comment(lane, tid, f"DEPLOY-SIN-CONFIRMAR {sha} · 15 min sin /health con el commit nuevo ({last})")
        self._edit(desk, lane, rec, where, "blocked", status_line(
            "⛔ deploy sin confirmar en 15 min", f"/health {last}", "revisa Railway y Vercel"))
        return True


def safe_env() -> dict[str, str]:
    """Entorno mínimo para el test_cmd: sin tokens del proceso y sin que cmd.exe busque `py`/`git` en el
    directorio actual (un py.bat del PR se ejecutaría en lugar del real)."""
    env = {k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV_KEYS}
    env["NoDefaultCurrentDirectoryInExePath"] = "1"
    return env


def apply_available(kind: str, lanes_py: Path | None = None) -> bool:
    """¿El checkout vivo ya tiene el subcomando de [🔁 Aplicar]? Lectura ESTÁTICA de lanes.py: ejecutar
    `lanes.py restart --help` para comprobarlo podría reiniciar de verdad si el parser no reconoce --help."""
    if kind != APPLY_RESTART_DRAIN:
        return False
    try:
        src = (lanes_py or ROOT / "lanes.py").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"""["']restart["']""", src)) and "--drain" in src


CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def launch_detached(argv: list[str], cwd: str) -> None:
    """Lanza y NO espera (el comando reinicia el proceso que lo lanza). Sin ventana, stdin/stdout cerrados; el
    comando deja su propio log. Entorno heredado: es el mismo runner que se reinicia a sí mismo.
    El runner corre como tarea programada (install-service.ps1), que mete sus procesos en un job: se pide salir del
    job (BREAKAWAY) para que parar la tarea no mate también al reinicio; si el job no lo permite (acceso denegado),
    se lanza sin él. Aun así, `restart --drain` no debe depender de sobrevivir a que maten al runner."""
    flags = 0
    if sys.platform == "win32":
        flags = (getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
                 | getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
    kw = dict(cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if not flags:
        subprocess.Popen(argv, **kw)
        return
    try:
        subprocess.Popen(argv, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError as exc:  # el job del Programador de tareas no permite salir de él
        log.info("reinicio sin BREAKAWAY_FROM_JOB (%s)", exc)
        subprocess.Popen(argv, creationflags=flags, **kw)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):  # un 30x no puede llevar el health a otro host
        return None


def _http_get(url: str, timeout: float = 15) -> tuple[int, str]:
    if not url.startswith("https://"):
        raise ValueError("health_url debe ser https")
    req = urllib.request.Request(url, headers={"User-Agent": "agent-lanes-integrator"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
            return resp.status, resp.read(20000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def build_integrator(env: dict, lanes: dict, **kw) -> Integrator | None:
    """Integrator del runner, o None si INTEGRATOR_ENABLED no está activo."""
    settings = load_integrator_settings(env=env)
    if not settings.enabled:
        return None
    return Integrator(settings, lanes, **kw)


COORDINATOR_AUTHOR = "coordinador"


def migration_applied_cli(tid: str, *, integrator: "Integrator | None" = None, out: Callable[[str], None] = print,
                          author: str = COORDINATOR_AUTHOR) -> int:
    """`lanes.py integrator migration-applied <t_id>`: lo mismo que el botón [✅ Migración aplicada], con autor
    `coordinador`. Con el bot de carriles, edita la ficha y ofrece [🚀 Desplegar] (lo atiende el runner-servicio, que
    lee los teclados de .state/callbacks); sin él, solo estado + comentario en la tarjeta."""
    integ = integrator or _cli_integrator()
    ok, msg = integ.mark_migration_applied(tid, author, desk=integ.desk)
    out(msg)
    return 0 if ok else 1


def _cli_integrator() -> "Integrator":
    from .config import load_env, load_lanes, load_telegram_settings
    from .decisions import CALLBACKS_DIR, OWNER_TELEGRAM_ID, CallbackStore, DecisionDesk
    from .hermes import HermesCLI
    from .notices import LinkBuilder
    from .runner import MESSAGES_DIR
    from .telegram import notifier_from_env

    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    env, tg, lanes = load_env(), load_telegram_settings(), load_lanes()
    settings = load_integrator_settings(env=env, lane_filter=False)
    cache: dict[str, HermesCLI] = {}
    hermes_for = lambda board: cache.setdefault(board, HermesCLI(board, author=COORDINATOR_AUTHOR))  # noqa: E731
    links = LinkBuilder(env.get("KANBAN_BASE_URL") or tg["kanban_base_url"])
    notifier, lanes_bot = notifier_from_env(env, tg)
    desk = None
    if lanes_bot and notifier.enabled:
        desk = DecisionDesk(notifier, CallbackStore(CALLBACKS_DIR), lanes=lanes, hermes_for=hermes_for, links=links,
                            owner_id=env.get("OWNER_TELEGRAM_ID") or OWNER_TELEGRAM_ID)
    return Integrator(settings, lanes, hermes_for=hermes_for, links=links, notifier=notifier if desk else None,
                      messages=MessageStore(MESSAGES_DIR), desk=desk)


def main(argv: list[str] | None = None) -> int:
    from .config import load_env, load_lanes, load_telegram_settings
    from .hermes import HermesCLI
    from .notices import LinkBuilder

    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Integrador en seco (el modo real solo lo lanza el runner)")
    ap.add_argument("--dry-run", action="store_true", required=True)
    ap.add_argument("--lane", help="carril (con --pr)")
    ap.add_argument("--pr", type=int, help="gates de este PR sin mirar el kanban (requiere --lane)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    lanes = load_lanes()
    settings = load_integrator_settings(env=load_env())
    cache: dict[str, HermesCLI] = {}
    links = LinkBuilder(load_telegram_settings()["kanban_base_url"])
    integ = Integrator(settings, lanes, hermes_for=lambda b: cache.setdefault(b, HermesCLI(b)), links=links,
                       dry_run=True)
    if args.pr:
        lane, policy = lanes[args.lane], settings.policies.get(args.lane) or Policy(lane=args.lane)
        pr = integ._pr_view(links.repo_slug(lane), args.pr)
        if not pr:
            print("no se pudo leer el PR")
            return 2
        tid = (pr.get("headRefName") or "").removeprefix("lane/") or f"pr{args.pr}"
        problem = integ._pr_problem(lane, tid, pr)
        if problem:
            print(f"aviso: {problem} (se ejecutan los gates igualmente)")
        result = integ.run_gates(lane, policy, re.sub(r"[^\w-]", "_", tid), args.pr, pr["headRefOid"])
        integ._report(tid, args.pr, result, policy)
        return 0 if result.ok else 1
    print(integ.run_pass(force=True) or "sin tarjetas aprobadas pendientes de integrar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
