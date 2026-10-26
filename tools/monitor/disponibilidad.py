"""Disponibilidad del hub en el PC (F1-06, 3e parcial de la auditoría del 25-10).

Piezas pequeñas y probables sin red, que el monitor headless llama en cada vuelta:

- `Latido`: cada 5 min empuja un latido a Oscar HQ (`POST /api/hub/latido`, clave de servicio `OHQ_API_KEY`; el job
  de F1-01 avisa si pasa de 30 min sin latir) y al monitor «push» de Uptime Kuma (`KUMA_PUSH_URL`, F0-08). Sin
  clave o sin URL esa mitad queda desactivada (lo dice `estado()`), sin bloquear el resto. Nunca imprime valores.
- `bateria()`: lectura de `psutil.sensors_battery()` en (porcentaje, enchufado) o None si no hay batería.
- `TareasDiarias`: laborable, pasada la hora + margen y sin línea de hoy en su log -> `schtasks /Run` una vez al
  día (DISP-09: el 14-10 no hubo auditoría ni resumen y nadie se enteró).
- `runner_pid_vivo()`: el PID de `.state/runner.lock` sigue vivo (DISP-10: «runner parado» falso con status lento).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

LATIDO_CADA = 300  # s entre latidos (el aviso de Oscar HQ salta a los 30 min; Kuma con gracia de 20)
LATIDO_PATH = "/api/hub/latido"
OHQ_KEY, KUMA_KEY, OHQ_URL_KEY = "OHQ_API_KEY", "KUMA_PUSH_URL", "OHQ_API_URL"
BATERIA_AVISO_SEG = 600  # s sin corriente antes del aviso MEDIA (DISP-05)
BATERIA_ALTA_PCT = 20    # por debajo, ALTA aunque lleve poco sin corriente


def bateria(sensors: Callable | None = None) -> tuple[float, bool] | None:
    """(porcentaje, enchufado) o None (sobremesa, o psutil no lo sabe)."""
    try:
        if sensors is None:
            import psutil
            sensors = psutil.sensors_battery
        b = sensors()
    except Exception:
        return None
    if b is None or getattr(b, "percent", None) is None:
        return None
    return float(b.percent), bool(getattr(b, "power_plugged", True))


def runner_pid_vivo(lock: Path, pid_exists: Callable[[int], bool] | None = None) -> bool:
    """True si runner.lock nombra un PID vivo y es un proceso de Python (un PID reutilizado por otro programa no
    cuenta). Es el mismo criterio que usa `lanes.py status` para decir VIVO: sirve cuando `status` va lento o falla.
    Sin lock legible = False (no se sabe: manda `status`)."""
    try:
        pid = int(json.loads(Path(lock).read_text(encoding="utf-8")).get("pid") or 0)
    except (OSError, ValueError, AttributeError):
        return False
    if pid <= 0:
        return False
    try:
        if pid_exists is None:
            import psutil

            def pid_exists(p: int) -> bool:
                return psutil.pid_exists(p) and "python" in psutil.Process(p).name().lower()
        return bool(pid_exists(pid))
    except Exception:
        return False


class Latido:
    """Un latido cada `cada` segundos a Oscar HQ y a Kuma. `post`/`get` inyectables (los tests no salen a la red)."""

    def __init__(self, *, api_url: str = "", api_key: str = "", kuma_url: str = "", cada: float = LATIDO_CADA,
                 clock: Callable[[], float] = time.time, post=None, get=None):
        self.api_url, self.api_key, self.kuma_url = api_url.rstrip("/"), api_key, kuma_url
        self.cada, self.clock = cada, clock
        self.post, self.get = post or _post_json, get or _get
        self.ultimo = 0.0
        self.fallos: dict[str, str] = {}  # destino -> motivo corto del último fallo ("" = fue bien)

    @classmethod
    def desde_env(cls, env: dict[str, str], api_url_defecto: str = "", **kw) -> Latido:
        return cls(api_url=env.get(OHQ_URL_KEY) or api_url_defecto, api_key=env.get(OHQ_KEY, ""),
                   kuma_url=env.get(KUMA_KEY, ""), **kw)

    def estado(self) -> list[str]:
        """Solo nombres y estado, nunca valores (línea INFO al arrancar)."""
        ohq = "activo" if self.api_url and self.api_key else f"desactivado (falta {OHQ_URL_KEY if not self.api_url else OHQ_KEY})"
        kuma = "activo" if self.kuma_url else f"desactivado (falta {KUMA_KEY})"
        return [f"latido Oscar HQ: {ohq}", f"latido Kuma: {kuma}"]

    def tick(self, detalle: dict | None = None, force: bool = False) -> dict[str, bool] | None:
        """None si aún no toca; si toca, {destino: ok} de los destinos activos."""
        ahora = self.clock()
        if not force and ahora - self.ultimo < self.cada:
            return None
        self.ultimo = ahora
        out: dict[str, bool] = {}
        if self.api_url and self.api_key:
            out["ohq"] = self._intento("ohq", lambda: self.post(
                self.api_url + LATIDO_PATH, {"fuente": "pc", "detalle": detalle or {}}, {"X-API-Key": self.api_key}))
        if self.kuma_url:
            sep = "&" if "?" in self.kuma_url else "?"
            out["kuma"] = self._intento("kuma", lambda: self.get(self.kuma_url + f"{sep}status=up&msg=OK"))
        return out

    def _intento(self, destino: str, fn) -> bool:
        try:
            code = int(fn() or 0)
        except Exception as exc:  # sin red, DNS, timeout: no tumba el monitor
            self.fallos[destino] = type(exc).__name__
            return False
        ok = 200 <= code < 300
        self.fallos[destino] = "" if ok else f"HTTP {code}"
        return ok


def _post_json(url: str, body: dict, headers: dict) -> int:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "oscar-monitor", **headers})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def _get(url: str) -> int:
    req = urllib.request.Request(url, headers={"User-Agent": "oscar-monitor"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


@dataclass(frozen=True)
class TareaDiaria:
    nombre: str        # nombre en el Programador de tareas
    log: Path          # log que escribe al ejecutarse (línea con la fecha de hoy al principio)
    hora: tuple[int, int]
    margen_min: int = 60  # minutos tras la hora antes de darla por perdida


def corrio_hoy(log: Path, hoy: str) -> bool:
    """¿Hay alguna línea de hoy (`YYYY-MM-DD` al principio) en el log? Lee solo el final (los logs crecen)."""
    try:
        with Path(log).open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200_000))
            data = f.read().decode("utf-8", "replace")
    except OSError:
        return False
    return any(ln.startswith(hoy) for ln in data.splitlines())


class TareasDiarias:
    """Relanza una vez al día la tarea diaria laborable que no dejó rastro de hoy en su log (DISP-09)."""

    def __init__(self, tareas: tuple[TareaDiaria, ...], *, estado: Path, run: Callable, now: Callable[[], datetime]):
        self.tareas, self.estado, self.run, self.now = tareas, Path(estado), run, now

    def _hechas(self) -> dict:
        try:
            data = json.loads(self.estado.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def tick(self) -> list[str]:
        """Nombres de las tareas relanzadas en esta vuelta ([] casi siempre)."""
        now = self.now()
        if now.weekday() >= 5:
            return []
        hoy, hechas, out = now.strftime("%Y-%m-%d"), self._hechas(), []
        for t in self.tareas:
            limite = now.replace(hour=t.hora[0], minute=t.hora[1], second=0, microsecond=0).timestamp() + t.margen_min * 60
            if now.timestamp() < limite or hechas.get(t.nombre) == hoy or corrio_hoy(t.log, hoy):
                continue
            hechas[t.nombre] = hoy  # antes de lanzar: un fallo no se repite en cada vuelta
            try:
                ok = self.run(["schtasks", "/Run", "/TN", t.nombre]).returncode == 0
            except Exception:
                ok = False
            if ok:
                out.append(t.nombre)
        if out or hechas != self._hechas():
            try:
                self.estado.parent.mkdir(parents=True, exist_ok=True)
                self.estado.write_text(json.dumps(hechas), encoding="utf-8")
            except OSError:
                pass
        return out


RESUMEN_TIPOS = ("bateria", "relanzada", "hermes_config")  # = claves de agent_lanes.resumen_carriles.TIPOS_RESUMEN que usa el monitor


def anotar_resumen(path: Path, tipo: str, texto: str, clave: str | None = None,
                   now: Callable[[], datetime] = datetime.now) -> bool:
    """Apunta un aviso INFO en el resumen diario del runner (F1-04: `.state/resumen_info.json` de agent-lanes, mismo
    formato que `DiarioInfo.anotar`). El monitor es otro proceso y no importa agent_lanes: escribe el JSON con un
    temporal propio. Una vez al día por clave. False si no se pudo (quien llama lo deja en alerts.log)."""
    if tipo not in RESUMEN_TIPOS:
        return False
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    for k, v in (("eventos", []), ("vistas", {}), ("sent", [])):
        if not isinstance(data.get(k), type(v)):
            data[k] = v
    dia, key = f"{now():%Y-%m-%d}", f"{tipo}:{clave or texto}"
    if data["vistas"].get(key) == dia:
        return True
    data["vistas"][key] = dia
    data["vistas"] = dict(list(data["vistas"].items())[-500:])
    data["eventos"].append({"tipo": tipo, "texto": texto[:300]})
    try:
        import os
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".monitor{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return False
    return True
