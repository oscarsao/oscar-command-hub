"""lanes CLI.

    py -3.12 lanes.py status                       # por carril: libre/ocupado, ready, review, en curso / última
    py -3.12 lanes.py take <task_id>               # consola interactiva: claim + worktree lane/<id>
    py -3.12 lanes.py close <task_id> "<resumen>"  # verificación mecánica + request-review (nunca merge)
"""
from __future__ import annotations

import json
import subprocess
import sys

from agent_lanes.config import ROOT, load_lanes
from agent_lanes.git_ops import GitOps
from agent_lanes.hermes import HermesCLI
from agent_lanes.runner import STATE_DIR, pid_alive
from agent_lanes.status import format_rows, lane_rows
from agent_lanes.verify import verify


def find_task(task_id: str):
    """(lane, hermes, task) for a task assigned to one of our implementer lanes, on any lane board."""
    lanes = {n: l for n, l in load_lanes().items() if l.kind == "implement"}
    for board in dict.fromkeys(l.board for l in lanes.values()):
        h = HermesCLI(board, author="consola-interactiva")
        try:
            task = h.show(task_id)["task"]
        except Exception:
            continue
        lane = lanes.get(task.get("assignee"))
        if lane is None:
            raise SystemExit(f"{task_id} está en el board {board} pero asignada a '{task.get('assignee')}', "
                             f"que no es un carril de lanes.yaml. Reasígnala primero.")
        return lane, h, task
    raise SystemExit(f"{task_id} no aparece en los boards de los carriles")


def take(task_id: str) -> int:
    lane, h, task = find_task(task_id)
    if task["status"] != "ready":
        raise SystemExit(f"{task_id} está en '{task['status']}', no en ready")
    if not h.claim(task_id, lane.claim_ttl_seconds):
        raise SystemExit(f"claim de {task_id} rechazado (¿otro runner la cogió?)")
    path = GitOps().prepare_worktree(lane, task_id)
    h.comment(task_id, f"LANE claim INTERACTIVO (consola de Oscar) · carril {lane.name} · rama lane/{task_id}")
    print(json.dumps({"task": task_id, "lane": lane.name, "worktree": path, "branch": f"lane/{task_id}",
                      "base": lane.base_ref, "lease_minutes": lane.claim_ttl_seconds // 60,
                      "title": task.get("title")}, ensure_ascii=False, indent=2))
    return 0


def close(task_id: str, summary: str) -> int:
    lane, h, task = find_task(task_id)
    path = f"{lane.worktree_root}/lane-{task_id}"
    head = subprocess.run(["git", "-C", path, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    changed = subprocess.run(["git", "-C", path, "diff", "--name-only", f"{lane.base_ref}...HEAD"],
                             capture_output=True, text=True).stdout.split()
    check = verify(lane, task_id, path, {"branch": f"lane/{task_id}", "head_sha": head})
    if not check.ok:
        print("Verificación FALLIDA; la tarea sigue en running:\n- " + "\n- ".join(check.reasons))
        return 1
    ok, err = h.request_review(task_id, summary, {"lane": lane.name, "branch": f"lane/{task_id}", "head_sha": head,
                                                  "changed_files": changed, "worktree": path, "verified": True,
                                                  "runner_test_exit": check.test_exit, "interactive": True})
    print(f"{task_id} → review" if ok else f"request-review rechazado: {err}")
    return 0 if ok else 1


def runner_line() -> str:
    lock = ROOT / ".state" / "runner.lock"
    try:
        pid = int(json.loads(lock.read_text(encoding="utf-8"))["pid"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return "runner: no arrancado (sin .state/runner.lock)"
    return f"runner: {'VIVO' if pid_alive(pid) else 'PARADO'} (pid {pid})"


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if argv[:1] == ["take"] and len(argv) == 2:
        return take(argv[1])
    if argv[:1] == ["close"] and len(argv) == 3:
        return close(argv[1], argv[2])
    if argv[:1] != ["status"]:
        print(__doc__)
        return 2
    cache: dict[str, HermesCLI] = {}
    rows = lane_rows(load_lanes(), hermes_for=lambda b: cache.setdefault(b, HermesCLI(b)), state_dir=STATE_DIR,
                     pid_alive=pid_alive)
    print(format_rows(rows, runner_line()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
