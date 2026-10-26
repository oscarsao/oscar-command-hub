"""Registro LOCAL de aprobaciones de PR (P08, F1-07): lo único de lo que se fía el sistema para «aprobado por Oscar».

El comentario `APROBADO-OSCAR … PR <url>` del kanban lo puede escribir cualquiera que tenga la CLI de Hermes con
`--author oscar-telegram`; el registro de `.state/integrator/approvals/<t_id>.json` solo lo escribe el bot de carriles
al pulsar ✅ Aprobar (DecisionDesk._approve -> Integrator.record_approval, con el commit de cabeza; o `registrar` si el
integrador está apagado). review.py, renotify.py y el integrador exigen los dos. Transitorio hasta F2 (hub.approvals
firmado por hub-core).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from .config import ROOT

APPROVALS_DIR = ROOT / ".state" / "integrator" / "approvals"  # = Integrator.approvals_dir con el state_dir por defecto
TID_RE = re.compile(r"t_[0-9A-Za-z_]{1,40}")  # solo caracteres de nombre de fichero (los ids reales: t_ + 8 hex)
PR_NUM_RE = re.compile(r"/pull/(\d+)")


def registro(tid: str, base: Path | None = None) -> dict:
    if not TID_RE.fullmatch(tid or ""):
        return {}
    try:
        data = json.loads((Path(base or APPROVALS_DIR) / f"{tid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def aprobado(tid: str, pr: int | None = None, base: Path | None = None) -> bool:
    """¿Hay registro local de la aprobación de `tid` (y, si se da, del PR `pr`)?"""
    rec = registro(tid, base)
    if not rec:
        return False
    return pr is None or int(rec.get("pr") or 0) == int(pr)


def pr_de_comentario(body: str | None) -> int | None:
    """Número de PR de un `APROBADO-OSCAR … PR <url>`, o None."""
    m = PR_NUM_RE.search(body or "")
    return int(m.group(1)) if m else None


def registrar(tid: str, pr: int, *, by: str = "", base: Path | None = None) -> bool:
    """Registro mínimo (sin commit de cabeza) cuando el integrador está apagado: basta para que review/renotify
    sepan que Oscar aprobó; el integrador, si se enciende, lo rechaza por no tener cabeza («commits después»)."""
    if not TID_RE.fullmatch(tid or ""):
        return False
    d = Path(base or APPROVALS_DIR)
    if aprobado(tid, pr, d):  # el del integrador (con cabeza) para este PR manda: no se pisa
        return True
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{tid}.json"
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"task_id": tid, "pr": int(pr), "head_sha": "", "by": by, "at": time.time()}),
                   encoding="utf-8")
    os.replace(tmp, path)
    return True
