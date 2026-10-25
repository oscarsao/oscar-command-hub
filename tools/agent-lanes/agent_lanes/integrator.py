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
import hashlib
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
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

if __package__ in (None, ""):  # `py agent_lanes/integrator.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "agent_lanes"

from . import consola  # noqa: E402
from . import drain  # noqa: E402
from . import proc as _proc  # noqa: E402
from .atascos import DEFAULT_APPEND_ONLY, AutoResolver, pr_branch_card_body  # noqa: E402
from .config import ROOT, _chat_thread, _load  # noqa: E402
from .deps import is_integration_comment, pending_parents, waiting_line  # noqa: E402
from .hermes import OSCAR_AUTHOR, REVIEW_AUTHOR, is_approval  # noqa: E402
from .integration import (CHANNEL, FAILED, WAITING, PinnedSummary, link_line, phase_for, render_ficha,  # noqa: E402
                          repo_name, risk_of, summary_state, summary_text)
from .titulos import rebase_title  # noqa: E402
from .notices import MessageStore, TaskNotices, card_url, markup_token, render, status_line, truncate  # noqa: E402
from .telegram import telegram_target  # noqa: E402
from .verify import render_test_cmd  # noqa: E402

log = logging.getLogger("agent_lanes")

GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"
RAILWAY_EXE = str(Path.home() / "AppData/Roaming/npm/node_modules/@railway/cli/bin/railway.exe")
STATE_DIR = ROOT / ".state" / "integrator"

INTEGRATOR_AUTHOR = "lane-integrator"
MERGE_RETRY_WAITS = (10, 30, 60)  # s entre reintentos de `gh pr merge` ante "Base branch was modified"
APPROVED_PREFIX = "APROBADO-OSCAR"
INTEGRATED_PREFIX = "INTEGRADO"
INT_MERGE, INT_DEPLOY, INT_MERGE_DEPLOY = "int_merge", "int_deploy", "int_merge_deploy"
INT_MIGRATION_APPLIED = "int_mig_applied"  # ✅ Migración aplicada (ficha fusionada con migración pendiente)
INT_APPLY = "int_apply"  # claude-hub: [🔁 Aplicar (reinicio ordenado)] tras fusionar
INT_BATCH_MERGE = "int_batch_merge"  # [🔀 Fusionar lote] (política con `batch: daily`, ver batch.py)
INT_BATCH_FIX, INT_BATCH_REBASE, INT_BATCH_SKIP = "int_batch_fix", "int_batch_rebase", "int_batch_skip"  # rama fuera del lote
BATCH_EXCLUDED_ACTIONS = (INT_BATCH_FIX, INT_BATCH_REBASE, INT_BATCH_SKIP)
INT_BATCH_BUILD = "int_batch_build"  # [🔀 Fusionar lote] del aviso diario «N PR esperando lote»: monta el lote ya
INT_ACTIONS = (INT_MERGE, INT_DEPLOY, INT_MERGE_DEPLOY, INT_MIGRATION_APPLIED, INT_APPLY, INT_BATCH_MERGE,
               INT_BATCH_BUILD, *BATCH_EXCLUDED_ACTIONS)
MIGRATION_APPLIED_PREFIX = "MIGRACION-APLICADA"
MIGRATION_KINDS = ("supabase", "alembic")  # "infra" no es una migración: no se da por aplicada con un botón
MIGRATION_BUTTON = {"text": "✅ Migración aplicada", "action": INT_MIGRATION_APPLIED}


def pending_migration(st: dict) -> str | None:
    """Migración (o infraestructura) que aún bloquea el deploy de una ficha: la de su estado salvo que Oscar o el
    coordinador la hayan marcado como aplicada (`migration_applied`)."""
    mig = st.get("migration")
    return None if (mig in MIGRATION_KINDS and st.get("migration_applied")) else (mig or None)
DEPLOY_RAILWAY_UP, DEPLOY_ON_MERGE, DEPLOY_NONE = "railway_up", "on_merge", "none"
DEPLOY_DOKPLOY = "dokploy"                       # 22-10: Oscar HQ / NextJobs corren en pildora-01 (Hetzner) con Dokploy
DEPLOY_BUTTON = (DEPLOY_RAILWAY_UP, DEPLOY_DOKPLOY)  # fusionar NO despliega: botón aparte [🚀 Desplegar]
DOKPLOY_OK, DOKPLOY_BAD = {"done"}, {"error"}    # applicationStatus de application.one (idle/running = en curso)
# Autor de los commits de fusión (lanes.yaml `integrator.commit_author`). Vercel bloquea los despliegues de commits cuyo
# autor no es miembro del equipo («Failed deployment from agent-lanes@localhost», 22-10): por defecto, el noreply de
# GitHub de Oscar. Es un dato: se cambia en lanes.yaml sin tocar código.
DEFAULT_COMMIT_NAME, DEFAULT_COMMIT_EMAIL = "oscarsao", "oscarsao@users.noreply.github.com"
IDENT_RE = re.compile(r"[^\s<>=,\"']([^<>\r\n\"]*[^\s<>\r\n\"])?")
EMAIL_RE = re.compile(r"[^\s@<>\"']+@[^\s@<>\"']+\.[^\s@<>\"']+")
CHECK_GITHUB_DEPLOYMENTS = "github_deployments"
GH_DEPLOY_OK, GH_DEPLOY_BAD = {"success"}, {"failure", "error", "inactive"}
# `apply` de una política con deploy none: qué hace [🔁 Aplicar] tras fusionar. Solo restart_drain (claude-hub):
# `py -3.12 lanes.py restart --drain` en el checkout VIVO (ROOT), lanzado sin esperar, porque reinicia el propio
# proceso que atiende el botón. El subcomando lo aporta feat/plan-d-runtime; mientras lanes.py no lo tenga, no hay
# botón. El fast-forward del checkout raíz a main es cosa de ese restart (deploy de HEAD), nunca del integrador.
APPLY_RESTART_DRAIN = "restart_drain"
APPLY_ARGV = {APPLY_RESTART_DRAIN: ["py", "-3.12", "lanes.py", "restart", "--drain"]}
APPLY_LABEL = "🔁 Aplicar (reinicio ordenado)"
APPLIED_PREFIX = "APLICADO"
APPLY_WAIT = 2 * 3600  # un reinicio sin veredicto pasado este tiempo se da por fallido (drain 1 h + arranque)
LAG_AFTER = 30 * 60    # checkout vivo por detrás de origin/<base> más de esto: aviso con [🔁 Aplicar]
LAG_REPEAT = 2 * 3600  # y se repite cada tanto mientras siga atrasado
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
        # La contraseña no puede ser un marcador de ejemplo: <contraseña>, ${VAR}, $VAR, ***, PASSWORD (02-10, PR #6).
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)(?:\+\w+)?://[^\s:/@'\"]+:"
        r"(?!<[^>@\s]*>@|\$\{?\w+\}?@|\*+@|(?:password|pass|pwd|contrase[nñ]a)@)[^\s@/'\"]+@", re.I)),
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
    # dokploy: API de Dokploy (https://…), id de la aplicación y NOMBRE de la variable de entorno con el token
    # (agent-lanes/.env o entorno del runner). El token nunca va en lanes.yaml.
    dokploy_url: str = ""
    dokploy_application_id: str = ""
    dokploy_token_env: str = ""
    health_url: str = ""
    health_commit_key: str = ""              # clave del JSON de health con el sha desplegado (MigraTeam: commit_sha)
    # on_merge: cómo se comprueba el deploy. Vacío = /health (health_url + health_commit_key); github_deployments =
    # estado de GitHub Deployments del commit fusionado (Scraper: Railway despliega desde la rama base).
    deploy_check: str = ""
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
    # Lote diario (batch.py): `daily` = las ramas aprobadas se juntan en release/lote-<marca>-<fecha> y Oscar recibe un
    # solo botón [🔀 Fusionar lote]. Vacío = PR a PR, como siempre.
    batch: str = ""
    batch_time: str = "08:30"                   # hora local de montaje automático
    batch_brand: str = ""                       # nombre en la rama y en /lote <marca>; vacío = el carril sin `claude-`
    batch_frontend_paths: tuple[str, ...] = ()  # si el lote toca estas rutas se ejecuta batch_frontend_cmd
    batch_frontend_cmd: str = ""                # build/typecheck del frontend (en la raíz del worktree del lote)
    # Ficheros que solo crecen por el final (atascos.py): un conflicto SOLO en ellos se resuelve solo (merge=union) en la
    # rama del PR. Glob de ruta; en lanes.yaml `append_only_paths: []` lo desactiva para el carril.
    append_only_paths: tuple[str, ...] = DEFAULT_APPEND_ONLY


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
    # PR en batch_pending sin salida: pasados estos días (o con conflictos) se avisa UNA vez a Oscar.
    batch_pending_days: int = 3
    # Repaso de PR de carriles abiertos (prlisto.py): avisa de los que llevan más de estas horas sin fusionar
    # (0 = apagado) y no repite el mismo conjunto antes de `pr_watch_repeat_hours`.
    pr_watch_hours: float = 2.0
    pr_watch_repeat_hours: float = 6.0
    # Mensaje vivo del tema de Integración (panel_vivo.py): bloque `live_panel:` de lanes.yaml (vacío = apagado).
    live_panel: dict = field(default_factory=dict)
    commit_name: str = DEFAULT_COMMIT_NAME
    commit_email: str = DEFAULT_COMMIT_EMAIL


def _api_url_ok(url: str) -> bool:
    """https, o http solo hacia este mismo PC (túnel SSH al panel de Dokploy: http://localhost:3300)."""
    return url.startswith("https://") or bool(re.match(r"http://(?:localhost|127\.0\.0\.1)(?::\d+)?(?:/|$)", url))


def ident_args(name: str = DEFAULT_COMMIT_NAME, email: str = DEFAULT_COMMIT_EMAIL) -> tuple[str, ...]:
    """Argumentos `-c` de git con el autor de los commits de fusión del integrador."""
    return ("-c", f"user.name={name}", "-c", f"user.email={email}", "-c", "commit.gpgsign=false")


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
        p["batch_frontend_paths"] = tuple(p.get("batch_frontend_paths") or ())
        p["append_only_paths"] = tuple(p["append_only_paths"] if "append_only_paths" in p and p["append_only_paths"]
                                       is not None else DEFAULT_APPEND_ONLY)
        if p.get("batch") and p["batch"] != "daily":
            raise ValueError(f"integrator.lanes.{name}.batch desconocido: {p['batch']}")
        if p.get("batch_time") is not None and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(p["batch_time"])):
            raise ValueError(f"integrator.lanes.{name}.batch_time debe ser HH:MM")
        if p.get("health_url") and not str(p["health_url"]).startswith("https://"):
            raise ValueError(f"integrator.lanes.{name}.health_url debe ser https://")
        policies[name] = Policy(lane=name, **{k: ("" if v is None else v) for k, v in p.items()})
        if policies[name].deploy not in (DEPLOY_RAILWAY_UP, DEPLOY_ON_MERGE, DEPLOY_NONE, DEPLOY_DOKPLOY):
            raise ValueError(f"integrator.lanes.{name}.deploy desconocido: {policies[name].deploy}")
        if policies[name].dokploy_url and not _api_url_ok(str(policies[name].dokploy_url)):
            raise ValueError(f"integrator.lanes.{name}.dokploy_url debe ser https:// (o http://localhost: el túnel SSH)")
        if policies[name].dokploy_token_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", policies[name].dokploy_token_env):
            raise ValueError(f"integrator.lanes.{name}.dokploy_token_env es el NOMBRE de la variable, no el token")
        if policies[name].deploy_check not in ("", CHECK_GITHUB_DEPLOYMENTS):
            raise ValueError(f"integrator.lanes.{name}.deploy_check desconocido: {policies[name].deploy_check}")
        if policies[name].apply and (policies[name].apply not in APPLY_ARGV or policies[name].deploy != DEPLOY_NONE):
            raise ValueError(f"integrator.lanes.{name}.apply: solo {sorted(APPLY_ARGV)} y con deploy none")
    author = cfg.get("commit_author") or {}
    c_name = str(author.get("name") or DEFAULT_COMMIT_NAME).strip()
    c_email = str(author.get("email") or DEFAULT_COMMIT_EMAIL).strip()
    if not IDENT_RE.fullmatch(c_name) or not EMAIL_RE.fullmatch(c_email):
        raise ValueError("integrator.commit_author: name/email no válidos")
    return IntegratorSettings(enabled=enabled, interval_seconds=int(cfg.get("interval_seconds") or 300),
                              commit_name=c_name, commit_email=c_email,
                              worktree_root=cfg.get("worktree_root") or str(ROOT / ".state" / "integrator" / "wt"),
                              deploy_timeout_seconds=int(cfg.get("deploy_timeout_seconds") or 900),
                              poll_seconds=int(cfg.get("poll_seconds") or 20), policies=policies,
                              integration_telegram=_chat_thread(data.get("integration_telegram")),
                              batch_pending_days=int(cfg.get("batch_pending_days") or 3),
                              pr_watch_hours=float(2 if cfg.get("pr_watch_hours") is None else cfg["pr_watch_hours"]),
                              pr_watch_repeat_hours=float(cfg.get("pr_watch_repeat_hours") or 6),
                              live_panel=data.get("live_panel") if isinstance(data.get("live_panel"), dict) else {})


# Texto de prueba ficticio (hub #171: «sk-ABCD…» en un test de redacción). Se admite si la COINCIDENCIA lleva ABCD,
# XXXX o una palabra de ejemplo entre delimitadores (sk-ejemplo-…), o si la línea lleva la marca explícita
# `secret-scan: allow`. Una clave real no lleva esas cadenas; la repetición de un solo carácter (ZZZZ…) NO cuenta.
ALLOW_MARK = "secret-scan: allow"
FICTITIOUS_RULES = frozenset({"clave sk-"})  # solo la de formato libre: AKIA…, ghp_… etc. tienen forma fija y no se exentan
FICTITIOUS_RE = re.compile(r"(?-i:ABCD)|x{4,}|(?<![A-Za-z0-9])(?:ejemplo|example|fake|dummy|placeholder|ficticio)(?![A-Za-z0-9])",
                           re.I)


def _fictitious(match: str) -> bool:
    return bool(FICTITIOUS_RE.search(match))


# Versión de los gates: sube a mano si cambia su lógica (no solo las reglas del secret-scan, que van en la huella).
GATE_VERSION = "2"  # 2: textos de prueba ficticios y marca `secret-scan: allow`


def gate_fingerprint() -> str:
    """Huella de las reglas de gate vigentes. Un PR `failed` se reevalúa si cambió desde que falló (hub #6: un falso
    positivo del secret-scan ya corregido seguía bloqueando el PR porque la cabeza no había cambiado)."""
    h = hashlib.sha1(GATE_VERSION.encode())
    for name, rx in SECRET_RULES:
        h.update(f"{name}|{rx.pattern}|{rx.flags}||".encode())
    return h.hexdigest()[:12]


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
            if ALLOW_MARK not in raw:
                for name, rx in SECRET_RULES:
                    m = rx.search(raw[1:])
                    if m and not (name in FICTITIOUS_RULES and _fictitious(m.group(0))):
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
    automatismos = True  # back-merge automático y retro semanal en cada pasada (los tests de otros flujos lo apagan)

    def __init__(self, settings: IntegratorSettings, lanes: dict, *, hermes_for: Callable[[str], object],
                 links=None, notifier=None, messages: MessageStore | None = None, desk=None,
                 runner=_proc.run, gh_exe: str = GH_EXE, railway_exe: str = RAILWAY_EXE,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 http_get: Callable[[str], tuple[int, str]] | None = None, state_dir: Path = STATE_DIR,
                 dry_run: bool = False, out: Callable[[str], None] = print,
                 now: Callable[[], float] = time.time, launcher: Callable[[list[str], str], None] | None = None,
                 apply_probe: Callable[[str], bool] | None = None, env: dict | None = None,
                 http_json: Callable[..., tuple[int, object]] | None = None):
        self.settings = settings
        self.env = env or {}                            # .env de agent-lanes: token de Dokploy por nombre de variable
        self._http_json = http_json or _http_json
        self._launch = launcher or launch_detached      # [🔁 Aplicar]: lanza sin esperar (reinicia este proceso)
        self._apply_probe = apply_probe or apply_available  # ¿existe ya el subcomando? (lectura estática)
        self.apply_result_path = drain.RESULT_FILE  # veredicto del último `restart --drain` (tests: otra ruta)
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
        from .panel_vivo import LivePanel, PanelSettings  # import tardío: panel_vivo no importa este módulo
        panel_cfg = PanelSettings.from_cfg(settings.live_panel)
        self.panel = LivePanel(self, panel_cfg) if panel_cfg.enabled and self.pinned else None
        self._seen: list[tuple[object, Policy, str]] = []  # (carril, política, tarea) vistos en la última pasada
        from .batch import BatchPlanner  # import tardío: batch usa constantes de este módulo
        self.batches = BatchPlanner(self)  # lote diario: solo actúa en carriles con `batch: daily`
        self.autoresolve = AutoResolver(self)  # conflicto solo en ficheros append-only: unión en la rama del PR
        from .prlisto import ReadyPRWatcher
        self.pr_watch = ReadyPRWatcher(self)  # recordatorio de PR de carriles abiertos (MERGEABLE/CONFLICTING/UNKNOWN)

    # --- utilidades ----------------------------------------------------------------------------------

    def _ident(self) -> tuple[str, ...]:
        """Autor de los commits de fusión (lanes.yaml `integrator.commit_author`)."""
        return ident_args(self.settings.commit_name, self.settings.commit_email)

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

    def _gh(self, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess:
        # cwd fuera de cualquier clon: `gh pr merge --delete-branch` dentro de un checkout cambia de rama y borra
        # la local, y en el checkout raíz eso está prohibido.
        # Timeout/red (t_cce5da81): reintento con espera solo en lecturas (pr view, api GET); en escrituras (merge,
        # create) un timeout devuelve un fallo sin repetir: el llamador relee el PR (pudo aplicarse).
        read = _proc.gh_is_read(args)
        return _proc.run_retrying([self.gh_exe, *args], runner=lambda cmd, **kw: self._exec(cmd, **kw),
                                  timeout=timeout, sleep=self._sleep, label=f"gh {' '.join(args[:2])}",
                                  retry_timeout=read, transient=_proc.gh_network_error if read else None,
                                  cwd=tempfile.gettempdir())

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
        try:
            if finalized := self.finalize_apply():
                log.info("integrador: veredicto de Aplicar en %s", finalized)
            self.check_live_lag()
        except Exception as exc:  # nunca tumba la pasada
            log.warning("integrador: Aplicar/checkout vivo: %s", exc)
        from . import backmerge, retro  # import tardío: ambos usan el integrador por duck typing
        for step in (backmerge.check, retro.check) if self.automatismos else ():  # back-merge y retro del lunes
            try:
                step(self)
            except Exception as exc:  # nunca tumba la pasada
                log.warning("integrador: %s: %s", step.__module__.rsplit(".", 1)[-1], exc)
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
        try:
            self.batches.run_due()
        except Exception as exc:  # el lote nunca tumba la pasada PR a PR de los demás carriles
            log.warning("integrador: lote diario falló: %s", exc)
        self.pr_watch.tick()  # nunca lanza
        self.refresh_summary()
        return results

    def _promote_markup(self) -> dict | None:
        """[🚀 Promover a producción] en el resumen fijado 🚦 (uno por proyecto con `promote:` en lanes.yaml)."""
        promoter = getattr(self.desk, "promoter", None)
        rows = []
        for key in (promoter.configs if promoter and promoter.configs else ()):
            rec = {"task_id": f"promo-start-{key}", "board": "", "lane": "", "key": key}
            rows.append([{"text": f"🚀 Promover {key.capitalize()} a producción", "action": "promo_start"}])
            token_markup = self.desk.store.issue(rec, [rows[-1]])[1]
            rows[-1] = token_markup["inline_keyboard"][0]
        if self.panel and self.desk:  # botones de los conflictos del panel vivo (🛠 Resolver · 👁 Ver · ⏸ Aparcar)
            rows += self.panel.markup_rows(self.desk.store.issue)
        return {"inline_keyboard": rows} if rows else None

    def reissue_panel_markup(self) -> str | None:
        return self.reissue_promote_markup()

    def reissue_promote_markup(self) -> str | None:
        """Repone los botones del fijado (🚀 y los del panel) tras pulsarlos (token de un solo uso). Nunca lanza."""
        if not self.pinned or self.dry_run:
            return None
        try:
            return self.pinned.reissue(self._promote_markup)
        except Exception as exc:
            log.warning("no se pudo reponer el botón 🚀: %s", exc)
            return None

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
                                "state": state, "since": st.get("since"), "repo": repo_name(lane),
                                "url": st.get("pr_url"),
                                "card": card_url(getattr(self.links, "kanban_base_url", None), lane.board, tid)})
            text = summary_text(entries, self._now())
            if self.panel:  # panel vivo: en marcha / cola / terminadas / conflictos delante de lo pendiente
                from .panel_vivo import compose, render_sections
                data = self.panel.gather()  # si Hermes falla lanza: el fijado se queda como estaba
                conflicts = self.panel.conflict_entries()
                sections = render_sections(conflicts=self.panel.conflicts, now=self._now(), cfg=self.panel.cfg, **data)
                if len(conflicts) > len(self.panel.conflicts):
                    sections += f"\n… y {len(conflicts) - len(self.panel.conflicts)} más"
                text = compose(sections, text)
            return self.pinned.update(text, self._promote_markup)
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
            if not is_approval(c):
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
        if policy.batch and st.get("status") == "batch_included":
            return None  # ya va en un lote abierto: lo cierra su botón (o /lote lo libera)
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
        if pr.get("mergeable") == "CONFLICTING" and not self.dry_run:
            outcome = self._conflict_pass(lane, policy, tid, number, url, pr, task, about)
            if outcome:
                return outcome
        if policy.batch:  # lote diario: sin ficha ni gates sueltos; el lote los pasa todos juntos (batch.py)
            if not self.dry_run and (st.get("status") != "batch_pending" or st.get("head_sha") != pr["headRefOid"]):
                keep = st.get("pending_since") if st.get("status") == "batch_pending" else None
                self._save(tid, status="batch_pending", pr=number, pr_url=url, head_sha=pr["headRefOid"],
                           pending_since=keep or self._now(), pending_alert=None, **about)
                st = self._state(tid)
            if not self.dry_run:
                self._warn_stale_batch(lane, task, number, url, pr, st)
            return "dry-run:batch_pending" if self.dry_run else "batch_pending"
        if st.get("head_sha") == pr["headRefOid"] and st.get("status") in ("failed", "offered", "merging"):
            # Gates ya hechos sobre esta cabeza; se repiten al pulsar si la base se movió. Un `failed` SÍ se reevalúa
            # si las reglas de gate cambiaron desde que falló (o tras `lanes.py integrator reset <t_id>`).
            if not (st.get("status") == "failed" and st.get("gate_fp") != gate_fingerprint()):
                return None
        result = self.run_gates(lane, policy, tid, number, pr["headRefOid"])
        if self.dry_run:
            self._report(tid, number, result, policy)
            return "dry-run:" + ("ok" if result.ok else "failed")
        if not result.ok:
            if self._send_back_conflict(lane, tid, number, result):
                self._save(tid, status="sent_back", pr=number, pr_url=url, head_sha=result.head_sha,
                           base_sha=result.base_sha, sent_back=int(st.get("sent_back") or 0) + 1, **about)
                return "sent_back"
            log.info("%s: PR #%s gates fallidos: %s", tid, number, "; ".join(result.reasons)[:300])
            self._publish_failed(lane, task, number, url, result)
            self._save(tid, status="failed", pr=number, pr_url=url, head_sha=result.head_sha, base_sha=result.base_sha,
                       gate_fp=gate_fingerprint(), fail_reasons=[truncate(r, 80) for r in result.reasons[:3]],
                       conflict=any(r.startswith("tiene conflictos con") for r in result.reasons), **about)
            return "gates_failed"
        self._offer(lane, policy, task, number, url, result)
        self._save(tid, status="offered", pr=number, pr_url=url, head_sha=result.head_sha, base_sha=result.base_sha,
                   migration=result.migration, **about)
        return "offered"

    def _conflict_pass(self, lane, policy: Policy, tid: str, number: int, url: str, pr: dict, task: dict,
                       about: dict) -> str | None:
        """PR aprobado con conflicto (atascos.py). Solo ficheros append-only: unión + gates + push a la rama del PR
        (la cabeza cambia y la próxima pasada lo integra). Código: de vuelta al carril sobre su rama. None = nada
        que hacer aquí y sigue el flujo normal (gates sueltos, lote o aviso de lote atascado)."""
        outcome, detail = self.autoresolve.run(lane, policy, tid, number, pr, task)
        if outcome == "pushed":
            return "autoresolved"
        if outcome in ("gates", "push_failed"):
            return "autoresolve_blocked"  # ya avisó; no se reintenta sobre la misma cabeza
        if outcome != "code":
            return None
        res = GateResult(ok=False, head_sha=pr["headRefOid"])
        res.reasons.append(f"tiene conflictos con {lane.base}")
        res.detail.append("conflictos de código en: " + ", ".join(detail[:10]))
        if self._send_back_conflict(lane, tid, number, res):
            self._save(tid, status="sent_back", pr=number, pr_url=url, head_sha=pr["headRefOid"],
                       sent_back=int(self._state(tid).get("sent_back") or 0) + 1, **about)
            return "sent_back"
        return None

    def _warn_stale_batch(self, lane, task: dict, number: int, url: str, pr: dict, st: dict) -> None:
        """batch_pending sin salida (más de `batch_pending_days` días, o el PR tiene conflictos): UNA alerta al tema de
        Integración con enlace y motivo. `pending_alert` guarda el motivo ya avisado para no repetirlo."""
        reason = None
        days = (self._now() - float(st.get("pending_since") or st.get("since") or self._now())) / 86400
        if pr.get("mergeable") == "CONFLICTING":
            reason = "conflict"
            why = f"el PR #{number} tiene conflictos con {lane.base}"
        elif days >= self.settings.batch_pending_days:
            reason = "stale"
            why = f"lleva {int(days)} días esperando lote"
        if not reason or st.get("pending_alert") == reason:
            return
        self._save(task["id"], pending_alert=reason)  # antes del aviso: si falla Telegram no se repite en bucle
        self._comment(lane, task["id"], f"INTEGRADOR: PR #{number} sin salida en el lote · {why}")
        rec = self._rec(lane, task, number, url)
        self._publish(lane, task, self._text("blocked", lane, rec, status_line(
            f"⏳ PR #{number} aprobado pero sin integrar", why, "revísalo o pide /lote"),
            phase="SIN SALIDA"), link=f"⏳ PR #{number} sin salida en el lote")

    def reset_pr(self, tid: str) -> tuple[bool, str]:
        """Fuerza la reevaluación de un PR `failed`/`pr_problem`/`sent_back` en la próxima vuelta (comando
        `lanes.py integrator reset <t_id>`). Solo toca el estado local; no fusiona ni despliega nada."""
        if not TID_RE.fullmatch(tid or ""):
            return False, f"id de tarea no válido: {tid!r}"
        st = self._state(tid)
        if st.get("status") not in ("failed", "pr_problem", "sent_back"):
            return False, f"{tid}: estado {st.get('status')!r}; solo se resetea failed/pr_problem/sent_back"
        self._save(tid, status="reset", head_sha=None, gate_fp=None, pr_problem=None)
        return True, f"{tid}: reseteado; el integrador lo reevaluará en la próxima vuelta"

    def _send_back_conflict(self, lane, tid: str, number: int, result: GateResult) -> bool:
        """Gates fallidos SOLO por conflicto con la base (#59): la tarea vuelve al carril con la instrucción de traer
        `origin/<base>` en vez de quedarse en failed esperando a Oscar. Máximo 2 veces por tarea."""
        if self.dry_run or int(self._state(tid).get("sent_back") or 0) >= 2:
            return False
        if not result.reasons or not all(r.startswith("tiene conflictos con") for r in result.reasons):
            return False
        h = self.hermes_for(lane.board)
        body = (f"CAMBIOS (integrador): el PR #{number} tiene conflictos con {lane.base}. En tu lane ejecuta "
                f"`git fetch origin {lane.base}` y `git merge origin/{lane.base}`, resuelve los conflictos, "
                "comprueba los tests y haz push de tu rama.\n" + "\n".join(f"- {d}" for d in result.detail[:5]))
        who = f"Lo da: el carril {lane.name}, sobre la rama lane/{tid} del PR #{number}"
        if not h.comment(tid, body[:3000], author=REVIEW_AUTHOR) or not h.reopen_review(tid):
            log.warning("%s: no se pudo devolver al carril el PR #%s con conflicto", tid, number)
            return self._conflict_card(lane, tid, number, result, who)  # el PR ya no está en review: tarjeta nueva
        log.info("%s: PR #%s con conflicto con %s: devuelta al carril %s", tid, number, lane.base, lane.name)
        self._comment(lane, tid, f"INTEGRADOR: PR #{number} con conflicto de código ({'; '.join(result.detail[:2])[:300]}"
                                 f"). {who}.")
        return True

    def _conflict_card(self, lane, tid: str, number: int, result: GateResult, who: str) -> bool:
        """No se pudo reabrir la tarjeta original: tarjeta nueva en el carril que trabaja SOBRE LA RAMA DEL PR
        (Rama-origen + Rama-push = lane/<tid>), nunca sobre su propia lane/<id_nuevo>."""
        branch = f"lane/{tid}"
        body = pr_branch_card_body(branch, number, lane.base, lane.base, rebase=True,
                                   origin=f"conflicto del PR #{number}: {'; '.join(result.detail[:2])[:400]}")
        try:
            new = self.hermes_for(lane.board).create(rebase_title(lane.name, number, lane.base), body,
                                                     lane.name, priority=10, key=f"conflicto-{tid}-{(result.head_sha or '')[:8]}",
                                                     created_by="oscar")
        except Exception as exc:
            log.warning("%s: no se pudo crear la tarjeta de rebase del PR #%s: %s", tid, number, exc)
            return False
        self._comment(lane, tid, f"INTEGRADOR: PR #{number} con conflicto de código; tarjeta {new} en {lane.name} "
                                 f"para rebasar la rama {branch}. {who}.")
        return True

    def rebase_card(self, lane, tid: str, number: int) -> bool:
        """[🛠 Resolver] del panel vivo: crea (o reanuda: misma clave idempotente) la tarjeta de rebase del PR."""
        if self.dry_run:
            return False
        st = self._state(tid)
        result = GateResult(ok=False, reasons=["tiene conflictos con " + str(lane.base)],
                            detail=list(st.get("fail_reasons") or ["conflicto con la base"]),
                            head_sha=st.get("head_sha") or "")
        return self._conflict_card(lane, tid, number, result, f"Lo pide Oscar desde el panel: carril {lane.name}")

    def _push_pr_branch(self, wt: str, remote: str, tid: str) -> subprocess.CompletedProcess:
        """ÚNICO push del integrador (atascos.py): HEAD del worktree -> `lane/<tid>`, la rama del PR. Sin force (un
        rechazo por non-fast-forward es lo esperado si la rama avanzó); nunca la base, master ni main."""
        if not TID_RE.fullmatch(tid or ""):
            raise PermissionError(f"id de tarea no válido para push: {tid!r}")
        return self._run(["git", "-C", str(wt), "-c", f"core.hooksPath={self._nohooks()}", "push", remote,
                          f"HEAD:refs/heads/lane/{tid}"], capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=120, stdin=subprocess.DEVNULL)

    def rebind_approval(self, tid: str, new_sha: str) -> None:
        """Tras empujar la fusión automática, la aprobación local de Oscar pasa a la nueva cabeza (la hizo el
        integrador, no un tercero): sin esto el PR quedaría como «commits después de tu aprobación»."""
        rec = self._approved_record(tid)
        if not rec or not re.fullmatch(r"[0-9a-f]{40}", new_sha or ""):
            return
        rec["head_sha"] = new_sha
        path = self.approvals_dir / f"{tid}.json"
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        os.replace(tmp, path)

    def _pr_view(self, slug: str, number: int) -> dict | None:
        cp = self._gh("pr", "view", str(number), "--repo", slug, "--json",
                      "number,url,state,isDraft,headRefName,headRefOid,baseRefName,isCrossRepository,mergeCommit,"
                      "mergeable")
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
        if self._state(tid).get("autoresolve_head") == pr["headRefOid"]:
            return None  # la cabeza la empujó el propio integrador (unión append-only con gates OK)
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
        merge = self._git(w, "-c", f"core.hooksPath={self._nohooks()}", *self._ident(),
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

        self._diff_gates(w, lane, policy, res, rng)
        self._migration_gates(wt, lane, policy, res)

    def _diff_gates(self, w: str, lane, policy: Policy, res: GateResult, rng: str) -> None:
        """Gates del diff (rutas vetadas, ejecutables, .env, secretos): los comparten el PR suelto y cada rama de un
        lote (batch.py). `res.changed_files` ya está rellenado y `w` es un worktree con el diff accesible."""
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

    def _migration_gates(self, wt: Path, lane, policy: Policy, res: GateResult) -> None:
        """Migraciones, infraestructura y test_cmd del carril sobre el PR ya fusionado en `wt`."""
        w = str(wt)
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
        if rec.get("batch"):  # ficha de un lote: tabla de lo que incluye (con o sin tema de Integración)
            return self.batches.ficha(lane, rec, state, status, phase=phase, override=override)
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
            return None if migration else [[consola.boton(consola.CS_INTEGRAR, policy.merge_label)]]
        return [[consola.boton(consola.CS_INTEGRAR)]]  # dos toques (consola.py): el ✅ ejecuta INT_MERGE

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
        if policy.deploy in DEPLOY_BUTTON:
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
        if action in consola.INT_PRODUCCION:  # defensa en el sumidero (H3): toda vía que llegue aquí
            actor = (where or {}).get("user_id")
            check = getattr(desk, "puede_aprobar_produccion", None)
            if check is None or not check(actor):
                log.warning("%s: %s rechazado en el integrador: %s sin aprobar_produccion", rec.get("task_id"),
                            action, actor)
                return False
        policy = self.settings.policies.get(rec.get("lane"))
        lane = self.lanes.get(rec.get("lane"))
        if not policy or not lane:
            return False
        if action == INT_BATCH_MERGE and policy.batch and rec.get("batch"):
            return self.batches.on_merge_button(lane, policy, rec, where, desk)
        if action == INT_BATCH_BUILD and policy.batch and rec.get("batch_build"):
            return self.batches.notice.on_build_button(lane, policy, rec, where, desk)
        if action in BATCH_EXCLUDED_ACTIONS and policy.batch and rec.get("batch_excluded_pr"):
            return self.batches.on_excluded_button(action, lane, policy, rec, where, desk)
        if action == INT_MERGE and policy.deploy != DEPLOY_ON_MERGE:
            return self._merge(lane, policy, rec, where, desk)
        if action == INT_MERGE_DEPLOY and policy.deploy == DEPLOY_ON_MERGE and not rec.get("migration"):
            return self._merge(lane, policy, rec, where, desk)
        if action == INT_DEPLOY and policy.deploy in DEPLOY_BUTTON and not rec.get("migration") \
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
        for other in st.get("batch_tids") or ():  # lote: "Migración aplicada" vale para todas sus ramas
            if other != tid and TID_RE.fullmatch(other):
                self._save(other, migration_applied={"by": author, "at": self._now()})
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
            if policy and policy.deploy in DEPLOY_BUTTON and sha:
                markup = desk.store.issue(rec, [[consola.boton(consola.CS_DESPLEGAR)]])[1]
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
        if markup:
            self._announce_buttons(desk, rec, markup, status)

    def _announce_buttons(self, desk, rec: dict, markup: dict, status: str) -> None:
        """Botón nuevo en una ficha ya publicada (🚀 Desplegar, ✅ Migración aplicada, 🔁 Aplicar): además de editarla,
        un mensaje NUEVO con ese teclado al final del tema y en el DM de Oscar (una edición no avisa ni sube al final).
        Un solo mensaje por teclado (marca `announced` en el store). Las copias comparten token: pulsar una retira
        las demás."""
        store, tid = self._notices.store if self._notices else None, rec.get("task_id") or ""
        token = markup_token(markup)
        main = store.get(tid) if store else None
        if not main or not token or token in (main.get("announced") or ()):
            return
        labels = [b["text"] for row in markup.get("inline_keyboard") or () for b in row]
        text = f"🔔 {tid} · {truncate(rec.get('title'), 40)}\n{status}\nAhora puedes pulsar: {' · '.join(labels)}"
        if rec.get("pr_url"):  # texto plano: Telegram enlaza las URL solas
            text += f"\nPR #{rec.get('pr_number')}: {rec['pr_url']}"
        card = card_url(getattr(self.links, "kanban_base_url", None), rec.get("board") or "", tid)
        if card:
            text += f"\nTarjeta: {card}"
        owner = str(getattr(desk, "owner_id", "") or "")
        dests = [(main.get("chat_id"), main.get("thread_id"))]
        if owner and owner != str(main.get("chat_id")):
            dests.append((owner, None))
        for chat_id, thread_id in dests:
            try:
                sent = desk.notifier.send_to(str(chat_id), thread_id, text, html=False, reply_markup=markup)
            except Exception as exc:  # DM sin /start, tema borrado...: la ficha ya está editada
                log.info("%s: no se pudo enviar el aviso del botón nuevo: %s", tid, exc)
                continue
            store.add_mirror(tid, sent, token=token, bot=getattr(desk.notifier, "bot_id", None))
        store.put(tid, {**(store.get(tid) or main), "announced": [*(main.get("announced") or ())[-9:], token]})

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
        log.info("%s: fusionando PR #%s (%s) sobre %s @ %s", tid, number, rec["head_sha"][:12], lane.base,
                 (rec.get("base_sha") or "?")[:12])
        cp = self._gh(*args, timeout=180)
        # "Base branch was modified": otro PR se fusionó entre medias; con espera, el reintento suele pasar.
        for wait in MERGE_RETRY_WAITS:
            if cp.returncode == 0 or "base branch was modified" not in (cp.stderr or "").lower():
                break
            log.info("%s: base modificada al fusionar PR #%s; reintento en %ss", tid, number, wait)
            self._sleep(wait)
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
        return self._after_merge(lane, policy, rec, where, desk, sha)

    def _after_merge(self, lane, policy: Policy, rec: dict, where: dict, desk, sha: str) -> bool:
        """Lo que sigue a una fusión ya hecha (verificar el deploy, botón de deploy/aplicar o nada). Lo comparte la
        fusión de un PR suelto y la de un lote (batch.py)."""
        short = sha[:7] or "?"
        if policy.deploy == DEPLOY_ON_MERGE:
            self._edit(desk, lane, rec, where, "running", status_line(
                f"🚀 fusionado en {lane.base} · {short}", "Railway despliega solo; comprobando…"))
            return self._verify_on_merge(lane, policy, rec, where, desk)
        if policy.deploy in DEPLOY_BUTTON:
            if rec.get("migration"):
                # Oscar aplica la migración a mano y lo dice con [✅ Migración aplicada]: desbloquea el 🚀.
                markup = (desk.store.issue(rec, consola.fila_migracion())[1]
                          if rec["migration"] in MIGRATION_KINDS else None)
                names = self._migration_files(lane, policy, sha) if rec["migration"] == "supabase" else []
                if names:
                    self._save(rec["task_id"], migration_files=names)
                self._edit(desk, lane, rec, where, "done", status_line(
                    f"✅ fusionado · {short}", NEEDS_MIGRATION,
                    ("migraciones: " + ", ".join(names[:8]) + (f" (+{len(names) - 8})" if len(names) > 8 else ""))
                    if names else None), markup=markup)
                return True
            markup = desk.store.issue(rec, [[consola.boton(consola.CS_DESPLEGAR)]])[1]
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
        if st.get("status") == "applying" and self._now() - float((st.get("apply") or {}).get("at") or 0) < APPLY_WAIT:
            self._edit(desk, lane, rec, where, "done", status_line(f"🔁 reinicio en curso · {short}",
                                                                   "espera el veredicto del reinicio"))
            return True
        # Todo el registro ANTES de lanzar: el reinicio puede parar este proceso en cuanto arranca, y lo que quede
        # después (estado, comentario, aviso, resumen fijado) no llegaría a hacerse. El APLICADO <sha> NO se dice
        # aquí: lo deja `finalize_apply` el runner nuevo, cuando el restart ha verificado HEAD == origin/main.
        self._save(tid, status="applying", apply={"at": self._now(), "lane": lane.name, "rec": rec, "where": where})
        self._comment_task(lane, tid, f"reinicio ordenado lanzado (`{' '.join(argv)}`); falta verificar que el "
                           f"checkout vivo queda en {lane.remote}/{lane.base}")
        self._edit(desk, lane, rec, where, "done", status_line(
            f"🔁 reinicio ordenado lanzado · {short}", "el runner se reinicia al terminar lo que tenga en curso"))
        self.refresh_summary()
        try:
            self._launch(argv, str(ROOT))
        except Exception as exc:
            log.warning("%s: no se pudo lanzar el reinicio ordenado: %s", tid, exc)
            self._save(tid, status="merged")
            self._comment_task(lane, tid, f"{APPLIED_PREFIX}: FALLÓ el lanzamiento del reinicio ordenado; sigue sin aplicar")
            self._edit(desk, lane, rec, where, "blocked", status_line(
                f"⛔ no se pudo aplicar · {short}", "detalle en el log", "puedes reintentar"))
            return False
        return True

    def _comment_task(self, lane, tid: str, text: str) -> None:
        if TID_RE.fullmatch(tid):  # el aviso «checkout atrasado» (lag-<carril>) no tiene tarjeta
            self._comment(lane, tid, text)

    def finalize_apply(self) -> list[str]:
        """Tras `lanes.py restart --drain`: deja en cada ficha «applying» el veredicto que escribió el restart
        (.state/apply_result.json). «APLICADO <sha>» solo si HEAD == origin/main y el runner arrancó; si no, ⛔ con el
        motivo y el botón para reintentar. Sin veredicto tras APPLY_WAIT s, ⛔ también (el restart murió a medias)."""
        verdict = drain.read_result(self.apply_result_path)
        done = []
        for path in sorted(self.state_dir.glob("*.json")):
            tid, st = path.stem, self._state(path.stem)
            pending = st.get("apply")
            if st.get("status") != "applying" or not isinstance(pending, dict):
                continue
            at = float(pending.get("at") or 0)
            fresh = bool(verdict) and float(verdict.get("at") or 0) >= at
            if not fresh and self._now() - at < APPLY_WAIT:
                continue
            ok = fresh and bool(verdict.get("ok"))
            reason = (verdict or {}).get("reason") if fresh else "el reinicio no dejó veredicto (¿murió a medias?)"
            try:
                self._finish_apply(tid, pending, ok, (verdict or {}).get("sha") or "", reason or "motivo desconocido")
            except Exception as exc:
                log.warning("%s: no se pudo dejar el veredicto de Aplicar: %s", tid, exc)
                continue
            done.append(tid)
        return done

    def _finish_apply(self, tid: str, pending: dict, ok: bool, sha: str, reason: str) -> None:
        lane, rec, where = self.lanes.get(pending.get("lane")), pending.get("rec") or {}, pending.get("where") or {}
        short = (rec.get("merge_sha") or "")[:7] or "?"
        if ok:
            self._save(tid, status="applied", applied_sha=sha)
            if lane:
                self._comment_task(lane, tid, f"{APPLIED_PREFIX} {rec.get('merge_sha') or '?'} · runner en "
                                   f"{sha[:12]} == {lane.remote}/{lane.base} e integrador activo")
            if lane and self.desk:
                self._edit(self.desk, lane, rec, where, "done", status_line(
                    f"🔁 aplicado · {short}", f"el runner ejecuta ya {sha[:7]}"))
        else:
            self._save(tid, status="merged")
            if lane:
                self._comment_task(lane, tid, f"APLICAR FALLÓ (no aplicado): {reason}")
            if lane and self.desk:
                markup = self.desk.store.issue(rec, [[{"text": APPLY_LABEL, "action": INT_APPLY}]])[1]
                self._edit(self.desk, lane, rec, where, "blocked", status_line(
                    f"⛔ no aplicado · {short}", truncate(reason, 300), "arréglalo y reintenta"), markup=markup)
        self.refresh_summary()

    def check_live_lag(self) -> None:
        """Carril con `apply` (claude-hub): si el checkout vivo va por detrás de origin/<base> más de LAG_AFTER s,
        ALTA al DM de Oscar con [🔁 Aplicar] (cada LAG_REPEAT s mientras siga atrasado). Solo lectura: fetch."""
        for name, policy in self.settings.policies.items():
            lane = self.lanes.get(name)
            if not (lane and policy.apply and self.desk and self.notifier and not self.dry_run):
                continue
            try:
                self._check_lag(name, lane)
            except Exception as exc:
                log.warning("integrador %s: comprobación del checkout vivo falló: %s", name, exc)

    def _check_lag(self, name: str, lane) -> None:
        tid, ref = f"lag-{name}", f"{lane.remote}/{lane.base}"
        if self._git(lane.repo, "fetch", lane.remote, lane.base).returncode:
            return
        cp = self._git(lane.repo, "rev-list", "--count", f"HEAD..{ref}")
        if cp.returncode:
            return
        behind, st, now = int((cp.stdout or "0").strip() or 0), self._state(tid), self._now()
        if not behind:
            if st.get("behind_since"):
                self._save(tid, behind_since=None, offered_at=None, status="ok")
            return
        since = st.get("behind_since")
        if not since:
            self._save(tid, behind_since=now)
            return
        if st.get("status") == "applying" and now - float((st.get("apply") or {}).get("at") or 0) < APPLY_WAIT:
            return
        if now - since < LAG_AFTER or now - float(st.get("offered_at") or 0) < LAG_REPEAT:
            return
        sha = (self._git(lane.repo, "rev-parse", ref).stdout or "").strip()
        rec = {"kind": "integrate", "task_id": tid, "board": lane.board, "lane": name, "merge_sha": sha,
               "title": f"checkout vivo atrasado ({behind} commit(s))", "pr_number": 0, "pr_url": ""}
        self._save(tid, status="lag", offered_at=now)
        markup = self.desk.store.issue(rec, [[{"text": APPLY_LABEL, "action": INT_APPLY}]])[1]
        text = (f"🚨 ALTA · el runner ejecuta código viejo: el checkout vivo va {behind} commit(s) por detrás de "
                f"{ref} desde hace {int((now - since) // 60)} min. Pulsa {APPLY_LABEL} para ponerlo al día.")
        owner = str(getattr(self.desk, "owner_id", "") or "")
        if owner:
            self.notifier.send_to(owner, None, text, html=False, reply_markup=markup)

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
            if policy.deploy == DEPLOY_DOKPLOY:
                return self._deploy_dokploy_locked(lane, policy, rec, where, desk)
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
                self._railway_incident(lane, tip, abrir=True)
                self._edit(desk, lane, rec, where, "blocked", "⛔ no se despliega: Railway no responde")
                return False
            self._railway_incident(lane, tip, abrir=False)  # Railway responde: si había incidencia abierta, se cierra
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
            self._deploy_fallo(tid, tip, f"railway up exit {up.returncode}")
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
            self._deploy_fallo(tid, tip, f"railway deployment {dep_id} {why}")
            self._edit(desk, lane, rec, where, "blocked", status_line("⛔ deploy falló", why, "revisa Railway"))
        return True

    # --- deploy: Dokploy (pildora-01) -----------------------------------------------------------------

    def _dokploy(self, policy: Policy, method: str, endpoint: str, body: dict | None = None,
                 query: dict | None = None) -> tuple[int, dict | list | None]:
        """Llamada a la API de Dokploy (`x-api-key`). El token se lee por el NOMBRE de variable de la política y no
        se registra jamás. (código HTTP, JSON o None); 0 si no hay red."""
        url = policy.dokploy_url.rstrip("/") + "/api/" + endpoint
        if query:
            url += "?" + urllib.parse.urlencode(query)
        token = os.environ.get(policy.dokploy_token_env) or self.env.get(policy.dokploy_token_env) or ""
        try:
            return self._http_json(method, url, {"x-api-key": token, "Accept": "application/json"}, body)
        except Exception as exc:
            log.warning("dokploy %s: %s", endpoint, type(exc).__name__)
            return 0, None

    def _dokploy_app(self, policy: Policy) -> dict | None:
        code, data = self._dokploy(policy, "GET", "application.one", query={"applicationId": policy.dokploy_application_id})
        return data if code == 200 and isinstance(data, dict) else None

    @staticmethod
    def _dokploy_deployments(app: dict) -> dict[str, str]:
        """id → estado de los despliegues que enumera application.one (vacío si esta versión no los devuelve)."""
        out = {}
        for d in app.get("deployments") or []:
            if isinstance(d, dict) and d.get("deploymentId"):
                out[str(d["deploymentId"])] = str(d.get("status") or "").lower()
        return out

    def _poll_dokploy(self, policy: Policy, before_ids: set) -> tuple[str | None, str]:
        """Sondea application.one hasta done/error del despliegue NUEVO. Un `done` leído justo tras pedir el redeploy
        es el del despliegue anterior: solo cuenta tras ver arrancar el nuevo (despliegue desconocido o estado
        `running`); sin esa señal se acepta tras 3 lecturas seguidas iguales. (done | error | None, id)."""
        deadline = self._clock() + self.settings.deploy_timeout_seconds
        started, steady, dep_id = False, 0, ""
        while self._clock() < deadline:
            app = self._dokploy_app(policy)
            if app is not None:
                status = str(app.get("applicationStatus") or "").lower()
                new = {k: v for k, v in self._dokploy_deployments(app).items() if k not in before_ids}
                if new:
                    started, dep_id = True, sorted(new)[-1]
                    bad = [k for k, v in new.items() if v in DOKPLOY_BAD]
                    if bad:
                        return "error", bad[0]
                    if all(v in DOKPLOY_OK for v in new.values()):
                        return "done", dep_id
                elif status == "running":
                    started = True
                elif status in DOKPLOY_OK | DOKPLOY_BAD:
                    steady += 1
                    if started or steady >= 3:
                        return status, dep_id
            self._sleep(self.settings.poll_seconds)
        return None, dep_id

    def _deploy_dokploy_locked(self, lane, policy: Policy, rec: dict, where: dict, desk) -> bool:
        """🚀 Desplegar con Dokploy: application.deploy (no redeploy) → sondeo de application.one hasta done/error → health_url.
        Dokploy reconstruye lo último de la rama que tiene configurada (la base): no hay worktree ni `up`."""
        tid, sha = rec["task_id"], rec["merge_sha"]
        if not (policy.dokploy_url and policy.dokploy_application_id and policy.dokploy_token_env):
            self._edit(desk, lane, rec, where, "blocked", "⛔ deploy no configurado (url, applicationId y variable del "
                                                          "token de Dokploy en lanes.yaml)")
            return True
        if not (os.environ.get(policy.dokploy_token_env) or self.env.get(policy.dokploy_token_env)):
            self._edit(desk, lane, rec, where, "blocked", f"⛔ no se despliega: falta la variable {policy.dokploy_token_env} "
                                                          "(token de Dokploy) en el entorno del runner")
            return True
        with self._lock:
            self._git(lane.repo, "fetch", lane.remote, f"+refs/heads/{lane.base}:refs/remotes/{lane.remote}/{lane.base}")
            tip = (self._git(lane.repo, "rev-parse", f"{lane.remote}/{lane.base}").stdout or "").strip()
            problem = self._tip_problem(lane, policy, tid, sha, tip)
        if problem:
            self._edit(desk, lane, rec, where, "blocked", status_line("⛔ no se despliega", problem, "despliega a mano"))
            return True
        extra = self._count(lane, f"{sha}..{tip}") if tip != sha else 0
        app = self._dokploy_app(policy)
        if app is None:
            self._edit(desk, lane, rec, where, "blocked", "⛔ no se despliega: Dokploy no responde (¿túnel o token?)")
            return False
        before = set(self._dokploy_deployments(app))
        self._edit(desk, lane, rec, where, "running", status_line(
            f"🚀 desplegando {tip[:7]}…", f"lo último de {lane.base}, {extra} commit(s) después de esta fusión"
            if extra else None))
        code, _ = self._dokploy(policy, "POST", "application.deploy", {"applicationId": policy.dokploy_application_id})
        if not 200 <= code < 300:
            log.warning("%s: dokploy deploy falló: HTTP %s", tid, code)
            self._edit(desk, lane, rec, where, "blocked", "⛔ deploy falló al pedirlo a Dokploy (detalle en el log)")
            self._comment(lane, tid, f"DEPLOY-FALLIDO {tip} · dokploy application.deploy HTTP {code}")
            self._deploy_fallo(tid, tip, f"dokploy application.deploy HTTP {code}")
            return True
        status, dep_id = self._poll_dokploy(policy, before)
        dep = dep_id or "dokploy"
        health = None
        if status == "done":
            for attempt in range(6):  # el contenedor nuevo tarda unos segundos en responder
                health = self._health(policy)
                if not policy.health_url or health == "OK":
                    break
                if attempt < 5:
                    self._sleep(self.settings.poll_seconds)
            if policy.health_url and health != "OK":
                status = f"health {health}"
        if status == "done":
            self._comment(lane, tid, f"DESPLEGADO {tip} · dokploy deployment {dep} done"
                          + (f" · health {health}" if health else "")
                          + (f" · incluye la fusión {sha[:12]} de esta tarea" if tip != sha else ""))
            self._save(tid, status="deployed", deployment=dep, deployed_sha=tip)
            also = self._mark_included(lane, tid, tip, dep, desk, via="dokploy deployment")
            self._edit(desk, lane, rec, where, "done", status_line(
                f"🚀 desplegado · {tip[:7]}", f"health {health}" if health else None,
                ("también PR " + ", ".join(f"#{n}" for n in also)) if also else None))
        else:
            why = "sin terminar en 15 min" if status is None else status
            self._comment(lane, tid, f"DEPLOY-FALLIDO {tip} · dokploy deployment {dep} {why}")
            self._deploy_fallo(tid, tip, f"dokploy deployment {dep} {why}")
            self._edit(desk, lane, rec, where, "blocked", status_line("⛔ deploy falló", why, "revisa Dokploy"))
        return True

    def _railway_incident(self, lane, sha: str, *, abrir: bool) -> None:
        """«Despliegue en cola por incidencia de Railway» (repo y SHA) al abrirse y otro aviso al normalizarse, por el
        mismo canal que las fichas (tema de Integración o DM de Oscar). Una sola vez por incidencia. Nunca lanza."""
        if self.dry_run or not self.notifier:
            return
        try:
            from .integrador_visibilidad import INCIDENCIA_FILE, IncidenciaRailway, load_ajustes
            inc = IncidenciaRailway(self.state_dir / INCIDENCIA_FILE, load_ajustes(), self._now)
            repo = repo_name(lane)
            text = inc.abrir(repo, sha) if abrir else inc.cerrar(repo)
            if not text:
                return
            target = self.settings.integration_telegram
            owner = str(getattr(self.desk, "owner_id", "") or "") if self.desk else ""
            chat, thread = target if target else (owner, None)
            if chat:
                self.notifier.send_to(str(chat), str(thread) if thread else None, text, html=False)
        except Exception as exc:
            log.warning("aviso de incidencia de Railway falló: %s", exc)

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

    def _deploy_fallo(self, tid: str, sha: str, motivo: str) -> None:
        """Deja constancia del último fallo de despliegue (lo lee /al_dia para ofrecer «🛠 Resolver»). Nunca lanza."""
        try:
            self._save(tid, deploy_fallo={"sha": sha, "motivo": truncate(motivo, 300)})
        except Exception as exc:
            log.warning("%s: no se pudo anotar el fallo de despliegue: %s", tid, exc)

    def _estados_de(self, lane) -> list[tuple[str, dict]]:
        """Fichas locales (estado del integrador) de este carril, de cualquier estado."""
        out = []
        for path in sorted(self.state_dir.glob("t_*.json")):
            if TID_RE.fullmatch(path.stem):
                st = self._state(path.stem)
                if st.get("lane") == lane.name:
                    out.append((path.stem, st))
        return out

    def _tip_problem(self, lane, policy: Policy, tid: str, sha: str, tip: str) -> str | None:
        problem = self._tip_problem_raw(lane, policy, tid, sha, tip)
        if problem:  # cada decisión de desplegar/no desplegar queda en runner.log (30-09) y en la tarjeta (01-10)
            log.warning("%s: despliegue de %s rechazado: %s", tid, lane.name, problem)
            self._comment(lane, tid, f"DEPLOY-RECHAZADO {tip or '?'} · {problem}")
        else:
            log.info("%s: despliegue de %s permitido sobre %s", tid, lane.name, (tip or "?")[:12])
        return problem

    def _tip_problem_raw(self, lane, policy: Policy, tid: str, sha: str, tip: str) -> str | None:
        """Por qué NO desplegar la punta de la base (o None). Antes el candado era "punta == esta fusión"; ahora se
        despliega lo último, así que se comprueba que no se cuele nada que tampoco se habría desplegado solo."""
        if not re.fullmatch(r"[0-9a-f]{40}", tip or ""):
            return f"no se pudo leer {lane.base}"
        if tip != sha and not self._is_ancestor(lane, sha, tip):
            return f"{lane.base} ya no contiene esta fusión"
        for other, st in self._merged_states(lane, tid):
            if pending_migration(st) and self._is_ancestor(lane, st["merge_sha"], tip):
                names = st.get("migration_files") or []
                return (f"{lane.base} incluye el PR #{st.get('pr', '?')} con migración o infraestructura pendiente"
                        + (f" ({', '.join(names[:5])})" if names else ""))
        if tip != sha:
            changed = (self._git(lane.repo, "diff", "--no-renames", "--name-only", sha, tip).stdout or "").split()
            risky = [f for f in changed if any(f.startswith(p) for p in policy.manual_migrations)
                     or any(f.startswith(p) or Path(f).name == p for p in policy.sensitive_paths)]
            if risky:
                return (f"después de esta fusión {lane.base} trae migración o infraestructura: "
                        + ", ".join(risky[:3]))
        return None

    def _migration_files(self, lane, policy: Policy, sha: str) -> list[str]:
        """Migraciones manuales (policy.manual_migrations) que trae la fusión `sha` (squash: su padre es la base)."""
        cp = self._git(lane.repo, "diff", "--no-renames", "--name-only", f"{sha}^", sha)
        files = (cp.stdout or "").split() if cp.returncode == 0 else []
        return [f for f in files if any(f.startswith(p) for p in policy.manual_migrations)]

    def _mark_included(self, lane, tid: str, tip: str, dep_id: str, desk, via: str = "railway deployment") -> list:
        """Tras un deploy correcto de `tip`: toda ficha fusionada cuyo merge_sha está en lo desplegado queda como
        desplegada (comentario, estado, avisos editados y su 🚀 Desplegar retirado). Devuelve sus números de PR."""
        done = []
        for other, st in self._merged_states(lane, tid):
            if pending_migration(st) or not self._is_ancestor(lane, st["merge_sha"], tip):
                continue
            self._comment(lane, other, f"DESPLEGADO {tip} · {via} {dep_id} SUCCESS · incluido en el "
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
        if policy.deploy_check == CHECK_GITHUB_DEPLOYMENTS and sha:
            return self._verify_github_deployments(lane, policy, rec, where, desk)
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
                served = str(data.get(policy.health_commit_key) or "").lower()
                # Sirve el merge o un commit POSTERIOR que lo contiene (otro PR se fusionó justo después): el deploy
                # sigue incluyendo este PR.
                if data.get("status") == "healthy" and re.fullmatch(r"[0-9a-f]{40}", served) \
                        and (served == sha.lower() or self._is_ancestor(lane, sha, served)):
                    later = served != sha.lower()
                    self._comment(lane, tid, f"DESPLEGADO {served if later else sha} · /health healthy con "
                                  + ("un commit posterior que incluye el merge" if later else "commit_sha del merge"))
                    self._save(tid, status="deployed", deployed_sha=served)
                    # Los PR anteriores fusionados e incluidos en ese commit también están desplegados; el resumen
                    # fijado se rehace en la siguiente pasada del integrador con el nuevo estado.
                    also = self._mark_included(lane, tid, served, "(health)", desk)
                    log.info("%s: MigraTeam /health confirma %s; desplegados también: %s", tid, served[:12], also)
                    self._edit(desk, lane, rec, where, "done", status_line(
                        f"🚀 desplegado · {served[:7]}", "health OK",
                        ("también PR " + ", ".join(f"#{n}" for n in also)) if also else None))
                    return True
                last = f"HTTP {code}" if code != 200 else f"sirve {str(data.get(policy.health_commit_key))[:7]}"
            except Exception as exc:
                last = type(exc).__name__
            self._sleep(self.settings.poll_seconds)
        self._comment(lane, tid, f"DEPLOY-SIN-CONFIRMAR {sha} · 15 min sin /health con el commit nuevo ({last})")
        self._edit(desk, lane, rec, where, "blocked", status_line(
            "⛔ deploy sin confirmar en 15 min", f"/health {last}", "revisa Railway y Vercel"))
        return True

    def _github_deploy_state(self, slug: str, sha: str) -> str:
        """Estado más reciente de los GitHub Deployments de ESTE commit (solo lectura, `gh api`): success | failure |
        error | inactive | queued | in_progress | pending | none (aún no hay deployment) | unknown (gh falló)."""
        cp = self._gh("api", f"repos/{slug}/deployments?sha={sha}&per_page=10")
        try:
            deps = json.loads(cp.stdout or "[]") if cp.returncode == 0 else None
        except json.JSONDecodeError:
            deps = None
        if not isinstance(deps, list):
            return "unknown"
        states = []
        for dep in deps:
            if str(dep.get("sha") or sha).lower() != sha.lower() or not isinstance(dep.get("id"), int):
                continue
            st = self._gh("api", f"repos/{slug}/deployments/{dep['id']}/statuses?per_page=1")
            try:
                items = json.loads(st.stdout or "[]") if st.returncode == 0 else None
            except json.JSONDecodeError:
                items = None
            if not isinstance(items, list):
                return "unknown"
            states.append(str(items[0].get("state") or "pending").lower() if items else "pending")
        if not states:
            return "none"
        for wanted in (GH_DEPLOY_OK, GH_DEPLOY_BAD):  # un success del commit manda; si no, el fallo
            hit = next((x for x in states if x in wanted), None)
            if hit:
                return hit
        return states[0]

    def _verify_github_deployments(self, lane, policy: Policy, rec: dict, where: dict, desk) -> bool:
        """Scraper: Railway despliega desde la rama base. Se sondea el estado de GitHub Deployments del commit
        fusionado hasta `success`; `failure`/`error` = aviso de fallo; el plazo agotado = "sin confirmar" (no se
        reintenta nada: solo lectura)."""
        tid, sha = rec["task_id"], rec["merge_sha"]
        slug = (self.links.repo_slug(lane) if self.links else None) or getattr(lane, "github", "") or ""
        if not slug:
            self._edit(desk, lane, rec, where, "done", status_line(
                f"🚀 fusionado · {sha[:7]}", "compruébalo en Railway y GitHub Deployments"))
            return True
        deadline = self._clock() + self.settings.deploy_timeout_seconds
        last = "sin deployment"
        while self._clock() < deadline:
            last = self._github_deploy_state(slug, sha)
            if last in GH_DEPLOY_OK:
                self._comment(lane, tid, f"DESPLEGADO {sha} · GitHub Deployments success")
                self._save(tid, status="deployed", deployed_sha=sha)
                self._edit(desk, lane, rec, where, "done", status_line(f"🚀 desplegado · {sha[:7]}",
                                                                       "GitHub Deployments: success"))
                return True
            if last in GH_DEPLOY_BAD:
                self._comment(lane, tid, f"DEPLOY-FALLIDO {sha} · GitHub Deployments {last}")
                self._deploy_fallo(tid, sha, f"GitHub Deployments {last}")
                self._edit(desk, lane, rec, where, "blocked", status_line(
                    "⛔ deploy falló", f"GitHub Deployments: {last}", "revisa Railway"))
                return True
            self._sleep(self.settings.poll_seconds)
        self._comment(lane, tid, f"DEPLOY-SIN-CONFIRMAR {sha} · plazo agotado sin success en GitHub Deployments ({last})")
        self._edit(desk, lane, rec, where, "blocked", status_line(
            "⛔ deploy sin confirmar", f"GitHub Deployments: {last}", "revisa Railway"))
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


def _http_json(method: str, url: str, headers: dict, body: dict | None = None,
               timeout: float = 20) -> tuple[int, dict | list | None]:
    """Petición JSON (API de Dokploy). Sin redirecciones; (código, JSON o None). Solo https."""
    if not _api_url_ok(url):
        raise ValueError("la URL debe ser https (o http://localhost)")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": "agent-lanes-integrator", **({"Content-Type": "application/json"} if data else {}), **headers})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout) as resp:
            raw = resp.read(2_000_000).decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, None
    try:
        return code, json.loads(raw) if raw.strip() else None
    except ValueError:
        return code, None


def build_integrator(env: dict, lanes: dict, **kw) -> Integrator | None:
    """Integrator del runner, o None si INTEGRATOR_ENABLED no está activo."""
    settings = load_integrator_settings(env=env)
    if not settings.enabled:
        return None
    kw.setdefault("env", env)
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


def reset_cli(tid: str, *, integrator: "Integrator | None" = None, out: Callable[[str], None] = print) -> int:
    """`lanes.py integrator reset <t_id>`: reevalúa un PR failed/pr_problem/sent_back en la próxima vuelta."""
    ok, msg = (integrator or _cli_integrator()).reset_pr(tid)
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
    ap.add_argument("--batch", help="prueba en seco de un lote con estos PR, en orden: 12,15,18 (requiere --lane)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    lanes = load_lanes()
    settings = load_integrator_settings(env=load_env())
    cache: dict[str, HermesCLI] = {}
    links = LinkBuilder(load_telegram_settings()["kanban_base_url"])
    integ = Integrator(settings, lanes, hermes_for=lambda b: cache.setdefault(b, HermesCLI(b)), links=links,
                       dry_run=True)
    if args.batch:
        lane, policy = lanes[args.lane], settings.policies.get(args.lane) or Policy(lane=args.lane)
        return integ.batches.dry_run(lane, policy, [int(n) for n in args.batch.split(",") if n.strip()])
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
