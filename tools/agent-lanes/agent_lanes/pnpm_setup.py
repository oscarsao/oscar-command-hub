"""Instala las dependencias pnpm del worktree de una tarea antes de lanzar al worker (25-10).

Los workers de claude-oscarhq no podían instalar `dashboard/node_modules` y ninguna tarjeta del panel pasaba tsc/vitest.
Aquí lo hace el runner: `pnpm install --frozen-lockfile` con un store compartido por carril (rápido: enlaces duros),
con timeout y log. Si falla, el worker sigue (el rol le dice qué hacer) y el motivo queda en el log del runner."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from . import proc as _proc

log = logging.getLogger("agent_lanes")

PNPM_SPEC = "pnpm@10.18.3"          # mismo pin que batch_frontend_cmd de lanes.yaml
INSTALL_TIMEOUT_SECONDS = 600
CANDIDATE_DIRS = ("dashboard", "frontend", ".")
_SECRET_KEY = re.compile(r"(TOKEN|SECRET|PASSWORD|API_?KEY|CREDENTIAL)", re.I)


def pnpm_dirs(worktree: Path) -> list[Path]:
    """Carpetas del worktree con `pnpm-lock.yaml` + `package.json` y sin `node_modules`."""
    out = []
    for d in CANDIDATE_DIRS:
        p = worktree if d == "." else worktree / d
        if (p / "pnpm-lock.yaml").is_file() and (p / "package.json").is_file() and not (p / "node_modules").exists():
            out.append(p)
    return out


def install_env(store: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not _SECRET_KEY.search(k)}
    env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] = "0"
    env["npm_config_store_dir"] = str(store)
    env["CI"] = "true"
    return env


def install_command(store: Path) -> list[str]:
    exe = shutil.which("corepack") or "corepack"
    return [exe, PNPM_SPEC, "install", "--frozen-lockfile", "--store-dir", str(store)]


def ensure_pnpm_deps(worktree: str, worktree_root: str, runner=_proc.run,
                     timeout: int = INSTALL_TIMEOUT_SECONDS) -> list[str]:
    """Devuelve una línea por carpeta intentada («dashboard: ok» / «dashboard: FALLÓ ...»). Nunca lanza."""
    results: list[str] = []
    try:
        wt = Path(worktree)
        store = Path(worktree_root) / ".pnpm-store"
        for d in pnpm_dirs(wt):
            label = d.relative_to(wt).as_posix()
            try:
                cp = runner(install_command(store), cwd=str(d), env=install_env(store), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
            except subprocess.TimeoutExpired:
                msg = f"{label}: FALLÓ pnpm install (timeout {timeout}s)"
            except (OSError, subprocess.SubprocessError) as exc:
                msg = f"{label}: FALLÓ pnpm install ({exc})"
            else:
                if cp.returncode == 0:
                    msg = f"{label}: ok"
                else:
                    tail = ((cp.stderr or "") + (cp.stdout or "")).strip()[-300:]
                    msg = f"{label}: FALLÓ pnpm install (código {cp.returncode}): {tail}"
            log.info("pnpm_setup %s %s", worktree, msg)
            results.append(msg)
    except Exception as exc:  # preparar dependencias es best effort: jamás debe impedir el trabajo
        log.warning("pnpm_setup %s: %s", worktree, exc)
    return results
