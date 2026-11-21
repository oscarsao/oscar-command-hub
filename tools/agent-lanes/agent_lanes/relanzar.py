"""Relanzado automático y cauteloso de tarjetas bloqueadas por fallos transitorios (21-11-2026).

Hasta ahora la Integradora relanzaba a mano, de una en una, las tarjetas bloqueadas por fallos transitorios (p. ej.
«verificación mecánica fallida: test_cmd falló»), respetando el tope de tarjetas en running. Aquí lo hace el runner,
SIEMPRE apagado por defecto (`relanzar.yaml`, `activo: false`): relanzar solo puede provocar bucles o gasto, así que
lo enciende la Integradora u Oscar.

Una pasada (`pasada`) relanza como máximo `max_por_pasada` tarjetas (por defecto 1) y SOLO si se cumplen TODAS:

  a) el total de tarjetas en running de todos los tableros es menor que `tope_running` (3);
  b) el último bloqueo de la tarjeta es de kind `transient` (nunca `needs_input`, dependencias ni nada que decida una
     persona);
  c) el motivo no contiene ninguna marca de `excluir_motivos` (sin mayúsculas ni tildes: «CONFIGURACIÓN»,
     «LIMITACION-GUARD», «producción», «migración»);
  d) está asignada a un carril de código (`kind: implement`) de lanes.yaml: no a `oscar`, `integrador`, ops ni review;
  e) no está en triage;
  f) no se ha relanzado ya `max_auto_por_tarjeta` veces (contador en `.state/relanzar.json`).

Entre las candidatas se elige la bloqueada MÁS ANTIGUA. Al relanzar: `unblock`, comentario «RELANZADA
AUTOMÁTICAMENTE (n/max): <motivo recortado>» y una línea de log. Ante cualquier fallo del kanban (listar, leer, unblock)
la pasada se detiene y el `RelanzarTicker` lo captura y espera `PAUSA_ERROR_SEGUNDOS` antes de volver a intentarlo:
nunca hay reintentos en bucle apretado. Cuando `activo` es false no se lee el kanban en absoluto.
"""
from __future__ import annotations

import json
import logging
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from . import proc as _proc
from .config import ROOT

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "relanzar.yaml"
STATE_PATH = ROOT / ".state" / "relanzar.json"
AUTHOR = "relanzar"
EXCLUIR_DEFECTO = ("CONFIGURACIÓN", "LIMITACION-GUARD", "producción", "migración")
MOTIVO_MAX = 160
MAX_LECTURAS = 40          # tarjetas bloqueadas leídas (show) por pasada como máximo
PAUSA_ERROR_SEGUNDOS = 300


@dataclass(frozen=True)
class Ajustes:
    activo: bool = False
    tope_running: int = 3
    max_por_pasada: int = 1
    max_auto_por_tarjeta: int = 1
    excluir_motivos: tuple[str, ...] = EXCLUIR_DEFECTO


def _int(raw, default: int, lo: int) -> int:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= lo else default


def load_ajustes(path: Path | str | None = None) -> Ajustes:
    """Ajustes desde relanzar.yaml; lo que falte o no sea válido queda en su valor por defecto (activo: false)."""
    d = Ajustes()
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return d
    if not isinstance(raw, dict):
        return d
    marcas = raw.get("excluir_motivos")
    ok = isinstance(marcas, list) and marcas and all(isinstance(m, str) and m.strip() for m in marcas)
    return Ajustes(
        activo=raw.get("activo") is True,
        tope_running=_int(raw.get("tope_running"), d.tope_running, 1),
        max_por_pasada=_int(raw.get("max_por_pasada"), d.max_por_pasada, 1),
        max_auto_por_tarjeta=_int(raw.get("max_auto_por_tarjeta"), d.max_auto_por_tarjeta, 1),
        excluir_motivos=tuple(m.strip() for m in marcas) if ok else d.excluir_motivos)


def _norm(text) -> str:
    plain = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(plain.lower().split())


def ultimo_bloqueo(show: dict | None) -> tuple[str, str, float] | None:
    """(kind, motivo, fecha) del último evento `blocked`; None si no hay o no se entiende."""
    show = show or {}
    block = next((e for e in reversed(show.get("events") or []) if e.get("kind") == "blocked"), None)
    if not block:
        return None
    payload = block.get("payload") if isinstance(block.get("payload"), dict) else {}
    try:
        when = float(block.get("created_at"))
    except (TypeError, ValueError):
        when = 0.0
    return str(payload.get("kind") or ""), str(payload.get("reason") or ""), when


def _cargar(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _guardar(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    tmp.replace(path)


def rama_con_trabajo(lane, tid: str) -> int | None:
    """Commits de origin/lane/<id> sobre la base del carril, o None si la rama NO existe en el remoto (o git falla).
    Sirve para no relanzar una tarjeta que ya tiene trabajo hecho: se integra, no se rehace (Integradora, 21-11)."""
    repo = getattr(lane, "repo", None)
    if not repo:
        return None
    rama = "lane/" + tid
    remote = getattr(lane, "remote", "origin")
    base = getattr(lane, "base", "main")

    def git(*args: str):
        return _proc.run_retrying(["git", "-C", str(repo), *args], label="git " + args[0], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")

    try:
        if git("fetch", "-q", remote, rama).returncode != 0:
            return None  # la rama no existe en el remoto (o no hay red): no se puede decir que tenga trabajo
        cp = git("rev-list", "--count", f"{remote}/{base}..{remote}/{rama}")
        return int(cp.stdout.strip()) if cp.returncode == 0 else None
    except Exception as exc:  # git no responde: falla del lado de NO relanzar solo si la rama se sabe existente
        log.warning("relanzar: no se pudo mirar la rama de %s: %s", tid, exc)
        return None


def _recortar(text: str) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= MOTIVO_MAX else one[: MOTIVO_MAX - 1] + "…"


def pasada(cfg: Ajustes, lanes: dict, hermes_for: Callable, state_path: Path | str | None = None,
           rama_de: Callable | None = None) -> list[str]:
    """Una pasada de relanzado; devuelve los ids relanzados. Lanza solo ante un fallo del kanban (lo captura el ticker)."""
    if not cfg.activo:
        return []
    state_path = Path(state_path or STATE_PATH)
    codigo = {n for n, l in lanes.items() if getattr(l, "kind", "implement") == "implement"}
    boards = sorted({l.board for l in lanes.values() if l.board})
    running: set[str] = set()
    bloqueadas: list[tuple[str, dict]] = []
    for board in boards:
        h = hermes_for(board)
        running |= {t["id"] for t in h.list_status_all("running")}
        bloqueadas += [(board, t) for t in h.list_status_all("blocked")]
    hueco = cfg.tope_running - len(running)
    if hueco <= 0:
        return []
    estado = _cargar(state_path)
    contador = estado.get("relanzadas") if isinstance(estado.get("relanzadas"), dict) else {}
    marcas = [_norm(m) for m in cfg.excluir_motivos]
    candidatas = []
    for board, t in bloqueadas:
        tid = t.get("id")
        if not tid or t.get("status", "blocked") != "blocked" or t.get("assignee") not in codigo:
            continue
        if _int(contador.get(tid), 0, 0) >= cfg.max_auto_por_tarjeta:
            continue
        candidatas.append((board, t))
    leidas: list[tuple[float, str, str, str]] = []
    for board, t in candidatas[:MAX_LECTURAS]:
        show = hermes_for(board).show(t["id"]) or {}
        if (show.get("task") or {}).get("status", "blocked") != "blocked":
            continue  # triage u otro estado: no se toca
        found = ultimo_bloqueo(show)
        if not found or found[0] != "transient":
            continue
        _kind, motivo, when = found
        if any(m in _norm(motivo) for m in marcas):
            continue
        leidas.append((when or float(t.get("created_at") or 0), t["id"], board, motivo))
    relanzadas: list[str] = []
    rama_de = rama_de or rama_con_trabajo
    con_rama = estado.get("con_rama") if isinstance(estado.get("con_rama"), dict) else {}
    for _when, tid, board, motivo in sorted(leidas):
        if len(relanzadas) >= min(cfg.max_por_pasada, hueco):
            break
        asignada = next((t.get("assignee") for b, t in candidatas if t.get("id") == tid), None)
        commits = rama_de(lanes.get(asignada), tid) if lanes.get(asignada) is not None else None
        if commits is not None:
            # Ya tiene rama lane/<id>: o tiene trabajo sin integrar o ya esta integrada. NUNCA se relanza: se integra.
            if tid not in con_rama:
                con_rama[tid] = commits
                estado["con_rama"] = con_rama
                _guardar(state_path, estado)
                try:
                    hermes_for(board).comment(
                        tid, f"NO RELANZADA AUTOMÁTICAMENTE: ya tiene la rama lane/{tid} ({commits} commits fuera de la base; "
                             "0 = ya integrada). Se integra o se cierra, no se rehace. Aviso para la Integradora.", author=AUTHOR)
                except Exception as exc:
                    log.warning("relanzar: comentario de %s no escrito: %s", tid, exc)
            log.info("relanzar: %s omitida: ya tiene rama lane/%s (%s commits)", tid, tid, commits)
            continue
        n = _int(contador.get(tid), 0, 0) + 1
        contador[tid] = n  # antes del unblock: si algo falla a medias, no hay bucle
        estado["relanzadas"] = contador
        _guardar(state_path, estado)
        h = hermes_for(board)
        if not h.unblock(tid):
            contador[tid] = n - 1
            _guardar(state_path, estado)
            raise RuntimeError(f"unblock de {tid} falló")
        relanzadas.append(tid)
        log.info("relanzar: %s relanzada automáticamente (%d/%d): %s", tid, n, cfg.max_auto_por_tarjeta, _recortar(motivo))
        try:
            h.comment(tid, f"RELANZADA AUTOMÁTICAMENTE ({n}/{cfg.max_auto_por_tarjeta}): {_recortar(motivo)}", author=AUTHOR)
        except Exception as exc:
            log.warning("relanzar: comentario de %s no escrito: %s", tid, exc)
    return relanzadas


class RelanzarTicker:
    """Bucle del runner: una pasada por ciclo. Apagado (relanzar.yaml) no hace nada; un fallo no tumba el runner y
    pausa el ticker `PAUSA_ERROR_SEGUNDOS` (sin reintentos en bucle apretado)."""

    def __init__(self, lanes: dict, hermes_for: Callable, settings_path: Path | str | None = None,
                 state_path: Path | str | None = None, clock: Callable[[], float] = time.monotonic):
        self.lanes, self.hermes_for = lanes, hermes_for
        self.settings_path, self.state_path, self.clock = settings_path, state_path, clock
        self._pausa_hasta = 0.0

    def tick(self) -> list[str]:
        cfg = load_ajustes(self.settings_path)
        if not cfg.activo or self.clock() < self._pausa_hasta:
            return []
        try:
            return pasada(cfg, self.lanes, self.hermes_for, self.state_path)
        except Exception as exc:
            self._pausa_hasta = self.clock() + PAUSA_ERROR_SEGUNDOS
            log.warning("relanzar falló (se reintenta en %d s): %s", PAUSA_ERROR_SEGUNDOS, exc)
            return []
