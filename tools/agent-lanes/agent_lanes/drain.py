"""Reinicio ordenado del runner (drain), tarjeta t_bdee05fe (incidente 27-09: un `schtasks /End` con 2 workers recién
lanzados dejó uno huérfano y duplicó otro).

    py -3.12 lanes.py restart --drain [--timeout 3600]

1. Crea .state/drain.json. El runner lo ve al empezar cada vuelta del bucle principal: deja de reclamar (ni carriles,
   ni review, ni pasadas del integrador) y escribe .state/drain_ack.json con su PID. Como las pasadas son síncronas,
   ese acuse solo llega cuando han terminado todos sus workers; `idle` es False mientras un botón de Oscar tenga un
   deploy o unos gates del integrador en marcha.
2. Con el acuse ocioso del runner vivo: `schtasks /End` de la tarea "agent-lanes runner", espera a que muera su PID
   (la tarea usa MultipleInstances IgnoreNew: un /Run con el viejo aún vivo no haría nada), borra el drenaje y
   `schtasks /Run`. Comprueba que arranca un runner nuevo.
3. Sin acuse en `--timeout` segundos: borra el drenaje, NO reinicia y sale con error.

Aplicar también actualiza el código (5/10): antes de drenar, `git fetch` del checkout vivo y, si hay cambios en
ficheros versionados o main local diverge, NO reinicia y avisa ALTA; con el runner parado hace `merge --ff-only
origin/main`; tras arrancar verifica HEAD == origin/main y la marca «integrador activo». El veredicto va a
.state/apply_result.json, que el integrador convierte en «APLICADO <sha>» o ⛔ con el motivo.

Un drain.json de más de 2 h se ignora siempre (y se borra al arrancar el runner): un `restart` que muriera a medias
no puede dejar los carriles sin reclamar indefinidamente.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

from . import proc as _proc
from .config import ROOT

log = logging.getLogger("agent_lanes")

STATE = ROOT / ".state"
DRAIN_FILE = STATE / "drain.json"
ACK_FILE = STATE / "drain_ack.json"
LOCK_FILE = STATE / "runner.lock"
BUSY_FILE = STATE / "busy.json"  # lo escribe Service mientras dura una pasada con trabajo (review incluido)
RESULT_FILE = STATE / "apply_result.json"  # veredicto del último restart: {ok, sha, reason, at}
ACTIVE_FILE = STATE / "integrator_active.json"  # {pid, at}: el runner nuevo ya tiene el integrador activo
LIVE_REPO = ROOT.parents[1]  # checkout vivo (oscar-command-hub, rama main) que ejecuta el runner
MAX_AGE_SECONDS = 2 * 3600
TASK_NAME = "agent-lanes runner"
SCHTASKS = "schtasks"


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def request(path: Path = DRAIN_FILE, *, now: float | None = None) -> dict:
    data = {"since": time.time() if now is None else now, "pid": os.getpid()}
    _write(path, data)
    return data


def active(path: Path = DRAIN_FILE, *, now: float | None = None, max_age: float = MAX_AGE_SECONDS) -> dict | None:
    """El drenaje vigente, o None (sin fichero, ilegible o de más de `max_age`: se ignora)."""
    data = _read(path)
    if not data:
        return None
    try:
        age = (time.time() if now is None else now) - float(data.get("since") or 0)
    except (TypeError, ValueError):
        return None
    return data if 0 <= age <= max_age else None


def discard_stale(path: Path = DRAIN_FILE, *, now: float | None = None, max_age: float = MAX_AGE_SECONDS) -> bool:
    """Al arrancar el runner: borra un drain.json caducado (o ilegible). True si había uno y se ignoró."""
    if not Path(path).exists() or active(path, now=now, max_age=max_age):
        return False
    log.warning("fichero de drenaje de más de %d h (o ilegible): se ignora y se borra", max_age // 3600)
    clear(path, None)
    return True


def clear(path: Path = DRAIN_FILE, ack: Path | None = ACK_FILE) -> None:
    for p in (path, ack):
        if p is not None:
            Path(p).unlink(missing_ok=True)


def write_ack(path: Path = ACK_FILE, *, pid: int | None = None, busy: list[str] | None = None,
              now: float | None = None) -> None:
    """Lo escribe el runner en cada vuelta mientras dura el drenaje (solo entre pasadas)."""
    busy = list(busy or ())
    _write(path, {"pid": os.getpid() if pid is None else pid, "at": time.time() if now is None else now,
                  "idle": not busy, "busy": busy})


def read_ack(path: Path = ACK_FILE) -> dict | None:
    return _read(path)


def runner_pid(lock: Path = LOCK_FILE) -> int | None:
    try:
        return int((_read(lock) or {}).get("pid") or 0) or None
    except (TypeError, ValueError):
        return None


def mark_integrator_active(path: Path = ACTIVE_FILE) -> None:
    """Lo escribe el runner al arrancar el integrador (línea «integrador activo»): el restart lo comprueba."""
    _write(path, {"pid": os.getpid(), "at": time.time()})


def _git(run, repo: Path, *args: str) -> tuple[int, str]:
    cp = run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
             timeout=120)
    # rstrip: el primer carácter de `status --porcelain` puede ser un espacio significativo
    return cp.returncode, ((cp.stdout or "") if cp.returncode == 0 else (cp.stderr or cp.stdout or "")).rstrip()


def sync_check(repo: Path, run=_proc.run, branch: str = "main") -> tuple[bool, str]:
    """Checkout vivo listo para avanzar a origin/<branch> con fast-forward: fetch, rama correcta, sin cambios en
    ficheros versionados (los no versionados y los __pycache__ se ignoran) y sin commits propios divergentes.
    No modifica el árbol de trabajo."""
    rc, out = _git(run, repo, "fetch", "origin", branch)
    if rc:
        return False, f"git fetch origin {branch} falló: {out[:200]}"
    rc, cur = _git(run, repo, "rev-parse", "--abbrev-ref", "HEAD")
    if rc or cur != branch:
        return False, f"el checkout vivo está en «{cur or '?'}», no en {branch}"
    rc, dirty = _git(run, repo, "status", "--porcelain", "--untracked-files=no")
    if rc or dirty:
        files = [l[3:] if len(l) > 3 else l for l in dirty.splitlines()]
        return False, "cambios locales en ficheros versionados: " + ", ".join(files[:10]) + (
            f" (+{len(files) - 10})" if len(files) > 10 else "")
    if _git(run, repo, "merge-base", "--is-ancestor", "HEAD", f"origin/{branch}")[0]:
        _, log_out = _git(run, repo, "log", "--oneline", "-n", "10", f"origin/{branch}..HEAD")
        return False, f"{branch} local diverge de origin/{branch}; commits propios: " + (
            " | ".join(log_out.splitlines()) or "?")
    return True, ""


def fast_forward(repo: Path, run=_proc.run, branch: str = "main") -> tuple[bool, str]:
    rc, out = _git(run, repo, "merge", "--ff-only", f"origin/{branch}")
    return (True, "") if rc == 0 else (False, f"git merge --ff-only origin/{branch} falló: {out[:200]}")


def live_vs_origin(repo: Path, run=_proc.run, branch: str = "main") -> tuple[str, str]:
    """(HEAD, origin/<branch>) del checkout vivo; '' si no se pueden leer."""
    return (_git(run, repo, "rev-parse", "HEAD")[1][:40], _git(run, repo, "rev-parse", f"origin/{branch}")[1][:40])


def dm_alert(env: dict | None = None) -> Callable[[str], None] | None:
    """Aviso ALTA al DM de Oscar con el bot de carriles (token de .env; nunca se imprime). None sin token."""
    from .config import load_env
    from .decisions import OWNER_TELEGRAM_ID
    from .telegram import TelegramNotifier

    env = load_env() if env is None else env
    token = env.get("CARRILES_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    notifier, owner = TelegramNotifier(token, None), env.get("OWNER_TELEGRAM_ID") or OWNER_TELEGRAM_ID
    return lambda text: notifier.send_to(owner, None, text, html=False)


def read_result(path: Path = RESULT_FILE) -> dict | None:
    return _read(path)


class Restarter:
    """`lanes.py restart --drain`. Todo inyectable: los tests no tocan schtasks ni el .state real.

    Con `repo` (el checkout vivo) también lo pone al día: comprueba antes de drenar que se puede avanzar con
    fast-forward, avanza con el runner parado y, ya reiniciado, verifica HEAD == origin/main y «integrador activo».
    El veredicto queda en `result_file` (lo lee el integrador para dejar la ficha) y, si falla, en `alert`."""

    def __init__(self, *, pid_alive: Callable[[int], bool], drain_file: Path = DRAIN_FILE, ack_file: Path = ACK_FILE,
                 lock_file: Path = LOCK_FILE, run=_proc.run, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], float] = time.time,
                 out: Callable[[str], None] = print, task_name: str = TASK_NAME, poll: float = 5,
                 repo: Path | None = None, git_run=None, result_file: Path = RESULT_FILE,
                 active_file: Path = ACTIVE_FILE, alert: Callable[[str], None] | None = None):
        self.repo = Path(repo) if repo else None
        self._git_run = git_run or run
        self.result_file, self.active_file, self.alert = Path(result_file), Path(active_file), alert
        self.pid_alive = pid_alive
        self.drain_file, self.ack_file, self.lock_file = Path(drain_file), Path(ack_file), Path(lock_file)
        self._run = run
        self._sleep = sleep
        self._clock = clock
        self._now = now
        self.out = out
        self.task_name = task_name
        self.poll = poll

    def _schtasks(self, verb: str) -> bool:
        cp = self._run([SCHTASKS, f"/{verb}", "/TN", self.task_name], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
        if cp.returncode != 0:
            self.out(f"schtasks /{verb} falló: {(cp.stderr or cp.stdout or '').strip()[:300]}")
        return cp.returncode == 0

    def _wait(self, cond: Callable[[], bool], seconds: float) -> bool:
        deadline = self._clock() + seconds
        while True:
            if cond():
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(self.poll)

    def _verdict(self, ok: bool, reason: str = "", sha: str | None = None) -> None:
        if not self.repo:  # sin checkout vivo (tests, uso suelto) no hay nada que contarle al integrador
            return
        _write(self.result_file, {"ok": ok, "sha": sha, "reason": reason, "at": self._now()})

    def _fail(self, reason: str) -> int:
        """Reinicio sin verificar: lo dice por consola, deja el veredicto para la ficha y avisa ALTA."""
        self.out(reason)
        self._verdict(False, reason)
        if self.alert:
            try:
                self.alert(f"🚨 ALTA · Aplicar no se pudo completar: {reason}")
            except Exception as exc:  # sin red: queda en consola y en la ficha
                self.out(f"no se pudo enviar el aviso ALTA: {exc}")
        return 1

    def restart(self, timeout: float = 3600) -> int:
        began = self._now()
        if self.repo:
            ok, why = sync_check(self.repo, self._git_run)
            if not ok:  # el runner sigue vivo y sin tocar: no se drena
                return self._fail(f"NO reinicio: {why}")
        old = runner_pid(self.lock_file)
        alive = bool(old) and self.pid_alive(old)
        ff_error = ""
        if alive:
            since = request(self.drain_file, now=self._now())["since"]
            self.out(f"drenando: el runner (pid {old}) deja de reclamar; espero a que acaben sus workers "
                     f"(máx. {int(timeout)} s)")

            def drained() -> bool:
                if not self.pid_alive(old):
                    return True  # murió solo: nada que esperar
                ack = read_ack(self.ack_file) or {}
                return ack.get("pid") == old and float(ack.get("at") or 0) >= since and bool(ack.get("idle"))

            if not self._wait(drained, timeout):
                busy = ", ".join((read_ack(self.ack_file) or {}).get("busy") or ()) or "workers en curso"
                clear(self.drain_file, self.ack_file)
                return self._fail(f"timeout de {int(timeout)} s sin quedar ocioso ({busy}): drenaje cancelado, "
                                  "NO reinicio")
            if self.pid_alive(old):
                if not self._schtasks("End"):
                    clear(self.drain_file, self.ack_file)
                    return self._fail("schtasks /End falló: no reinicio")
                if not self._wait(lambda: not self.pid_alive(old), 60):
                    clear(self.drain_file, self.ack_file)
                    return self._fail(f"el runner (pid {old}) sigue vivo tras /End: no relanzo "
                                      "(IgnoreNew no arrancaría otro)")
        else:
            self.out("runner no vivo: lo arranco sin drenar")
        if self.repo:  # con el runner parado: ya no hay código cargándose a medias
            ok, ff_error = fast_forward(self.repo, self._git_run)
            if not ok:
                self.out(ff_error)
        clear(self.drain_file, self.ack_file)
        if not self._schtasks("Run"):
            return self._fail("schtasks /Run falló: el runner no se ha relanzado")

        def started() -> bool:
            pid = runner_pid(self.lock_file)
            return bool(pid) and pid != old and self.pid_alive(pid)

        # El runner coge el lock tras el test de contrato de hermes (~16 llamadas a la CLI): margen amplio.
        if not self._wait(started, 180):
            return self._fail("la tarea se lanzó, pero no veo un runner nuevo en 180 s: mira .state/runner.log")
        self.out(f"runner reiniciado (pid {runner_pid(self.lock_file)})")
        if not self.repo:
            return 0
        new = runner_pid(self.lock_file)

        def integrator_up() -> bool:
            a = _read(self.active_file) or {}
            return a.get("pid") == new and float(a.get("at") or 0) >= began

        head, origin = live_vs_origin(self.repo, self._git_run)
        if ff_error or not head or head != origin:
            return self._fail(ff_error or f"el checkout vivo ({head[:7] or '?'}) no coincide con origin/main "
                              f"({origin[:7] or '?'}) tras reiniciar")
        if not self._wait(integrator_up, 180):
            return self._fail("el runner arrancó pero no veo «integrador activo» en 180 s: mira .state/runner.log")
        self._verdict(True, sha=head)
        self.out(f"aplicado: HEAD == origin/main ({head[:7]}) e integrador activo")
        return 0
