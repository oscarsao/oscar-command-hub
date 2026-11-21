"""Worker remoto por SSH (E1 de specs/worker-remoto-ssh.md): el hub despacha un carril con `host:` a un segundo equipo.

Nada de esto toca a los carriles sin `host:`: el runner elige estas clases solo para los que lo tienen
(`pila_para`), así que el camino local (GitOps, ClaudeWorker, verify) queda exactamente como estaba.

Piezas
  SshRunner         misma firma que el `runner` de `worker.run_claude` (y que `subprocess.run` para git y test_cmd).
                    Corre en el host, por `remote_exec.py`, y devuelve un CompletedProcess normal.
  GitOpsRemoto      worktree de la tarjeta y sync de la base en el host (el push de la rama lo hace el worker allí).
  ClaudeWorkerRemoto  ClaudeWorker que despacha por SshRunner y apunta el PID REMOTO en claves propias del estado.
  verify_remoto     parte git en el hub contra GitHub (sin fiarse del host) + parte test_cmd por SSH en el host.

E2 (este fichero + host_ops.py + remote_exec.py): revisión en el host (ClaudeReviewerRemoto), cortar huérfanos
(ClaudeWorkerRemoto.cortar_huerfano), gasto del host, `cleanup`/`poner_al_dia`/`base_contiene` remotos y limpieza de lo ya
hecho (sweep_done_remoto). Los reencolados por fallo de transporte y la puerta de lanzamiento viven en runner.py/host_ops.py.
E3 (solo lo mínimo): `ultimaSenal` por sondeo del hub; sin panel, sin Telegram, sin resumen diario ni /ajustes.
"""
from __future__ import annotations

import json
import logging
import ntpath
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import host_ops
from . import proc as _proc
from .config import ROOT, HostCfg, Lane
from .git_ops import _MARCAS_BLOQUEO, _REINTENTOS_BLOQUEO, AlDia, GitError
from .verify import VerifyResult, _git, split_paths, vetada
from .review import ClaudeReviewer, review_args, review_prompt
from .worker import ClaudeWorker, WorkerOutcome, run_claude

log = logging.getLogger("agent_lanes")

ENV_PREFIX = "AGENT_LANES_"      # prefijo que además exige el lanzador remoto
# Lista blanca EXACTA de lo que viaja al host (las que pone ClaudeWorker); cualquier otra, aunque lleve el prefijo, se queda.
ENV_ALLOWED = frozenset(ENV_PREFIX + n for n in ("TASK", "LANE", "MERGE_OK", "PUSH_OK", "WORKTREE", "BASE", "ROLE"))
LAUNCHER_REL = ("tools", "agent-lanes", "agent_lanes", "remote_exec.py")
MAX_OUTPUT_BYTES = 8 * 1024 * 1024  # tope de stdout+stderr remotos acumulados; pasado, se corta con aviso (HostError)
TIMEOUT_GRACE_S = 30             # el lanzador remoto corta al llegar a `timeout`; ssh local espera algo más
LOCAL_RC_TIMEOUT = 124


class SshTransportError(RuntimeError):
    """Fallo de transporte o del lanzador: nada que ver con lo que hiciera (o no) el worker."""


class HostUnreachable(SshTransportError):
    """ssh terminó con 255: no hubo conexión (red, clave, host apagado, clave de host no aceptada)."""


class HostError(SshTransportError):
    """ssh conectó pero el lanzador remoto no llegó a ejecutar el proceso (py/ruta/clon del host mal)."""


class RemotePid(int):
    """PID del proceso en el HOST. Es un int para que `on_start(pid)` conserve su firma; lleva además su hora de creación
    y el destino. NUNCA debe tratarse como un PID local (matar, medir RAM, `pid_alive`)."""
    created: float | None
    host: str

    def __new__(cls, pid: int, created: float | None, host: str):
        obj = super().__new__(cls, pid)
        obj.created, obj.host = created, host
        return obj


def launcher_command(host: HostCfg) -> str:
    """Comando remoto, fijo: solo `py` y la ruta del lanzador dentro del clon del host (sin datos de la tarjeta)."""
    return f'py -3.12 "{ntpath.join(host.hub_dir or host.repo_dir, *LAUNCHER_REL)}"'


def ssh_argv(host: HostCfg) -> list[str]:
    """ssh no interactivo. Nunca StrictHostKeyChecking=no: la clave del host se acepta una vez a mano (guía)."""
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes", "-o", f"ConnectTimeout={host.conexion_timeout_s}",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
            # Un `Host *` del ~/.ssh/config del hub no debe filtrar agente, X11 ni túneles al host.
            "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-o", "ClearAllForwardings=yes",
            "-o", "PermitLocalCommand=no", "--", host.destino, launcher_command(host)]


def real_transport(cmd: list[str], *, input: str, timeout: float | None, on_stderr_line=None, on_local_pid=None,
                   max_bytes: int = MAX_OUTPUT_BYTES) -> subprocess.CompletedProcess:
    """Ejecuta el ssh local con el JSON por stdin; lee stderr línea a línea (para el marcador del PID). Con `timeout`
    agotado mata el ssh y lanza TimeoutExpired. Es la costura que los tests sustituyen por `FakeSsh`."""
    kw = {"creationflags": _proc.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
    if on_local_pid:
        try:
            on_local_pid(p.pid)  # el ssh LOCAL: el runner lo reconoce y lo mata si muere (worker_pid)
        except Exception:
            log.warning("ssh: on_local_pid falló", exc_info=True)
    out: list[bytes] = []
    err: list[str] = []
    total = [0]
    excedido = threading.Event()

    def cuenta(n: int) -> bool:
        total[0] += n
        if total[0] > max_bytes and not excedido.is_set():
            excedido.set()
            p.kill()  # salida desmesurada: se corta el ssh (el lanzador remoto morirá al cerrarse la conexión)
        return not excedido.is_set()

    def read_out():
        while chunk := p.stdout.read(65536):
            if not cuenta(len(chunk)):
                return
            out.append(chunk)

    def read_err():
        for raw in iter(lambda: p.stderr.readline(65536), b""):  # con tope: línea acotada
            if not cuenta(len(raw)):
                return
            line = raw.decode("utf-8", "replace")
            err.append(line)
            if on_stderr_line:
                try:
                    on_stderr_line(line)
                except Exception:  # apuntar el PID es best effort: nunca debe romper la lectura
                    log.warning("ssh: on_stderr_line falló", exc_info=True)

    def write_in():
        try:
            p.stdin.write(input.encode("utf-8"))
        except OSError:
            pass
        finally:
            try:
                p.stdin.close()
            except OSError:
                pass

    threads = [threading.Thread(target=f, daemon=True) for f in (read_out, read_err, write_in)]
    for t in threads:
        t.start()
    try:
        p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        for t in threads:
            t.join(5)
        raise subprocess.TimeoutExpired(cmd, timeout) from None
    for t in threads:
        t.join(30)
    if excedido.is_set():
        raise HostError(f"salida remota superior a {max_bytes} bytes: ssh cortado; el proceso remoto pudo seguir vivo (muere al plazo)")
    return subprocess.CompletedProcess(cmd, p.returncode, b"".join(out).decode("utf-8", "replace"), "".join(err))


class SshRunner:
    """Con la firma del `runner` de `worker.run_claude` — `runner(args, input=, cwd=, env=, timeout=, on_start=, **)` —
    y también la de `subprocess.run` (`shell=True`, cmd en str) para el test_cmd. Ejecuta en el host (`cwd` es allí).

    Devuelve CompletedProcess con el rc del PROCESO REMOTO. Lanza: HostUnreachable (ssh rc 255), HostError (el lanzador
    no corrió), subprocess.TimeoutExpired (se pasó el `timeout`, igual que el runner local)."""

    def __init__(self, host: HostCfg, transport=None, local_root: Path = ROOT, on_local_pid=None):
        self.host = host
        self._transport = transport
        self._on_local_pid = on_local_pid
        self._local_roots = [str(local_root), Path(local_root).as_posix()]
        self.remote_finished = False  # True si hubo línea RC (el proceso remoto ya no corre): limpiar host_worker_* es seguro

    def __call__(self, args, *, input=None, cwd=None, env=None, timeout=None, on_start=None, shell=False,
                 **_ignored) -> subprocess.CompletedProcess:
        nonce = secrets.token_hex(8)
        req = {"v": 1, "nonce": nonce, "shell": bool(shell), "cwd": cwd, "input": input, "timeout": timeout,
               # Lista blanca: run_claude pasa {**os.environ, **env_extra}; ningún secreto del hub viaja al host.
               "env": {k: v for k, v in (env or {}).items() if k in ENV_ALLOWED},
               "local_roots": self._local_roots}
        req["cmd" if shell else "args"] = args if shell else list(args)
        return self._send(req, nonce, args, timeout, on_start)

    def op(self, nombre: str, timeout: float = 60, **params) -> dict:
        """Op propia del lanzador remoto (lista cerrada en remote_exec.OPS: `status`, `kill`). Devuelve el JSON de su
        stdout. Un lanzador antiguo (sin ops) falla sin línea RC -> HostError: falla cerrado."""
        nonce = secrets.token_hex(8)
        cp = self._send({"v": 1, "nonce": nonce, "op": nombre, "timeout": timeout, **params}, nonce, [nombre], timeout, None)
        if cp.returncode != 0:
            raise HostError(f"host {self.host.destino}: op {nombre} terminó con rc={cp.returncode}")
        try:
            data = json.loads(cp.stdout or "")
        except ValueError:
            raise HostError(f"host {self.host.destino}: op {nombre} devolvió algo que no es JSON") from None
        if not isinstance(data, dict):
            raise HostError(f"host {self.host.destino}: op {nombre} devolvió un JSON inesperado")
        return data

    def _send(self, req: dict, nonce: str, args, timeout, on_start) -> subprocess.CompletedProcess:
        tag = f"@@LANE-{nonce}-"
        seen: dict = {}

        def on_line(line: str) -> None:
            line = line.strip()
            if not line.startswith(tag):
                return
            kind, _, data = line[len(tag):].partition(" ")
            if kind == "PID" and on_start and "pid" not in seen:
                pid, _, created = data.partition(" ")
                seen["pid"] = int(pid)
                try:
                    created_at = float(created)
                except ValueError:
                    created_at = None
                try:
                    on_start(RemotePid(int(pid), created_at, self.host.destino))
                except Exception:
                    log.warning("on_start falló para el PID remoto", exc_info=True)

        transport = self._transport or real_transport
        deadline = None if timeout is None else float(timeout) + TIMEOUT_GRACE_S + self.host.conexion_timeout_s
        extra = {"on_local_pid": self._on_local_pid} if self._on_local_pid else {}
        try:
            cp = transport(ssh_argv(self.host), input=json.dumps(req), timeout=deadline, on_stderr_line=on_line, **extra)
        except subprocess.TimeoutExpired:
            # El plazo del ssh LOCAL (no el marcador TIMEOUT del lanzador): no sabemos qué pasó allí.
            vivo = f" el proceso remoto PID {seen['pid']}" if "pid" in seen else " el proceso remoto"
            raise HostError(f"host {self.host.destino}: el ssh agotó su plazo sin respuesta; {vivo.strip()} pudo seguir vivo "
                            "en el host") from None
        if len(cp.stdout or "") + len(cp.stderr or "") > MAX_OUTPUT_BYTES:
            raise HostError(f"salida remota superior a {MAX_OUTPUT_BYTES} bytes: descartada; el proceso remoto pudo seguir vivo")

        rc, timed_out, kept = None, False, []
        for line in (cp.stderr or "").splitlines(keepends=True):
            s = line.strip()
            if s.startswith(tag):
                kind, _, data = s[len(tag):].partition(" ")
                if kind == "RC":
                    try:
                        rc = int(data)
                    except ValueError:
                        pass
                elif kind == "TIMEOUT":
                    timed_out = True
            else:
                kept.append(line)
        stderr = "".join(kept).strip("\n")
        if rc is None:
            tail = " ".join(stderr.split())[-300:]
            if cp.returncode == 255:
                vivo = (f" ATENCIÓN: el proceso remoto PID {seen['pid']} pudo seguir vivo en el host (se corta solo al "
                        f"plazo); el runner lo comprueba y lo corta antes de relanzar") if "pid" in seen else ""
                raise HostUnreachable(f"host {self.host.destino} sin conexión (ssh rc=255): {tail}.{vivo}")
            raise HostError(f"host {self.host.destino}: el lanzador remoto no llegó a correr (ssh rc={cp.returncode}): {tail}")
        if timed_out:
            raise subprocess.TimeoutExpired(args, timeout)
        self.remote_finished = True  # llegó la línea RC: el proceso remoto terminó (o lo cortó el lanzador al plazo)
        return subprocess.CompletedProcess(args, rc, cp.stdout, stderr)


# --- Estado: PID remoto en claves propias --------------------------------------------------------------------------

def record_host_worker(state_path: Path, pid) -> None:
    """Apunta host, PID remoto y su hora de creación en .state/tasks/<id>.json. Claves propias (`host`,
    `host_worker_pid`, `host_worker_created`) y NUNCA `worker_pid`: el runner mata/reconoce `worker_pid` como proceso
    local y, con un número del host, actuaría sobre un proceso cualquiera del hub (E2 cortará los huérfanos remotos)."""
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(state, dict):
        return
    state["host"] = getattr(pid, "host", None)
    state["host_worker_pid"] = int(pid)
    state["host_worker_created"] = getattr(pid, "created", None)
    state["host_worker_started"] = time.time()  # hora del hub: caduca un PID sin hora de creación (ver cortar_huerfano)
    tmp = Path(state_path).with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, state_path)


def sanear_coste(cost, presupuesto: float):
    """`cost_usd` lo declara el host: no es de fiar. Negativo, no numérico o NaN = 0; se satura a 2x el presupuesto de
    la tarjeta (un coste real puede pasarse algo del tope, uno absurdo no). None se queda None."""
    if cost is None:
        return None
    try:
        c = float(cost)
    except (TypeError, ValueError):
        return 0.0
    if c != c or c < 0:
        return 0.0
    return min(c, 2 * float(presupuesto))


def limpiar_host_worker(state_path: Path) -> None:
    """El proceso remoto YA terminó (hubo línea RC): se borra lo apuntado de él. Si no se llegó a saber (transporte caído),
    se deja para que el siguiente despacho lo corte antes de relanzar (E2)."""
    try:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(state, dict) or "host_worker_pid" not in state:
        return
    for k in ("host_worker_pid", "host_worker_created", "host_worker_started"):
        state.pop(k, None)
    tmp = Path(state_path).with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, state_path)


def _presupuesto_de(args) -> float | None:
    """El `--max-budget-usd` de la llamada (para estimar el gasto cuando el host no declara coste)."""
    try:
        return float(args[list(args).index("--max-budget-usd") + 1])
    except (ValueError, IndexError):
        return None


class ClaudeWorkerRemoto(ClaudeWorker):
    """ClaudeWorker de un carril con host: mismo prompt, mismos args y mismo reparto de presupuesto; solo cambia quién
    ejecuta (SshRunner) y a quién se apunta el PID. Sin GuardiaRam (mide procesos locales)."""

    def __init__(self, pid_dir: Path | None = None, transport=None, store: host_ops.HostStore | None = None, **kw):
        super().__init__(pid_dir=pid_dir, **kw)
        self._transport = transport
        self._store = store or (host_ops.default_store(pid_dir) if pid_dir else None)

    def cortar_huerfano(self, lane: Lane, task_id: str, now: float | None = None) -> str:
        """Antes de relanzar una tarjeta: si el estado apunta a un `claude` remoto de un intento anterior, se corta SOLO si
        sigue siendo el mismo proceso (hora de creación; la comprueba el lanzador del host). Devuelve el estado:
        `limpio` (nada que cortar o ya cortado: se borra lo apuntado y se puede lanzar) o `pendiente` (no se pudo
        comprobar o sigue vivo: NO se lanza, la tarjeta sigue en ready). Un fallo de transporte se propaga."""
        if not self._pid_dir:
            return "limpio"
        path = Path(self._pid_dir) / f"{task_id}.json"
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "limpio"
        if not isinstance(state, dict) or state.get("host_worker_pid") is None:
            return "limpio"
        if state.get("host") not in (None, lane.host.destino):
            log.warning("%s: el worker remoto apuntado era de otro host (%s); se ignora", task_id, state.get("host"))
            limpiar_host_worker(path)
            return "limpio"
        now = time.time() if now is None else now
        res = SshRunner(lane.host, self._transport).op("kill", timeout=60, pid=int(state["host_worker_pid"]),
                                                       created=state.get("host_worker_created"))
        estado = str(res.get("estado") or "")
        if estado in ("cortado", "no_existe", "otro_proceso", "pid_invalido"):
            log.warning("%s: worker remoto huérfano (pid %s en %s): %s", task_id, state["host_worker_pid"], lane.host.destino,
                        estado)
            limpiar_host_worker(path)
            return "limpio"
        if estado == "sin_hora":
            # Sin hora de creación no se corta a ciegas. Pasado el plazo máximo del carril el lanzador ya lo habrá cortado.
            if now - float(state.get("host_worker_started") or now) > lane.max_runtime_seconds + 300:
                limpiar_host_worker(path)
                return "limpio"
        log.warning("%s: worker remoto huérfano sin poder cortarlo (%s); no se relanza", task_id, estado)
        return "pendiente"

    def _exec(self, args, prompt, cwd, timeout, lane, task_id, extra_env=None):
        state_path = Path(self._pid_dir) / f"{task_id}.json" if self._pid_dir else None

        def on_start(pid) -> None:
            if state_path:
                record_host_worker(state_path, pid)

        def on_local_pid(pid: int) -> None:
            # PID del ssh LOCAL en worker_pid: si el runner muere, reconcile lo mata (y _live_worker lo ve). Matar el ssh
            # no mata al claude remoto (corta al plazo); eso, y el PID remoto (host_worker_pid), es de E2.
            if state_path:
                from .worker import record_worker_pid
                record_worker_pid(state_path, pid)

        ssh = SshRunner(lane.host, self._transport, on_local_pid=on_local_pid)
        try:
            out = run_claude(ssh, args, prompt, cwd, timeout,
                             {"AGENT_LANES_TASK": task_id, "AGENT_LANES_LANE": lane.name, **(extra_env or {})},
                             on_start=on_start if state_path else None)
        except SshTransportError:
            self._gastar(lane, None, args)  # no sabemos qué gastó: se estima con el presupuesto de la llamada
            raise
        out.cost_usd = sanear_coste(out.cost_usd, lane.max_budget_usd)
        self._gastar(lane, out.cost_usd, args)
        if state_path and ssh.remote_finished:
            limpiar_host_worker(state_path)  # el proceso remoto terminó: nada que cortar después
        return out

    def _gastar(self, lane: Lane, cost, args) -> None:
        """Gasto del host del día: lo declarado (saneado) o, si no hay, el presupuesto de la llamada (estimado, conservador)."""
        if self._store is None:
            return
        usd = cost if cost is not None else (_presupuesto_de(args) or lane.max_budget_usd)
        try:
            self._store.add_gasto(lane.host.destino, float(usd))
        except (OSError, ValueError) as exc:
            log.warning("gasto del host %s no apuntado: %s", lane.host.destino, exc)


class ClaudeReviewerRemoto(ClaudeReviewer):
    """Revisor de un carril con `host_review: true`: el `claude` revisor corre EN el host (mismos args y mismo guard de solo
    lectura que el local) y al hub solo vuelve el informe JSON. Nada de lo que escribió el host se ejecuta en el hub."""

    def __init__(self, transport=None, store: host_ops.HostStore | None = None):
        super().__init__(runner=None)
        self._transport, self._store = transport, store

    def _ejecutar(self, lane: Lane, presupuesto: float, args, prompt: str, cwd: str, timeout: float, env: dict) -> WorkerOutcome:
        out = run_claude(SshRunner(lane.host, self._transport), args, prompt, cwd, timeout, env)
        out.cost_usd = sanear_coste(out.cost_usd, presupuesto)  # el tope es el del CARRIL REVISOR
        if self._store is not None:
            try:
                self._store.add_gasto(lane.host.destino, out.cost_usd if out.cost_usd is not None
                                      else (_presupuesto_de(args) or presupuesto))
            except (OSError, ValueError) as exc:
                log.warning("gasto del host %s no apuntado: %s", lane.host.destino, exc)
        return out

    def run(self, review_lane, lane, task, meta, cwd, session_id, timeout) -> WorkerOutcome:
        args = review_args(review_lane, ["--session-id", session_id, "--name", f"review-{task['id']}"])
        return self._ejecutar(lane, review_lane.max_budget_usd, args, review_prompt(lane, task, meta), cwd, timeout, self._env(review_lane, task))

    def resume(self, review_lane, lane, task, cwd, session_id, timeout) -> WorkerOutcome:
        return self._ejecutar(lane, review_lane.max_budget_usd, review_args(review_lane, ["--resume", session_id]),
                              "Termina la revisión y devuelve el JSON del schema.", cwd, timeout,
                              self._env(review_lane, task))


def pila_review_remota(lane: Lane, transport=None, store=None):
    """(git, reviewer, verifier) que ejecutan la revisión EN el host (la factoría que recibe ReviewRunner)."""
    return GitOpsRemoto(transport), ClaudeReviewerRemoto(transport, store), (
        lambda l, tid, cwd, result: verify_remoto(l, tid, cwd, result, transport=transport))


# --- Git en el host -------------------------------------------------------------------------------------------------

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def _lock(host: HostCfg) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(f"{host.destino}|{host.repo_dir.lower()}", threading.Lock())


_TID = re.compile(r"t_[0-9A-Za-z_]{1,40}")
_SHA = re.compile(r"[0-9a-fA-F]{7,40}")


def worktree_remoto(lane: Lane, task_id: str) -> str:
    if not _TID.fullmatch(task_id or ""):  # el id acaba en una ruta del host: solo caracteres de nombre de fichero
        raise ValueError(f"id de tarjeta no válido para una ruta remota: {task_id!r}")
    return ntpath.join(lane.host.worktree_root, f"lane-{task_id}")


class GitOpsRemoto:
    """GitOps de un carril con host: el repo y el worktree viven en el host (`lane.host.repo_dir/worktree_root`).
    Un fallo de transporte (HostUnreachable/HostError) se propaga tal cual: no es un GitError de la tarjeta."""

    def __init__(self, transport=None, sleep=time.sleep):
        self._transport = transport
        self._sleep = sleep

    def _ssh(self, lane: Lane) -> SshRunner:
        return SshRunner(lane.host, self._transport)

    def _git(self, lane: Lane, repo: str, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
        return self._ssh(lane)(["git", "-C", repo, *args], timeout=timeout)

    def _git_ok(self, lane: Lane, repo: str, *args: str) -> subprocess.CompletedProcess:
        """`git` que reintenta si .git/config está bloqueado por otro git del host y lanza GitError si falla."""
        for intento in range(len(_REINTENTOS_BLOQUEO) + 1):
            cp = self._git(lane, repo, *args)
            if cp.returncode == 0:
                return cp
            if intento == len(_REINTENTOS_BLOQUEO) or not any(m in cp.stderr for m in _MARCAS_BLOQUEO):
                break
            self._sleep(_REINTENTOS_BLOQUEO[intento])
        raise GitError(f"git {' '.join(args[:2])} falló en {lane.host.destino}: {cp.stderr.strip()[:300]}")

    def prepare_worktree(self, lane: Lane, task_id: str) -> str:
        """Ruta (del HOST) del worktree `lane-<id>` en la rama lane/<id>. Misma lógica que GitOps._prepare_worktree."""
        host, repo, remote, branch = lane.host, lane.host.repo_dir, lane.remote, f"lane/{task_id}"
        path = worktree_remoto(lane, task_id)
        with _lock(host):
            self._git_ok(lane, repo, "fetch", remote, lane.base)
            cur = self._git(lane, path, "branch", "--show-current")
            if cur.returncode == 0:  # reintento de la misma tarea: se reutiliza su worktree y su rama
                if cur.stdout.strip() != branch:
                    raise GitError(f"{path} existe en {host.destino} pero está en '{cur.stdout.strip()}', no en {branch}")
                return path
            if self._git(lane, repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
                self._git_ok(lane, repo, "worktree", "add", path, branch)
            elif self._git(lane, repo, "fetch", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
                           ).returncode == 0:
                self._git_ok(lane, repo, "worktree", "add", path, "-b", branch, f"{remote}/{branch}")
            else:
                self._git_ok(lane, repo, "worktree", "add", path, "-b", branch, f"{remote}/{lane.base}")
            return path

    def sync_base(self, lane: Lane, path: str) -> str | None:
        """Como GitOps.sync_base, en el host: fetch + merge de <remote>/<base>; con conflicto devuelve el texto."""
        ref = lane.base_ref
        fetch = self._git(lane, path, "fetch", lane.remote, lane.base)
        if fetch.returncode != 0:
            log.warning("sync_base %s@%s: fetch falló: %s", path, lane.host.destino, fetch.stderr.strip()[:200])
            return None
        cp = self._git(lane, path, "merge", "--no-edit", ref)
        if cp.returncode == 0:
            return None
        out = (cp.stdout + cp.stderr).strip()
        if "CONFLICT" in out:
            return out[-600:]
        log.warning("sync_base %s@%s: merge %s falló sin conflicto: %s", path, lane.host.destino, ref, out[-200:])
        return None

    def prepare_review_worktree(self, lane: Lane, task_id: str) -> str:
        """Worktree de revisión EN el host: el de la tarjeta si sigue allí; si no, se recrea desde la rama remota. Nunca crea
        una rama nueva desde la base (revisaría algo vacío): si lane/<id> no existe en el remoto, GitError."""
        branch = f"lane/{task_id}"
        with_remote = self._git(lane, lane.host.repo_dir, "fetch", lane.remote, f"+refs/heads/{branch}:refs/remotes/{lane.remote}/{branch}")
        if with_remote.returncode != 0:
            raise GitError(f"{branch} no se pudo traer de {lane.remote} en {lane.host.destino}: {with_remote.stderr.strip()[:200]}")
        return self.prepare_worktree(lane, task_id)

    def _lista_worktrees(self, lane: Lane) -> set[str] | None:
        cp = self._git(lane, lane.host.repo_dir, "worktree", "list", "--porcelain")
        if cp.returncode != 0:
            return None
        return {_norm_ruta(l[len("worktree "):]) for l in (cp.stdout or "").splitlines() if l.startswith("worktree ")}

    def cleanup(self, lane: Lane, task_id: str) -> tuple[bool, str]:
        """Retira SOLO el worktree remoto `lane-<id>` de ESTA tarjeta (`git worktree remove`; `--force` únicamente si el
        HEAD del worktree es exactamente la rama que GitHub tiene, comprobado desde el hub). No borra la rama del host ni
        carpetas ni nada más: limpiar el resto lo decide Oscar. Un fallo de transporte se propaga."""
        path = worktree_remoto(lane, task_id)  # valida el id
        conocidos = self._lista_worktrees(lane)
        if conocidos is None:
            return False, "no se pudo listar los worktrees del host"
        if _norm_ruta(path) not in conocidos:
            return True, "sin worktree remoto"
        cp = self._git(lane, lane.host.repo_dir, "worktree", "remove", path)
        if cp.returncode == 0:
            return True, "worktree remoto retirado"
        head = self._git(lane, path, "rev-parse", "HEAD")
        remoto = _git(lane.repo, "ls-remote", lane.remote, f"refs/heads/lane/{task_id}")
        sha = remoto.stdout.split()[0].lower() if remoto.returncode == 0 and remoto.stdout.strip() else ""
        mine = head.stdout.strip().lower() if head.returncode == 0 else ""
        if not sha or sha != mine:
            return False, (f"worktree remoto no retirado: tiene cambios y la rama de GitHub ({sha[:10] or 'ausente'}) no "
                           f"coincide con su HEAD ({mine[:10] or '?'}); no se fuerza. git: {cp.stderr.strip()[:150]}")
        cp = self._git(lane, lane.host.repo_dir, "worktree", "remove", "--force", path)
        return (True, "worktree remoto retirado (--force: su HEAD ya está en GitHub)") if cp.returncode == 0 else (
            False, f"worktree remoto no retirado ni con --force: {cp.stderr.strip()[:150]}")

    def base_contiene(self, lane: Lane, cwd: str, *shas: str) -> bool:
        """«Ya resuelto» con prueba, desde el HUB: cada SHA existe y es punta o ancestro de <remote>/<base> en el clon del
        hub (tras fetch). El host no opina. Cualquier fallo -> False (la tarjeta sigue su camino normal)."""
        if not shas or not all(shas) or not all(_SHA.fullmatch(x) for x in shas):
            return False
        try:
            if _git(lane.repo, "fetch", lane.remote, lane.base).returncode != 0:
                return False
            return all(_git(lane.repo, "cat-file", "-e", f"{x}^{{commit}}").returncode == 0
                       and _git(lane.repo, "merge-base", "--is-ancestor", x, lane.base_ref).returncode == 0 for x in shas)
        except Exception as exc:  # noqa: BLE001
            log.warning("base_contiene (hub) %s: %s", lane.repo, exc)
            return False

    def poner_al_dia(self, lane: Lane, path: str, task_id: str) -> AlDia:
        """P4 en el host (misma lógica que GitOps.poner_al_dia): merge (nunca rebase ni force), test_cmd en el host y push de
        la rama propia. El SHA resultante se confirma DESDE EL HUB (ls-remote): lo que declare el host no cuenta. Todo lo
        dudoso es «omitida»: la tarjeta sigue a review como antes. Un fallo de transporte se propaga."""
        ref, branch = lane.base_ref, f"lane/{task_id}"

        def git(*a, timeout=300):
            return self._ssh(lane)(["git", "-C", path, *a], timeout=timeout)
        if git("fetch", lane.remote, lane.base).returncode != 0:
            return AlDia("omitida", detalle="fetch falló en el host")
        atras = git("rev-list", "--count", f"HEAD..{ref}")
        if atras.returncode != 0:
            return AlDia("omitida", detalle="rev-list falló en el host")
        if atras.stdout.strip() in ("", "0"):
            return AlDia("al_dia")
        mt = git("merge-tree", "--write-tree", "--name-only", "--no-messages", "HEAD", ref)
        if mt.returncode == 1:
            lineas = mt.stdout.splitlines()[1:]
            ficheros = [l.strip() for l in (lineas[:lineas.index("")] if "" in lineas else lineas) if l.strip()]
            return AlDia("conflicto", ficheros=ficheros, detalle=f"{ref} choca con {branch}")
        if mt.returncode != 0:
            return AlDia("omitida", detalle=f"merge-tree no disponible en el host: {mt.stderr.strip()[:150]}")
        antes = git("rev-parse", "HEAD").stdout.strip()
        if git("merge", "--no-edit", ref).returncode != 0:
            git("merge", "--abort")
            return AlDia("omitida", detalle="merge falló sin conflicto en merge-tree")
        if lane.test_cmd:
            t = self._ssh(lane)(render_test_cmd_host(lane), shell=True, cwd=path, timeout=1800)
            if t.returncode != 0:
                git("reset", "--hard", antes)  # su propio worktree: vuelve a como estaba
                return AlDia("tests", detalle=f"test_cmd falló en {lane.host.destino} tras fusionar {ref} (exit "
                             f"{t.returncode}): {(t.stdout + t.stderr).strip()[-300:]}")
        if git("push", lane.remote, f"HEAD:refs/heads/{branch}").returncode != 0:
            git("reset", "--hard", antes)
            return AlDia("omitida", detalle="push de la rama fusionada rechazado")
        local = git("rev-parse", "HEAD").stdout.strip().lower()
        ls = _git(lane.repo, "ls-remote", lane.remote, f"refs/heads/{branch}")
        nuevo = ls.stdout.split()[0].lower() if ls.returncode == 0 and ls.stdout.strip() else ""
        if not nuevo or nuevo != local:
            return AlDia("omitida", detalle="el SHA de la rama puesta al día no se pudo confirmar desde el hub")
        return AlDia("fusionada", sha=nuevo)


def _norm_ruta(p: str) -> str:
    return ntpath.normcase(ntpath.normpath(p.strip().replace("/", "\\")))


def sweep_done_remoto(lane: Lane, hermes, git: "GitOpsRemoto", *, max_tarjetas: int = 3, hechas: set | None = None) -> list[str]:
    """Limpieza de worktrees remotos de tarjetas en done (mismo criterio que review.sweep_done, pero en el host). Como mucho
    `max_tarjetas` por pasada y sin repetir las ya comprobadas (`hechas`): cada comprobación es una llamada SSH."""
    hechas = hechas if hechas is not None else set()
    limpias, intentadas = [], 0
    for task in hermes.list_status(lane.name, "done"):
        tid = task["id"]
        if tid in hechas or not _TID.fullmatch(tid or ""):
            continue
        if max_tarjetas and intentadas >= max_tarjetas:
            break
        intentadas += 1
        try:
            ok, msg = git.cleanup(lane, tid)
        except SshTransportError as exc:
            log.info("%s: limpieza remota aplazada (transporte): %s", tid, exc)
            break  # sin host no hay más que probar esta pasada
        if ok:
            hechas.add(tid)
            if "retirado" in msg:
                limpias.append(tid)
        else:
            log.warning("%s: limpieza remota pendiente: %s", tid, msg)
    return limpias


# --- verify dividido ------------------------------------------------------------------------------------------------

def render_test_cmd_host(lane: Lane) -> str:
    """test_cmd (solo de lanes.yaml) con {AGENT_LANES_DIR} del HOST y {BASE}."""
    return lane.test_cmd.replace("{AGENT_LANES_DIR}", lane.host.agent_lanes_dir).replace("{BASE}", lane.base_ref)


def verify_remoto(lane: Lane, task_id: str, cwd: str, result: dict, runner=_proc.run, transport=None) -> VerifyResult:
    """Mismos controles que `verify.verify`, repartidos: la parte git en el HUB contra el remoto de GitHub (`lane.repo` es
    el clon del hub; sin worktree; ni el SHA ni el estado que declare el host cuentan) y `test_cmd` por SSH en el worktree
    remoto, exigiendo exit 0 y que `rev-parse HEAD` del host sea el SHA que GitHub tiene. `cwd` es la ruta DEL HOST."""
    branch = f"lane/{task_id}"
    reasons: list[str] = []
    claimed = (result.get("head_sha") or "").strip().lower()
    if result.get("branch") != branch:
        reasons.append(f"branch declarada '{result.get('branch')}' != '{branch}'")

    repo = lane.repo
    ls = _git(repo, "ls-remote", lane.remote, f"refs/heads/{branch}", runner=runner)
    remote_sha = ls.stdout.split()[0].lower() if ls.returncode == 0 and ls.stdout.strip() else None
    if not remote_sha:
        reasons.append(f"{branch} no existe en {lane.remote}")
    elif len(claimed) < 7 or not remote_sha.startswith(claimed):
        reasons.append(f"head_sha declarado '{claimed[:12]}' != remoto '{remote_sha[:12]}'")

    if remote_sha:
        # Sin worktree local: se traen la base y la rama a `lane.repo` para leer los commits de GitHub, no del host.
        fetch = _git(repo, "fetch", lane.remote, lane.base, f"+refs/heads/{branch}:refs/remotes/{lane.remote}/{branch}",
                     runner=runner)
        if fetch.returncode != 0:
            reasons.append(f"no se pudo traer {branch} de {lane.remote} al hub: {(fetch.stderr or '').strip()[-150:]}")
        else:
            if _git(repo, "cat-file", "-e", f"{remote_sha}^{{commit}}", runner=runner).returncode != 0:
                reasons.append(f"commit {remote_sha[:12]} no existe en el hub tras traerlo de {lane.remote}")
            ahead = _git(repo, "rev-list", "--count", f"{lane.remote}/{lane.base}..{remote_sha}", runner=runner)
            if ahead.returncode != 0 or ahead.stdout.strip() in ("", "0"):
                reasons.append(f"{branch} no tiene commits sobre {lane.remote}/{lane.base}")
            if lane.forbidden_paths:
                changed = split_paths(_git(repo, "diff", "-z", "--no-renames", "--name-only",
                                           f"{lane.base_ref}...{remote_sha}", runner=runner).stdout)
                hits = [f for f in changed if vetada(f, lane.forbidden_paths)]
                if hits:
                    reasons.append(f"toca rutas vetadas en este carril: {', '.join(hits[:10])}")
            if not reasons:
                from .integrator import scan_secrets  # import tardío: integrator importa verify
                diff = _git(repo, "diff", "--no-color", "--no-ext-diff", "--no-renames", f"{lane.base_ref}...{remote_sha}",
                            runner=runner)
                if diff.returncode != 0:
                    reasons.append("no se pudo revisar si la rama lleva secretos (git diff falló)")
                else:
                    hits = scan_secrets(diff.stdout or "")
                    if hits:
                        reasons.append("posible secreto en la rama empujada: "
                                       + ", ".join(f"{p}:{n} ({r})" for p, n, r in hits[:5]))

    ssh = SshRunner(lane.host, transport)
    if remote_sha and not reasons:
        try:
            head = ssh(["git", "-C", cwd, "rev-parse", "HEAD"], timeout=120)
        except (SshTransportError, subprocess.TimeoutExpired) as exc:
            reasons.append(f"no se pudo comprobar el HEAD del host {lane.host.destino}: {exc}")
        else:
            got = head.stdout.strip().lower() if head.returncode == 0 else ""
            if got != remote_sha:
                reasons.append(f"HEAD del host '{got[:12]}' != remoto '{remote_sha[:12]}' (cambios sin empujar)")
            else:  # los tests deben probar lo empujado, no ficheros sueltos del worktree del host
                try:
                    dirty = ssh(["git", "-C", cwd, "status", "--porcelain", "--untracked-files=all"], timeout=120)
                except (SshTransportError, subprocess.TimeoutExpired) as exc:
                    reasons.append(f"no se pudo comprobar el worktree del host {lane.host.destino}: {exc}")
                else:
                    if dirty.returncode != 0 or dirty.stdout.strip():
                        reasons.append("worktree del host con cambios sin commitear o ficheros sin seguimiento")

    if reasons or not lane.test_cmd:
        # test_cmd ejecuta código de la rama: nunca sobre una rama que ya falló los controles.
        return VerifyResult(ok=not reasons, reasons=reasons, test_exit=None, remote_sha=remote_sha, verificado_en="host")
    test_exit = None
    try:
        t = ssh(render_test_cmd_host(lane), shell=True, cwd=cwd, timeout=1800)
        test_exit = t.returncode
        if t.returncode != 0:
            reasons.append(f"test_cmd falló en {lane.host.destino} (exit {t.returncode}): "
                           f"{(t.stdout + t.stderr).strip()[-300:]}")
    except subprocess.TimeoutExpired:
        reasons.append("test_cmd superó 30 min")
    except SshTransportError as exc:  # transporte, no «tests rojos»
        reasons.append(f"no se pudo ejecutar test_cmd en el host: {exc}")
    return VerifyResult(ok=not reasons, reasons=reasons, test_exit=test_exit, remote_sha=remote_sha,
                        verificado_en="host")
