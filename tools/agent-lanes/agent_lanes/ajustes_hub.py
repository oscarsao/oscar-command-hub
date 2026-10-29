"""Capa única de ajustes editables del hub (regla de Oscar 21-oct: «todo configurable desde la UI, nada hardcodeado»).

Cada ajuste tiene un valor POR DEFECTO igual al que había fijo en el código: si nadie edita, nada cambia. Los valores
editados viven en `ajustes_hub.json` (junto a lanes.yaml; NO es `.env` ni `.state/`) y se releen cuando el fichero cambia
(recarga en caliente, sin reiniciar). Cada cambio se anota en `ajustes_hub_auditoria.jsonl` (quién, cuándo, valor
anterior) y puede revertirse al valor por defecto. Los ajustes `reinicio=True` los lee el runner al arrancar: se guardan
al instante pero rigen tras reiniciarlo (esta capa nunca reinicia nada). Los de lanes.yaml (`default=None`) siguen el
yaml mientras no haya valor editado.

Quién puede editar lo decide el llamador (comandos: solo operadores del bot). Aquí solo se valida y se registra.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import ROOT

log = logging.getLogger("agent_lanes")

VALUES_PATH = ROOT / "ajustes_hub.json"
AUDIT_PATH = ROOT / "ajustes_hub_auditoria.jsonl"
GRUPOS = ("Avisos", "Atascos", "Lotes", "Carriles")
AVANZADO = "Avanzado"
HORA_RE = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
PATH_RE = re.compile(r"[\w.\-]+(?:/[\w.\-]+)*")
LOTE_RIESGO = (".env", "environment", "secret", "token", "credential", "stripe", "billing", "payment", "pago", "checkout",
               "factur", "invoice", "cliente", "customer", "migration", "alembic", "supabase", "dockerfile", "docker-compose",
               ".github", "railway", "vercel", "dokploy", "deploy", "produccion", "production", "auth")
SOLO_ANADIR = ("docs/kb/CHANGELOG.md", "docs/kb/LEDGER_ESTATUS.md", "docs/kb/archivo/CHANGELOG.md", "CHANGELOG.md")


class AjusteInvalido(ValueError):
    """Valor no válido: el mensaje es el que ve el usuario."""


@dataclass(frozen=True)
class Ajuste:
    key: str
    grupo: str
    etiqueta: str
    default: Any
    tipo: str                       # int | float | hora (HH:MM, "" = el de lanes.yaml) | lista
    ayuda: str = ""
    minimo: float | None = None
    maximo: float | None = None
    opciones: tuple = ()            # valores que salen como botones
    reinicio: bool = False          # se lee al arrancar el runner
    avanzado: bool = False          # solo por texto: /ajustes <clave> <valor>
    aviso_ram: float | None = None  # a partir de este valor se avisa de la RAM

    def describe(self, value) -> str:
        if self.tipo == "lista":
            return ", ".join(value) if value else "(vacía)"
        if value is None or value == "":
            return "el de lanes.yaml"
        if self.tipo in ("int", "float"):
            n = float(value)
            return str(int(n)) if n == int(n) else str(n)
        return str(value)


def _topes() -> list[Ajuste]:
    from .notices import LIMITS
    et = {"questions_max_items": "Preguntas por aviso", "questions_max_chars": "Caracteres del bloque de preguntas",
          "question_max_chars": "Caracteres por pregunta", "option_max_chars": "Caracteres por opción",
          "reason_max": "Caracteres del motivo del bloqueo", "text_max": "Caracteres por mensaje de Telegram"}
    top = {"text_max": 4096, "questions_max_chars": 3500}
    return [Ajuste(f"topes.{k}", "Avisos", et[k], None, "int", "Tope de los avisos (lanes.yaml: notices).", 1,
                   top.get(k, 2000), (), True) for k in LIMITS]


def _build() -> dict[str, Ajuste]:
    a = [
        Ajuste("avisos.hora_parte", "Avisos", "Hora del resumen por carril (parte de las 9:00)", None, "int",
               "Hora (0-21, Madrid) del parte diario. Sin editar vale la de integrador_visibilidad.yaml (9).", 0, 21,
               (7, 8, 9, 10, 12)),
        Ajuste("avisos.revision_lote", "Avisos", "Tarjetas listas que disparan el resumen", 3, "int",
               "Con tantas tarjetas listas para revisar sale ya el resumen agrupado (antes, si la ventana vence).", 1, 20,
               (1, 2, 3, 5)),
        Ajuste("avisos.revision_ventana_horas", "Avisos", "Horas máximas hasta el resumen de tarjetas listas", 2.0,
               "float", "Si no se llena el lote, el resumen sale cuando la tarjeta más antigua cumple estas horas.", 0.25,
               24, (1, 2, 4, 8)),
        Ajuste("avisos.lote_aprobar_min", "Avisos", "Tarjetas de bajo riesgo para ofrecer «Aprobar todas»", 5, "int",
               "Con tantas tarjetas de bajo riesgo del mismo carril, el resumen ofrece aprobarlas de una vez.", 2, 50,
               (3, 5, 8, 10)),
        Ajuste("avisos.lote_rutas_riesgo", "Avisos", "Palabras que sacan una tarjeta del lote de bajo riesgo",
               LOTE_RIESGO, "lista",
               "Si una de estas palabras aparece en un archivo cambiado o en el título, la tarjeta se aprueba sola, no "
               "en lote (entorno, pagos, datos de cliente, migraciones, deploy). Separadas por comas.", avanzado=True),
        Ajuste("avisos.estado_max_horas", "Avisos", "Horas tras las que el estado del integrador se da por viejo",
               None, "float", "Sin editar vale el de integrador_visibilidad.yaml (24).", 1, 168, (6, 12, 24, 48)),
        Ajuste("avisos.resumen_pasos", "Avisos", "Pasos del resumen corto del integrador", None, "int",
               "El resto sale con «Ver más». Sin editar vale el de integrador_visibilidad.yaml (3).", 1, 10,
               (2, 3, 5)),
        Ajuste("avisos.detalle_max", "Avisos", "Caracteres de «Ver más»", None, "int",
               "Sin editar vale el de integrador_visibilidad.yaml (3500).", 500, 3900, (2000, 3500), avanzado=True),
        Ajuste("avisos.resumen_ventana_horas", "Avisos", "Margen del resumen si el runner estaba parado", 2, "int",
               "Horas tras la del resumen hasta las que aún se envía.", 1, 6, (1, 2, 3, 6)),
        Ajuste("avisos.decisiones_en_resumen", "Avisos", "Decisiones que lista el resumen", 5, "int",
               "Cuántas decisiones pendientes se nombran; el resto se cuenta.", 1, 20, (3, 5, 8, 10)),
        Ajuste("avisos.recordatorio_atascadas_horas", "Avisos", "Recordatorio de [ATENCIÓN] sin respuesta", 24, "float",
               "Horas sin respuesta antes de recordar una tarjeta atascada.", 1, 168, (12, 24, 48, 72)),
        Ajuste("avisos.alerta_cada_horas", "Avisos", "Horas mínimas entre avisos repetidos del monitor", 24, "float",
               "Una misma alerta (RAM, develop↔master…) sale como mucho una vez en este plazo. /silenciar <clave> <horas> "
               "la calla del todo un tiempo. Lo lee el monitor.", 1, 168, (6, 12, 24, 48)),
        Ajuste("avisos.retro_hora", "Avisos", "Hora de la retro semanal del lunes", 9, "int",
               "Hora (0-23, Madrid) desde la que el lunes sale el informe de fricción de la semana.", 0, 23,
               (8, 9, 10, 12)),
        Ajuste("equipo.horas", "Avisos", "Horas del resumen del tema 👥 Equipo", ("9", "17"), "lista",
               "Horas (0-23, Madrid) separadas por comas. La primera lleva además las solicitudes abiertas y la "
               "revisión de permisos."),
        Ajuste("equipo.urgentes_hora", "Avisos", "Avisos urgentes del equipo por hora", 5, "int",
               "Pasado el tope, el resto sale agrupado en un solo mensaje.", 1, 20, (3, 5, 10)),
        Ajuste("equipo.fichas_hora", "Avisos", "Fichas de solicitud nuevas por hora", 10, "int",
               "Pasado el tope, el resto sale agrupado en un solo mensaje.", 1, 30, (5, 10, 20)),
        Ajuste("atascos.escalado_guard_horas", "Atascos", "Escalado a Oscar de un bloqueo de guard", 2.0, "float",
               "Horas en la cola del integrador antes de pasar a Oscar (mínimo 1).", 1, 72, (1, 2, 4, 8)),
        Ajuste("atascos.aplazar_horas", "Atascos", "Duración de «Aplazar»", 24.0, "float",
               "Horas que el bloqueo no se escala tras pulsar Aplazar.", 1, 168, (8, 24, 48, 72)),
        Ajuste("atascos.aviso_hora", "Atascos", "Hora del aviso diario de PR esperando lote", 9, "int",
               "Hora (0-23) desde la que sale el aviso «🚦 N PR…».", 0, 23, (8, 9, 10, 12)),
        Ajuste("atascos.titulo_max", "Atascos", "Caracteres del título en el aviso", 50, "int",
               "Largo máximo del título de cada PR en el aviso.", 20, 120, (30, 50, 80)),
        Ajuste("atascos.paradas_hora", "Atascos", "Hora del aviso diario de tarjetas paradas", 9, "int",
               "Hora (0-23, Madrid) desde la que sale el aviso «⏳ N tarjetas paradas».", 0, 23, (8, 9, 10, 12)),
        Ajuste("atascos.paradas_horas", "Atascos", "Horas sin moverse para considerar parada una tarjeta", 24.0, "float",
               "Una tarjeta de carril (en cola, bloqueada, en triage o en review) parada más de estas horas sale en el aviso.",
               1, 720, (12, 24, 48, 72)),
        Ajuste("atascos.paradas_max", "Atascos", "Fichas del aviso de tarjetas paradas", 8, "int",
               "Fichas con botones por aviso; el resto se cuenta en la cabecera.", 1, 30, (5, 8, 12)),
        Ajuste("atascos.solo_anadir", "Atascos", "Ficheros que solo crecen por el final", SOLO_ANADIR, "lista",
               "Un conflicto en ellos se resuelve juntando ambas partes. Rutas relativas separadas por comas.",
               avanzado=True),
        Ajuste("lotes.titulo_excluido_max", "Lotes", "Caracteres del título de un PR fuera del lote", 80, "int",
               "Largo máximo del título en el aviso «Fuera del lote».", 20, 200, (50, 80, 120)),
        Ajuste("lotes.hora", "Lotes", "Hora de montaje del lote diario", "", "hora",
               "HH:MM. Vacío = la de cada proyecto en lanes.yaml (08:30).", opciones=("", "07:30", "08:30", "09:30",
                                                                                     "12:00")),
        Ajuste("carriles.max_workers", "Carriles", "Workers a la vez (todos los carriles)", None, "int",
               "Tope global de procesos claude vivos. Con 3 o más hay riesgo de quedarse sin RAM (OOM el 27-09).", 1, 4,
               (1, 2, 3, 4), True, aviso_ram=3),
        Ajuste("carriles.intervalo_segundos", "Carriles", "Cada cuántos segundos mira el runner", None, "int",
               "Intervalo entre pasadas del runner.", 10, 600, (30, 60, 120), True),
        Ajuste("carriles.ciego_minutos", "Carriles", "Minutos sin ver tareas para dar la alarma de «runner ciego»",
               None, "int", "Tras ese tiempo avisa a Oscar y hace un restart --drain.", 2, 60, (3, 5, 10, 15), True),
        Ajuste("carriles.max_parallel", "Carriles", "Tareas a la vez por carril (valor por defecto)", None, "int",
               "Solo para carriles sin max_parallel propio en lanes.yaml.", 1, 3, (1, 2), True),
    ]
    return {x.key: x for x in a + _topes()}


SETTINGS: dict[str, Ajuste] = _build()


def parse(spec: Ajuste, raw) -> Any:
    """Valor limpio y validado o AjusteInvalido con el motivo en llano."""
    if spec.tipo in ("int", "float"):
        if isinstance(raw, bool):
            raise AjusteInvalido(f"«{spec.etiqueta}» tiene que ser un número.")
        try:
            n = float(str(raw).strip().replace(",", ".")) if not isinstance(raw, (int, float)) else float(raw)
        except ValueError:
            raise AjusteInvalido(f"«{spec.etiqueta}» tiene que ser un número (has puesto «{str(raw)[:30]}»).") from None
        if spec.tipo == "int" and n != int(n):
            raise AjusteInvalido(f"«{spec.etiqueta}» tiene que ser un número entero.")
        if (spec.minimo is not None and n < spec.minimo) or (spec.maximo is not None and n > spec.maximo):
            raise AjusteInvalido(f"«{spec.etiqueta}» tiene que estar entre {spec.describe(spec.minimo)} y "
                                 f"{spec.describe(spec.maximo)}.")
        return int(n) if spec.tipo == "int" else n
    if spec.tipo == "hora":
        s = str(raw).strip()
        if s and not HORA_RE.fullmatch(s):
            raise AjusteInvalido("La hora tiene que ser HH:MM, por ejemplo 08:30.")
        return s
    items = [p.strip() for p in (raw if isinstance(raw, (list, tuple)) else re.split(r"[,\n;]", str(raw))) if str(p).strip()]
    bad = [p for p in items if not PATH_RE.fullmatch(p) or ".." in p.split("/")]
    if bad:
        raise AjusteInvalido(f"Ruta no válida: «{bad[0][:60]}». Usa rutas relativas del repo, separadas por comas.")
    return tuple(dict.fromkeys(items))


def advertencia(spec: Ajuste, value) -> str:
    """Aviso (no error) tras guardar: RAM y cuándo rige."""
    out = []
    if spec.aviso_ram is not None and isinstance(value, (int, float)) and value >= spec.aviso_ram:
        out.append("⚠️ Cuidado con la RAM: con tantos workers el PC puede quedarse sin memoria. Si pasa, baja el valor.")
    if spec.reinicio:
        out.append("🔁 Se aplica al reiniciar el runner (yo no lo reinicio).")
    return "\n".join(out)


class Ajustes:
    """Almacén JSON de valores editados + auditoría. Releer es barato: se compara mtime y tamaño del fichero."""

    def __init__(self, path: Path | str | None = None, audit: Path | str | None = None,
                 now: Callable[[], float] = time.time):
        self.path = Path(path or VALUES_PATH)
        self.audit_path = Path(audit or AUDIT_PATH)
        self._now = now
        self._cache: tuple[tuple, dict] | None = None

    def _stamp(self) -> tuple:
        try:
            st = self.path.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return (0, 0)

    def _valores(self) -> dict:
        stamp = self._stamp()
        if self._cache and self._cache[0] == stamp:
            return self._cache[1]
        vals: dict = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        valores = raw.get("valores") if isinstance(raw, dict) else None
        for k, v in (valores if isinstance(valores, dict) else {}).items():
            spec = SETTINGS.get(k)
            if spec is None:
                continue
            try:
                vals[k] = parse(spec, v)
            except AjusteInvalido:  # editado a mano y roto: se ignora, rige el valor por defecto
                log.warning("ajustes_hub: valor no válido para %s; se usa el de por defecto", k)
        self._cache = (stamp, vals)
        return vals

    def valor(self, key: str):
        """Valor vigente: el editado si lo hay y es válido; si no, el de por defecto (None = el de lanes.yaml)."""
        spec = SETTINGS[key]
        v = self._valores().get(key)
        return spec.default if v is None else v

    def editado(self, key: str) -> bool:
        return key in self._valores()

    def _guardar(self, vals: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"valores": {k: list(v) if isinstance(v, tuple) else v for k, v in vals.items()}},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        self._cache = None

    def _auditar(self, accion: str, key: str, quien: str, antes, despues) -> None:
        reg = {"ts": self._now(), "accion": accion, "key": key, "quien": quien,
               "antes": list(antes) if isinstance(antes, tuple) else antes,
               "despues": list(despues) if isinstance(despues, tuple) else despues}
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(reg, ensure_ascii=False) + "\n")

    def set(self, key: str, raw, quien: str) -> str:
        """Guarda un valor validado y lo audita. Devuelve el aviso a mostrar (RAM, reinicio) o ''. Lanza AjusteInvalido."""
        spec = SETTINGS.get(key)
        if spec is None:
            raise AjusteInvalido(f"No existe el ajuste «{str(key)[:40]}».")
        value = parse(spec, raw)
        vals = dict(self._valores())
        antes = self.valor(key)
        vals[key] = value
        self._guardar(vals)
        self._auditar("cambio", key, quien, antes, value)
        return advertencia(spec, value)

    def reset(self, key: str, quien: str) -> bool:
        """Vuelve al valor por defecto (borra el editado). False si no estaba editado."""
        if key not in SETTINGS:
            raise AjusteInvalido(f"No existe el ajuste «{str(key)[:40]}».")
        vals = dict(self._valores())
        if key not in vals:
            return False
        antes = vals.pop(key)
        self._guardar(vals)
        self._auditar("restablecer", key, quien, antes, SETTINGS[key].default)
        return True

    def historial(self, key: str | None = None, limit: int = 10) -> list[dict]:
        out = []
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for ln in lines:
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and (key is None or r.get("key") == key):
                out.append(r)
        return out[-limit:]


_default = Ajustes()


def usar(store: Ajustes | None) -> Ajustes:
    """Cambia el almacén global (tests); None lo devuelve al de fábrica. Devuelve el anterior."""
    global _default
    previo, _default = _default, store or Ajustes()
    return previo


def almacen() -> Ajustes:
    return _default


def valor(key: str):
    """Valor vigente de un ajuste, con recarga en caliente. Nunca lanza: un fallo de lectura da el valor por defecto."""
    try:
        return _default.valor(key)
    except Exception as exc:  # los avisos y el runner no pueden caerse por un fichero de ajustes roto
        log.warning("ajustes_hub: %s sin leer (%s); valor por defecto", key, type(exc).__name__)
        return SETTINGS[key].default


def aplicar_runner(settings: dict) -> dict:
    """lanes.yaml `runner:` con los valores editados encima (para config.load_runner_settings, que lee el runner al arrancar)."""
    out = dict(settings)
    for key, field in (("carriles.max_workers", "max_workers"), ("carriles.intervalo_segundos", "interval_seconds"),
                       ("carriles.ciego_minutos", "ciego_minutes")):
        v = valor(key)
        if v is not None:
            out[field] = v
    return out


def aplicar_visibilidad(aj):
    """integrador_visibilidad.Ajustes (yaml) con los valores editados encima."""
    from dataclasses import replace
    over = {f: valor(k) for k, f in (("avisos.hora_parte", "hora_parte"), ("avisos.estado_max_horas", "estado_max_horas"),
                                     ("avisos.resumen_pasos", "resumen_pasos"), ("avisos.detalle_max", "detalle_max"))
            if valor(k) is not None}
    return replace(aj, **over) if over else aj


def aplicar_topes(limits: dict) -> dict:
    """`notices:` de lanes.yaml con los topes editados encima."""
    out = dict(limits)
    for key in SETTINGS:
        if key.startswith("topes.") and valor(key) is not None:
            out[key.split(".", 1)[1]] = valor(key)
    return out
