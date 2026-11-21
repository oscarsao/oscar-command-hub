"""Worker remoto por SSH (E1 de specs/worker-remoto-ssh.md): el hub despacha un carril con `host:` a un segundo equipo.

Nada de esto toca a los carriles sin `host:`: el runner elige estas clases solo para los que lo tienen
(`pila_para`), así que el camino local (GitOps, ClaudeWorker, verify) queda exactamente como estaba.

Piezas
  SshRunner         misma firma que el `runner` de `worker.run_claude` (y que `subprocess.run` para git y test_cmd).
                    Corre en el host, por `remote_exec.py`, y devuelve un CompletedProcess normal.
  GitOpsRemoto      worktree de la tarjeta y sync de la base en el host (el push de la rama lo hace el worker allí).
  ClaudeWorkerRemoto  ClaudeWorker que despacha por SshRunner y apunta el PID REMOTO en claves propias del estado.
  verify_remoto     parte git en el hub contra GitHub (sin fiarse del host) + parte test_cmd por SSH en el host.

TODO documentados (fuera de E1, ver la spec §4 y §6):
  E2  reencolar sin contar bloqueo ante HostUnreachable, cortar huérfanos remotos (host_worker_pid/created ya se
      guardan para eso), disco mínimo, pausa de cuota por host, tope `host.max_parallel` compartido entre carriles,
      limpieza (`cleanup`) del worktree remoto, `poner_al_dia` y `base_contiene` en remoto, comprobar SHA/hash del
      contrato del host antes de despachar, dependencias (pnpm) del worktree remoto.
  E3  latido, panel, aviso de 5 min, resumen diario, ajustes en /ajustes y `host_gasto_dia_usd`.
"""
from __future__ import annotations

import json
import logging
import ntpath
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import proc as _proc
from .config import ROOT, HostCfg, Lane
from .git_ops import _MARCAS_BLOQUEO, _REINTENTOS_BLOQUEO, AlDia, GitError
from .verify import VerifyResult, _git, split_paths, vetada
from .worker import ClaudeWorker, run_claude

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
        for raw in iter(p.stderr.readline, b""):
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
        raise HostError(f"salida remota superior a {max_bytes} bytes: ssh cortado (el proceso remoto muere al plazo)")
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

    def __call__(self, args, *, input=None, cwd=None, env=None, timeout=None, on_start=None, shell=False,
                 **_ignored) -> subprocess.CompletedProcess:
        nonce = secrets.token_hex(8)
        req = {"v": 1, "nonce": nonce, "shell": bool(shell), "cwd": cwd, "input": input, "timeout": timeout,
               # Lista blanca: run_claude pasa {**os.environ, **env_extra}; ningún secreto del hub viaja al host.
               "env": {k: v for k, v in (env or {}).items() if k in ENV_ALLOWED},
               "local_roots": self._local_roots}
        req["cmd" if shell else "args"] = args if shell else list(args)
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
        cp = transport(ssh_argv(self.host), input=json.dumps(req), timeout=deadline, on_stderr_line=on_line, **extra)
        if len(cp.stdout or "") + len(cp.stderr or "") > MAX_OUTPUT_BYTES:
            raise HostError(f"salida remota superior a {MAX_OUTPUT_BYTES} bytes: descartada")

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
                        f"plazo); no relances esta tarjeta hasta comprobarlo (E2)") if "pid" in seen else ""
                raise HostUnreachable(f"host {self.host.destino} no alcanzable (ssh rc=255): {tail}.{vivo}")
            raise HostError(f"host {self.host.destino}: el lanzador remoto no llegó a correr (ssh rc={cp.returncode}): {tail}")
        if timed_out:
            raise subprocess.TimeoutExpired(args, timeout)
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


class ClaudeWorkerRemoto(ClaudeWorker):
    """ClaudeWorker de un carril con host: mismo prompt, mismos args y mismo reparto de presupuesto; solo cambia quién
    ejecuta (SshRunner) y a quién se apunta el PID. Sin GuardiaRam (mide procesos locales)."""

    def __init__(self, pid_dir: Path | None = None, transport=None, **kw):
        super().__init__(pid_dir=pid_dir, **kw)
        self._transport = transport

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

        out = run_claude(SshRunner(lane.host, self._transport, on_local_pid=on_local_pid), args, prompt, cwd, timeout,
                         {"AGENT_LANES_TASK": task_id, "AGENT_LANES_LANE": lane.name, **(extra_env or {})},
                         on_start=on_start if state_path else None)
        out.cost_usd = sanear_coste(out.cost_usd, lane.max_budget_usd)
        return out


# --- Git en el host -------------------------------------------------------------------------------------------------

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}


def _lock(host: HostCfg) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(f"{host.destino}|{host.repo_dir.lower()}", threading.Lock())


def worktree_remoto(lane: Lane, task_id: str) -> str:
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

    def poner_al_dia(self, lane: Lane, path: str, task_id: str) -> AlDia:
        """E2: «omitida» = la tarjeta sigue su camino a review como antes de existir este paso (no se fusiona nada)."""
        return AlDia("omitida", detalle="carril remoto: poner al día la rama llega en E2")


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
