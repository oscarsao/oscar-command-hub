"""Clasificador de bloqueos (entrega E1 de specs/aviso-bloqueo.md): del motivo técnico de un bloqueo `transient` saca
la clase (transitorio / estructural), un resumen llano del motivo real y lo que falta para seguir.

- Transitorio: reintentar tiene sentido (dos carriles a la vez, tiempo agotado, límite de uso) -> [Reintentar][Aparcar].
- Estructural: guard/rutas vetadas, push perdido, conflicto de merge, migración vetada -> reintentar repite el error en
  bucle, así que NO hay [Reintentar]: [Que lo resuelva el integrador][Aparcar].
- Sin patrón que case: `desconocido` = comportamiento de siempre (botones del transitorio).

Todo es configuración (regla «nada hardcodeado»): `bloqueos.yaml` junto a lanes.yaml; lo que falte vale DEFAULTS, así que
el fichero puede no existir. Se edita en el repo y se aplica con /aplicar. Los textos del aviso salen SOLO de la
configuración (nunca del stderr del motivo), por lo que son públicos: sin rutas ni trazas. El detalle técnico lo da el
botón [🔎 Más detalle]. Funciones puras: sin red, sin Hermes.
"""
from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import ROOT

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "bloqueos.yaml"
TRANSITORIO, ESTRUCTURAL, DESCONOCIDO = "transitorio", "estructural", "desconocido"
CLASES = (TRANSITORIO, ESTRUCTURAL)
BUTTON_IDS = ("retry", "park", "integrador", "detalle")

# Cuota/límite de uso de Claude (cuota.py reutiliza el regex para pausar el planificador hasta la hora de reset).
CUOTA_REGEX = r"usage limit|hit your limit"  # estricto: un «limit reached» o «resets in» ajeno no debe pausar todo el planificador

DEFAULTS: dict = {
    # El primer patrón que casa gana (sin distinguir mayúsculas). `regex` se busca dentro del motivo técnico.
    "patrones": [
        {"clase": ESTRUCTURAL, "regex": r"toca rutas vetadas",
         "resumen": "este carril tiene vetadas las rutas que toca la tarea",
         "que_falta": "que otro carril con permiso (el integrador) la haga, o quitar esas rutas de la tarea"},
        {"clase": ESTRUCTURAL, "regex": r"Destino-Ops no permitido",
         "resumen": "el destino que pide la tarea no está permitido a este carril",
         "que_falta": "cambiar el destino de la tarea o ampliarlo en lanes.yaml"},
        {"clase": ESTRUCTURAL, "regex": r"non-fast-forward|no llegó al remoto|rama no empujada|push (rechazado|rejected)",
         "resumen": "el push de la rama no llegó a GitHub",
         "que_falta": "que el integrador compruebe la rama en el remoto y la empuje"},
        {"clase": ESTRUCTURAL, "regex": r"\bCONFLICT\b|conflicto de merge|merge conflict",
         "resumen": "hay un conflicto de merge con main",
         "que_falta": "que el integrador resuelva el conflicto a mano"},
        {"clase": ESTRUCTURAL, "regex": r"alembic|migraci[oó]n vetada",
         "resumen": "la tarea toca una migración, que este carril no puede aplicar",
         "que_falta": "que el integrador (único propietario de Alembic) la revise y la cree"},
        {"clase": TRANSITORIO, "regex": r"cannot lock ref|unable to create '.*\.lock'",
         "resumen": "git no pudo bloquear una referencia (dos carriles a la vez)",
         "que_falta": "nada: basta reintentar"},
        {"clase": TRANSITORIO, "regex": CUOTA_REGEX,
         "resumen": "se agotó la cuota de uso de Claude",
         "que_falta": "nada: el carril se pausa solo y se reanuda a la hora de reset"},
        {"clase": TRANSITORIO, "regex": r"timed out|max_runtime|el worker no terminó|tiempo o presupuesto",
         "resumen": "el worker se quedó sin tiempo o sin presupuesto",
         "que_falta": "reintentar; si se repite, partir la tarea en otras más pequeñas"},
        {"clase": TRANSITORIO, "regex": r"rate.?limit|overloaded|\b429\b|\b529\b",
         "resumen": "el modelo está saturado o con límite de uso",
         "que_falta": "esperar un poco y reintentar"},
        {"clase": TRANSITORIO, "regex": r"runner reiniciado",
         "resumen": "el runner se reinició con la tarea en curso",
         "que_falta": "nada: basta reintentar"},
    ],
    "textos": {
        "falta": "Falta: {que_falta}",
    },
    # Botones por clase (ids de BUTTON_IDS). El estructural NUNCA lleva «retry», aunque el yaml lo pida.
    "botones": {
        TRANSITORIO: ["retry", "park", "detalle"],
        ESTRUCTURAL: ["integrador", "park", "detalle"],
    },
    "umbrales": {
        "detalle_max": 3000,         # caracteres del motivo técnico que devuelve [🔎 Más detalle]
        "detalle_comentarios": 3,    # últimos comentarios de la tarjeta que acompañan al motivo
    },
}


@dataclass(frozen=True)
class Diagnostico:
    clase: str            # transitorio | estructural | desconocido
    resumen: str = ""     # motivo real, llano y público ("" si desconocido)
    que_falta: str = ""

    @property
    def estructural(self) -> bool:
        return self.clase == ESTRUCTURAL


def _merge(base, over):
    """Como tipo_peticion._merge pero las listas de `patrones` se sustituyen enteras (el yaml manda)."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base[k], v) if k in base else v
        return out
    return over if type(over) is type(base) or base is None else base


def load_config(path: Path | str | None = None) -> dict:
    """DEFAULTS + lo que diga el YAML (un valor de otro tipo que el del defecto se ignora). Nunca lanza."""
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else SETTINGS_PATH
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return cfg
    return _merge(cfg, data) if isinstance(data, dict) else cfg


def clasificar(reason: str | None, cfg: dict | None = None) -> Diagnostico:
    """Diagnóstico del motivo técnico de un bloqueo. Un patrón mal formado (clase rara, regex inválida, sin resumen) se
    ignora con un aviso en el log: nunca rompe el aviso."""
    cfg = cfg or load_config()
    text = str(reason or "")
    for pat in cfg.get("patrones") or ():
        try:
            clase, rx, resumen = pat["clase"], pat["regex"], str(pat["resumen"]).strip()
            if clase not in CLASES or not resumen:
                raise ValueError(f"clase {clase!r} o resumen vacío")
            if re.search(rx, text, re.IGNORECASE):
                return Diagnostico(clase, resumen, str(pat.get("que_falta") or "").strip())
        except (KeyError, TypeError, ValueError, re.error) as exc:
            log.warning("bloqueos.yaml: patrón ignorado (%s): %r", exc, pat)
    return Diagnostico(DESCONOCIDO)


def texto_publico(diag: Diagnostico, cfg: dict | None = None) -> str | None:
    """«<resumen> · Falta: <qué>» para la línea de estado del aviso; None si no se pudo diagnosticar."""
    if diag.clase == DESCONOCIDO:
        return None
    cfg = cfg or load_config()
    falta = ""
    if diag.que_falta:
        try:
            falta = str((cfg.get("textos") or {}).get("falta") or "Falta: {que_falta}").format(que_falta=diag.que_falta)
        except (KeyError, IndexError, ValueError):
            falta = f"Falta: {diag.que_falta}"
    return " · ".join(p for p in (diag.resumen, falta) if p)


def botones_ids(clase: str, cfg: dict | None = None) -> list[str]:
    """Ids de botón de la clase, sin ids desconocidos ni duplicados; el estructural nunca incluye «retry»."""
    cfg = cfg or load_config()
    raw = (cfg.get("botones") or {}).get(clase)
    ids = [b for b in dict.fromkeys(raw or ()) if b in BUTTON_IDS]
    if clase == ESTRUCTURAL:
        ids = [b for b in ids if b != "retry"]
    return ids or list(DEFAULTS["botones"][ESTRUCTURAL if clase == ESTRUCTURAL else TRANSITORIO])
