"""Lanzador que corre EN EL HOST (segundo ordenador) cuando el hub despacha un worker por SSH (E1, specs/worker-remoto-ssh.md).

Solo biblioteca estándar (+ `agent_lanes.proc`, que también lo es). Viaja con el clon de oscar-command-hub del host,
fijado al mismo SHA que el hub: es el único código que el hub ejecuta allí, con un comando remoto fijo
(`py -3.12 "<repo_dir>\\tools\\agent-lanes\\agent_lanes\\remote_exec.py"`) y SIN comillas ni argumentos del usuario.

Protocolo
  stdin   un JSON utf-8: {"v": 1, "nonce": str, "args": [str] | "cmd": str, "shell": bool, "cwd": str, "env": {..},
          "input": str | null, "timeout": float | null, "local_roots": [str]}
  stdout  la salida del proceso hijo, tal cual (bytes).
  stderr  la del hijo, más líneas de control `@@LANE-<nonce>-<TIPO> <datos>` (el nonce solo lo conoce el hub: un worker
          no puede falsificarlas):  PID <pid> <hora de creación|->  (al arrancar el hijo)  y  RC <código>  /  TIMEOUT
          (al terminar). Se sale con 0 siempre que el hijo llegó a correr: así el rc 255 de `ssh` (conexión) no se
          confunde nunca con un fallo del worker. Sin línea RC = el propio lanzador falló.

Seguridad: del entorno del hub solo se aceptan variables `AGENT_LANES_*`; las rutas del hub dentro de los argumentos se
traducen a las del clon del host; no hay `command=` ni shell intermedia salvo `shell: true` (test_cmd, de lanes.yaml).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # tools/agent-lanes del clon del host
sys.path.insert(0, str(ROOT))

from agent_lanes import proc as _proc  # noqa: E402  (stdlib puro: created_at, kill_tree)

ENV_PREFIX = "AGENT_LANES_"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def translate(arg: str, local_roots: list[str]) -> str:
    """Rutas del hub (ROOT en sus dos formas) -> las del clon del host. Estructural: solo sustituye ese prefijo."""
    mine = (str(ROOT), ROOT.as_posix())
    for i, lr in enumerate(local_roots[:2]):
        if lr and lr in arg:
            arg = arg.replace(lr, mine[min(i, 1)])
    return arg


def render_settings_if_needed(args: list[str]) -> None:
    """`--settings <ROOT>/.state/<x>.rendered.json`: el hub lo renderizó con SU ruta; aquí se renderiza con la del host
    (plantilla contract/<x>.json del mismo clon, así el guard es el del SHA fijado)."""
    for i, a in enumerate(args[:-1]):
        if a != "--settings":
            continue
        out = Path(args[i + 1])
        if not out.name.endswith(".rendered.json"):
            continue
        template = ROOT / "contract" / (out.name[: -len(".rendered.json")] + ".json")
        if not template.is_file():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        text = template.read_text(encoding="utf-8").replace("{AGENT_LANES_DIR}", ROOT.as_posix())
        out.write_text(text, encoding="utf-8")


def _free_gb(path: str) -> float | None:
    import shutil
    p = Path(path or ".")
    while True:  # la carpeta de worktrees puede no existir aún: se mide el primer ancestro que sí
        try:
            return round(shutil.disk_usage(str(p)).free / 1024 ** 3, 2)
        except OSError:
            if p.parent == p:
                return None
            p = p.parent


def op_status(req: dict) -> dict:
    """Op `status` (sondeo del hub): disco libre, HEAD del clon del hub y hash de contract/ de ESTE clon. Solo lectura."""
    from agent_lanes.contrato import contract_hash
    head = None
    try:
        cp = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30,
                            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        head = (cp.stdout.strip() or None) if cp.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    return {"host_time": time.time(), "disk_free_gb": _free_gb(str(req.get("worktree_root") or "")),
            "hub_head": head, "contract_hash": contract_hash(ROOT)}


def op_kill(req: dict) -> dict:
    """Op `kill`: corta el ARBOL de un `claude` huérfano solo si el PID sigue siendo el mismo proceso (hora de creación
    igual, +-2 s). Sin hora de creación no se corta (podría ser otro proceso con el PID reutilizado)."""
    pid, created = int(req["pid"]), req.get("created")
    if pid <= 0:
        return {"estado": "pid_invalido"}
    if created is None:
        return {"estado": "sin_hora"}
    now = _proc.created_at(pid)
    if now is None:
        return {"estado": "no_existe"}
    if abs(now - float(created)) > 2:
        return {"estado": "otro_proceso"}
    _proc.kill_tree(pid)
    return {"estado": "cortado" if _proc.created_at(pid) is None else "sigue_vivo"}


OPS = {"status": op_status, "kill": op_kill}


def main() -> int:
    req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    nonce = str(req["nonce"])
    err = sys.stderr.buffer

    def mark(kind: str, data: str = "") -> None:
        err.write(f"\n@@LANE-{nonce}-{kind} {data}\n".encode("utf-8"))
        err.flush()

    if req.get("op"):  # operaciones propias del lanzador (nada que venga del hub se ejecuta): lista cerrada
        fn = OPS.get(str(req["op"]))
        if fn is None:
            err.write(f"remote_exec: op desconocida {req['op']!r}\n".encode("utf-8"))
            return 2  # sin línea RC: el hub lo ve como fallo del lanzador (HostError)
        try:
            out = fn(req)
        except Exception as exc:  # noqa: BLE001
            err.write(f"remote_exec: op {req['op']} falló: {exc}\n".encode("utf-8", "replace"))
            return 3
        sys.stdout.buffer.write(json.dumps(out).encode("utf-8"))
        sys.stdout.buffer.flush()
        mark("RC", "0")
        return 0

    local_roots = [str(x) for x in req.get("local_roots") or ()]
    shell = bool(req.get("shell"))
    if shell:
        cmd = translate(str(req["cmd"]), local_roots)
    else:
        args = [translate(str(a), local_roots) for a in req["args"]]
        render_settings_if_needed(args)
        cmd = args
    env = {**os.environ, **{k: str(v) for k, v in (req.get("env") or {}).items() if str(k).startswith(ENV_PREFIX)}}
    data = req.get("input")
    timeout = req.get("timeout")
    try:
        child = subprocess.Popen(cmd, shell=shell, cwd=req.get("cwd") or None, env=env,
                                 stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    except OSError as exc:
        err.write(f"remote_exec: no se pudo lanzar el proceso: {exc}\n".encode("utf-8", "replace"))
        mark("RC", "127")
        return 0
    created = _proc.created_at(child.pid)
    mark("PID", f"{child.pid} {created if created is not None else '-'}")
    try:
        out, e = child.communicate(data.encode("utf-8") if data is not None else None, timeout=timeout)
        rc, timed_out = child.returncode, False
    except subprocess.TimeoutExpired:
        _proc.kill_tree(child.pid)
        child.kill()
        out, e = child.communicate()
        rc, timed_out = 124, True
    sys.stdout.buffer.write(out or b"")
    sys.stdout.buffer.flush()
    err.write(e or b"")
    if timed_out:
        mark("TIMEOUT")
    mark("RC", str(rc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
