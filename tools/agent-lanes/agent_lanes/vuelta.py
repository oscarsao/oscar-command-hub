"""Vuelta principal del runner: cronómetro por paso, registro de operaciones lentas y pasos en hilo propio (t_11ee04e0).

El bucle de `runner.py` ejecutaba en serie y sin medir limpieza de worktrees, integrador y tickers: una vuelta podía
pasar de 60 min sin dejar rastro (diagnóstico t_b9861cd4). Aquí:

- `Cronometro`: mide cada paso de una vuelta; solo escribe en el log si un paso supera `umbral` s y, al cerrar la
  vuelta, si el total supera `umbral_total` s. No cambia el orden ni el resultado de los pasos.
- `medir(etiqueta)`: registra inicio y fin de una operación lenta (`git worktree add/remove`, `test_cmd`), con su
  duración, aunque acabe en excepción.
- `HiloAparte`: ejecuta un paso en su propio hilo, sin solaparse consigo mismo (si la pasada anterior sigue viva, la
  vuelta principal no espera ni lanza otra). `activo()` alimenta `busy()` del reinicio ordenado.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Callable

log = logging.getLogger("agent_lanes")

UMBRAL_PASO = 30.0     # s: un paso más lento que esto deja línea en el log
UMBRAL_VUELTA = 300.0  # s: una vuelta más larga que esto deja línea con el total


class Cronometro:
    """Cronómetro de una vuelta: `with crono.paso("integrador"): ...` y `crono.cerrar()` al final de la vuelta."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, umbral: float = UMBRAL_PASO,
                 umbral_total: float = UMBRAL_VUELTA) -> None:
        self._clock, self.umbral, self.umbral_total = clock, umbral, umbral_total
        self.pasos: list[tuple[str, float]] = []
        self._inicio = clock()

    @contextmanager
    def paso(self, nombre: str):
        t0 = self._clock()
        try:
            yield
        finally:  # un paso que lanza también se mide; la excepción sigue su camino
            dur = self._clock() - t0
            self.pasos.append((nombre, dur))
            if dur > self.umbral:
                log.info("vuelta: paso «%s» tardó %.0f s", nombre, dur)

    def cerrar(self) -> float:
        total = self._clock() - self._inicio
        if total > self.umbral_total:
            lentos = ", ".join(f"{n} {d:.0f}s" for n, d in sorted(self.pasos, key=lambda p: -p[1])[:4])
            log.warning("vuelta principal de %.0f s (más lentos: %s)", total, lentos or "ninguno")
        return total


@contextmanager
def medir(etiqueta: str, clock: Callable[[], float] = time.monotonic):
    """Línea de inicio y de fin (con duración y si acabó en error) de una operación que puede colgarse."""
    t0 = clock()
    log.info("inicio: %s", etiqueta)
    estado = "ok"
    try:
        yield
    except BaseException as exc:
        estado = f"error ({type(exc).__name__})"
        raise
    finally:
        log.info("fin: %s · %.1f s · %s", etiqueta, clock() - t0, estado)


class HiloAparte:
    """Un paso del bucle principal en su propio hilo. `lanzar()` no espera: si la pasada anterior sigue viva, no hace
    nada (misma semántica que el throttle del paso). Un error se registra y no mata nada."""

    def __init__(self, nombre: str, fn: Callable[[], object],
                 al_terminar: Callable[[object], None] | None = None) -> None:
        self.nombre, self._fn, self._al_terminar = nombre, fn, al_terminar
        self._hilo: threading.Thread | None = None
        self._cerrojo = threading.Lock()

    def activo(self) -> bool:
        h = self._hilo
        return h is not None and h.is_alive()

    def lanzar(self) -> bool:
        with self._cerrojo:
            if self.activo():
                return False
            self._hilo = threading.Thread(target=self._correr, name=f"lane-{self.nombre}", daemon=True)
            self._hilo.start()
            return True

    def esperar(self, timeout: float | None = None) -> None:
        h = self._hilo
        if h is not None:
            h.join(timeout)

    def _correr(self) -> None:
        t0 = time.monotonic()
        try:
            res = self._fn()
            if self._al_terminar:
                self._al_terminar(res)
        except Exception as exc:  # noqa: BLE001 - un paso en hilo aparte nunca tumba el runner
            log.warning("%s falló: %s", self.nombre, exc)
        dur = time.monotonic() - t0
        if dur > UMBRAL_PASO:
            log.info("vuelta: «%s» (hilo aparte) tardó %.0f s", self.nombre, dur)
