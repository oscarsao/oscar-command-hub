"""Identidad del proceso del runner: que un PID esté vivo NO basta (incidente del 14-11-2026).

Windows reutiliza los PID: el runner murió hacia las 12:12 y su PID (10976) pasó a ser otro programa
(StartMenuExperienceHost). `pid_alive` decía «vivo» y el candado, el latido y el estado externo se lo creyeron durante
~2 h 45 min. Aquí se comprueba además que el proceso del PID es el runner de verdad:

- su línea de comandos contiene `runner.py`, y
- no arrancó DESPUÉS de que el candado (`started`) o el latido (`desde`) se escribieran: un proceso nacido más tarde
  que el candado no puede ser quien lo escribió.

Si no se puede saber (sin psutil, acceso denegado) devuelve None: quien llama se queda con `pid_alive`, como antes.
`info` es inyectable: los tests no miran procesos reales.
"""
from __future__ import annotations

from typing import Callable

TOLERANCIA_ARRANQUE = 5.0   # s de margen entre la hora de arranque del proceso y la guardada (relojes y redondeos)
MARCAS_RUNNER = ("runner.py", "agent_lanes.runner")


def proceso_info(pid: int) -> tuple[float | None, str] | None:
    """(hora de arranque epoch o None, línea de comandos) del proceso; None si no existe o no se puede leer."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        p = psutil.Process(int(pid))
        try:
            cmd = " ".join(p.cmdline())
        except (psutil.AccessDenied, OSError):
            cmd = ""
        try:
            creado: float | None = float(p.create_time())
        except (psutil.AccessDenied, OSError):
            creado = None
        return creado, cmd
    except psutil.NoSuchProcess:
        return None
    except Exception:  # noqa: BLE001 - es una comprobación best effort
        return None


def es_el_runner(pid: int, started: float | None, info: Callable[[int], tuple | None] | None = None,
                 tolerancia: float = TOLERANCIA_ARRANQUE) -> bool | None:
    """True = es el runner; False = el PID lo usa OTRO proceso (candado obsoleto); None = no se puede saber."""
    datos = (info or proceso_info)(pid)
    if datos is None:
        return None
    creado, cmd = datos
    if cmd and not any(m in cmd.replace("\\", "/").lower() for m in MARCAS_RUNNER):
        return False
    if creado is not None and started:
        try:
            if float(creado) > float(started) + tolerancia:
                return False
        except (TypeError, ValueError):
            pass
    if not cmd and creado is None:
        return None
    return True


def runner_vivo(pid: int, started: float | None, pid_alive: Callable[[int], bool],
                info: Callable[[int], tuple | None] | None = None) -> bool:
    """`pid_alive` Y que el proceso sea el runner. Sin poder comprobar la identidad vale solo `pid_alive`."""
    if not pid_alive(pid):
        return False
    return es_el_runner(pid, started, info) is not False
