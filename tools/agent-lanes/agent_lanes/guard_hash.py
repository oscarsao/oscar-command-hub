"""Sello del guard de los workers (H11 de la auditoría del segundo worker, 19-11).

Un PC nuevo con un guard ausente, viejo o editado ejecutaría workers sin el candado de push/producción. Tres capas, de la
más barata a la más cierta, todas en `runner.py --check` (y al arrancar el runner con el backend supabase):

1. SELLO. `contract/guard-manifest.json` (versionado) guarda el sha256 de cada fichero que define el guard del worker y su
   contrato (hook, settings, política, esquemas, roles). `verify()` recalcula y compara: un fichero editado a mano, ausente
   o de otra versión rompe el sello. Los finales de línea se normalizan (git en Windows puede convertirlos). Se regenera con
   `py -3.12 -m agent_lanes.guard_hash --write` y se commitea con el cambio del guard; un test falla si se olvida.
2. SONDA. Se ejecuta el hook REAL, con el comando que lleva `worker-settings.json` (no un sustituto), con una orden
   prohibida (push a master, alembic contra una base remota) que debe dar exit 2 y una inocua que debe dar exit 0. Falla
   cerrado: sin `py -3.12`, sin el fichero o con un guard que deja pasar, el equipo no es apto.
3. ADMISIÓN. El runner anota el sello en `hub.workers.version` y `SupabaseKanban.claim` lo compara con
   `hub.ajustes.guard_hash_aprobado` si ese ajuste existe (oscar-hq debe darlo de alta): un worker con otro guard no reclama.

No hashea CLAUDE.md ni `~/.claude` (son privados de cada persona, no están en el repo): el hook de usuario instalado solo
se COMPARA con la copia del repo y se avisa si difiere (`user_hook_warnings`).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from .config import ROOT

MANIFEST = Path("contract") / "guard-manifest.json"
MANIFEST_VERSION = 1

# Lo que define qué puede hacer un worker y cómo se le habla (relativo a tools/agent-lanes). Cambiar cualquiera exige
# regenerar el manifest en el mismo commit.
GUARD_FILES = (
    "contract/guard.py", "contract/ops_guard.py",
    "contract/worker-settings.json", "contract/ops-worker-settings.json", "contract/reviewer-settings.json",
    "contract/result.schema.json", "contract/ops-result.schema.json", "contract/review.schema.json",
    "contract/task-template.md",
    "hooks-usuario/destino.mjs", "hooks-usuario/preautoriza.mjs", "hooks-usuario/politica-permisos.json",
    "roles/implementador.md", "roles/ops.md", "roles/revisor.md", "roles/hub.md",
)

# (descripción, entrada del hook, exit esperado)
PROBES = (
    ("push a master", {"tool_name": "Bash", "tool_input": {"command": "git push origin master"}}, 2),
    ("alembic contra una base remota", {"tool_name": "Bash", "tool_input": {"command": "alembic upgrade head"}}, 2),
    ("orden inocua", {"tool_name": "Bash", "tool_input": {"command": "git status"}}, 0),
)
PROBE_TIMEOUT = 30


def _file_sha(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def compute(root: Path = ROOT, files=GUARD_FILES) -> dict:
    """{"digest": sha256 del conjunto, "files": {ruta: sha256 | None si falta}}."""
    shas = {rel: (_file_sha(root / rel) if (root / rel).is_file() else None) for rel in files}
    h = hashlib.sha256()
    for rel in sorted(shas):
        h.update(f"{rel}\0{shas[rel] or 'FALTA'}\n".encode())
    return {"digest": h.hexdigest(), "files": shas}


def write_manifest(root: Path = ROOT) -> dict:
    data = compute(root)
    missing = [r for r, s in data["files"].items() if s is None]
    if missing:
        raise FileNotFoundError("faltan ficheros del guard: " + ", ".join(missing))
    out = {"version": MANIFEST_VERSION, **data}
    (root / MANIFEST).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return out


def current_digest(root: Path = ROOT) -> str | None:
    """Sello de este checkout (None si no se puede calcular o falta algún fichero del guard)."""
    try:
        data = compute(root)
    except OSError:
        return None
    return None if any(s is None for s in data["files"].values()) else data["digest"]


def verify_manifest(root: Path = ROOT) -> list[str]:
    """Problemas del sello (vacío = el guard de este checkout es el aprobado en el repo)."""
    try:
        approved = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"falta o es ilegible {MANIFEST.as_posix()} (sello del guard)"]
    now = compute(root)
    problems = [f"falta el fichero del guard {rel}" for rel, sha in now["files"].items() if sha is None]
    old = approved.get("files") or {}
    problems += [f"el guard cambió: {rel} (distinto del aprobado)" for rel, sha in now["files"].items()
                 if sha is not None and old.get(rel) not in (None, sha)]
    problems += [f"el guard tiene un fichero sin sellar: {rel}" for rel, sha in now["files"].items()
                 if sha is not None and rel not in old]
    if not problems and now["digest"] != approved.get("digest"):
        problems.append("el sello del guard no coincide con el aprobado")
    if problems:
        problems.append("si el cambio es intencionado: py -3.12 -m agent_lanes.guard_hash --write y commitear; si no, "
                        "git pull / git checkout de esos ficheros")
    return problems


def hook_command(root: Path = ROOT, template: str = "contract/worker-settings.json") -> list[str] | None:
    """Comando del hook PreToolUse que lleva el settings del worker, con {AGENT_LANES_DIR} resuelto (None si no hay)."""
    try:
        text = (root / template).read_text(encoding="utf-8").replace("{AGENT_LANES_DIR}", root.as_posix())
        hooks = json.loads(text)["hooks"]["PreToolUse"][0]["hooks"][0]
        return [hooks["command"], *hooks.get("args", [])]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def probe(root: Path = ROOT, run=subprocess.run) -> list[str]:
    """Ejecuta el hook real con las órdenes de PROBES; falla cerrado si no se puede ejecutar o no contesta lo esperado."""
    cmd = hook_command(root)
    if not cmd:
        return ["worker-settings.json no declara el hook PreToolUse del guard"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_LANES_")}
    problems = []
    for what, payload, expected in PROBES:
        try:
            cp = run(cmd, input=json.dumps(payload), capture_output=True, text=True, timeout=PROBE_TIMEOUT, env=env,
                     encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError) as exc:
            return [f"no se pudo ejecutar el guard ({type(exc).__name__}: {cmd[0]}): sin él un worker no es seguro"]
        if cp.returncode != expected:
            problems.append(f"el guard no responde como debe a «{what}»: exit {cp.returncode}, esperado {expected}")
    return problems


def user_hook_warnings(root: Path = ROOT, home: Path | None = None) -> list[str]:
    """Avisos (no bloquean): el hook de usuario instalado en ~/.claude difiere de la copia del repo o no está."""
    base = (home or Path.home()) / ".claude" / "hooks"
    out = []
    for name in ("destino.mjs", "politica-permisos.json"):
        installed, repo = base / name, root / "hooks-usuario" / name
        if not installed.is_file():
            out.append(f"~/.claude/hooks/{name} no está instalado (solo afecta a sesiones interactivas de esta persona)")
        elif repo.is_file() and _file_sha(installed) != _file_sha(repo):
            out.append(f"~/.claude/hooks/{name} difiere de hooks-usuario/{name} del repo")
    return out


def problems(root: Path = ROOT, *, worker: str | None = None, run=subprocess.run) -> list[str]:
    """Todo lo que impide que este equipo ejecute workers: id de worker, sello del guard y sonda del hook."""
    out = []
    if worker is not None:
        from .kanban_api import worker_id_problem
        if (why := worker_id_problem(worker)):
            out.append(why)
    out += verify_manifest(root)
    out += probe(root, run)
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="agent_lanes.guard_hash", description="sello del guard de los workers")
    ap.add_argument("--write", action="store_true", help="regenerar contract/guard-manifest.json (tras cambiar el guard)")
    args = ap.parse_args(argv)
    if args.write:
        data = write_manifest()
        print(f"sello escrito: {data['digest'][:16]}… ({len(data['files'])} ficheros)")
        return 0
    found = problems()
    for p in found:
        print("-", p)
    for w in user_hook_warnings():
        print("aviso:", w)
    print("guard OK" if not found else f"guard NO apto ({len(found)})")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
