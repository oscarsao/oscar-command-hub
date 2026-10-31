"""Consumo de la suscripción de Claude (ventana de 5 h y semanal) y coste por tarjeta.

El dato lo escribe la línea de estado de Claude Code (`~/.claude/statusline-consumo.py`) en `estado-consumo.json`
(`{"actualizado": epoch, "five_hour": {used_percentage, resets_at}, "seven_day": {...}}`). Solo se refresca mientras haya
una sesión interactiva abierta, así que un dato de más de 60 min es «sin dato fiable». Nada de aquí falla ni lanza: sin
fichero, roto o viejo, simplemente no hay dato (y ni se avisa ni se frena).

- `leer()`: porcentaje, reinicio y antigüedad. Una ventana cuyo reinicio ya pasó no tiene porcentaje (el dato es de la anterior).
- `registrar_coste()`: una línea por ejecución de worker en `.state/consumo_tarjetas.jsonl` (tarjeta, carril, modelo, coste,
  fecha; nunca contenido de la tarjeta). El coste es la estimación de API de `total_cost_usd`, NO el % del plan.
- `resumen()`/`texto()`: lo que enseña `/consumo`.
- `frenar()`: ¿hay que dejar de lanzar tarjetas nuevas? (`consumo.frenar_activo` y `consumo.frenar_al`). Lo usa cuota.pausada().
- `Avisador`: UN aviso al cruzar `consumo.avisar_al` por ventana; no se repite hasta el siguiente reinicio.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import ajustes_hub
from .config import ROOT

log = logging.getLogger("agent_lanes")

ESTADO_PATH = Path(os.environ.get("CONSUMO_ESTADO") or Path.home() / ".claude" / "estado-consumo.json")
TARJETAS_PATH = ROOT / ".state" / "consumo_tarjetas.jsonl"
AVISO_PATH = ROOT / ".state" / "consumo_aviso.json"
MAX_EDAD_S = 60 * 60  # más viejo que esto = «sin dato fiable»
VENTANAS = (("five_hour", "5 h"), ("seven_day", "semanal"))
MISMA_VENTANA_S = 30 * 60  # dos `resets_at` así de cerca son la misma ventana
TOP_TARJETAS = 5


@dataclass(frozen=True)
class Ventana:
    clave: str
    nombre: str
    porcentaje: float | None   # None = sin dato (o ventana ya reiniciada)
    reinicio: float | None     # epoch


@dataclass(frozen=True)
class Lectura:
    ventanas: tuple[Ventana, ...]
    edad_s: float | None       # None = no hay fichero legible
    fiable: bool

    def ventana(self, clave: str) -> Ventana | None:
        return next((v for v in self.ventanas if v.clave == clave), None)


def _num(valor) -> float | None:
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # descarta NaN


def leer(path: Path | str | None = None, now: float | None = None) -> Lectura:
    """Lectura del fichero de estado. Nunca lanza."""
    now = time.time() if now is None else now
    vacias = tuple(Ventana(k, n, None, None) for k, n in VENTANAS)
    try:
        data = json.loads(Path(path or ESTADO_PATH).read_text(encoding="utf-8"))
        edad = max(0.0, now - float(data["actualizado"]))
    except (OSError, ValueError, KeyError, TypeError):
        return Lectura(vacias, None, False)
    if not isinstance(data, dict) or edad > MAX_EDAD_S:
        return Lectura(vacias, edad, False)
    ventanas = []
    for clave, nombre in VENTANAS:
        w = data.get(clave)
        w = w if isinstance(w, dict) else {}
        pct, reinicio = _num(w.get("used_percentage")), _num(w.get("resets_at"))
        if reinicio is not None and reinicio <= now:
            pct = None  # la ventana ya se reinició: el porcentaje guardado es el de la anterior
        ventanas.append(Ventana(clave, nombre, pct, reinicio))
    return Lectura(tuple(ventanas), edad, any(v.porcentaje is not None for v in ventanas))


def hora(epoch: float | None, now: float | None = None) -> str:
    """«18:00» si es hoy o en pocas horas; con día si es más lejos (reinicio semanal)."""
    if epoch is None:
        return "?"
    now = time.time() if now is None else now
    t = datetime.fromtimestamp(epoch)
    return t.strftime("%H:%M") if abs(epoch - now) < 20 * 3600 else t.strftime("%d-%m %H:%M")


def edad_texto(edad_s: float | None) -> str:
    if edad_s is None:
        return "sin fichero de estado"
    if edad_s < 90:
        return "hace menos de 2 min"
    if edad_s < 5400:
        return f"hace {int(edad_s // 60)} min"
    return f"hace {edad_s / 3600:.1f} h"


# --- coste por tarjeta -------------------------------------------------------------------------------------------------

def registrar_coste(tarjeta: str, carril: str, modelo: str | None, coste: float | None, now: float | None = None,
                    path: Path | str | None = None) -> bool:
    """Añade una línea al registro. Sin coste (None) no se anota. Nunca lanza; True si escribió."""
    if coste is None:
        return False
    try:
        reg = {"ts": time.time() if now is None else now, "tarjeta": str(tarjeta), "carril": str(carril),
               "modelo": str(modelo or "?"), "coste": round(float(coste), 4)}
        p = Path(path or TARJETAS_PATH)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(reg, ensure_ascii=False) + "\n")
        return True
    except (OSError, ValueError, TypeError) as exc:
        log.warning("consumo: coste de %s sin registrar (%s)", tarjeta, type(exc).__name__)
        return False


def _registros(path: Path | str | None = None) -> list[dict]:
    try:
        lines = Path(path or TARJETAS_PATH).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
            if isinstance(r, dict) and isinstance(r.get("coste"), (int, float)) and isinstance(r.get("ts"), (int, float)):
                out.append(r)
        except ValueError:
            continue
    return out


def resumen(path: Path | str | None = None, now: float | None = None) -> dict:
    """Costes de hoy y de los últimos 7 días: totales, por carril, por modelo y las tarjetas más caras (7 días)."""
    now = time.time() if now is None else now
    hoy0 = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    sem0 = (datetime.fromtimestamp(now) - timedelta(days=7)).timestamp()
    out = {"hoy": 0.0, "semana": 0.0, "carril": defaultdict(float), "modelo": defaultdict(float), "top": []}
    por_tarjeta: dict[str, list] = {}
    for r in _registros(path):
        if not sem0 <= r["ts"] <= now:
            continue
        c = float(r["coste"])
        out["semana"] += c
        out["hoy"] += c if r["ts"] >= hoy0 else 0.0
        out["carril"][r.get("carril") or "?"] += c
        out["modelo"][r.get("modelo") or "?"] += c
        fila = por_tarjeta.setdefault(str(r.get("tarjeta")), [0.0, r.get("carril") or "?"])
        fila[0] += c
    out["top"] = sorted(((t, f[1], f[0]) for t, f in por_tarjeta.items()), key=lambda x: -x[2])[:TOP_TARJETAS]
    return out


def texto(estado: Lectura, costes: dict, now: float | None = None) -> str:
    """Mensaje de /consumo (texto plano)."""
    now = time.time() if now is None else now
    lin = ["📊 Consumo de la suscripción de Claude"]
    if not estado.fiable:
        motivo = ("el dato tiene más de 60 min" if estado.edad_s is not None and estado.edad_s > MAX_EDAD_S
                  else "no hay dato todavía" if estado.edad_s is None else "el dato no trae porcentajes")
        lin.append(f"Sin dato fiable ({motivo}). Se actualiza solo mientras haya una sesión de Claude Code abierta.")
    for v in estado.ventanas:
        if estado.fiable and v.porcentaje is not None:
            lin.append(f"• Ventana {v.nombre}: {v.porcentaje:.0f} % · se reinicia {hora(v.reinicio, now)}")
        elif estado.fiable:
            lin.append(f"• Ventana {v.nombre}: sin dato (o ya reiniciada)")
    if estado.edad_s is not None:
        lin.append(f"Dato de {edad_texto(estado.edad_s)}.")
    lin.append("")
    lin.append("💵 Coste estimado a precio de API (NO es el % del plan: sirve para comparar tarjetas y carriles)")
    lin.append(f"• Hoy: ${costes['hoy']:.2f} · últimos 7 días: ${costes['semana']:.2f}")
    for titulo, clave in (("Por carril", "carril"), ("Por modelo", "modelo")):
        if costes[clave]:
            lin.append(f"{titulo} (7 días): " + " · ".join(
                f"{k} ${v:.2f}" for k, v in sorted(costes[clave].items(), key=lambda x: -x[1])))
    if costes["top"]:
        lin.append("Tarjetas más caras (7 días):")
        lin += [f"  {i}. {t} ({carril}) ${c:.2f}" for i, (t, carril, c) in enumerate(costes["top"], 1)]
    else:
        lin.append("Aún no hay costes registrados.")
    return "\n".join(lin)


# --- freno --------------------------------------------------------------------------------------------------------------

_frenado = False  # solo para dejar una línea de log al entrar y salir del freno


def frenar(now: float | None = None, path: Path | str | None = None) -> bool:
    """¿Alguna ventana con dato fiable está en `consumo.frenar_al` % o más? Con 0 (apagado) o sin dato, nunca. Nunca lanza."""
    global _frenado
    try:
        umbral = (ajustes_hub.valor("consumo.frenar_al") or 0) if ajustes_hub.valor("consumo.frenar_activo") else 0
        lectura = leer(path, now) if umbral else None
        alta = [v for v in (lectura.ventanas if lectura and lectura.fiable else ())
                if v.porcentaje is not None and v.porcentaje >= umbral]
    except Exception as exc:  # noqa: BLE001
        log.warning("consumo: freno no comprobado (%s)", type(exc).__name__)
        return False
    if bool(alta) != _frenado:
        _frenado = bool(alta)
        log.warning("consumo: %s", f"freno activo (ventana {alta[0].nombre} al {alta[0].porcentaje:.0f} %): no se lanzan "
                    "tarjetas nuevas" if alta else "freno levantado: se vuelve a lanzar")
    return bool(alta)


# --- aviso al cruzar el umbral ------------------------------------------------------------------------------------------

class Avisador:
    """UN mensaje al cruzar `consumo.avisar_al` en la ventana de 5 h o la semanal; no se repite hasta el siguiente reinicio
    (se guarda el `resets_at` avisado). Si el envío falla no se marca y se reintenta en la pasada siguiente."""

    def __init__(self, send: Callable[[str], object], state_path: Path | str | None = None,
                 estado_path: Path | str | None = None, clock: Callable[[], float] = time.time):
        self.send, self.clock = send, clock
        self.state_path = Path(state_path or AVISO_PATH)
        self.estado_path = estado_path

    def _cargar(self) -> dict:
        try:
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def tick(self) -> bool:
        """True si ha enviado un aviso. Nunca lanza."""
        try:
            umbral = ajustes_hub.valor("consumo.avisar_al") or 0
            if not umbral:
                return False
            now = self.clock()
            lectura = leer(self.estado_path, now)
            if not lectura.fiable:
                return False
            previo = self._cargar()
            nuevas = []
            for v in lectura.ventanas:
                if v.porcentaje is None or v.reinicio is None or v.porcentaje < umbral:
                    continue
                visto = _num(previo.get(v.clave))
                if visto is not None and abs(visto - v.reinicio) < MISMA_VENTANA_S:
                    continue  # ya avisado en esta ventana
                nuevas.append(v)
            if not nuevas:
                return False
            partes = [f"{v.nombre}: {v.porcentaje:.0f} % (se reinicia {hora(v.reinicio, now)})" for v in nuevas]
            if not self.send("📊 Consumo de Claude alto — " + " · ".join(partes) + f". Umbral de aviso: {umbral} %. "
                             "Detalle con /consumo."):
                return False
            previo.update({v.clave: v.reinicio for v in nuevas})
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(previo), encoding="utf-8")
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("consumo: aviso fallido (%s)", type(exc).__name__)
            return False


class AvisadorTicker:
    """Para el bucle del runner: como mucho una comprobación cada `minutes` minutos."""

    def __init__(self, avisador: Avisador, minutes: float = 5, clock: Callable[[], float] = time.time):
        self.avisador, self.minutes, self.clock, self._last = avisador, minutes, clock, 0.0

    def tick(self) -> bool:
        now = self.clock()
        if now - self._last < self.minutes * 60:
            return False
        self._last = now
        return self.avisador.tick()
