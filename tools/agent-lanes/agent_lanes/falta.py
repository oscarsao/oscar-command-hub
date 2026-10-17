"""Piezas puras de "qué falta": motivos de exclusión del lote en lenguaje llano y checklist de /falta.

Sin red, sin Telegram, sin estado: reciben datos ya leídos y devuelven texto o listas. Quien las llama (batch.py,
commands.py) pone los botones; aquí solo se decide el texto y la causa.

Causas de una exclusión (`kind`): secret (secret-scan), conflict (choca con la base u otra rama del lote o de
Alembic), tests (rompe tests/build), other (rutas vetadas, ejecutables, rama cambiada…: solo se explica).
"""
from __future__ import annotations

import html
import re

from .notices import truncate

SECRET, CONFLICT, TESTS, OTHER = "secret", "conflict", "tests", "other"
ENV_RE = re.compile(r"^\s*Env-Pendiente:\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*(.*?))?\s*$", re.M)
ENV_VALUE_MAX = 60


def classify(reason: str | None) -> str:
    low = (reason or "").lower()
    if "secreto" in low:
        return SECRET
    if "choca con" in low:
        return CONFLICT
    if "rompe los tests" in low or "tests del carril" in low or "build/typecheck" in low:
        return TESTS
    return OTHER


def plain_reason(reason: str | None) -> str:
    """Motivo en una o dos frases llanas. Sin recortes: lo que no cabe en el aviso lo cubre [💬 Explícame más]."""
    reason = (reason or "").strip()
    kind = classify(reason)
    if kind == SECRET:
        where = reason.split("posible secreto en el código:", 1)[-1].split(";")[0].strip()
        return ("el escáner cree ver una clave o contraseña en el código" + (f" ({where})" if where else "")
                + ". Suele ser un dato de prueba (falso positivo): conviene renombrarlo para que no lo parezca")
    if kind == CONFLICT:
        files = re.search(r"\(([^)]*)\)\s*$", reason)
        what = "una migración de Alembic" if "alembic" in reason.lower() else "los mismos archivos"
        return (f"toca {what} que otra rama o la rama principal" + (f" ({files.group(1)})" if files else "")
                + ", así que no se puede juntar sin rebasarla antes")
    if kind == TESTS:
        return "con esta rama los tests o el build del lote fallan; hay que arreglarla para que pase"
    return reason or "motivo no indicado"


def env_pending(body: str | None) -> list[tuple[str, str]]:
    """`Env-Pendiente: NOMBRE=valor-ejemplo` del cuerpo de una tarjeta -> [(nombre, ejemplo)]. Sin duplicados."""
    seen, out = set(), []
    for name, example in ENV_RE.findall(body or ""):
        if name not in seen:
            seen.add(name)
            out.append((name, truncate(example, ENV_VALUE_MAX)))
    return out


def falta_message(brand: str | None, steps: list[str], empty: str = "Nada te frena: no hay nada pendiente 🎉") -> str:
    """Un solo mensaje HTML: el siguiente paso arriba, el resto numerado debajo."""
    head = "🧭 <b>Qué falta</b>" + (f" · {html.escape(brand)}" if brand else "")
    if not steps:
        return head + "\n" + empty
    out = [head, "", "👉 <b>Siguiente:</b> " + steps[0]]
    out += [f"{n}. {s}" for n, s in enumerate(steps[1:], start=2)]
    return "\n".join(out)
