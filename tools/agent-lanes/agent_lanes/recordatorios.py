"""Recordatorios escalonados de decisiones sin responder (decisión de Oscar 31-10, fábrica D6).

Una tarjeta de decisión que bloquea trabajo se recuerda a su responsable con una escalera configurable (por defecto
1 h, 2 h, 4 h y 24 h desde que la tarjeta lleva esperando). Cada recordatorio responde al mismo mensaje (o, si no
se puede editar, lo cita), etiqueta al responsable y respeta el horario silencioso: lo que cae dentro se aplaza al
final del silencio, no se pierde. Tras el último paso (24 h):
  - tarjeta REVERSIBLE con recomendada y `decisiones.aplicar_reversible = 1`: se aplica la recomendada con el mismo
    camino que el plazo de veto (`apply_recommended`; el trabajo resultante vive siempre en ramas `lane/*`, esto
    nunca toca master ni producción) y se avisa;
  - tarjeta NO reversible: escala a las personas de `decisiones.escalado` (por defecto María y luego Oscar). Nunca se
    aplica sola.
Todo se lee de ajustes_hub (apagado por defecto: `decisiones.recordatorios_activa = 0`). La lógica es pura y con reloj
inyectado; commands.py solo la cablea en el tic del runner.
"""
from __future__ import annotations

import html
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from . import ajustes_hub
from .notices import truncate
from .people import norm_marca

log = logging.getLogger("agent_lanes")

DUENO, OSCAR = "dueno", "oscar"
MADRID = ZoneInfo("Europe/Madrid")


@dataclass(frozen=True)
class Escalera:
    activa: bool
    horas: tuple          # horas de espera de cada paso, ascendentes
    destinos: tuple       # quién recibe cada paso: "dueno", "oscar" o la clave de una persona de people.yaml
    silencio_ini: str     # "HH:MM"; igual a silencio_fin = sin horario silencioso
    silencio_fin: str
    max_avisos: int
    aplicar_reversible: bool
    escalado: tuple       # a quién se escala una decisión irreversible tras el último paso (en orden)
    duenos: dict          # marca normalizada -> clave de persona (ajuste `decisiones.duenos`: "marca/persona")
    margen_h: float = 1.0  # horas mínimas desde el aviso anterior antes de aplicar la reversible


def _horas(raw) -> tuple:
    out = []
    for r in raw or ():
        try:
            n = float(str(r).replace(",", "."))
        except ValueError:
            continue
        if n > 0:
            out.append(n)
    return tuple(sorted(dict.fromkeys(out)))


def _margen(raw) -> float:
    try:
        return max(0.0, float(str(raw).replace(",", "."))) if raw not in (None, "") else 1.0
    except ValueError:
        return 1.0


def cargar(valor: Callable[[str], object] = ajustes_hub.valor) -> Escalera:
    horas = _horas(valor("decisiones.escalera_horas")) or (1.0, 2.0, 4.0, 24.0)
    destinos = tuple(valor("decisiones.escalera_destinos") or ())
    destinos = tuple(destinos[i] if i < len(destinos) else (destinos[-1] if destinos else DUENO) for i in range(len(horas)))
    duenos = {}
    for par in valor("decisiones.duenos") or ():
        marca, _, clave = str(par).partition("/")
        if marca and clave:
            duenos[norm_marca(marca)] = clave
    return Escalera(bool(valor("decisiones.recordatorios_activa")), horas, destinos,
                    str(valor("decisiones.silencio_inicio") or ""), str(valor("decisiones.silencio_fin") or ""),
                    int(valor("decisiones.max_avisos") or len(horas)), bool(valor("decisiones.aplicar_reversible")),
                    tuple(valor("decisiones.escalado") or ()), duenos, _margen(valor("decisiones.margen_aplicar_horas")))


def _minutos(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def en_silencio(now: datetime, ini: str, fin: str) -> bool:
    """True si `now` (hora local de quien lo pasa) cae en el horario silencioso; admite ventanas que cruzan la medianoche."""
    a, b = _minutos(ini), _minutos(fin)
    if a is None or b is None or a == b:
        return False
    cur = now.hour * 60 + now.minute
    return a <= cur < b if a < b else (cur >= a or cur < b)


def paso_debido(esc: Escalera, since: float, enviados: int, now: datetime) -> int | None:
    """Índice del paso a enviar ahora, o None. Si el runner estuvo parado y varios pasos vencieron, salta al último
    (un solo aviso, no una ráfaga). Dentro del horario silencioso no sale nada: se envía al terminar."""
    if not esc.activa or not since or enviados >= min(len(esc.horas), esc.max_avisos):
        return None
    if en_silencio(now, esc.silencio_ini, esc.silencio_fin):
        return None
    transcurrido = now.timestamp() - since
    debido = [i for i, h in enumerate(esc.horas[:esc.max_avisos]) if transcurrido >= h * 3600 and i >= enviados]
    return debido[-1] if debido else None


class LibroEscalera:
    """.state/escalera.json {task_id: {"enviados": n, "msg": {"chat": id, "id": n}, "fin": bool}}."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def get(self, tid: str) -> dict:
        return dict(self._load().get(tid) or {})

    def put(self, tid: str, rec: dict) -> None:
        with self._lock:
            data = self._load()
            data[tid] = rec
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)


def _persona(people, clave: str):
    if clave == OSCAR:
        return people.owner
    for uid in sorted(people.collaborator_ids()):
        p = people.lookup(uid)
        if p and p.key == clave:
            return p
    return None


def dueno(people, esc: Escalera, marca) -> object:
    """Responsable de la decisión: el de `decisiones.duenos` para esa marca; si no, un operador con esa marca en
    people.yaml; si no, Oscar."""
    wanted = norm_marca(marca)
    p = _persona(people, esc.duenos[wanted]) if wanted in esc.duenos else None
    if p is None and wanted:
        for uid in sorted(people.collaborator_ids()):
            c = people.lookup(uid)
            if c and c.operador and wanted in {norm_marca(b) for b in c.brands}:
                p = c
                break
    return p or people.owner


def etiqueta(persona) -> str:
    return f'<a href="tg://user?id={html.escape(str(persona.user_id))}">{html.escape(persona.name)}</a>'


def texto_aviso(c: dict, paso: int, esc: Escalera, now: float, quien) -> str:
    t = c["task"]
    horas = int((now - (c.get("since") or now)) // 3600)
    n = min(len(esc.horas), esc.max_avisos)
    cola = "Es el último aviso." if paso >= n - 1 else f"Próximo aviso en {esc.horas[paso + 1] - esc.horas[paso]:g} h."
    return (f"⏰ {etiqueta(quien)}: la decisión <code>{html.escape(t['id'])}</code> · {html.escape(truncate(t.get('title'), 70))} "
            f"lleva {horas} h sin respuesta y bloquea trabajo (aviso {paso + 1}/{n}). {cola}")


class Escalonador:
    """Un tic: avisa de lo que toque y, tras el último paso, aplica la reversible o escala la irreversible.
    `cards()` -> tarjetas de decisión [{task, board, since}]; `spec(c)` -> CardSpec; `marca(c)` -> marca de la tarjeta;
    `apply(member)` -> bool (apply_recommended); `vetos` -> VetoBook opcional (no doble aplicación)."""

    def __init__(self, notifier, people, book: LibroEscalera, cards: Callable[[], list], spec: Callable, marca: Callable,
                 apply: Callable[[dict], bool], *, vetos=None, deshacer: Callable | None = None,
                 now: Callable[[], datetime] = datetime.now, cargar_esc: Callable[[], Escalera] = cargar):
        self.notifier, self.people, self.book = notifier, people, book
        self._cards, self._spec, self._marca, self._apply = cards, spec, marca, apply
        self.vetos, self._now, self._cargar = vetos, now, cargar_esc
        self._deshacer = deshacer  # (task_id, board, texto) -> reply_markup con [↩️ Deshacer], o None

    def _enviar(self, persona, texto: str, rec: dict, markup: dict | None = None) -> dict | None:
        """Primer aviso: mensaje nuevo. Siguientes: respuesta al aviso anterior del mismo chat, para que Telegram
        notifique y el hilo se mantenga. Sin mensaje previo en ese chat, mensaje nuevo."""
        chat = str(persona.user_id)
        prev = rec.get("msg") or {}
        kw = {"reply_markup": markup} if markup else {}
        sent = self.notifier.send_to(chat, None, texto, reply_to=prev.get("id") if prev.get("chat") == chat else None, **kw)
        return {"chat": chat, "id": (sent or {}).get("message_id")} if sent else None

    def tick(self) -> list[str]:
        esc = self._cargar()
        if not esc.activa:
            return []
        now = self._now()
        hechos = []
        for c in self._cards():
            tid = c["task"]["id"]
            rec = self.book.get(tid)
            if rec.get("fin"):
                continue
            if rec.get("final_pendiente"):
                self._reintenta_final(esc, c, rec, now, hechos)
                continue
            paso = paso_debido(esc, c.get("since") or 0, int(rec.get("enviados") or 0), now)
            if paso is None:
                continue
            try:
                self._paso(esc, c, paso, rec, now)
                hechos.append(tid)
            except Exception as exc:  # una tarjeta rota no para al resto
                log.warning("recordatorio de %s falló: %s", tid, exc)
        return hechos

    def _paso(self, esc: Escalera, c: dict, paso: int, rec: dict, now: datetime) -> None:
        tid = c["task"]["id"]
        resp = dueno(self.people, esc, self._marca(c))
        clave = esc.destinos[paso]
        destino = resp if clave == DUENO else (_persona(self.people, clave) or resp)
        previo = float(rec.get("ultimo_ts") or 0) if rec.get("enviados") else 0.0
        rec["enviados"] = paso + 1
        rec["ultimo_ts"] = now.timestamp()
        msg = self._enviar(destino, texto_aviso(c, paso, esc, now.timestamp(), resp), rec)
        if msg:
            rec["msg"] = msg
        if paso == len(esc.horas) - 1:
            # Solo el último paso de la escalera aplica o escala. Sin aviso previo (tarjeta vieja al activar el ajuste,
            # runner parado) o con el previo demasiado reciente, este aviso va primero y el cierre espera al margen.
            if previo and now.timestamp() - previo >= esc.margen_h * 3600:
                rec["fin"] = True
                self._final(esc, c, resp, rec)
            else:
                rec["final_pendiente"] = True
        elif rec["enviados"] >= esc.max_avisos:
            rec["fin"] = True  # el tope corta la escalera: se cierra sin aplicar ni escalar
        self.book.put(tid, rec)

    def _reintenta_final(self, esc: Escalera, c: dict, rec: dict, now: datetime, hechos: list) -> None:
        """Último paso ya avisado pero aplazado por el margen: cuando pasa, se cierra (aplica o escala)."""
        if en_silencio(now, esc.silencio_ini, esc.silencio_fin):
            return
        if now.timestamp() - float(rec.get("ultimo_ts") or 0) < esc.margen_h * 3600:
            return
        tid = c["task"]["id"]
        rec["fin"], rec["final_pendiente"] = True, False
        try:
            self._final(esc, c, dueno(self.people, esc, self._marca(c)), rec)
            hechos.append(tid)
        except Exception as exc:
            log.warning("cierre de %s falló: %s", tid, exc)
        self.book.put(tid, rec)  # cerrada aunque falle el aviso: nunca se aplica dos veces

    def _escalar(self, esc: Escalera, c: dict, resp, motivo: str) -> None:
        tid, titulo = c["task"]["id"], html.escape(truncate(c["task"].get("title"), 70))
        for clave in esc.escalado:
            p = _persona(self.people, clave)
            if p is not None:
                self._enviar(p, f"🚨 {etiqueta(p)}: <code>{html.escape(tid)}</code> · {titulo} lleva {esc.horas[-1]:g} h "
                                f"sin respuesta de {etiqueta(resp)}. {motivo}", {})

    def _final(self, esc: Escalera, c: dict, resp, rec: dict) -> None:
        tid, titulo = c["task"]["id"], html.escape(truncate(c["task"].get("title"), 70))
        spec = self._spec(c)
        if spec.reversible and spec.has_options and spec.recommended is not None and esc.aplicar_reversible:
            if self.vetos is not None and self.vetos.state(tid) in ("vetoed", "ejecutada"):
                return  # vetada por Oscar o ya aplicada por el plazo de veto
            member = {"task_id": tid, "board": c["board"], "title": c["task"].get("title") or "",
                      "options": spec.options, "recommended": spec.recommended}
            if self._apply(member):
                if self.vetos is not None:
                    self.vetos.set_state(tid, "ejecutada")
                letra = "ABC"[spec.recommended]
                markup = self._deshacer(tid, c["board"], f"la recomendada ({letra}) aplicada sola") if self._deshacer else None
                self._enviar(resp, f"✅ Sin respuesta en {esc.horas[-1]:g} h: apliqué la recomendada "
                                   f"({letra}) en <code>{html.escape(tid)}</code> · {titulo}. Era reversible y "
                                   "el trabajo sale en una rama lane/*, nunca en master. Si no la quieres, pulsa Deshacer "
                                   "(mientras el worker no la haya recogido) o reabre la tarjeta.", rec, markup)
            else:
                self._enviar(resp, f"⚠️ No pude aplicar la recomendada en <code>{html.escape(tid)}</code> · {titulo}: "
                                   "decídela tú; aviso también a quien escala.", rec)
                self._escalar(esc, c, resp, "No se pudo aplicar la recomendada sola: decídela tú.")
            return
        if not spec.reversible:
            self._escalar(esc, c, resp, "No es reversible: no la aplico sola, decídela tú.")
