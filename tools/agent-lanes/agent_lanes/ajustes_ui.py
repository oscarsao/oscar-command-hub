"""Pantallas de /ajustes (texto + botones) sobre ajustes_hub. Puro: no envía ni guarda; commands.py lo une a Telegram.

Tres niveles de botones: grupos -> ajustes del grupo -> valores posibles (+ «Valor por defecto»). Lo raro (listas de
ficheros) va en el grupo Avanzado y se edita con texto: `/ajustes <clave> <valor>`.
"""
from __future__ import annotations

from datetime import datetime

from .ajustes_hub import AVANZADO, GRUPOS, SETTINGS, Ajustes
from .notices import truncate

AJUSTES = "ajustes"
RESET = "__reset__"
ATRAS = "__atras__"
AYUDA_AVANZADO = "Modo avanzado: /ajustes <clave> <valor> (ej. /ajustes atascos.solo_anadir CHANGELOG.md, docs/NOTAS.md) · /ajustes historial [clave]"


def _claves(grupo: str) -> list[str]:
    if grupo == AVANZADO:
        return [k for k, s in SETTINGS.items() if s.avanzado]
    return [k for k, s in SETTINGS.items() if s.grupo == grupo and not s.avanzado]


def _valor_txt(store: Ajustes, key: str) -> str:
    spec, v = SETTINGS[key], store.valor(key)
    return spec.describe(v) + ("" if not store.editado(key) else " ✏️")


def menu() -> tuple[str, list[str], list[list[dict]]]:
    items = [*GRUPOS, AVANZADO]
    text = ("⚙️ Ajustes del hub\nElige un grupo. Cada cambio queda anotado (quién y cuándo) y se puede dejar como venía "
            "de fábrica con un botón.")
    return text, items, [[{"text": g, "action": AJUSTES, "index": i}] for i, g in enumerate(items)]


def grupo(store: Ajustes, nombre: str) -> tuple[str, list[str], list[list[dict]]]:
    keys = _claves(nombre)
    lines = [f"⚙️ {nombre}" + ("  (editado = ✏️)" if keys else "")]
    for k in keys:
        s = SETTINGS[k]
        lines.append(f"• {s.etiqueta}: {_valor_txt(store, k)}" + (" · al reiniciar el runner" if s.reinicio else ""))
    if nombre == AVANZADO:
        lines += ["", AYUDA_AVANZADO]
    rows = [[{"text": truncate(SETTINGS[k].etiqueta, 40), "action": AJUSTES, "index": i}] for i, k in enumerate(keys)]
    rows.append([{"text": "⬅️ Grupos", "action": AJUSTES, "index": len(keys)}])
    return "\n".join(lines), [*keys, ATRAS], rows


def detalle(store: Ajustes, key: str) -> tuple[str, list, list[list[dict]]]:
    s = SETTINGS[key]
    lines = [f"⚙️ {s.etiqueta}", f"Ahora: {_valor_txt(store, key)}",
             f"De fábrica: {s.describe(s.default)}"]
    if s.ayuda:
        lines.append(s.ayuda)
    if s.reinicio:
        lines.append("🔁 Se aplica al reiniciar el runner (el cambio se guarda ya; yo no lo reinicio).")
    if s.aviso_ram is not None:
        lines.append("⚠️ Más workers = más RAM: con 3 o más puede quedarse sin memoria.")
    hist = store.historial(key, 3)
    if hist:
        lines.append("Últimos cambios: " + " · ".join(
            f"{datetime.fromtimestamp(h['ts']):%d/%m %H:%M} {h.get('quien')}" for h in hist))
    values = list(s.opciones)
    rows, row = [], []
    for i, v in enumerate(values):
        mark = "✅ " if store.editado(key) and store.valor(key) == v else ""
        row.append({"text": mark + (s.describe(v) if v != "" else "Como en lanes.yaml"), "action": AJUSTES, "index": i})
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "↩️ Valor por defecto", "action": AJUSTES, "index": len(values)},
                 {"text": "⬅️ Atrás", "action": AJUSTES, "index": len(values) + 1}])
    if not values:
        lines.append(AYUDA_AVANZADO)
    return "\n".join(lines), [*values, RESET, ATRAS], rows


def historial(store: Ajustes, key: str | None = None) -> str:
    hist = store.historial(key, 10)
    if not hist:
        return "Todavía no se ha cambiado ningún ajuste."
    return "📜 Últimos cambios\n" + "\n".join(
        f"• {datetime.fromtimestamp(h['ts']):%d/%m %H:%M} · {h.get('quien')} · {h['key']}: {h.get('antes')} → "
        f"{h.get('despues')} ({h.get('accion')})" for h in hist)
