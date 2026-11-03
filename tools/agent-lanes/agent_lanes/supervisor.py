"""Supervisión del runner (Fábrica v3 §F0): latido real, reinicio condicionado y límite de RAM por worker.

Qué cubre cada pieza (el reloj y los procesos se inyectan: sin red, sin Telegram, sin `hermes`):

- **Latido** (`Latido`): el hilo planificador y el bucle principal del runner anotan en `.state/runner-latido.json`
  cuándo latieron por última vez y qué tareas tienen worker vivo. Es un latido de VIDA REAL del código que reclama
  tareas, no solo «el proceso existe»: un runner con el proceso vivo pero el planificador colgado (ni reclama ni
  libera huecos) deja de latir. El monitor lo lee con `evaluar` y avisa si se para (por defecto a los 3 min).
- **Reinicio condicionado** (`reinicio_si_colgado`): si el latido se para y el último latido NO tenía workers en
  vuelo, se lanza `lanes.py restart --drain --colgado` aparte (máx. 1/hora). Con workers en vuelo solo se avisa:
  nunca se corta un worker por un latido parado. `Restarter` vuelve a comprobarlo (`colgado_sin_workers`) antes de
  saltarse la espera del acuse, que un runner colgado no puede dar.
- **Límite de RAM por worker** (`GuardiaRam`): un hilo vigila la memoria (RSS) del árbol de procesos de cada
  `claude`; si pasa del límite, lo corta y avisa. Con 6 workers a la vez, uno desbocado se llevaba a los demás por
  delante (OOM del 27-09).

Todo se configura en lanes.yaml `runner:` (`latido_parado_segundos`, `reinicio_auto_colgado`, `worker_ram_mb`,
`worker_ram_intervalo_segundos`).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable

from .config import ROOT

log = logging.getLogger("agent_lanes")

FILE = ROOT / ".state" / "runner-latido.json"
RESTART_FILE = ROOT / ".state" / "supervisor.json"
DEFAULT_PARADO_SECS = 180.0      # sin latir más de esto = runner colgado (el aviso sale en < 5 min con el sondeo del monitor)
DEFAULT_RAM_MB = 0               # 0 = sin límite; lanes.yaml fija el valor real
DEFAULT_RAM_INTERVALO = 15.0
RESTART_COOLDOWN = 3600.0        # máximo un reinicio automático por hora (como ciego.py)


def _atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def leer(path: Path = FILE) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class Latido:
    """Escribe el latido. `beat("planificador", busy)` desde el hilo que lanza workers; `beat("principal")` desde el
    bucle de mantenimiento. Nunca lanza: un disco lleno no puede tumbar al runner."""

    def __init__(self, path: Path = FILE, clock: Callable[[], float] = time.time, pid: int | None = None):
        self.path, self.clock, self.pid = Path(path), clock, pid or os.getpid()
        self._lock = threading.Lock()
        self._data: dict = {"pid": self.pid, "desde": clock(), "planificador_at": None, "principal_at": None, "busy": []}

    def beat(self, parte: str, busy: list[str] | None = None) -> None:
        with self._lock:
            now = self.clock()
            self._data[f"{parte}_at"] = now
            if busy is not None:
                self._data["busy"] = list(busy)
            self._data["at"] = now
            try:
                _atomic(self.path, self._data)
            except OSError as exc:
                log.warning("latido: no se pudo escribir (%s)", exc)


def evaluar(data: dict | None, now: float, parado_secs: float = DEFAULT_PARADO_SECS,
            pid_alive: Callable[[int], bool] | None = None) -> tuple[str, str]:
    """(estado, detalle). Estados: `ok`; `colgado` (proceso vivo, planificador sin latir); `parado` (el proceso del
    último latido ya no existe: lo cubre «runner-dead» del monitor); `sin_latido` (no hay fichero o es ilegible)."""
    if not data or not data.get("pid"):
        return "sin_latido", "no hay latido del runner"
    if pid_alive is not None and not pid_alive(int(data["pid"])):
        return "parado", f"el proceso {data['pid']} del último latido ya no existe"
    ultimo = data.get("planificador_at") or data.get("desde") or 0
    edad = now - float(ultimo)
    if edad > parado_secs:
        busy = data.get("busy") or []
        return "colgado", (f"el planificador lleva {int(edad // 60)} min {int(edad % 60)} s sin latir"
                           + (f" (workers en vuelo: {', '.join(busy)})" if busy else ""))
    return "ok", ""


def colgado_sin_workers(now: float | None = None, parado_secs: float = DEFAULT_PARADO_SECS, path: Path = FILE,
                        pid_alive: Callable[[int], bool] | None = None) -> bool:
    """¿Latido parado Y sin workers en vuelo en el último latido? Lo único que permite saltarse el acuse del drenaje."""
    data = leer(path)
    estado, _ = evaluar(data, time.time() if now is None else now, parado_secs, pid_alive)
    return estado == "colgado" and not (data or {}).get("busy")


def reinicio_si_colgado(data: dict | None, estado: str, *, now: float, lanzar: Callable[[], None],
                        habilitado: bool = True, path: Path = RESTART_FILE, cooldown: float = RESTART_COOLDOWN) -> str:
    """Decide y, si procede, ejecuta el reinicio. Devuelve la acción para el aviso: «» (nada que hacer), o el texto de
    lo ocurrido. `lanzar` arranca `lanes.py restart --drain --colgado` en un proceso aparte."""
    if estado != "colgado":
        return ""
    if not habilitado:
        return "reinicio automático apagado en lanes.yaml: hay que reiniciarlo a mano"
    if data and data.get("busy"):
        return f"hay workers en vuelo ({', '.join(data['busy'])}): NO reinicio, para no cortarlos"
    last = float((leer(path) or {}).get("reinicio_at") or 0)
    if now - last < cooldown:
        return "ya se reinició hace menos de una hora: NO reinicio otra vez, requiere a Oscar"
    try:
        lanzar()
        _atomic(path, {"reinicio_at": now})  # solo si arrancó: un fallo al lanzar no gasta el cupo de la hora
    except Exception as exc:  # noqa: BLE001 - el aviso debe salir aunque el reinicio no arranque
        log.warning("supervisor: no se pudo lanzar el reinicio: %s", exc)
        return f"intenté reiniciarlo y no pude ({exc})"
    return "reinicio automático lanzado (sin workers en vuelo)"


def rss_arbol_mb(pid: int, psutil_mod=None) -> float | None:
    """MB de RSS del proceso y todos sus hijos; None si no se puede medir (sin psutil o proceso ya muerto)."""
    ps = psutil_mod
    if ps is None:
        try:
            import psutil as ps  # noqa: PLC0415 - opcional
        except ImportError:
            return None
    try:
        root = ps.Process(pid)
        total = 0
        for p in [root, *root.children(recursive=True)]:
            try:
                total += p.memory_info().rss
            except (ps.NoSuchProcess, ps.AccessDenied):
                continue
        return total / (1024 * 1024)
    except Exception:  # noqa: BLE001 - medir es best effort
        return None


class GuardiaRam:
    """Vigila un worker: si el árbol de procesos pasa de `limite_mb`, lo mata (`kill`) y avisa (`alert`) una sola vez.
    `exceeded` guarda los MB medidos al cortar (None = no se cortó). `revisar()` es una comprobación (los tests la
    llaman a mano); `start()`/`stop()` gobiernan el hilo."""

    def __init__(self, pid: int, task_id: str, limite_mb: float, *, intervalo: float = DEFAULT_RAM_INTERVALO,
                 medir: Callable[[int], float | None] | None = None, kill: Callable[[int], None] | None = None,
                 alert: Callable[[str], None] | None = None):
        if kill is None:
            from . import proc as _proc
            kill = _proc.kill_tree
        self.pid, self.task_id, self.limite_mb, self.intervalo = pid, task_id, float(limite_mb), float(intervalo)
        self.medir, self.kill, self.alert = medir or (lambda p: rss_arbol_mb(p)), kill, alert
        self.exceeded: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def revisar(self) -> bool:
        """True si ha cortado el worker en esta comprobación."""
        if self.exceeded is not None or self.limite_mb <= 0:
            return False
        mb = self.medir(self.pid)
        if mb is None or mb <= self.limite_mb:
            return False
        self.exceeded = mb
        log.error("worker %s: %.0f MB por encima del límite de %.0f MB: se corta", self.task_id, mb, self.limite_mb)
        self.kill(self.pid)
        if self.alert:
            try:
                self.alert(f"🚨 ALTA · Worker cortado por memoria: la tarjeta {self.task_id} usaba {mb / 1024:.1f} GB "
                           f"(límite {self.limite_mb / 1024:.1f} GB). Se mató para que no tumbe al resto del PC; "
                           "la tarjeta se reintenta o se bloquea según sus reglas normales.")
            except Exception as exc:  # noqa: BLE001 - sin red: queda en el log
                log.warning("aviso de memoria no enviado: %s", exc)
        return True

    def _loop(self) -> None:
        while not self._stop.wait(self.intervalo):
            try:
                if self.revisar():
                    return
            except Exception as exc:  # noqa: BLE001 - la guardia no puede morir en silencio
                log.warning("guardia de RAM: %s", exc)

    def start(self) -> "GuardiaRam":
        if self.limite_mb > 0:
            self._thread = threading.Thread(target=self._loop, name=f"ram-{self.task_id}", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
