"""subprocess.run sin ventana de consola en Windows.

El runner corre con pythonw (sin consola); cada hermes/git/claude que lanza abriría una ventana
que roba el foco varias veces por minuto (27-09: dejaba el PC inutilizable).
"""
import subprocess
import sys
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
