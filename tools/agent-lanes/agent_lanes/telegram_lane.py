"""Carril `claude-telegram` (22-10, decisión de Oscar): mejoras continuas de los bots de Telegram sin esperar a los carriles
generales. Tres piezas, todas por datos de lanes.yaml / clasificador.yaml:

- Repo por tarjeta: el carril trabaja en DOS repos (el hub y oscar-hq). La tarjeta lo elige con una línea `Repo: hub` o
  `Repo: oscar-hq` en el cuerpo, validada contra la lista blanca `repos:` del carril. Sin línea: se deduce de las rutas
  que cite (src/api/routes/telegram…, equipo_tareas → oscar-hq) y, si no, el repo por defecto del carril.
- Enrutado: un título `[Telegram] …` o una tarjeta que cite los bots/rutas de Telegram va a este carril en vez de a
  claude-hub / claude-oscarhq (`ruta`, en la creación; `reasignar_abiertas`, para las que ya existían y siguen en ready).
- La plaza reservada (fuera de max_workers) la aplica Service con `Lane.reserved_slots`.
"""
from __future__ import annotations

import copy
import dataclasses
import logging
import re
from pathlib import Path

import yaml

from .config import ROOT, Lane

log = logging.getLogger("agent_lanes")

_AVISADAS: set[str] = set()  # tarjetas de otro tablero ya avisadas en el log (en memoria del proceso)
LANE = "claude-hub"  # desde 23-10 «Hub y Telegram» es un solo carril; `claude-telegram` es su alias
ALIASES = ("claude-telegram",)  # nombres antiguos de LANE (lanes.yaml `aliases:`)
ORIGENES = ("claude-oscarhq", "claude-telegram")  # de dónde se pasan tarjetas (oscarhq: solo Telegram; alias: todas)
REPO_LINE = re.compile(r"^\s*Repo:\s*(\S+)\s*$", re.M | re.I)
SETTINGS_PATH = ROOT / "clasificador.yaml"
# Claves de la entrada `repos.<alias>` que sustituyen a las del carril.
REPO_KEYS = ("repo", "github", "base", "test_cmd", "role", "forbidden_paths", "remote")

DEFAULTS: dict = {
    "titulo": r"^\s*\[\s*telegram\s*\]",
    # Citas que delatan que la tarjeta toca los bots (regex, sin distinguir mayúsculas).
    "patrones": [r"@pildora_carriles_bot", r"@PildoraD_BOT", r"\btools/telegram/", r"\bsrc/api/routes/telegram\w*",
                 r"\bsrc/services/telegram_\w*", r"\bequipo_tareas\b"],
    # De los anteriores, los que son del repo de oscar-hq (el resto, hub).
    "patrones_oscar_hq": [r"@PildoraD_BOT", r"\bsrc/api/routes/telegram\w*", r"\bsrc/services/telegram_\w*",
                          r"\bequipo_tareas\b"],
}


def _config(cfg: dict | None = None) -> dict:
    if cfg is not None:
        return cfg
    out = copy.deepcopy(DEFAULTS)
    try:
        data = (yaml.safe_load(Path(SETTINGS_PATH).read_text(encoding="utf-8")) or {}).get("telegram") or {}
    except (OSError, yaml.YAMLError, AttributeError):
        return out
    for k, v in data.items():
        if k in out and type(v) is type(out[k]):
            out[k] = v
    return out


def _texto(title, body) -> str:
    return f"{title or ''}\n{body or ''}"


def es_telegram(title, body="", cfg: dict | None = None) -> bool:
    """¿Es una tarjeta de los bots de Telegram? Título `[Telegram] …` o cita de sus rutas/bots."""
    cfg = _config(cfg)
    if re.search(cfg["titulo"], title or "", re.I):
        return True
    return any(re.search(p, _texto(title, body), re.I) for p in cfg["patrones"])


def ruta(assignee: str, title, body="", cfg: dict | None = None) -> str:
    """Carril al que va una tarjeta nueva: claude-hub si es de Telegram y venía de oscarhq, o si se creó con el nombre
    antiguo `claude-telegram` (alias: así no espera a que reasignar_abiertas la mueva); si no, el mismo."""
    if assignee in ALIASES:
        return LANE
    return LANE if assignee in ORIGENES and es_telegram(title, body, cfg) else assignee


def board_destino() -> str:
    """Tablero donde trabaja claude-telegram (lanes.yaml); Hermes no mueve tarjetas entre tableros."""
    from .config import load_lanes
    try:
        return load_lanes()[LANE].board
    except Exception:
        return "default"


def con_repo(body: str, origen: str, title="") -> str:
    """Al reencaminar desde claude-oscarhq la tarjeta pasa a un carril de dos repos: se le escribe `Repo: oscar-hq` salvo
    que ya diga cuál."""
    if origen == "claude-oscarhq" and not REPO_LINE.search(body or ""):
        return f"{(body or '').rstrip()}\n\nRepo: oscar-hq\n"
    return body


def alias_de(lane: Lane, task: dict, cfg: dict | None = None) -> tuple[str | None, str | None]:
    """(alias, error). Línea `Repo:` > deducción por rutas de oscar-hq > primer alias de `repos:`. Un alias fuera de la
    lista blanca es un error (la tarjeta se bloquea con la pregunta). Sin `repos:` en el carril: (None, None)."""
    repos = lane.repos or {}
    if not repos:
        return None, None
    m = REPO_LINE.search((task or {}).get("body") or "")
    if m:
        alias = m.group(1).strip().lower()
        if alias in repos:
            return alias, None
        return None, f"Repo «{m.group(1)}» no está permitido en {lane.name}; opciones: {', '.join(repos)}"
    text = _texto((task or {}).get("title"), (task or {}).get("body"))
    cfg = _config(cfg)
    if "oscar-hq" in repos and any(re.search(p, text, re.I) for p in cfg["patrones_oscar_hq"]):
        return "oscar-hq", None
    return next(iter(repos)), None


def para_tarjeta(lane: Lane, task: dict, cfg: dict | None = None) -> tuple[Lane, str | None]:
    """El Lane efectivo de ESTA tarjeta (repo, base, test_cmd, role y límites del alias elegido) y el error de
    validación, si lo hay. Un carril sin `repos:` se devuelve tal cual."""
    alias, error = alias_de(lane, task, cfg)
    if error or alias is None:
        return lane, error
    entry = {k: (tuple(v) if k == "forbidden_paths" else v) for k, v in lane.repos[alias].items() if k in REPO_KEYS}
    return dataclasses.replace(lane, **entry, repos={}), None


def lane_de_tarjeta(lanes: dict, task: dict | None) -> Lane | None:
    """Lane de la tarjeta según su assignee, con el repo que ella elija (revisión, avisos con enlace al PR)."""
    lane = lanes.get((task or {}).get("assignee"))
    if lane is not None and lane.repos:
        lane, _ = para_tarjeta(lane, task or {})
    return lane


def reasignar_abiertas(hermes_for, lanes: dict, cfg: dict | None = None) -> list[str]:
    """Pasa a claude-telegram las tarjetas ready de Telegram que estén en claude-hub / claude-oscarhq DEL MISMO TABLERO
    que el carril (Hermes no mueve tarjetas entre tableros: las de otro tablero se devuelven en el log para Oscar).
    Las running no se tocan. Devuelve los ids reasignados."""
    dest = lanes.get(LANE)
    if dest is None:
        return []
    moved = []
    for origen in ORIGENES:
        src = lanes.get(origen)
        if src is None:
            continue
        hermes = hermes_for(src.board)
        for t in hermes.list_ready(origen):
            alias = origen in dest.aliases  # tarjetas abiertas con el nombre antiguo del carril: se pasan todas
            if not alias and not es_telegram(t.get("title"), t.get("body"), cfg):
                continue
            if src.board != dest.board:
                if t["id"] not in _AVISADAS:  # una vez por tarjeta, no en cada pasada
                    _AVISADAS.add(t["id"])
                    log.info("%s: tarjeta de Telegram en el tablero %s; %s trabaja en %s: no se mueve", t["id"],
                             src.board, LANE, dest.board)
                continue
            if hermes.reassign(t["id"], LANE, f"enrutado automático: {'alias del carril' if alias else 'tarjeta de Telegram'} (antes en {origen})"):
                moved.append(t["id"])
    return moved
