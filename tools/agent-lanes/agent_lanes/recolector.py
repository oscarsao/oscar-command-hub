"""Recolector nocturno de worktrees y ramas locales ya integradas (tarjeta t_88f59559, Ola 2).

Una vez al día (RecolectorTicker, bucle del runner; apagado por defecto, bloque `recolector:` de lanes.yaml) o a mano
(`python -m agent_lanes.recolector [--real]`) recorre los repos de los carriles y quita un worktree `lane-<tid>` solo si
se cumplen TODAS estas condiciones:

- la tarjeta está en done/archived (una tarjeta running/ready/review/desconocida nunca se toca);
- el worktree está limpio (`status --porcelain`, con no rastreados) y no tiene commits sin empujar (todo su HEAD
  alcanzable desde alguna rama remota);
- su rama `lane/<tid>` ya está integrada en la base: `merge-base --is-ancestor` o PR fusionado.

Después borra la rama local con `branch -d` (nunca -D): si git la rechaza porque no está fusionada, se conserva.
NUNCA `--force`. `seco: true` (por defecto) solo informa. El informe y el registro quedan en `.state/recolector.json`.
Un fallo al listar tarjetas o al leer git = se conserva todo lo afectado.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from . import proc as _proc
from .config import ROOT

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "recolector.json"
CERRADAS = ("done", "archived")  # únicos estados cuyo worktree puede quitarse
PREFIJO = "lane-t_"


@dataclass(frozen=True)
class Ajustes:
    activo: bool = False
    hora: int = 3
    seco: bool = True


def load(path: Path | str | None = None) -> Ajustes:
    """Bloque `recolector:` de lanes.yaml; lo que falte o no sea válido queda en su valor por defecto (nunca lanza)."""
    d = Ajustes()
    try:
        raw = (yaml.safe_load(Path(path or ROOT / "lanes.yaml").read_text(encoding="utf-8")) or {}).get("recolector")
    except (OSError, yaml.YAMLError, AttributeError):
        return d
    if not isinstance(raw, dict):
        return d
    hora = raw.get("hora")
    return Ajustes(activo=raw["enabled"] if isinstance(raw.get("enabled"), bool) else d.activo,
                   hora=hora if isinstance(hora, int) and not isinstance(hora, bool) and 0 <= hora <= 23 else d.hora,
                   seco=raw["seco"] if isinstance(raw.get("seco"), bool) else d.seco)


@dataclass
class Informe:
    seco: bool = True
    quitados: list[str] = field(default_factory=list)       # "<repo>: lane-t_x (rama lane/t_x)"
    conservados: list[tuple[str, str]] = field(default_factory=list)  # (elemento, motivo)

    def texto(self) -> str:
        modo = "SECO (no se ha borrado nada)" if self.seco else "REAL"
        verbo = "Quitaría" if self.seco else "Quitados"
        lineas = [f"Recolector de worktrees · {modo}", f"{verbo}: {len(self.quitados)} · Se quedan: {len(self.conservados)}"]
        lineas += [f"  - {q}" for q in self.quitados]
        lineas += [f"  · {e}: {m}" for e, m in self.conservados]
        return "\n".join(lineas)


def _norm(p) -> str:
    return os.path.normcase(os.path.normpath(str(p))).replace("\\", "/")


class Recolector:
    def __init__(self, lanes: dict, hermes_for: Callable, *, run=_proc.run, merged_pr: Callable | None = None,
                 state_path: Path | None = None):
        self.lanes, self.hermes_for, self._run = lanes, hermes_for, run
        self.merged_pr = merged_pr or (lambda lane, tid: None)
        self.state_path = Path(state_path or STATE_PATH)

    # --- git (solo lectura salvo worktree remove / branch -d, y solo con seco=False) ---------------------------------
    def _git(self, repo: str, *args: str, timeout: int = 120):
        return self._run(["git", "-C", repo, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=timeout)

    def _ok(self, repo: str, *args: str) -> bool:
        return self._git(repo, *args).returncode == 0

    def _worktrees(self, repo: str) -> dict[str, str]:
        """{ruta normalizada: ruta} de los worktrees que git conoce (el primero es el checkout principal)."""
        cp = self._git(repo, "worktree", "list", "--porcelain")
        if cp.returncode != 0:
            raise RuntimeError(f"git worktree list: {(cp.stderr or '').strip()[:150]}")
        rutas = [l[len("worktree "):].strip() for l in (cp.stdout or "").splitlines() if l.startswith("worktree ")]
        return {_norm(r): r for r in rutas[1:]}

    def _estados_cerrados(self, lanes: list) -> set[str] | None:
        """Ids de tarjetas done/archived de estos carriles. None si no se pudo leer algún tablero (se conserva todo)."""
        cerradas: set[str] = set()
        try:
            for lane in lanes:
                h = self.hermes_for(lane.board)
                for st in CERRADAS:
                    cerradas |= {t["id"] for t in h.list_status(lane.name, st)}
        except Exception as exc:
            log.warning("recolector: no se pudo listar tarjetas: %s", exc)
            return None
        return cerradas

    # --- decisión ---------------------------------------------------------------------------------------------------
    def motivo_conservar(self, lane, tid: str, path: str | None, cerradas: set[str]) -> str | None:
        """None si se puede quitar; si no, el motivo por el que se queda."""
        repo, branch = lane.repo, f"lane/{tid}"
        if tid not in cerradas:
            return "la tarjeta no está en done/archived"
        if path:
            sucio = self._git(path, "status", "--porcelain", "--untracked-files=all")
            if sucio.returncode != 0:
                return "no se pudo leer el estado del worktree"
            if (sucio.stdout or "").strip():
                return "worktree con cambios sin commitear"
            sin_empujar = self._git(path, "rev-list", "--count", "HEAD", "--not", "--remotes")
            try:
                if sin_empujar.returncode != 0 or int(sin_empujar.stdout.strip()) > 0:
                    return "commits sin empujar (o no se pudo comprobar)"
            except ValueError:
                return "commits sin empujar (o no se pudo comprobar)"
        if not self._ok(repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
            return None if path else "sin rama local"
        if self._ok(repo, "merge-base", "--is-ancestor", branch, lane.base_ref):
            return None
        if self.merged_pr(lane, tid):
            return None
        return "rama sin fusionar en la base"

    # --- pasada -----------------------------------------------------------------------------------------------------
    def recoger(self, seco: bool = True) -> Informe:
        inf = Informe(seco=seco)
        por_repo: dict[str, list] = {}
        for lane in self.lanes.values():
            if getattr(lane, "kind", "implement") == "implement" and lane.repo and lane.worktree_root and lane.base:
                por_repo.setdefault(_norm(lane.repo), []).append(lane)
        for lanes in por_repo.values():
            lane = lanes[0]
            nombre = Path(lane.repo).name
            cerradas = self._estados_cerrados(lanes)
            if cerradas is None:
                inf.conservados.append((nombre, "no se pudieron leer las tarjetas: se conserva todo"))
                continue
            try:
                worktrees = self._worktrees(lane.repo)
                self._git(lane.repo, "fetch", lane.remote, lane.base, timeout=300)  # solo lectura; si falla se usa lo que hay
            except Exception as exc:
                inf.conservados.append((nombre, f"git no respondió: {exc}"))
                continue
            raiz = _norm(lane.worktree_root)
            for clave, path in sorted(worktrees.items()):
                pp = Path(path)
                if pp.name[:len(PREFIJO)] != PREFIJO or _norm(pp.parent) != raiz:
                    continue  # solo los worktrees de carril bajo la raíz configurada; jamás el checkout principal
                self._uno(inf, lane, nombre, pp.name[len("lane-"):], path, cerradas, seco)
            ramas = self._git(lane.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/lane/t_*")
            con_worktree = {Path(p).name[len("lane-"):] for p in worktrees.values()}
            for rama in (ramas.stdout or "").split():
                tid = rama[len("lane/"):]
                if tid not in con_worktree:
                    self._uno(inf, lane, nombre, tid, None, cerradas, seco)
        self._guardar(inf)
        return inf

    def _uno(self, inf: Informe, lane, nombre: str, tid: str, path: str | None, cerradas: set[str], seco: bool) -> None:
        etiqueta = f"{nombre}: {'lane-' + tid if path else 'lane/' + tid}"
        try:
            motivo = self.motivo_conservar(lane, tid, path, cerradas)
        except Exception as exc:
            motivo = f"error al comprobar: {exc}"
        if motivo:
            inf.conservados.append((etiqueta, motivo))
            return
        if seco:
            inf.quitados.append(etiqueta)
            return
        if path:
            cp = self._git(lane.repo, "worktree", "remove", path, timeout=300)  # sin --force: si falla, se queda
            if cp.returncode != 0:
                inf.conservados.append((etiqueta, f"git no lo quitó: {(cp.stderr or '').strip()[:120]}"))
                return
        branch = f"lane/{tid}"
        if self._ok(lane.repo, "rev-parse", "--verify", "-q", f"refs/heads/{branch}"):
            cp = self._git(lane.repo, "branch", "-d", branch)  # -d, nunca -D: git rechaza lo no fusionado
            if cp.returncode != 0:
                inf.quitados.append(f"{etiqueta} (worktree quitado)")
                inf.conservados.append((f"{nombre}: {branch}", f"branch -d la rechazó: {(cp.stderr or '').strip()[:120]}"))
                return
        inf.quitados.append(etiqueta)

    def _guardar(self, inf: Informe) -> None:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        data["ultimo"] = {"cuando": time.strftime("%Y-%m-%d %H:%M:%S"), "seco": inf.seco, "quitados": inf.quitados,
                          "conservados": [list(c) for c in inf.conservados]}
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        except OSError as exc:
            log.info("recolector: estado no guardado: %s", exc)


class RecolectorTicker:
    """Bucle del runner: una vez al día pasada la hora del bloque `recolector:` de lanes.yaml (Madrid). Apagado por defecto.
    Un fallo no tumba el runner."""

    def __init__(self, recolector: Recolector, ajustes: Callable[[], Ajustes] = load, minutes: float = 30,
                 clock: Callable[[], float] = time.time):
        self.recolector, self.ajustes, self.minutes, self.clock = recolector, ajustes, minutes, clock
        self._last = 0.0

    def tick(self) -> Informe | None:
        from .resumen_carriles import MADRID
        now = self.clock()
        if now - self._last < self.minutes * 60:
            return None
        self._last = now
        try:
            cfg = self.ajustes()
            local = datetime.fromtimestamp(now, MADRID)
            dia = local.strftime("%Y-%m-%d")
            if not cfg.activo or local.hour < cfg.hora or self._ultimo_dia() == dia:
                return None
            self._marcar_dia(dia)  # antes de ejecutar: sin bucles si algo falla
            inf = self.recolector.recoger(seco=cfg.seco)
            log.info("%s", inf.texto())
            return inf
        except Exception as exc:
            log.warning("recolector falló: %s", exc)
            return None

    def _ultimo_dia(self) -> str:
        try:
            return str(json.loads(self.recolector.state_path.read_text(encoding="utf-8")).get("ultimo_dia", ""))
        except (OSError, ValueError, AttributeError):
            return ""

    def _marcar_dia(self, dia: str) -> None:
        p = self.recolector.state_path
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        data["ultimo_dia"] = dia
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")


def main(argv: list[str], *, recolector: Recolector | None = None, out: Callable[[str], None] = print) -> int:
    """`python -m agent_lanes.recolector` = en seco (informa, no borra). `--real` borra (solo lo que cumple todo)."""
    ap = argparse.ArgumentParser(prog="recolector")
    ap.add_argument("--real", action="store_true", help="borrar de verdad (por defecto: seco)")
    args = ap.parse_args(argv)
    if recolector is None:
        from .config import load_lanes
        from .git_ops import GitOps
        from .kanban_api import backend_for
        recolector = Recolector(load_lanes(), lambda board: backend_for(board), merged_pr=GitOps().merged_pr)
    out(recolector.recoger(seco=not args.real).texto())
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
