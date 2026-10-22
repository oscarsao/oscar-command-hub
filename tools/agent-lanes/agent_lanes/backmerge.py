"""Back-merge automático: una tarjeta (una sola vez) cuando la rama de producción lleva commits que la base del carril
no tiene, en lugar de repetir «develop va N commits por detrás de master» (retro 22-10). Configuración: backmerge.yaml.

El integrador llama a `check(i)` en cada pasada; aquí solo hay lectura de git (fetch + rev-list) y la creación de la
tarjeta con clave idempotente. La tarjeta la hace el carril como cualquier otra (rama lane/<id>, PR a la base).
"""
from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .config import ROOT

log = logging.getLogger("agent_lanes")

CONFIG_PATH = ROOT / "backmerge.yaml"


def load(path: Path | str | None = None) -> tuple[float, dict[str, dict]]:
    """(segundos entre comprobaciones, {carril: {desde, hacia}}). Fichero ausente o roto = apagado."""
    try:
        raw = yaml.safe_load(Path(path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return 1800.0, {}
    lanes = {}
    for name, spec in (raw.get("carriles") or {}).items():
        if isinstance(spec, dict) and spec.get("desde") and spec.get("hacia"):
            lanes[str(name)] = {"desde": str(spec["desde"]), "hacia": str(spec["hacia"])}
    try:
        every = max(float(raw.get("cada_minutos", 30)), 1.0) * 60
    except (TypeError, ValueError):
        every = 1800.0
    return every, lanes


def card_body(lane, desde: str, hacia: str, behind: int, sha: str) -> str:
    return (
        f"Origen: detectado por el integrador (retro semanal del 22-10, back-merge automático). Sin decisión nueva de Oscar.\n"
        f"Repo: {lane.repo}\nBase: {lane.remote}/{hacia}\n\n"
        f"`{lane.remote}/{desde}` tiene {behind} commit(s) que `{lane.remote}/{hacia}` no contiene (último: {sha[:9]}), "
        f"normalmente un hotfix que entró directo en `{desde}`.\n\n"
        f"Alcance: en tu rama lane/<id> (desde {lane.remote}/{hacia}) integra `{lane.remote}/{desde}` con un merge "
        f"normal (sin rebase ni --force), resuelve conflictos conservando los dos lados y deja el PR hacia `{hacia}`. "
        f"Este merge `{desde}` → `{hacia}` está pedido expresamente por esta tarjeta; es la única excepción a «sin merge».\n"
        f"Si hay un conflicto que no sabes resolver con seguridad, o toca migraciones de Alembic: `needs_input` con la "
        f"pregunta concreta, sin adivinar.\n"
        f"Criterio de hecho: `git rev-list --count {lane.remote}/{hacia}..{lane.remote}/{desde}` = 0 tras fusionar el PR; "
        f"tests del carril en verde.\nLímites: no tocar nada más; nada de push a `{hacia}` ni a `{desde}`."
    )


def check(i) -> list[str]:
    """Una comprobación por carril configurado. `i` es el Integrator (git, hermes_for, estado, reloj). Devuelve los ids
    de tarjeta creados. Nunca lanza por un carril: un fallo en uno no corta los demás."""
    every, lanes = load()
    created = []
    for name, spec in lanes.items():
        lane = i.lanes.get(name)
        if not lane or i.dry_run:
            continue
        try:
            card = _check_lane(i, name, lane, spec, every)
        except Exception as exc:  # noqa: BLE001
            log.warning("back-merge %s: %s", name, exc)
            continue
        if card:
            created.append(card)
    return created


def _check_lane(i, name: str, lane, spec: dict, every: float) -> str | None:
    tid, now = f"backmerge-{name}", i._now()
    st = i._state(tid)
    if now - float(st.get("checked_at") or 0) < every:
        return None
    i._save(tid, checked_at=now)
    desde, hacia = spec["desde"], spec["hacia"]
    if i._git(lane.repo, "fetch", "-q", lane.remote, desde, hacia).returncode:
        return None
    cp = i._git(lane.repo, "rev-list", "--count", f"{lane.remote}/{hacia}..{lane.remote}/{desde}")
    if cp.returncode:
        return None
    behind = int((cp.stdout or "0").strip() or 0)
    if not behind:  # al día: el próximo desfase abrirá un episodio nuevo
        if st.get("card"):
            i._save(tid, card=None, sha=None)
        return None
    if st.get("card"):  # ya hay tarjeta para este desfase: no se repite
        return None
    sha = (i._git(lane.repo, "rev-parse", f"{lane.remote}/{desde}").stdout or "").strip()
    title = f"[{lane.board or name}] Back-merge {desde} → {hacia} · {behind} commit(s) · merge"
    card = i.hermes_for(lane.board).create(
        title, card_body(lane, desde, hacia, behind, sha), lane.name, key=f"backmerge-{name}-{sha[:12]}",
        created_by="lane-integrator")
    i._save(tid, card=card, sha=sha)
    log.info("back-merge %s: tarjeta %s (%s tiene %d commit(s) que %s no)", name, card, desde, behind, hacia)
    return card
