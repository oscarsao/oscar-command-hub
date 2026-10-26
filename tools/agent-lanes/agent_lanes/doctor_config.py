"""`lanes.py doctor-config` (F1-02, CF-14): qué funciones del hub están activas y cuáles apagadas, y por qué.

Solo lectura y solo NOMBRES: nunca imprime el valor de una variable (ni un trozo). Recorre lo que el código lee por nombre
(.env del hub, entorno del proceso y tools/monitor/.env) y lo que lanes.yaml declara (token_env, dokploy_*, destinos de
Telegram) y dice, por función, «activo» o «desactivado: falta X». Avisa si el repo del hub vuelve a ser público (F0-04).

El runner lo publica una vez al arrancar en el tema de Integración (`publicar_al_arrancar`); nunca tumba el arranque.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import ROOT, _load, load_env, ohq_api_url

log = logging.getLogger("agent_lanes")

GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"
MONITOR_ENV = ROOT.parent / "monitor" / ".env"
HUB_LANE = "claude-hub"
ACTIVO, APAGADO, AVISO = "activo", "desactivado", "aviso"


@dataclass(frozen=True)
class Fila:
    funcion: str
    estado: str           # activo | desactivado | aviso
    motivo: str = ""      # solo nombres de variables o claves, nunca valores


def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def entorno(env_files=None) -> dict[str, str]:
    """.env del hub + tools/monitor/.env + entorno del proceso (el proceso manda, como en prodwatch.read_env)."""
    out: dict[str, str] = {}
    for f in env_files if env_files is not None else (ROOT / ".env", MONITOR_ENV):
        out.update(load_env(Path(f)))
    out.update({k: v for k, v in os.environ.items() if v})
    return out


def _falta(*nombres: str, env: dict) -> list[str]:
    return [n for n in nombres if not env.get(n)]


def _fila(funcion: str, faltan: list[str], extra: str = "") -> Fila:
    if faltan:
        return Fila(funcion, APAGADO, "falta " + ", ".join(faltan) + (f" ({extra})" if extra else ""))
    return Fila(funcion, ACTIVO, extra)


def diagnostico(env: dict, data: dict, *, publico: bool | None = None, lanes_path: Path | None = None) -> list[Fila]:
    """Filas de doctor-config. `data` = lanes.yaml ya cargado; `publico` = ¿el repo del hub es público? (None = no se
    pudo saber)."""
    tg = data.get("telegram") or {}
    filas = [
        _fila("bot_carriles (botones y escucha)", _falta("CARRILES_BOT_TOKEN", env=env)),
        _fila("avisos por defecto (Gestión)", _falta("TELEGRAM_CHAT_ID", env=env)),
    ]
    base = ohq_api_url(env, lanes_path)
    faltan_ohq = ([] if base else ["OHQ_API_URL (ni ohq.api_url en lanes.yaml)"]) + _falta("OHQ_API_KEY", env=env)
    filas.append(_fila("sync_usuarios (suspensiones del panel)", faltan_ohq,
                       "" if env.get("OHQ_API_URL") or not base else "URL por defecto de lanes.yaml"))
    filas.append(_fila("vigilancia_equipo (👥 Equipo con la API de Oscar HQ)", faltan_ohq))
    filas.append(_fila("latido del PC a Oscar HQ (monitor, F1-06)", faltan_ohq))
    filas.append(_fila("latido del PC a Uptime Kuma (monitor, F1-06)", _falta("KUMA_PUSH_URL", env=env)))
    integ = (data.get("integrator") or {}).get("lanes") or {}
    if not _truthy(env.get("INTEGRATOR_ENABLED")):
        filas.append(Fila("integrador", APAGADO, "falta INTEGRATOR_ENABLED=true"))
    else:
        filas.append(Fila("integrador", ACTIVO, "carriles: " + (env.get("INTEGRATOR_LANES") and "INTEGRATOR_LANES"
                                                               or "todos")))
    for lane, p in sorted(integ.items()):
        if not isinstance(p, dict) or p.get("deploy") != "dokploy":
            continue
        faltan = ([] if p.get("dokploy_application_id") else [f"integrator.lanes.{lane}.dokploy_application_id"])
        token = str(p.get("dokploy_token_env") or "")
        faltan += _falta(token, env=env) if token else [f"integrator.lanes.{lane}.dokploy_token_env"]
        filas.append(_fila(f"deploy por botón ({lane}, Dokploy)", faltan))
    vercel = (data.get("monitor_alerts") or {}).get("vercel") or {}
    if vercel:
        token = str(vercel.get("token_env") or "")
        faltan = (_falta(token, env=env) if token else ["monitor_alerts.vercel.token_env"])
        faltan += [] if vercel.get("projects") else ["monitor_alerts.vercel.projects"]
        filas.append(_fila("prodwatch Vercel (despliegues de producción fallidos)", faltan))
    filas.append(_fila("vigilancia de Supabase (monitor)", _falta("SUPABASE_SERVICE_ROLE_KEY", env=env)))
    dest = (tg.get("destinos") or {}).get("decisiones") or {}
    filas.append(_fila("tema 🎯 Decisiones", [] if dest.get("chat") and dest.get("thread") else
                       ["telegram.destinos.decisiones"]))
    filas.append(_fila("tema de Integración", [] if (data.get("integration_telegram") or {}).get("chat") else
                       ["integration_telegram"]))
    filas.append(Fila("board_guard (recolocar tarjetas)", ACTIVO if _truthy(env.get("BOARD_GUARD_APPLY")) else AVISO,
                      "" if _truthy(env.get("BOARD_GUARD_APPLY")) else "solo avisa (BOARD_GUARD_APPLY no es true)"))
    from .telegram import allowlist, env_allowed_chats
    try:
        derivados = allowlist(env, lanes_path=lanes_path)
        filas.append(Fila("allowlist de Origen-Telegram", ACTIVO,
                          f"{len(derivados)} chats ({len(env_allowed_chats(env))} del .env; el resto de people.yaml "
                          "y lanes.yaml)"))
    except Exception as exc:  # people.yaml roto: la allowlist se queda en el .env (falla cerrado)
        filas.append(Fila("allowlist de Origen-Telegram", AVISO, f"solo TELEGRAM_ALLOWED_CHATS ({type(exc).__name__})"))
    if publico is True:
        filas.append(Fila("repo del hub", AVISO, "es PÚBLICO: debe ser privado (F0-04)"))
    elif publico is False:
        filas.append(Fila("repo del hub", ACTIVO, "privado"))
    else:
        filas.append(Fila("repo del hub", AVISO, "visibilidad desconocida (gh no respondió)"))
    return filas


def hub_slug(data: dict) -> str:
    return str(((data.get("lanes") or {}).get(HUB_LANE) or {}).get("github") or "")


def repo_publico(slug: str, run: Callable | None = None) -> bool | None:
    """True/False según `gh api repos/<slug>`; None si gh falla o no está (nunca lanza)."""
    if not slug:
        return None
    run = run or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=20, encoding="utf-8"))
    try:
        out = run([GH_EXE if Path(GH_EXE).exists() else "gh", "api", f"repos/{slug}"])
        if getattr(out, "returncode", 1) != 0:
            return None
        return not bool(json.loads(out.stdout or "{}").get("private"))
    except Exception:
        return None


ICONOS = {ACTIVO: "🟢", APAGADO: "⚪", AVISO: "🟠"}


def render(filas: list[Fila]) -> str:
    apagadas = sum(1 for f in filas if f.estado != ACTIVO)
    lines = [f"🩺 doctor-config · {len(filas) - apagadas} activas · {apagadas} desactivadas o con aviso"]
    for f in filas:
        lines.append(f"{ICONOS[f.estado]} {f.funcion}: {f.estado}" + (f" ({f.motivo})" if f.motivo else ""))
    return "\n".join(lines)


def informe(env: dict | None = None, data: dict | None = None, *, gh_run: Callable | None = None,
            lanes_path: Path | None = None) -> str:
    data = _load(lanes_path) if data is None else data
    env = entorno() if env is None else env
    return render(diagnostico(env, data, publico=repo_publico(hub_slug(data), gh_run), lanes_path=lanes_path))


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="lanes.py doctor-config",
                                 description="funciones activas/desactivadas del hub (solo nombres, nunca valores)")
    ap.add_argument("--sin-gh", action="store_true", help="no pregunta a GitHub si el repo es público")
    args = ap.parse_args(argv)
    gh = (lambda _args: None) if args.sin_gh else None
    print(informe(gh_run=gh))
    return 0


def publicar_al_arrancar(send: Callable[[str], object], *, informe_fn: Callable[[], str] = informe,
                         anotar: Callable | None = None) -> bool:
    """Una vez al arrancar el runner: el informe al tema de Integración y, con `anotar` (resumen diario INFO, F1-04),
    cada función apagada como «vigilancia apagada». Nunca lanza (un fallo solo deja log)."""
    try:
        text = informe_fn()
        log.info("doctor-config al arrancar:\n%s", text)
        send(text)
        for line in text.splitlines() if anotar else ():
            if line.startswith(ICONOS[APAGADO]):
                anotar("vigilancia", line.removeprefix(ICONOS[APAGADO]).strip(), line)
        return True
    except Exception as exc:
        log.warning("doctor-config al arrancar falló: %s", exc)
        return False


def resumen_vigilancias(filas: list[Fila]) -> str:
    """Una línea para el log del monitor/runner: «vigilancias: X activo · Y desactivado (falta Z) · …»."""
    return "vigilancias: " + " · ".join(f"{f.funcion.split(' (')[0]} {f.estado}" + (f" ({f.motivo})" if f.estado ==
                                        APAGADO and f.motivo else "") for f in filas)
