"""Tope de memoria global: mientras la RAM usada esté por encima de `carriles.memoria_tope_pct` el planificador no
reclama tarjetas nuevas (las que ya corren no se tocan y ninguna tarjeta se mueve ni se bloquea). Histéresis: se
reanuda cuando el uso baja de `carriles.memoria_reanudar_pct`. Aviso a Telegram como mucho una vez por hora mientras
el tope esté activo. Los valores se leen en cada consulta (ajustes_hub), así que /ajustes rige sin reiniciar.
Todo inyectable (lector, reloj, aviso): sin psutil real ni Telegram en los tests. Sin psutil no hay tope (se asume ok)."""
from __future__ import annotations

import logging
import time
from typing import Callable

log = logging.getLogger("agent_lanes")

DEFAULT_TOPE = 85
DEFAULT_REANUDAR = 80
AVISO_CADA_SEGUNDOS = 3600
LOG_CADA_SEGUNDOS = 300  # una línea de log cada 5 min, no una por tick


def leer_memoria_pct(psutil_mod=None) -> float | None:
    """% de RAM usada o None si no se puede medir (sin psutil o fallo)."""
    try:
        ps = psutil_mod
        if ps is None:
            import psutil as ps  # noqa: PLC0415 - opcional
        return float(ps.virtual_memory().percent)
    except Exception as exc:  # noqa: BLE001 - medir nunca para al planificador
        log.info("tope de memoria: no se pudo leer la RAM (%s)", type(exc).__name__)
        return None


def _ajuste(key: str, default: float) -> float:
    try:
        from .ajustes_hub import valor  # noqa: PLC0415
        v = valor(key)
        return float(default if v is None else v)
    except Exception:  # noqa: BLE001
        return float(default)


def umbrales() -> tuple[float, float]:
    """(tope, reanudar) vigentes. tope 0 = apagado. reanudar nunca queda por encima del tope (sin histéresis inversa)."""
    tope = _ajuste("carriles.memoria_tope_pct", DEFAULT_TOPE)
    reanudar = _ajuste("carriles.memoria_reanudar_pct", DEFAULT_REANUDAR)
    return tope, min(reanudar, tope - 1) if tope > 0 else reanudar


class TopeMemoria:
    def __init__(self, lector: Callable[[], float | None] = leer_memoria_pct,
                 umbrales_fn: Callable[[], tuple[float, float]] = umbrales,
                 alert: Callable[[str], object] | None = None, now: Callable[[], float] = time.time):
        self.lector, self.umbrales_fn, self.alert, self.now = lector, umbrales_fn, alert, now
        self.activo = False
        self._ultimo_aviso = 0.0
        self._ultimo_log = 0.0

    def pausada(self) -> bool:
        """¿Hay que dejar de reclamar ahora? Nunca lanza."""
        try:
            return self._evaluar()
        except Exception as exc:  # noqa: BLE001
            log.warning("tope de memoria no comprobado: %s", exc)
            return False

    def _evaluar(self) -> bool:
        tope, reanudar = self.umbrales_fn()
        pct = self.lector() if tope > 0 else None
        if tope <= 0 or pct is None:  # apagado o sin medida: comportamiento de siempre
            if self.activo:
                log.info("tope de memoria: sin medida o apagado; se reanuda el reclamo")
            self.activo = False
            return False
        ahora = self.now()
        if self.activo and pct < reanudar:
            self.activo = False
            log.info("tope de memoria: RAM al %.0f %% (< %.0f %%); se reanuda el reclamo de tarjetas", pct, reanudar)
            return False
        if not self.activo and pct > tope:
            self.activo = True
            self._ultimo_log = 0.0
            self._ultimo_aviso = 0.0
        if not self.activo:
            return False
        if ahora - self._ultimo_log >= LOG_CADA_SEGUNDOS:
            self._ultimo_log = ahora
            log.info("tope de memoria: RAM al %.0f %% (tope %.0f %%, reanuda bajo %.0f %%); no se reclaman tarjetas "
                     "nuevas", pct, tope, reanudar)
        if self.alert and ahora - self._ultimo_aviso >= AVISO_CADA_SEGUNDOS:
            self._ultimo_aviso = ahora
            try:
                self.alert(f"🧠 Memoria al {pct:.0f} %: el runner no reclama tarjetas nuevas hasta que baje de "
                           f"{reanudar:.0f} % (tope {tope:.0f} %). Las que ya corren siguen. Se cambia en /ajustes.")
            except Exception as exc:  # noqa: BLE001
                log.warning("tope de memoria: aviso no enviado: %s", exc)
        return True
