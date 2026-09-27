"""subprocess.run sin ventana de consola en Windows.

El runner corre con pythonw (sin consola); cada hermes/git/claude que lanza abriría una ventana
que roba el foco varias veces por minuto (27-09: dejaba el PC inutilizable).
"""
import subprocess
import sys

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def run(*args, **kwargs) -> subprocess.CompletedProcess:
    if sys.platform == "win32":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW
    return subprocess.run(*args, **kwargs)
