"""Bandeja de decisiones más corta: ajustes (bandeja.yaml), desduplicado, agrupación por origen, auto-aplazado y veto.

Todo es lógica pura sobre las tarjetas de /decisiones ({board, task, since}); CommandCenter y DecisionDesk la usan.

- `Bandeja`: umbrales. Se leen de tools/agent-lanes/bandeja.yaml; con el fichero ausente o un valor inválido valen los
  defaults de aquí (nunca lanza).
- `dedupe`: misma tarjeta repetida (título sin etiquetas + origen) -> una sola, con las repetidas enlazadas.
- `rank`: más prioridad primero y, a igual prioridad, la más antigua.
- `split_low_priority`: tarjetas de prioridad baja con más de N días de espera (candidatas a aplazarse solas).
- `group_counts`: "MigraTeam 4 · Otros 3" para la línea "N más".
- `VetoBook`: plazo de veto de las decisiones reversibles (.state/card_veto.json).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from .config import ROOT
from .notices import BOARD_BRANDS

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "bandeja.yaml"
VETO_AVISO, VETO_EJECUTAR = "aviso", "ejecutar"
_TAG_RE = re.compile(r"\[[^\]]*\]|·\s*DECISIÓN\]", re.I)


@dataclass(frozen=True)
class Bandeja:
    top: int = 5
    dedupe: bool = True
    autosnooze_dias: float = 14
    autosnooze_aplazar_dias: float = 7
    prioridad_baja_max: int = -1
    veto_horas: float = 48
    veto_modo: str = VETO_AVISO

    @property
    def veto_activo(self) -> bool:
        return self.veto_horas > 0


def _num(raw, default, *, lo: float = 0, integer: bool = False):
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < lo:
        return default
    return int(raw) if integer else raw


def load(path: Path | str | None = None) -> Bandeja:
    """Ajustes desde bandeja.yaml; lo que falte o no sea válido queda en su valor por defecto."""
    d = Bandeja()
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.info("bandeja.yaml no leído (%s): valores por defecto", exc)
        return d
    if not isinstance(raw, dict):
        return d
    modo = str(raw.get("veto_modo", d.veto_modo)).strip().lower()
    return Bandeja(
        top=_num(raw.get("top"), d.top, lo=1, integer=True),
        dedupe=raw["dedupe"] if isinstance(raw.get("dedupe"), bool) else d.dedupe,
        autosnooze_dias=_num(raw.get("autosnooze_dias"), d.autosnooze_dias),
        autosnooze_aplazar_dias=_num(raw.get("autosnooze_aplazar_dias"), d.autosnooze_aplazar_dias, lo=1),
        prioridad_baja_max=_num(raw.get("prioridad_baja_max"), d.prioridad_baja_max, lo=-1000, integer=True),
        veto_horas=_num(raw.get("veto_horas"), d.veto_horas),
        veto_modo=modo if modo in (VETO_AVISO, VETO_EJECUTAR) else VETO_AVISO,  # ante la duda, solo avisar
    )


def origin(card: dict) -> str:
    """Origen de una tarjeta: la marca de su tablero (o "Otros")."""
    return BOARD_BRANDS.get(card.get("board") or "", "Otros")


def title_key(title: str | None) -> str:
    """Título sin etiquetas [DECISIÓN…], sin tildes ni signos, para detectar avisos repetidos."""
    folded = "".join(c for c in unicodedata.normalize("NFD", _TAG_RE.sub(" ", title or ""))
                     if unicodedata.category(c) != "Mn").lower()
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def priority(task: dict | None) -> int | None:
    p = (task or {}).get("priority")
    return p if isinstance(p, int) and not isinstance(p, bool) else None


def rank(cards: list[dict]) -> list[dict]:
    """Importancia: más prioridad primero; a igual prioridad, la que lleva más esperando."""
    return sorted(cards, key=lambda c: (-(priority(c["task"]) or 0), c.get("since") or 0, c["task"]["id"]))


def dedupe(cards: list[dict]) -> list[dict]:
    """Una tarjeta por (origen, título normalizado). Gana la primera de la lista (la más antigua si viene ordenada) y
    lleva en `duplicates` los ids de las repetidas. Sin título no hay nada que comparar."""
    kept: dict[tuple[str, str], dict] = {}
    out = []
    for c in cards:
        key = (origin(c), title_key(c["task"].get("title")))
        main = kept.get(key) if key[1] else None
        if main is None:
            c = {k: v for k, v in c.items() if k != "duplicates"}
            if key[1]:
                kept[key] = c
            out.append(c)
        else:
            main.setdefault("duplicates", []).append(c["task"]["id"])
    return out


def split_low_priority(cards: list[dict], cfg: Bandeja, now: float) -> tuple[list[dict], list[dict]]:
    """(resto, a aplazar): prioridad explícitamente negativa y <= prioridad_baja_max, con más de autosnooze_dias días de espera.
    La prioridad 0 (la de por defecto) o sin informar nunca se aplaza sola."""
    if cfg.autosnooze_dias <= 0:
        return list(cards), []
    keep, low = [], []
    for c in cards:
        p, since = priority(c["task"]), c.get("since")
        if p is not None and p < 0 and p <= cfg.prioridad_baja_max and since and now - since >= cfg.autosnooze_dias * 86400:
            low.append(c)
        else:
            keep.append(c)
    return keep, low


def group_counts(cards: list[dict]) -> str:
    """"MigraTeam 4 · Otros 3" (mayor grupo primero)."""
    counts: dict[str, int] = {}
    for c in cards:
        counts[origin(c)] = counts.get(origin(c), 0) + 1
    return " · ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


_REVERSIBLE_RE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?reversible(?:\*\*)?\s*:\s*(s[ií]|true|yes|1)\s*\.?\s*$", re.I | re.M)


def is_reversible(body: str | None) -> bool:
    """`Reversible: sí` en el CUERPO de la tarjeta (lo escribe quien la crea; un comentario del worker no cuenta)."""
    return bool(_REVERSIBLE_RE.search(body or ""))


class VetoBook:
    """Plazo de veto: .state/card_veto.json {task_id: {"armed": epoch, "state": armed|vetoed|avisada|ejecutada}}."""

    def __init__(self, path: Path | str, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self.path)

    def arm(self, task_id: str) -> float:
        """Empieza el plazo la primera vez que se enseña la tarjeta; devuelve cuándo (epoch)."""
        with self._lock:
            data = self._load()
            if task_id not in data:
                data[task_id] = {"armed": self._clock(), "state": "armed"}
                self._save(data)
            return float(data[task_id].get("armed") or 0)

    def state(self, task_id: str) -> str | None:
        return self._load().get(task_id, {}).get("state")

    def set_state(self, task_id: str, state: str) -> None:
        with self._lock:
            data = self._load()
            data.setdefault(task_id, {"armed": self._clock()})["state"] = state
            self._save(data)

    def due(self, horas: float) -> list[str]:
        """Armadas cuyo plazo ya ha pasado y nadie ha vetado."""
        now = self._clock()
        return sorted(t for t, v in self._load().items()
                      if v.get("state") == "armed" and float(v.get("armed") or now) + horas * 3600 <= now)
