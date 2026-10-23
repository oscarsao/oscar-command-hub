"""subprocess.run sin ventana de consola en Windows.

El runner corre con pythonw (sin consola); cada hermes/git/claude que lanza abriría una ventana
que roba el foco varias veces por minuto (27-09: dejaba el PC inutilizable).
"""
import logging
import subprocess
import sys
import threading
import time

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def run(*args, on_start=None, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run`. `on_start(pid)`: se llama en cuanto el proceso hijo existe (el runner apunta ahí el PID de
    cada `claude` para poder matarlo o reconocerlo si el propio runner muere antes de que acabe)."""
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
    if on_start is None:
        return subprocess.run(*args, **kwargs)
    # Mismo contrato que subprocess.run (input, capture_output, timeout, check), pero con Popen para ver el PID.
    data = kwargs.pop("input", None)
    timeout = kwargs.pop("timeout", None)
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    if data is not None:
        kwargs["stdin"] = subprocess.PIPE
    with subprocess.Popen(*args, **kwargs) as proc:
        try:
            on_start(proc.pid)
        except Exception:  # apuntar el PID es best effort: nunca debe impedir el trabajo
            pass
        try:
            out, err = proc.communicate(data, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            kill_tree(proc.pid)
            proc.kill()
            proc.communicate()
            raise subprocess.TimeoutExpired(proc.args, timeout, output=exc.output, stderr=exc.stderr) from None
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def created_at(pid: int) -> float | None:
    """Hora de creación (epoch) del proceso, o None si no se sabe (no es Windows o no existe). Distingue un PID
    reutilizado por otro proceso tras un reinicio del PC."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        created, _exit, _kernel, _user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in (created, _exit, _kernel, _user))):
            return None
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime  # 100 ns desde 1601-01-01
        return ticks / 10_000_000 - 11644473600
    finally:
        kernel32.CloseHandle(handle)


def kill_tree(pid: int) -> None:
    """Mata el proceso y sus hijos (claude lanza node, git, pytest…). Best effort."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
        else:
            import os
            import signal
            os.kill(pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass


# --- Llamadas a CLIs externas (hermes, gh, git remoto) con fallos transitorios (t_cce5da81, 23-10) ----------------
# El 23-10 ~100 llamadas a `hermes kanban` y `gh` agotaron los 120 s entre las 14:00 y las 18:00: el planificador no
# listaba tareas, las respuestas de Oscar no se leían y 3 decisiones se perdieron. Un timeout es transitorio: se
# reintenta con espera progresiva y, agotado, se devuelve un fallo (returncode TIMEOUT_RC) en vez de lanzar, para que
# la pasada del hilo siga. Solo se reintenta lo idempotente: un timeout en una escritura es ambiguo (pudo aplicarse).
TIMEOUT_RC = 124
DEFAULT_CLI_TIMEOUT = 120
DEFAULT_CLI_BACKOFF = (5.0, 20.0)
GH_NET_MARKS = ("error connecting to", "i/o timeout", "connection reset", "tls handshake timeout",
                "could not resolve host", "http 502", "http 503", "http 504", "502 bad gateway",
                "503 service unavailable", "504 gateway timeout")
_cli_cfg: dict | None = None
_tl = threading.local()


def cli_settings() -> dict:
    """`runner.cli_timeout_seconds` y `runner.cli_timeout_backoff` de lanes.yaml (se leen una vez). Nunca lanza."""
    global _cli_cfg
    if _cli_cfg is None:
        cfg = {"timeout": DEFAULT_CLI_TIMEOUT, "backoff": DEFAULT_CLI_BACKOFF}
        try:
            from .config import _load
            runner = (_load(None) or {}).get("runner") or {}
            if runner.get("cli_timeout_seconds"):
                cfg["timeout"] = int(runner["cli_timeout_seconds"])
            if isinstance(runner.get("cli_timeout_backoff"), (list, tuple)):
                cfg["backoff"] = tuple(float(x) for x in runner["cli_timeout_backoff"])
        except Exception:  # sin lanes.yaml legible: valores de fábrica
            pass
        _cli_cfg = cfg
    return _cli_cfg


def configure_cli(timeout: int | None = None, backoff: tuple[float, ...] | None = None) -> None:
    """Fija los valores (tests); None en ambos vuelve a leerlos de lanes.yaml."""
    global _cli_cfg
    if timeout is None and backoff is None:
        _cli_cfg = None
        return
    base = dict(cli_settings())
    if timeout is not None:
        base["timeout"] = timeout
    if backoff is not None:
        base["backoff"] = tuple(backoff)
    _cli_cfg = base


def mark_transient(reason: str) -> None:
    """Anota en el hilo actual que una llamada agotó sus reintentos por una causa transitoria (timeout, base de datos
    de solo lectura, red). Lo consulta `DecisionDesk._safe` para encolar la decisión en vez de perderla."""
    _tl.transient = reason


def take_transient() -> str | None:
    """Motivo transitorio anotado en este hilo desde la última llamada (y lo borra)."""
    reason = getattr(_tl, "transient", None)
    _tl.transient = None
    return reason


def gh_is_read(args) -> bool:
    """`gh pr list/view/checks/status`, `gh api` sin método de escritura, `--help`: se pueden repetir sin riesgo."""
    args = [str(a) for a in args]
    if "--help" in args:
        return True
    if args[:1] == ["api"]:
        low = [a.lower() for a in args]
        for i, a in enumerate(low):
            if a in ("-x", "--method") and i + 1 < len(low) and low[i + 1] != "get":
                return False
            if a.startswith("--method=") and a != "--method=get":
                return False
            if a in ("-f", "-F", "--field", "--raw-field", "--input"):
                return False
        return True
    return args[:1] == ["pr"] and args[1:2] in (["list"], ["view"], ["checks"], ["status"], ["diff"])


def gh_network_error(cp) -> bool:
    text = f"{getattr(cp, 'stderr', '') or ''} {getattr(cp, 'stdout', '') or ''}".lower()
    return any(m in text for m in GH_NET_MARKS)


def run_retrying(cmd: list, *, runner=None, timeout: float | None = None, backoff: tuple[float, ...] | None = None,
                 sleep=None, label: str = "", retry_timeout: bool = True, transient=None,
                 **kwargs) -> subprocess.CompletedProcess:
    """`runner(cmd, timeout=…, **kwargs)` con reintento ante `TimeoutExpired` (si `retry_timeout`) y ante un resultado
    que `transient(cp)` declara transitorio. Espera progresiva `backoff` (lanes.yaml). Nunca lanza TimeoutExpired:
    agotados los intentos devuelve un CompletedProcess con returncode TIMEOUT_RC y anota el fallo transitorio."""
    cfg = cli_settings()
    runner = runner or run
    sleep = sleep or time.sleep
    timeout = timeout or cfg["timeout"]
    backoff = cfg["backoff"] if backoff is None else tuple(backoff)
    label = label or " ".join(str(a) for a in cmd[1:3])
    attempt = 0
    while True:
        try:
            cp = runner(cmd, timeout=timeout, **kwargs)
        except subprocess.TimeoutExpired:
            if not retry_timeout or attempt >= len(backoff):
                tries = attempt + 1
                reason = f"{label}: sin respuesta en {int(timeout)} s ({tries} intento{'s' if tries > 1 else ''})"
                mark_transient(reason)
                return subprocess.CompletedProcess(cmd, TIMEOUT_RC, "", f"timeout (transitorio): {reason}")
            reason = f"sin respuesta en {int(timeout)} s"
        else:
            if cp.returncode == 0 or transient is None or not transient(cp):
                return cp
            if attempt >= len(backoff):
                mark_transient(f"{label}: {(cp.stderr or cp.stdout or '').strip()[:200]}")
                return cp
            reason = "fallo transitorio"
        wait = backoff[attempt]
        attempt += 1
        logging.getLogger("agent_lanes").warning("%s: %s; reintento %d/%d en %gs", label, reason, attempt,
                                                 len(backoff), wait)
        sleep(wait)
