"""Ayuda del bot de carriles (t_93e677e8): /ayuda agrupada por tarea y por rol, menú «/» por ámbito y sugerencias.

Deterministas, sin LLM y sin red. Los textos de las sugerencias y el enlace al documento están en sugerencias.yaml
(lo que falte o no sea válido se ignora; sin el YAML simplemente no hay sugerencias). Las funciones reciben la lista
de comandos (`COMMANDS` de commands.py) para no importarlo: commands.py importa este módulo.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "sugerencias.yaml"
DOCS_RELATIVE = "docs/bots/comandos.md"
ENFRIAMIENTO_S = 600.0  # un mismo comando se sugiere como mucho una vez cada 10 min por chat

# Comandos agrupados por lo que Oscar quiere hacer (no por orden alfabético). Todo comando de COMMANDS debe salir
# en algún grupo: lo comprueba tests/test_ayuda.py (los que falten van a «Otros»).
GRUPOS = (
    ("👀 Ver cómo va todo", ("hoy", "estado", "falta", "tareas", "cola", "tarea", "salud", "ram", "consumo", "integrador", "choques", "paradas",
                           "revision", "limpieza")),
    ("✅ Decidir y aprobar", ("decisiones", "atencion", "pendiente", "pendientes", "aprobar", "preparar")),
    ("➕ Pedir trabajo", ("hazlo", "pedir", "flujo", "workflow", "sesion")),
    ("🚀 Publicar", ("lote", "desplegar", "promover", "al_dia", "aldia", "encolar")),
    ("🧹 Ordenar el kanban", ("prioridad", "agrupar", "personal", "epicas", "retenidas", "backlog")),
    ("🔔 Avisos y ajustes", ("avisos", "silenciar", "ajustes")),
)
# La sesión de correcciones acumula el texto libre del DM: en un grupo no sirve, así que no se ofrece en su menú.
SOLO_DM = ("sesion",)
MENU_EXTRA = (("ayuda", "Qué puedo pedirte y cómo: comandos por tarea y por rol"),)


def comandos_de_ambito(commands, scope: str) -> list[tuple[str, str]]:
    """Menú «/» (setMyCommands) de un ámbito: /ayuda primero y luego los comandos; los grupos sin los solo-DM."""
    lista = list(MENU_EXTRA) + [(c, d) for c, d in commands]
    return [(c, d) for c, d in lista if not (scope == "all_group_chats" and c in SOLO_DM)]


def texto_ayuda(commands, solo_todas_las_marcas, docs_url: str | None = None) -> str:
    """Parte de la ayuda con los comandos por tarea y los roles (lo de «dónde contesta cada bot» lo añade commands)."""
    desc = dict(commands)
    usados = {c for _, cs in GRUPOS for c in cs}
    grupos = list(GRUPOS) + [("Otros", tuple(c for c, _ in commands if c not in usados))]
    partes = ["Comandos del bot de trabajos, por tarea:"]
    for titulo, cmds in grupos:
        lineas = [f"/{c} · {desc[c]}" for c in cmds if c in desc and not desc[c].startswith("Alias de")]  # alias: aparte
        if lineas:
            partes.append(f"\n{titulo}\n" + "\n".join(lineas))
    partes.append(
        "\nQuién puede usarlos:\n"
        "· Oscar y María: todos.\n"
        "· Operador acotado a una marca (Angel): los de su marca, sin "
        + ", ".join(f"/{c}" for c in solo_todas_las_marcas[:4]) + "… (lista en la guía).\n"
        "· Los demás: ninguno; hablan con Hermes o con Avisos.\n"
        "Si un comando no responde, dilo: mejor que dejar de usarlo.")
    guia = f"\nGuía con ejemplos: {docs_url}" if docs_url else f"\nGuía con ejemplos: {DOCS_RELATIVE} (repo del hub)"
    partes.append(guia)
    return "\n".join(partes)


# --- sugerencias por palabras -----------------------------------------------------------------------------------

def load_config(path: Path | str | None = None) -> dict:
    """{activo, docs_url, enfriamiento_s, reglas:[{comando, palabras, ejemplo}]} desde el YAML. Nunca lanza."""
    cfg = {"activo": True, "docs_url": "", "enfriamiento_s": ENFRIAMIENTO_S, "reglas": []}
    try:
        data = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return cfg
    if not isinstance(data, dict):
        return cfg
    if isinstance(data.get("activo"), bool):
        cfg["activo"] = data["activo"]
    if isinstance(data.get("docs_url"), str):
        cfg["docs_url"] = data["docs_url"].strip()
    if isinstance(data.get("enfriamiento_s"), (int, float)) and not isinstance(data["enfriamiento_s"], bool):
        cfg["enfriamiento_s"] = max(0.0, float(data["enfriamiento_s"]))
    for r in data.get("reglas") or ():
        if (isinstance(r, dict) and isinstance(r.get("comando"), str)
                and isinstance(r.get("palabras"), list) and all(isinstance(p, str) for p in r["palabras"])):
            cfg["reglas"].append({"comando": r["comando"].strip().lstrip("/").lower(), "palabras": r["palabras"],
                                  "ejemplo": r["ejemplo"].strip() if isinstance(r.get("ejemplo"), str) else ""})
    return cfg


def _norm(text: str) -> str:
    """Minúsculas, sin tildes y con espacios colapsados: «¿Qué FALTA?» -> «que falta»."""
    plano = unicodedata.normalize("NFD", (text or "").casefold())
    plano = "".join(ch for ch in plano if unicodedata.category(ch) != "Mn")
    return " ".join(re.sub(r"[^\w\s]", " ", plano).split())


def sugerir(text: str, cfg: dict, *, permitido=lambda comando: True) -> dict | None:
    """Primera regla cuya frase aparece en el texto (palabras completas): {comando, ejemplo}. None si ninguna o si el
    comando no está permitido para quien escribe. Reglas fijas: sin LLM."""
    if not cfg.get("activo"):
        return None
    plano = _norm(text)
    for regla in cfg.get("reglas", ()):
        for frase in regla["palabras"]:
            f = _norm(frase)
            if f and re.search(rf"(?<!\w){re.escape(f)}(?!\w)", plano):
                if permitido(regla["comando"]):
                    return {"comando": regla["comando"], "ejemplo": regla["ejemplo"]}
                break
    return None


def texto_sugerencia(sug: dict, descripcion: str | None) -> str:
    ejemplo = f" Por ejemplo: {sug['ejemplo']}." if sug.get("ejemplo") else ""
    que = f": {descripcion}" if descripcion else ""
    return f"💡 Eso ya lo hace /{sug['comando']}{que}.{ejemplo} Todos los comandos: /ayuda"
