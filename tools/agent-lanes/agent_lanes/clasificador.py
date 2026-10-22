"""Clasificador de tarjetas (retrospectiva del 22-10): ¿lo puede hacer un carril, o hay que llevarlo al integrador?

36 de ~91 bloqueos de una semana eran LIMITACION-GUARD descubiertos DESPUÉS de gastar una ejecución (producción/env, SQL
en Supabase, escribir en ~/.hermes, otro repo, binarios, ramas de otras tareas). Aquí se detectan ANTES, sin LLM, con
reglas que son DATOS (`clasificador.yaml`; lo que falte vale DEFAULTS, así que el fichero puede no existir).

Dos usos, mismas reglas:
- el runner (antes de lanzar al worker): una regla `integrador` aparca la tarjeta en la cola del integrador (el mismo
  bloqueo LIMITACION-GUARD de siempre, sin gastar worker); una regla `aviso` solo avisa al worker;
- `/pedir` y `/hazlo` (al crear): avisan a Oscar en la confirmación de que la tarjeta no la hará un carril.

Y el tipo «consulta» (`es_consulta`): tarjeta sin rama ni verificación mecánica; la atiende `consulta.py`.

Salidas de emergencia (línea del cuerpo, como `Rama-origen:`): `Clasificador: omitir` desactiva todo el chequeo. Tampoco
se aplica si Oscar/el coordinador/la revisión ya han decidido algo en la tarjeta (lo gestiona el runner).
"""
from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import ROOT, Lane

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "clasificador.yaml"
SKIP_LINE = re.compile(r"^\s*Clasificador:\s*omitir\s*$", re.M | re.I)
ORIGIN_LINE = re.compile(r"^\s*Rama-origen:\s*\S+\s*$", re.M | re.I)
LANE_BRANCH = re.compile(r"\blane/t_[0-9a-f]{8}\b", re.I)
CONSULTA_LINE = re.compile(r"^\s*Tipo:\s*consulta\b", re.M | re.I)
CONSULTA_TITLE = re.compile(r"^\s*\[\s*consulta\s*\]", re.I)

DEFAULTS: dict = {
    "enabled": True,
    # id, motivo (texto llano para Oscar), accion (integrador | aviso), patrones (regex, sin distinguir mayúsculas).
    "reglas": [
        {"id": "produccion_env", "accion": "integrador",
         "motivo": "toca producción o variables de entorno (despliegues, env vars): un carril no puede",
         "patrones": [r"\bvariables? de entorno\b", r"\benv vars?\b", r"\bvercel env\b", r"\brailway (?:variables|up)\b",
                      r"\bdesplieg\w+ (?:a|en) producci[oó]n\b", r"\bdeploy\w* (?:a|to) prod", r"\bdokploy\b"]},
        {"id": "sql_remoto", "accion": "integrador",
         "motivo": "ejecuta SQL o migraciones contra una base remota (Supabase/Alembic): un carril no puede",
         "patrones": [r"supabase.{0,60}\b(?:sql|migraci\w+|tabla|policy|rls)\b", r"\b(?:sql|migraci\w+).{0,60}supabase",
                      r"\bexecute_sql\b", r"\bapply_migration\b", r"\balembic upgrade\b"]},
        {"id": "fuera_worktree", "accion": "integrador",
         "motivo": "escribe fuera del worktree (~/.hermes, ~/.claude, configuración del sistema): un carril no puede",
         "patrones": [r"~/\.hermes\b", r"[\\/]\.hermes[\\/]", r"~/\.claude\b", r"%USERPROFILE%", r"\bhermes config\b"]},
        {"id": "binario", "accion": "integrador",
         "motivo": "instala o descarga un binario/programa en la máquina: un carril no puede",
         "patrones": [r"\bgitleaks\b", r"\bwinget install\b", r"\bchoco(?:latey)? install\b",
                      r"\b(?:instalar|descargar)\b.{0,30}\.exe\b"]},
        {"id": "rama_ajena", "accion": "aviso",
         "motivo": "menciona la rama de otra tarea ({ramas}) sin línea `Rama-origen:`: tu rama parte de {base}, no de esa",
         "patrones": []},  # regla estructural (ver evaluar): rama lane/t_… sin Rama-origen
    ],
    # Repos que NO son de ningún carril y una tarjeta de código no puede tocar (se añaden los repos de los demás carriles
    # por su slug owner/repo de lanes.yaml). Nombres tal cual aparecen en GitHub.
    "otros_repos": ["pildora-handbook", "pildora-mcp", "pildora-website", "pildora-recursos", "pildora-cmo-workspace"],
    "consulta": {"max_usd": 0.8, "timeout_seconds": 300},
    "motivo_otro_repo":"pide cambios en otro repo ({repo}) distinto del del carril: un carril trabaja solo el suyo",
}


@dataclass
class Veredicto:
    integrador: list[tuple[str, str]] = field(default_factory=list)  # (id de regla, motivo): no lo hace un carril
    avisos: list[tuple[str, str]] = field(default_factory=list)      # (id, aviso): se lo decimos al worker

    @property
    def va_al_integrador(self) -> bool:
        return bool(self.integrador)

    def motivos(self) -> list[str]:
        return [m for _, m in self.integrador]

    def texto_integrador(self) -> str:
        return "\n".join(f"- {m}" for m in self.motivos())


def _merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _merge(base[k], v) if k in base else v
        return out
    return over if type(over) is type(base) else base


def load_config(path: Path | str | None = None) -> dict:
    """DEFAULTS + lo que diga el YAML (un valor de otro tipo que el del defecto se ignora). Nunca lanza."""
    cfg = copy.deepcopy(DEFAULTS)
    try:
        data = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return cfg
    return _merge(cfg, data) if isinstance(data, dict) else cfg


def es_consulta(task: dict | None) -> bool:
    """Tarjeta de tipo «consulta»: línea `Tipo: consulta` en el cuerpo (la escribe /pedir) o título `[Consulta] …`."""
    task = task or {}
    return bool(CONSULTA_LINE.search(task.get("body") or "") or CONSULTA_TITLE.search(task.get("title") or ""))


def _otros_repos(lane: Lane | None, lanes: dict | None, cfg: dict) -> list[str]:
    own = (getattr(lane, "github", "") or "").lower()
    out = [str(r) for r in cfg.get("otros_repos") or ()]
    for other in (lanes or {}).values():
        slug = (getattr(other, "github", "") or "").lower()
        if slug and slug != own:
            out.append(slug)  # owner/repo completo: «oscar-hq» a secas aparece como contexto en tarjetas del hub
    return out


def evaluar(lane: Lane | None, task: dict | None, *, lanes: dict | None = None, cfg: dict | None = None) -> Veredicto:
    """Veredicto para una tarjeta de código en `lane`. Nunca lanza: ante un fallo, veredicto vacío (se trabaja como siempre)."""
    veredicto = Veredicto()
    try:
        cfg = cfg or load_config()
        task = task or {}
        text = f"{task.get('title') or ''}\n{task.get('body') or ''}"
        if not cfg.get("enabled") or getattr(lane, "kind", "implement") != "implement" or SKIP_LINE.search(text):
            return veredicto
        for regla in cfg.get("reglas") or ():
            if not isinstance(regla, dict):
                continue
            rid, accion, motivo = regla.get("id"), regla.get("accion"), str(regla.get("motivo") or regla.get("id"))
            if rid == "rama_ajena":
                ramas = list(dict.fromkeys(m.lower() for m in LANE_BRANCH.findall(text)))
                ramas = [r for r in ramas if r != f"lane/{task.get('id')}"]
                if ramas and not ORIGIN_LINE.search(text):
                    veredicto.avisos.append((rid, motivo.format(ramas=", ".join(ramas),
                                                                base=getattr(lane, "base_ref", "la base"))))
                continue
            if any(re.search(p, text, re.I | re.S) for p in regla.get("patrones") or ()):
                (veredicto.integrador if accion == "integrador" else veredicto.avisos).append((rid, motivo))
        for repo in _otros_repos(lane, lanes, cfg):
            if re.search(rf"(?<![\w-]){re.escape(repo)}(?![\w-])", text, re.I):
                veredicto.integrador.append(("otro_repo", str(cfg["motivo_otro_repo"]).format(repo=repo)))
                break
    except Exception as exc:  # el clasificador nunca impide trabajar
        log.warning("clasificador: fallo ignorado: %s", exc)
        return Veredicto()
    return veredicto


def aviso_creacion(veredicto: Veredicto) -> str:
    """Línea para la confirmación de /pedir y /hazlo («HTML» no: texto plano) o '' si el carril puede con ello."""
    if not veredicto.va_al_integrador:
        return ""
    return ("\n⚠️ Ojo: un carril no podrá hacer esto solo (" + "; ".join(veredicto.motivos())
            + "). Irá a la cola del integrador (/integrador) en vez de gastar una ejecución.")
