"""Cola de salida de Telegram (t_695e44b1, decisión de Oscar 20-11): un aviso que no se puede enviar por una caída de
red o de Telegram no se pierde; se guarda en `.state/cola_salida.json` y se reintenta con espera creciente.

- Qué se guarda: solo fallos TRANSITORIOS (timeout, «no alcanzable», HTTP 5xx, 429, 408). Un rechazo definitivo de
  Telegram (400, 403, chat no encontrado) sigue subiendo como siempre: reintentarlo no lo arregla.
- Sin duplicar: cada entrada lleva una clave de idempotencia por tarjeta, tipo y destino (`tipo:tid:chat:tema`; sin
  tarjeta, un hash del destino y del texto). Un aviso nuevo con la misma clave sustituye al retenido, y uno que llega directo borra el retenido.
- Prioridad: preguntas, aprobaciones, bloqueos urgentes, runner parado y producción salen primero; el resto (informativos)
  caduca antes (`avisos.cola_info_horas`) y los importantes a las `avisos.cola_horas`.
- Recuperación: cuando Telegram vuelve con 2 o más avisos retenidos sale UN mensaje («hubo N avisos retenidos…»), después
  los urgentes por orden y, de los informativos, solo unos pocos (RECUPERACION_INFO_MAX); el resto se cuenta como caducado.
- Registro: enviados, reintentados, caducados y fallidos por tipo y día; `linea_resumen` lo da en una línea para las 9:00.

Quién lo usa: `TelegramNotifier.send_to` (retener/entregado), `CommandCenter.salida_tick` (drenar cada minuto) y
`canal_avisos.publicar/enviar` (dicen el tipo y la tarjeta con `contexto`). Sin cola instalada (`notifier.salida = None`:
tests, consolas, --once) nada cambia.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("agent_lanes")

MADRID = ZoneInfo("Europe/Madrid")
URGENTES = frozenset({"needs_input", "aprobar", "bloqueo_urgente", "monitor", "prodwatch", "integrar"})
BACKOFF_BASE = 60.0            # s: primera espera; se duplica con cada fallo hasta BACKOFF_MAX
BACKOFF_MAX = 15 * 60.0
RECUPERACION_INFO_MAX = 3      # informativos que aún salen tras una caída (el resto caduca)
DIAS_REGISTRO = 14
MAX_COLA = 300                 # tope duro: ante una caída larguísima se descartan los informativos más antiguos
CAMPOS = ("enviados", "reintentados", "caducados", "fallidos")

_ctx = threading.local()


@contextlib.contextmanager
def contexto(tipo: str, tid: str | None = None, meta: dict | None = None) -> Iterator[None]:
    """Dice al notificador de qué tipo y tarjeta es el aviso que se envía dentro del bloque (hilo actual).
    `meta`: qué anotar si el aviso queda retenido y luego se entrega (`on_entregado`): {"clase": "main"|"dm"|"decision",
    ...datos del registro}. Se guarda en la entrada de la cola."""
    previo, previo_meta = getattr(_ctx, "valor", None), getattr(_ctx, "meta", None)
    _ctx.valor, _ctx.meta = (tipo, tid), meta
    try:
        yield
    finally:
        _ctx.valor, _ctx.meta = previo, previo_meta


def contexto_actual() -> tuple[str, str | None]:
    return getattr(_ctx, "valor", None) or ("info", None)


def meta_actual() -> dict | None:
    return getattr(_ctx, "meta", None)


def consumir_retenido() -> bool:
    """¿El último envío de este hilo quedó retenido en la cola? Lo lee (y limpia) quien necesita distinguirlo de
    «Telegram apagado» (ambos devuelven None): `notices.publish` para seguir con las copias."""
    valor = bool(getattr(_ctx, "retenido", False))
    _ctx.retenido = False
    return valor


def urgente(tipo: str) -> bool:
    return tipo in URGENTES


def es_transitorio(exc: BaseException) -> bool:
    """¿Un reintento puede arreglarlo? Red caída, timeouts, 5xx, 408 y 429. Un 4xx de Telegram no."""
    if hasattr(exc, "description"):  # TelegramAPIError
        m = re.search(r"HTTP (\d{3})", str(exc))
        if m:
            code = int(m.group(1))
            return code >= 500 or code in (408, 429)
        return "too many requests" in str(getattr(exc, "description", "")).lower()
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    return isinstance(exc, RuntimeError) and "no alcanzable" in str(exc)


def _hoy(ts: float) -> str:
    return f"{datetime.fromtimestamp(ts, MADRID):%Y-%m-%d}"


def _horas(key: str, defecto: float) -> float:
    try:
        from .ajustes_hub import valor
        return float(valor(key) or defecto)
    except Exception:
        return defecto


class ColaSalida:
    """`path`: JSON de estado. `owner_dm`: a quién va el mensaje único de recuperación. `horas`/`horas_info`: vigencia
    (por defecto, los ajustes `avisos.cola_horas` y `avisos.cola_info_horas`, que se leen en cada uso)."""

    def __init__(self, path: Path | str, owner_dm: str | None = None, *, clock: Callable[[], float] = time.time,
                 horas: Callable[[], float] | None = None, horas_info: Callable[[], float] | None = None):
        self.path, self.owner_dm, self.clock = Path(path), str(owner_dm) if owner_dm else None, clock
        self._horas = horas or (lambda: _horas("avisos.cola_horas", 6))
        self._horas_info = horas_info or (lambda: _horas("avisos.cola_info_horas", 2))
        self._lock = threading.RLock()

    # --- almacén -------------------------------------------------------------------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("entradas", [])
        data.setdefault("registro", {})
        data.setdefault("pausa_hasta", 0)
        data.setdefault("caida", {})
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["registro"] = dict(sorted(data["registro"].items())[-DIAS_REGISTRO:])
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def _contar(self, data: dict, campo: str, tipo: str, n: int = 1) -> None:
        fila = data["registro"].setdefault(_hoy(self.clock()), {}).setdefault(tipo, {})
        fila[campo] = int(fila.get(campo, 0)) + n

    def pendientes(self) -> list[dict]:
        with self._lock:
            return list(self._load()["entradas"])

    # --- lado del notificador --------------------------------------------------------------------------------------

    @staticmethod
    def clave_de(payload: dict, tipo: str, tid: str | None) -> str:
        if tid:  # un mismo aviso sale a varios destinos (tema, DM, copia): uno por destino
            return f"{tipo}:{tid}:{payload.get('chat_id')}:{payload.get('message_thread_id') or 0}"
        crudo = json.dumps([payload.get("chat_id"), payload.get("message_thread_id"), payload.get("text")],
                           ensure_ascii=False)
        return f"{tipo}:{hashlib.sha1(crudo.encode()).hexdigest()[:16]}"

    def entregado(self, payload: dict) -> None:
        """Un envío directo salió bien: cuenta y borra el aviso retenido de la misma clave (llegó una sola vez)."""
        tipo, tid = contexto_actual()
        try:
            with self._lock:
                data = self._load()
                clave = self.clave_de(payload, tipo, tid)
                antes = len(data["entradas"])
                data["entradas"] = [e for e in data["entradas"] if e["clave"] != clave]
                self._contar(data, "enviados", tipo)
                self._save(data)
                if antes != len(data["entradas"]):
                    log.info("cola de salida: %s llegó directo; se retira el retenido", clave)
        except Exception as exc:  # el registro es una ayuda: nunca tumba el envío
            log.warning("cola de salida: no se pudo anotar el envío: %s", exc)

    def retener(self, payload: dict, exc: BaseException) -> bool:
        """Guarda el aviso para reintentarlo. False si el fallo no es transitorio (quien llama lo propaga)."""
        if not es_transitorio(exc):
            return False
        tipo, tid = contexto_actual()
        try:
            with self._lock:
                data, now = self._load(), self.clock()
                clave = self.clave_de(payload, tipo, tid)
                previa = next((e for e in data["entradas"] if e["clave"] == clave), None)
                entrada = {"clave": clave, "tipo": tipo, "tid": tid, "prioridad": 0 if urgente(tipo) else 1,
                           "payload": payload, "creado": previa["creado"] if previa else now,
                           "intentos": previa["intentos"] if previa else 0, "meta": meta_actual()}
                data["entradas"] = [e for e in data["entradas"] if e["clave"] != clave] + [entrada]
                if len(data["entradas"]) > MAX_COLA:  # ante una caída enorme se pierden primero los informativos viejos
                    data["entradas"].sort(key=lambda e: (e["prioridad"], e["creado"]))
                    for e in data["entradas"][MAX_COLA:]:
                        self._contar(data, "caducados", e["tipo"])
                    data["entradas"] = data["entradas"][:MAX_COLA]
                if not data["caida"]:
                    data["caida"] = {"desde": now, "caducados": 0, "avisado": False}
                    data["pausa_hasta"] = now + BACKOFF_BASE
                self._save(data)
            log.warning("cola de salida: %s retenido (%s)", clave, type(exc).__name__)
            _ctx.retenido = True
            return True
        except Exception as err:  # si ni la cola funciona, el fallo original sube como antes
            log.warning("cola de salida: no se pudo guardar el aviso: %s", err)
            return False

    # --- reintentos --------------------------------------------------------------------------------------------------

    def _caduca(self, e: dict, now: float) -> bool:
        horas = self._horas() if e["prioridad"] == 0 else min(self._horas(), self._horas_info())
        return now - float(e["creado"]) >= horas * 3600

    @staticmethod
    def _espera(intentos: int) -> float:
        return min(BACKOFF_BASE * (2 ** max(intentos - 1, 0)), BACKOFF_MAX)

    def _aviso_recuperacion(self, n: int, urgentes: int, info_caducada: int, caducados: int) -> str:
        partes = [f"⚠️ Hubo {n} avisos retenidos por una caída de Telegram: aquí van los urgentes ({urgentes})."]
        if info_caducada:
            partes.append(f"{info_caducada} informativos ya no se envían.")
        if caducados:
            partes.append(f"{caducados} avisos caducaron sin llegar.")
        return " ".join(partes)

    def drenar(self, enviar: Callable[[dict], dict | None],
               on_entregado: Callable[[dict, dict | None], None] | None = None) -> dict:
        """Una pasada: caduca lo vencido y, pasada la espera, reintenta por prioridad con `enviar(payload)` (lanza ante
        fallo). Ante el primer fallo transitorio para y alarga la espera; el resto sigue en cola. Devuelve un resumen
        {enviados, caducados, fallidos, aviso} (para el log y los tests). Nunca lanza.
        `on_entregado(entrada, result)`: tras cada entrega (no el mensaje de recuperación) para completar el registro
        de mensajes (message_id, token, copias); si falla, solo se anota en el log."""
        out = {"enviados": 0, "caducados": 0, "fallidos": 0, "aviso": False}
        try:
            with self._lock:
                return self._drenar(enviar, out, on_entregado)
        except Exception as exc:
            log.warning("cola de salida: la pasada falló: %s", exc)
            return out

    def _drenar(self, enviar: Callable[[dict], dict | None], out: dict, on_entregado=None) -> dict:
        data, now = self._load(), self.clock()
        if not data["entradas"]:
            if data["caida"]:
                data["caida"] = {}
                self._save(data)
            return out
        vivas = []
        for e in data["entradas"]:  # caducidad: no se avisa pieza a pieza, va en el mensaje de recuperación y el parte
            if self._caduca(e, now):
                self._contar(data, "caducados", e["tipo"])
                data["caida"]["caducados"] = int(data["caida"].get("caducados", 0)) + 1
                out["caducados"] += 1
            else:
                vivas.append(e)
        data["entradas"] = vivas
        if not vivas or now < float(data["pausa_hasta"]):
            self._guardar_o_cerrar(data, out)
            return out
        vivas.sort(key=lambda e: (e["prioridad"], e["creado"]))
        urgentes = [e for e in vivas if e["prioridad"] == 0]
        info = [e for e in vivas if e["prioridad"] != 0]
        if len(vivas) >= 2 and not data["caida"].get("avisado") and self.owner_dm:
            recortados = max(len(info) - RECUPERACION_INFO_MAX, 0)
            texto = self._aviso_recuperacion(len(vivas) + int(data["caida"].get("caducados", 0)), len(urgentes),
                                             recortados, int(data["caida"].get("caducados", 0)))
            try:
                enviar({"chat_id": self.owner_dm, "text": texto, "disable_web_page_preview": True})
            except Exception as exc:
                return self._fallo(data, out, vivas[0], exc, now)
            data["caida"]["avisado"] = True
            out["aviso"] = True
            for e in info[RECUPERACION_INFO_MAX:]:  # la avalancha de informativos no sale: se cuenta como caducada
                data["entradas"].remove(e)
                self._contar(data, "caducados", e["tipo"])
                out["caducados"] += 1
            vivas = [e for e in vivas if e in data["entradas"]]
            self._save(data)
        for e in vivas:
            try:
                result = enviar(e["payload"])
            except Exception as exc:
                return self._fallo(data, out, e, exc, now)
            if on_entregado is not None:
                try:
                    on_entregado(e, result)
                except Exception as exc:  # el registro es una ayuda: el aviso ya llegó
                    log.warning("cola de salida: no se pudo registrar %s: %s", e["clave"], exc)
            data["entradas"].remove(e)
            self._contar(data, "reintentados", e["tipo"])
            out["enviados"] += 1
            self._save(data)
        data["pausa_hasta"] = 0
        self._guardar_o_cerrar(data, out)
        return out

    def _guardar_o_cerrar(self, data: dict, out: dict) -> None:
        if not data["entradas"]:
            data["caida"] = {}
        self._save(data)

    def _fallo(self, data: dict, out: dict, entrada: dict, exc: BaseException, now: float) -> dict:
        if es_transitorio(exc):
            entrada["intentos"] = int(entrada.get("intentos", 0)) + 1
            data["pausa_hasta"] = now + self._espera(entrada["intentos"])
        else:  # rechazo definitivo (chat borrado, bot bloqueado, mensaje mal formado): reintentar no lo arregla
            log.warning("cola de salida: %s descartado (%s)", entrada["clave"], exc)
            data["entradas"] = [e for e in data["entradas"] if e is not entrada]
            self._contar(data, "fallidos", entrada["tipo"])
            out["fallidos"] += 1
        self._save(data)
        return out

    # --- registro de entrega ---------------------------------------------------------------------------------------

    def linea_resumen(self, now: float | None = None) -> str:
        """Una línea para el parte de las 9:00: lo de AYER (día completo) por tipo. Vacía si no hubo envíos."""
        with self._lock:
            registro = self._load()["registro"]
            pendientes = len(self._load()["entradas"])
        ahora = self.clock() if now is None else now
        dia = _hoy(ahora - 86400)
        tipos = registro.get(dia) or {}
        tot = {c: sum(int(t.get(c, 0)) for t in tipos.values()) for c in CAMPOS}
        if not any(tot.values()) and not pendientes:
            return ""
        linea = (f"📬 Avisos de ayer: {tot['enviados']} enviados · {tot['reintentados']} reintentados · "
                 f"{tot['caducados']} caducados · {tot['fallidos']} fallidos")
        malos = [f"{t} ({int(v.get('caducados', 0))} cad., {int(v.get('fallidos', 0))} fall.)"
                 for t, v in sorted(tipos.items()) if v.get("caducados") or v.get("fallidos")]
        if malos:
            linea += " — sin llegar: " + ", ".join(malos[:4])
        if pendientes:
            linea += f" · {pendientes} retenidos ahora"
        return linea
