"""Validador de `args` de la plantilla de workflow `implementar-con-revision` (F0 del pitch workflows-desde-carriles).

El guion vive en `workflows/implementar-con-revision.js.txt` y valida por su cuenta al arrancar; este módulo es el espejo
en Python para que el runner (F1) rechace una tarjeta mal formada ANTES de lanzar nada. Un test comprueba que ambas listas
de campos obligatorios coinciden. Lección del piloto: `autorizacion` es obligatoria (sin la fuente de la autorización los
agentes se niegan a ejecutar). Sin red ni estado en disco.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import ROOT

PLANTILLA = ROOT / "workflows" / "implementar-con-revision.js.txt"
REQUERIDOS = ("repo", "remoto", "base", "worktree", "rama", "encargo", "autorizacion", "test_cmd", "atribucion_commit")
LISTAS = ("ficheros_permitidos", "ficheros_vetados")
_RAMA = re.compile(r"^lane/[A-Za-z0-9_.-]+$")
_TARJETA = re.compile(r"t_[0-9a-f]{4,}", re.I)
_FECHA = re.compile(r"\d{1,2}[-/ ]\w+|\d{4}-\d{2}-\d{2}")
_SECRETO = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|gho_[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|bot\d{6,}:[A-Za-z0-9_-]{20,}"
    r"|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+)")


def validar_args(args: dict) -> list[str]:
    """Lista de errores (vacía = válidos). Mismas reglas que la validación del guion."""
    a = args or {}
    errores = [f"falta el argumento obligatorio «{k}»" for k in REQUERIDOS
               if not isinstance(a.get(k), str) or not a[k].strip()]
    for k in LISTAS:
        v = a.get(k)
        if not isinstance(v, list) or any(not isinstance(x, str) or not x.strip() for x in v):
            errores.append(f"«{k}» debe ser una lista de rutas (puede ir vacía)")
    rama = a.get("rama")
    if isinstance(rama, str) and rama.strip():
        if not _RAMA.match(rama):
            errores.append("«rama» debe ser lane/<id>")
        if rama == a.get("base") or rama in ("main", "master"):
            errores.append("«rama» no puede ser la base ni main/master")
    aut = a.get("autorizacion")
    if isinstance(aut, str) and aut.strip() and not _TARJETA.search(aut) and not _FECHA.search(aut):
        errores.append("«autorizacion» debe citar la tarjeta (t_xxxx) y/o la decisión de Oscar con fecha y texto literal")
    return errores


def enmascarar(texto: str) -> str:
    """Sustituye por *** lo que parece un secreto (para logs y para el dry-run)."""
    return _SECRETO.sub("***", texto)


def autorizacion_desde_tarjeta(tarjeta_id: str, cuerpo: str) -> str:
    """Fuente de la autorización a partir de la tarjeta: id + su cuerpo (que lleva las respuestas literales de Oscar)."""
    return f"Autorizado por la tarjeta {tarjeta_id} del kanban de Hermes. Texto de la tarjeta y decisiones de Oscar:\n{cuerpo.strip()}"
