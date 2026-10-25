"""Copias de un aviso de decisión en el DM de otros operadores (p. ej. María), con los mismos botones.

El notificador registra aquí qué mensajes son copias del mismo aviso. Editarlo (texto o botones) en uno lo edita en
todos: lo decidido por uno se ve en el del otro («decidido por María 10:32») y los botones desaparecen en ambos. Que no
se decida dos veces lo garantiza el token del teclado, de un solo uso (CallbackStore.consume).
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

MAX_GRUPOS = 300  # los más recientes; Telegram no deja editar mensajes de >48 h igualmente


class Espejos:
    """Grupos de copias [(chat_id, message_id), ...]. Con `path`, persisten entre reinicios; sin él, en memoria."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else None
        self._mem: list[list[list[str]]] = []
        self._lock = threading.Lock()

    def _load(self) -> list:
        if self.path is None:
            return self._mem
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, groups: list) -> None:
        if self.path is None:
            self._mem = groups
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(groups), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, copias: list[tuple]) -> None:
        group = [[str(c), str(m)] for c, m in copias]
        with self._lock:
            groups = self._load() + [group]
            self._save(groups[-MAX_GRUPOS:])

    def hermanos(self, chat_id, message_id) -> list[tuple[str, str]]:
        """Las demás copias del mensaje (vacío si no es una copia)."""
        key = [str(chat_id), str(message_id)]
        for group in self._load():
            if key in group:
                return [(c, m) for c, m in group if [c, m] != key]
        return []
