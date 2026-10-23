"""/tareas desde el móvil (23-10): responder con el número de la línea, buscar por el final del id y actuar sobre la
ficha respondiéndole. Piezas puras, sin Telegram ni Hermes (CommandCenter las usa):

- `coincidencias`: ids que casan con 3-8 caracteres hexadecimales (primero por el final del id, si no en cualquier
  posición). Solo [0-9a-f]: nunca llega texto libre a la CLI.
- `MapaMensajes`: (chat, mensaje) -> qué es ese mensaje (lista numerada, ficha, aviso de fusión), con caducidad de 24 h.
  Se guarda en disco si tiene ruta, para que sobreviva a un reinicio del bot.
- `es_pregunta`: distingue «¿qué falta?» (se contesta con el estado real) de una instrucción (se anota en la tarjeta).
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

SUFFIX_RE = re.compile(r"^[0-9a-f]{3,8}$")
NUMERO_RE = re.compile(r"^#?(\d{1,3})\.?$")
MAP_TTL = 24 * 3600  # s: el mapa número -> tarea de un mensaje caduca a las 24 h
MAX_OPCIONES = 8  # botones de una búsqueda con varias coincidencias
PRIO_PASO = 10  # lo que sube [⬆️ Prioridad] de la ficha

TR_PICK, TR_PRIO, TR_FUSION = "tr_pick", "tr_prio", "tr_fusion"
TR_ARCHIVAR, TR_RELANZAR, TR_ANOTAR = "tr_archivar", "tr_relanzar", "tr_anotar"
TR_ACTIONS = (TR_PICK, TR_PRIO, TR_FUSION, TR_ARCHIVAR, TR_RELANZAR, TR_ANOTAR)

KIND_LISTA, KIND_FICHA, KIND_FUSION = "lista", "ficha", "fusion"

_PREGUNTA_INICIO = ("qué", "que ", "cuál", "cual", "cómo", "como ", "cuándo", "cuando", "por qué", "porque ", "quién",
                    "quien", "dónde", "donde", "cuánto", "cuanto", "cuántas", "cuantas", "está", "esta ", "hay ", "falta",
                    "en qué", "estado")


def coincidencias(ids: list[str], texto: str) -> list[str]:
    """Ids (`t_xxxxxxxx`) que casan con `texto` (3-8 hex): los que TERMINAN así; si ninguno, los que lo contienen."""
    s = (texto or "").strip().lower()
    if not SUFFIX_RE.match(s):
        return []
    hexes = sorted((i for i in ids if i.startswith("t_")), key=lambda i: i)
    final = [i for i in hexes if i[2:].endswith(s)]
    return final or [i for i in hexes if s in i[2:]]


def es_pregunta(texto: str) -> bool:
    t = (texto or "").strip().lower()
    return t.startswith("¿") or t.endswith("?") or t.startswith(_PREGUNTA_INICIO)


class MapaMensajes:
    """{(chat, mensaje): {"kind", "data", "t"}} con caducidad. `path` None = solo en memoria (tests)."""

    def __init__(self, path: Path | str | None = None, ttl: float = MAP_TTL):
        self.path = Path(path) if path else None
        self.ttl = ttl
        self._mem: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _load(self) -> dict:
        if not self.path:
            return self._mem
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        if not self.path:
            self._mem = data
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass  # sin disco no hay mapa persistente; el bot sigue

    @staticmethod
    def _key(chat, mid) -> str:
        return f"{chat}:{mid}"

    def put(self, chat, mid, kind: str, data: dict, now: float) -> None:
        if mid is None:
            return
        with self._lock:
            cur = {k: v for k, v in self._load().items() if now - float(v.get("t", 0)) < self.ttl}
            cur[self._key(chat, mid)] = {"kind": kind, "data": data, "t": now}
            self._save(cur)

    def get(self, chat, mid, now: float) -> dict | None:
        with self._lock:
            rec = self._load().get(self._key(chat, mid))
        if not rec or now - float(rec.get("t", 0)) >= self.ttl:
            return None
        return rec
