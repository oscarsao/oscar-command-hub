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
AUTOMÁTICAMENTE (n/max): <motivo recortado>» y una línea de log. Ante un fallo del kanban (listar, leer) la pasada se
detiene; un `unblock` que devuelve False se registra y se sigue con la siguiente y el `RelanzarTicker` lo captura y espera `PAUSA_ERROR_SEGUNDOS` antes de volver a intentarlo:
nunca hay reintentos en bucle apretado. Cuando `activo` es false no se lee el kanban en absoluto.
"""
from __future__ import annotations

import json
import logging
import re
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
MAX_RAMAS = 5              # comprobaciones de rama (git) por pasada como máximo
CACHE_SEGUNDOS = 600       # caducidad de la decisión cacheada por tarjeta (descartada / con rama)
CACHE_DESCONOCIDA_SEGUNDOS = 120
SIN_RAMA = "sin_rama"      # la rama lane/<id> NO existe en el remoto (seguro)
DESCONOCIDA = "desconocida"  # git no pudo decirlo (red, timeout...): NO se relanza
_RELANZADA_RE = re.compile(r"^RELANZADA AUTOM[ÁA]TICAMENTE \(\d+/\d+\)")
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


class EstadoCorrupto(Exception):
    """`.state/relanzar.json` existe pero no se puede leer o no es un dict: no se resetea, se desactiva la pasada."""


def _cargar(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EstadoCorrupto(str(exc)) from exc
    if not isinstance(data, dict):
        raise EstadoCorrupto("el estado no es un objeto JSON")
    return data


def _guardar(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    tmp.replace(path)


def rama_con_trabajo(lane, tid: str):
    """Estado de origin/lane/<id>: nº de commits sobre la base del carril (int, 0 = ya integrada), `SIN_RAMA` si NO existe
    en el remoto o `DESCONOCIDA` si git no pudo decirlo (sin repo, red, timeout, rev-list fallido). «No existe» solo si
    `git ls-remote --exit-code` devuelve 2 (o 128 con «couldn't find remote ref»). Cualquier otro fallo es DESCONOCIDA y el
    llamador NO relanza: se integra, no se rehace (Integradora, 21-11)."""
    repo = getattr(lane, "repo", None)
    if not repo:
        return DESCONOCIDA
    rama = "lane/" + tid
    remote = getattr(lane, "remote", "origin")
    base = getattr(lane, "base", "main")

    def git(*args: str):
        return _proc.run_retrying(["git", "-C", str(repo), *args], label="git " + args[0], capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")

    try:
        ls = git("ls-remote", "--exit-code", remote, f"refs/heads/{rama}")
        if ls.returncode == 2 or (ls.returncode == 128 and "couldn't find remote ref" in (ls.stderr or "").lower()):
            return SIN_RAMA
        if ls.returncode != 0:
            return DESCONOCIDA
        if git("fetch", "-q", remote, rama).returncode != 0:
            return DESCONOCIDA
        cp = git("rev-list", "--count", f"{remote}/{base}..{remote}/{rama}")
        return int(cp.stdout.strip()) if cp.returncode == 0 else DESCONOCIDA
    except Exception as exc:  # git no responde: desconocido, nunca «no existe»
        log.warning("relanzar: no se pudo mirar la rama de %s: %s", tid, exc)
        return DESCONOCIDA


def _recortar(text: str) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= MOTIVO_MAX else one[: MOTIVO_MAX - 1] + "…"


def _relanzos_escritos(show: dict | None) -> int:
    """Comentarios «RELANZADA AUTOMÁTICAMENTE (n/max)» ya escritos en la tarjeta (no cuenta «NO RELANZADA…»)."""
    return sum(1 for c in (show or {}).get("comments") or ()
               if isinstance(c, dict) and _RELANZADA_RE.match(str(c.get("body") or "").strip()))


def _cacheada(decisiones: dict, tid: str, ahora: float) -> bool:
    d = decisiones.get(tid)
    try:
        return isinstance(d, dict) and ahora - float(d["t"]) < float(d["ttl"])
    except (KeyError, TypeError, ValueError):
        return False


def _cachear(decisiones: dict, tid: str, motivo: str, ahora: float, ttl: float = CACHE_SEGUNDOS) -> None:
    decisiones[tid] = {"d": motivo, "t": ahora, "ttl": ttl}


def pasada(cfg: Ajustes, lanes: dict, hermes_for: Callable, state_path: Path | str | None = None,
           rama_de: Callable | None = None, ahora: Callable[[], float] = time.time) -> list[str]:
    """Una pasada de relanzado; devuelve los ids relanzados. Lanza solo ante un fallo del kanban (lo captura el ticker)."""
    if not cfg.activo:
        return []
    state_path = Path(state_path or STATE_PATH)
    try:
        estado = _cargar(state_path)
    except EstadoCorrupto as exc:
        log.warning("relanzar: %s ilegible (%s): pasada desactivada, NO se resetea el estado; revísalo a mano",
                    state_path, exc)
        return []
    codigo = {n for n, l in lanes.items() if getattr(l, "kind", "implement") == "implement"}
    boards = sorted({l.board for l in lanes.values() if l.board})
    ocupadas: set[str] = set()
    bloqueadas: list[tuple[str, dict]] = []
    for board in boards:
        h = hermes_for(board)
        ocupadas |= {t["id"] for t in h.list_status_all("running")}
        # una tarjeta recién desbloqueada sigue en ready hasta que el planificador la reclama: también ocupa hueco
        ocupadas |= {t["id"] for t in h.list_status_all("ready") if t.get("assignee") in codigo}
        for t in h.list_status_all("blocked"):
            if t.get("id") not in {b[1].get("id") for b in bloqueadas}:  # una tarjeta se lee y cuenta una sola vez
                bloqueadas.append((board, t))
    hueco = cfg.tope_running - len(ocupadas)
    if hueco <= 0:
        return []
    now = ahora()
    contador = estado.get("relanzadas") if isinstance(estado.get("relanzadas"), dict) else {}
    decisiones = estado.get("decisiones") if isinstance(estado.get("decisiones"), dict) else {}
    con_rama = estado.get("con_rama") if isinstance(estado.get("con_rama"), dict) else {}
    marcas = [_norm(m) for m in cfg.excluir_motivos]
    candidatas: list[tuple[str, dict]] = []
    for board, t in bloqueadas:
        tid = t.get("id")
        if not tid or t.get("status", "blocked") != "blocked" or t.get("assignee") not in codigo:
            continue
        if _int(contador.get(tid), 0, 0) >= cfg.max_auto_por_tarjeta or _cacheada(decisiones, tid, now):
            continue
        candidatas.append((board, t))
    candidatas.sort(key=lambda bt: float(bt[1].get("created_at") or 0))
    asignada = {t["id"]: t.get("assignee") for _b, t in candidatas}
    leidas: list[tuple[float, str, str, str]] = []
    for board, t in candidatas[:MAX_LECTURAS]:
        tid = t["id"]
        show = hermes_for(board).show(tid) or {}
        if (show.get("task") or {}).get("status", "blocked") != "blocked":
            _cachear(decisiones, tid, "no_blocked", now)  # triage u otro estado: no se toca
            continue
        escritos = _relanzos_escritos(show)
        if escritos > _int(contador.get(tid), 0, 0):
            contador[tid] = escritos  # el estado se perdió o se quedó corto: manda lo escrito en la tarjeta
            estado["relanzadas"] = contador
        if escritos >= cfg.max_auto_por_tarjeta:
            _cachear(decisiones, tid, "limite_relanzos", now)
            continue
        found = ultimo_bloqueo(show)
        if not found or found[0] != "transient":
            _cachear(decisiones, tid, "no_transient", now)
            continue
        _kind, motivo, when = found
        if any(m in _norm(motivo) for m in marcas):
            _cachear(decisiones, tid, "motivo_excluido", now)
            continue
        leidas.append((when or float(t.get("created_at") or 0), tid, board, motivo))
    relanzadas: list[str] = []
    rama_de = rama_de or rama_con_trabajo
    ramas_vistas = 0
    for _when, tid, board, motivo in sorted(leidas):
        if len(relanzadas) >= min(cfg.max_por_pasada, hueco) or ramas_vistas >= MAX_RAMAS:
            break
        lane_ = lanes.get(asignada.get(tid))
        ramas_vistas += 1
        res = rama_de(lane_, tid) if lane_ is not None else DESCONOCIDA
        if res != SIN_RAMA and not (isinstance(res, int) and not isinstance(res, bool)):
            # DESCONOCIDA (o algo que no se entiende): no se sabe si hay rama; NO se relanza ni se comenta
            _cachear(decisiones, tid, "rama_desconocida", now, CACHE_DESCONOCIDA_SEGUNDOS)
            log.warning("relanzar: %s omitida: no se pudo comprobar la rama lane/%s", tid, tid)
            continue
        if res != SIN_RAMA:
            # Ya tiene rama lane/<id>: o tiene trabajo sin integrar o ya esta integrada. NUNCA se relanza: se integra.
            _cachear(decisiones, tid, "con_rama", now)
            if tid not in con_rama:
                con_rama[tid] = res
                estado["con_rama"] = con_rama
                _guardar(state_path, estado)
                try:
                    hermes_for(board).comment(
                        tid, f"NO RELANZADA AUTOMÁTICAMENTE: ya tiene la rama lane/{tid} ({res} commits fuera de la base; "
                             "0 = ya integrada). Se integra o se cierra, no se rehace. Aviso para la Integradora.", author=AUTHOR)
                except Exception as exc:
                    log.warning("relanzar: comentario de %s no escrito: %s", tid, exc)
            log.info("relanzar: %s omitida: ya tiene rama lane/%s (%s commits)", tid, tid, res)
            continue
        h = hermes_for(board)
        fresca = h.show(tid) or {}  # releer justo antes: otro agente puede haberla movido ya
        if (fresca.get("task") or {}).get("status", "blocked") != "blocked":
            _cachear(decisiones, tid, "no_blocked", now)
            log.info("relanzar: %s ya no está blocked: no se toca", tid)
            continue
        if _relanzos_escritos(fresca) >= cfg.max_auto_por_tarjeta:
            _cachear(decisiones, tid, "limite_relanzos", now)
            continue
        previo = _int(contador.get(tid), 0, 0)
        n = previo + 1
        contador[tid] = n  # antes del unblock: si algo falla a medias, no hay bucle
        estado["relanzadas"] = contador
        _guardar(state_path, estado)
        if not h.unblock(tid):
            contador[tid] = previo
            _cachear(decisiones, tid, "unblock_fallido", now, CACHE_DESCONOCIDA_SEGUNDOS)
            _guardar(state_path, estado)
            log.warning("relanzar: unblock de %s falló: se sigue con la siguiente", tid)
            continue
        decisiones.pop(tid, None)
        relanzadas.append(tid)
        log.info("relanzar: %s relanzada automáticamente (%d/%d): %s", tid, n, cfg.max_auto_por_tarjeta, _recortar(motivo))
        try:
            h.comment(tid, f"RELANZADA AUTOMÁTICAMENTE ({n}/{cfg.max_auto_por_tarjeta}): {_recortar(motivo)}", author=AUTHOR)
        except Exception as exc:
            log.warning("relanzar: comentario de %s no escrito: %s", tid, exc)
    estado["decisiones"] = {k: v for k, v in decisiones.items() if _cacheada(decisiones, k, now)}
    _guardar(state_path, estado)
    return relanzadas


class RelanzarTicker:
    """Bucle del runner: una pasada por ciclo. Apagado (relanzar.yaml) no hace nada; un fallo no tumba el runner y
    pausa el ticker `PAUSA_ERROR_SEGUNDOS` (sin reintentos en bucle apretado)."""

    def __init__(self, lanes: dict, hermes_for: Callable, settings_path: Path | str | None = None,
                 state_path: Path | str | None = None, clock: Callable[[], float] = time.monotonic,
                 rama_de: Callable | None = None):
        self.lanes, self.hermes_for = lanes, hermes_for
        self.settings_path, self.state_path, self.clock, self.rama_de = settings_path, state_path, clock, rama_de
        self._pausa_hasta = 0.0

    def tick(self) -> list[str]:
        cfg = load_ajustes(self.settings_path)
        if not cfg.activo or self.clock() < self._pausa_hasta:
            return []
        try:
            return pasada(cfg, self.lanes, self.hermes_for, self.state_path, self.rama_de)
        except Exception as exc:
            self._pausa_hasta = self.clock() + PAUSA_ERROR_SEGUNDOS
            log.warning("relanzar falló (se reintenta en %d s): %s", PAUSA_ERROR_SEGUNDOS, exc)
            return []
