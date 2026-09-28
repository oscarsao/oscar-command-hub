"""Carril ops: trabajo operativo sin repo (carpeta de trabajo por tarea, evidencias en vez de git).

- `OpsWorkspace` sustituye a GitOps en el LaneRunner: crea `<worktree_root>/<task_id>` (carpeta, no git).
- `task_targets` lee `Origen-Ops:` / `Destino-Ops:` del cuerpo y valida los destinos contra `dest_roots`.
- `verify_ops` comprueba las evidencias del worker: rutas dentro del workspace o de un destino declarado,
  hashes y nº de archivos recalculados. Nunca marca done: el runner siempre pasa a review para Oscar.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .config import Lane, load_lanes
from .ops_paths import inside_any, normalize, parse_targets, validate_dests
from .verify import VerifyResult

PATH_EVIDENCE = ("path", "hash", "count")
MAX_COUNT_FILES = 200_000  # contar más que esto no es verificación, es un escaneo del disco


class OpsTargetError(ValueError):
    pass


def workspace_path(lane: Lane, task_id: str) -> Path:
    return Path(lane.worktree_root) / task_id


def lane_repos() -> list[str]:
    """Checkouts de los carriles de código: nunca destino de un worker ops."""
    try:
        return [l.repo for l in load_lanes().values() if l.repo]
    except Exception:  # sin lanes.yaml legible no se valida ningún destino: fail closed en validate_dests
        return ["C:/"]


def task_targets(lane: Lane, task: dict | None) -> tuple[list[str], list[str], list[str]]:
    """(orígenes, destinos válidos normalizados, motivos de rechazo) de la tarea."""
    origins, dests = parse_targets((task or {}).get("body"))
    ok, bad = validate_dests(dests, origins, lane.dest_roots, lane.worktree_root, lane_repos() if dests else ())
    return origins, ok, bad


class OpsWorkspace:
    """Mismo contrato que GitOps.prepare_worktree para el LaneRunner, sin git."""

    def prepare_worktree(self, lane: Lane, task_id: str, task: dict | None = None) -> str:
        if task is not None:
            _, _, bad = task_targets(lane, task)
            if bad:
                raise OpsTargetError("Destino-Ops no permitido: " + "; ".join(bad))
        path = workspace_path(lane, task_id)
        path.mkdir(parents=True, exist_ok=True)  # reintento o vuelta de review: se reutiliza
        return str(path)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _count_files(path: str) -> int:
    n = 0
    for _, _, files in os.walk(path):
        n += len(files)
        if n > MAX_COUNT_FILES:
            break
    return n


def verify_ops(lane: Lane, task_id: str, cwd: str, result: dict, task: dict | None = None) -> VerifyResult:
    """Evidencias verificables del worker ops. Exige al menos una ruta existente dentro de las raíces permitidas."""
    reasons: list[str] = []
    _, dests, bad = task_targets(lane, task)
    if bad:
        reasons.append("Destino-Ops no permitido: " + "; ".join(bad))
    roots = [normalize(cwd)] + dests
    verified = 0
    for i, ev in enumerate(result.get("evidence") or []):
        kind, value, raw = ev.get("type"), str(ev.get("value") or "").strip(), ev.get("path")
        if kind not in PATH_EVIDENCE:
            continue  # enlaces y notas se pasan tal cual a Oscar
        if kind == "path" and not raw:
            raw = value
        if not raw:
            reasons.append(f"evidencia {i} ({kind}) sin ruta")
            continue
        p = normalize(raw, cwd)
        if not inside_any(p, roots):
            reasons.append(f"evidencia {i}: {raw} fuera del workspace y de los destinos declarados")
            continue
        if not os.path.exists(p):
            reasons.append(f"evidencia {i}: {raw} no existe")
            continue
        if kind == "hash":
            if not os.path.isfile(p):
                reasons.append(f"evidencia {i}: hash de algo que no es un archivo ({raw})")
                continue
            if _sha256(p) != value.lower().removeprefix("sha256:"):
                reasons.append(f"evidencia {i}: sha256 de {raw} no coincide")
                continue
        if kind == "count":
            try:
                expected = int(value)
            except ValueError:
                reasons.append(f"evidencia {i}: nº de archivos '{value}' no es un entero")
                continue
            real = _count_files(p) if os.path.isdir(p) else 1
            if real != expected:
                reasons.append(f"evidencia {i}: {raw} tiene {real} archivo(s), no {expected}")
                continue
        verified += 1
    if not verified and not reasons:
        reasons.append("sin evidencias verificables (ruta, hash o nº de archivos dentro del workspace/destino)")
    return VerifyResult(ok=not reasons, reasons=reasons, test_exit=None)


def worker_env(lane: Lane, task: dict, cwd: str) -> dict[str, str]:
    """Entorno del worker ops (y de su hook): lo fija el runner, el worker no puede cambiarlo."""
    _, dests, _ = task_targets(lane, task)
    return {"AGENT_LANES_ROLE": "ops", "AGENT_LANES_WORKSPACE": cwd, "AGENT_LANES_OPS_DESTS": json.dumps(dests)}
