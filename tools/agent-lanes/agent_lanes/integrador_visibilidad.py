"""Visibilidad del integrador en Telegram (t_5624611d; spec en specs/integrador-visibilidad-telegram.md).

Lo que el integrador hace desde Claude Code (merges, migraciones, semillas) no pasa por el kanban y no salía en Telegram.
Tres piezas, sin un canal de avisos nuevo (se reutilizan /integrador, el parte de las 9:00 y el notificador del bot):

1. Estado: `.state/integrador_estado.md`, que escribe el integrador tras cada paso. `parse_estado` lo lee tolerando líneas
   desconocidas; `resumen`/`detalle` lo muestran (corto + «ver más»); si falta o es viejo se dice en una línea.
2. Cola de PR de sesiones interactivas: `/encolar <repo> <PR>` los anota en `ColaExternos` con el commit exacto. El botón
   de Oscar registra el OK sobre ESE commit; si el PR cambia, el OK deja de valer. Aquí no se fusiona nada.
3. Incidencia de Railway: `IncidenciaRailway` avisa una vez al abrirse («despliegue en cola…», con repo y SHA) y otra al
   normalizarse.

Todo lo que se puede ajustar (hora del parte, antigüedad máxima, tamaño del resumen, repos, textos) vive en
`integrador_visibilidad.yaml`; con el fichero ausente o un valor inválido valen los defaults de `Ajustes`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path

import yaml

from .config import ROOT
from .notices import truncate

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "integrador_visibilidad.yaml"
ESTADO_FILE = "integrador_estado.md"
EXTERNOS_FILE = "integrador_externos.json"
INCIDENCIA_FILE = "integrador_railway.json"
KEYS = ("repo", "pr", "sha", "estado", "siguiente")  # lo demás se tolera y se ignora
PR_RE = re.compile(r"(?:/pull/|#)?(\d{1,6})/?$")

TEXTOS = {
    "estado_falta": "🧑‍💻 Estado del integrador: no hay parte (falta .state/integrador_estado.md). No sé qué ha hecho desde sus consolas.",
    "estado_viejo": "🧑‍💻 Estado del integrador: el último parte tiene {horas} h (límite {max} h); puede haber pasos sin reflejar.",
    "incidencia_abre": "🚧 Despliegue en cola por incidencia de Railway · {repo} · {sha}. Se reintenta al normalizarse.",
    "incidencia_cierra": "✅ Railway vuelve a responder · {repo}: ya se puede desplegar {sha}.",
}


@dataclass(frozen=True)
class Ajustes:
    hora_parte: int = 9            # hora (Madrid) del parte diario por carril
    estado_max_horas: float = 24   # un estado más viejo se declara viejo
    resumen_pasos: int = 3         # pasos que entran en el resumen corto (el resto, en «ver más»)
    detalle_max: int = 3500        # tope de caracteres de «ver más» (el límite de Telegram es 4096)
    repos: dict = field(default_factory=dict)  # alias -> owner/repo encolables; vacío = los repos de los carriles
    textos: dict = field(default_factory=dict)  # sobrescribe TEXTOS


def load_ajustes(path: Path | str | None = None) -> Ajustes:
    """Ajustes desde el YAML; lo que falte o no sea válido queda en su valor por defecto."""
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return Ajustes()
    if not isinstance(raw, dict):
        return Ajustes()
    d, out = Ajustes(), {}
    for f in fields(Ajustes):
        v = raw.get(f.name)
        if f.name == "hora_parte" and isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 21:
            out[f.name] = v
        elif f.name == "estado_max_horas" and isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            out[f.name] = float(v)
        elif f.name in ("resumen_pasos", "detalle_max") and isinstance(v, int) and not isinstance(v, bool) and v >= 1:
            out[f.name] = min(v, 3900) if f.name == "detalle_max" else v
        elif f.name == "repos" and isinstance(v, dict):
            out[f.name] = {str(k).lower(): str(s) for k, s in v.items() if re.fullmatch(r"[\w.-]+/[\w.-]+", str(s))}
        elif f.name == "textos" and isinstance(v, dict):
            out[f.name] = {str(k): str(s) for k, s in v.items() if k in TEXTOS and isinstance(s, str)}
    return Ajustes(**{**{f.name: getattr(d, f.name) for f in fields(Ajustes)}, **out})


def texto(aj: Ajustes, clave: str, **kw) -> str:
    """Texto de un aviso (editable en el YAML). Una plantilla rota no tumba el aviso: vale el default."""
    try:
        return (aj.textos.get(clave) or TEXTOS[clave]).format(**kw)
    except (KeyError, IndexError, ValueError):
        return TEXTOS[clave].format(**kw)


# --- 1. estado del integrador ----------------------------------------------------------------------------------

def parse_estado(text: str, mtime: float | None = None) -> dict:
    """{"actualizado": epoch|None, "pasos": [{"paso", "repo", "pr", "sha", "estado", "siguiente"}]}.

    Formato: cabecera opcional `Actualizado: <ISO 8601>` y un bloque por paso: `## <paso>` seguido de líneas
    `clave: valor` (repo, pr, sha, estado, siguiente). Cualquier otra línea o clave se ignora; el último bloque es el más
    reciente. Sin cabecera, la fecha es la del fichero (`mtime`)."""
    actualizado, pasos, cur = None, [], None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            cur = {"paso": line[3:].strip(), **{k: "" for k in KEYS}}
            pasos.append(cur)
            continue
        m = re.match(r"^[-*]?\s*([A-Za-záéíóú]+)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1).lower(), m.group(2).strip()
        if cur is None and key == "actualizado":
            try:
                dt = datetime.fromisoformat(val)
                actualizado = dt.timestamp()
            except ValueError:
                pass
        elif cur is not None and key in KEYS:
            cur[key] = val
    return {"actualizado": actualizado if actualizado is not None else mtime, "pasos": [p for p in pasos if p["paso"]]}


def leer_estado(path: Path | str | None) -> dict | None:
    """El estado parseado, o None si el fichero no existe o no se puede leer."""
    if not path:
        return None
    p = Path(path)
    try:
        return parse_estado(p.read_text(encoding="utf-8"), p.stat().st_mtime)
    except OSError:
        return None


def _linea_paso(p: dict, largo: bool = False) -> str:
    ref = " ".join(x for x in (p["repo"], f"PR #{p['pr'].lstrip('#')}" if p["pr"] else "", p["sha"][:9]) if x)
    sig = f" ➡️ {truncate(p['siguiente'], 120 if largo else 60)}" if p["siguiente"] else ""
    return f"• {truncate(p['paso'], 80 if largo else 50)}" + (f" · {ref}" if ref else "") \
        + (f" · {p['estado']}" if p["estado"] else "") + sig


def aviso_estado(est: dict | None, aj: Ajustes, now: float) -> str | None:
    """Una línea si el estado falta o está viejo; None si está al día."""
    if est is None or not est["pasos"] and est.get("actualizado") is None:
        return texto(aj, "estado_falta")
    ts = est.get("actualizado")
    horas = (now - ts) / 3600 if ts else None
    if horas is None or horas > aj.estado_max_horas:
        return texto(aj, "estado_viejo", horas="?" if horas is None else int(horas), max=int(aj.estado_max_horas))
    return None


def resumen(est: dict | None, aj: Ajustes, now: float) -> tuple[str, bool]:
    """(texto corto, hay_mas): el aviso de falta/viejo, o los últimos `resumen_pasos` pasos. `hay_mas` pone el botón."""
    aviso = aviso_estado(est, aj, now)
    if est is None or not est["pasos"]:
        return aviso or "🧑‍💻 Estado del integrador: sin pasos anotados.", False
    ultimos = est["pasos"][-aj.resumen_pasos:][::-1]
    lines = [aviso] if aviso else []
    lines += ["🧑‍💻 Estado del integrador (lo último primero)", *[_linea_paso(p) for p in ultimos]]
    return "\n".join(lines), len(est["pasos"]) > len(ultimos)


def detalle(est: dict | None, aj: Ajustes, now: float) -> str:
    """«Ver más»: todos los pasos, más recientes primero, dentro del tope de Telegram."""
    if est is None or not est["pasos"]:
        return aviso_estado(est, aj, now) or "🧑‍💻 Estado del integrador: sin pasos anotados."
    lines = [aviso_estado(est, aj, now) or "", f"🧑‍💻 Estado del integrador · {len(est['pasos'])} pasos"]
    lines = [l for l in lines if l] + [_linea_paso(p, True) for p in est["pasos"][::-1]]
    out = "\n".join(lines)
    return out if len(out) <= aj.detalle_max else out[:aj.detalle_max].rsplit("\n", 1)[0] + "\n… (hay más en el fichero)"


# --- 2. cola de PR de sesiones interactivas --------------------------------------------------------------------

def parse_encolar(args: str) -> tuple[str, int] | None:
    """`<repo> <PR>` (PR como 12, #12 o URL …/pull/12) -> (repo en minúsculas, número). None = no se entiende."""
    parts = args.split()
    if len(parts) != 2:
        return None
    m = PR_RE.search(parts[1])
    return (parts[0].lower(), int(m.group(1))) if m else None


class ColaExternos:
    """{items: {"<alias>#<pr>": {repo, slug, pr, title, sha, queued_at, ok_sha, ok_at}}} en un JSON."""

    def __init__(self, path: Path | str, now=time.time):
        self.path, self._now = Path(path), now

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        if not isinstance(data.get("items"), dict):
            data["items"] = {}
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def items(self) -> dict[str, dict]:
        return self._load()["items"]

    def add(self, alias: str, slug: str, pr: int, title: str, sha: str) -> tuple[str, dict]:
        """Anota (o refresca) un PR. Si el commit cambió, el OK anterior deja de valer."""
        data, key = self._load(), f"{alias}#{pr}"
        old = data["items"].get(key) or {}
        mismo = old.get("sha") == sha  # con otro commit el OK anterior se descarta
        keep = {k: old[k] for k in ("queued_at", "ok_sha", "ok_at") if old.get(k) and (mismo or k == "queued_at")}
        data["items"][key] = {"repo": alias, "slug": slug, "pr": pr, "title": title, "sha": sha,
                              "queued_at": self._now(), **keep}
        self._save(data)
        return key, data["items"][key]

    def dar_ok(self, key: str, sha_actual: str) -> bool:
        """El OK de Oscar vale para `sha_actual`. False si ya no está en la cola o el PR cambió desde que se mostró."""
        data = self._load()
        it = data["items"].get(key)
        if not it or not sha_actual or it.get("sha") != sha_actual:
            return False
        it["ok_sha"], it["ok_at"] = sha_actual, self._now()
        self._save(data)
        return True

    def con_ok(self, key: str, sha_actual: str) -> bool:
        """¿Hay OK de Oscar sobre este commit exacto? Lo consulta quien fusiona."""
        it = self.items().get(key) or {}
        return bool(sha_actual) and it.get("ok_sha") == sha_actual

    def quitar(self, key: str) -> bool:
        data = self._load()
        if data["items"].pop(key, None) is None:
            return False
        self._save(data)
        return True


def vista_externos(items: dict[str, dict], now: float) -> str:
    """Líneas de /integrador y del parte de las 9:00 para los PR de sesiones interactivas."""
    if not items:
        return ""
    lines = [f"🔀 PR de sesiones interactivas ({len(items)})"]
    for it in sorted(items.values(), key=lambda x: x.get("queued_at") or 0):
        ok = "con tu OK" if it.get("ok_sha") == it.get("sha") else "espera tu OK"
        horas = int(max(0, now - float(it.get("queued_at") or now)) // 3600)
        lines.append(f"• {it['repo']} #{it['pr']} · {truncate(it.get('title'), 50)} · {it['sha'][:9]} · {ok} · hace {horas} h")
    return "\n".join(lines)


# --- 3. incidencia de Railway ----------------------------------------------------------------------------------

class IncidenciaRailway:
    """Una incidencia abierta por repo: avisa al abrirse y al cerrarse, nunca dos veces seguidas lo mismo."""

    def __init__(self, path: Path | str, aj: Ajustes | None = None, now=time.time):
        self.path, self.aj, self._now = Path(path), aj or Ajustes(), now

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def abrir(self, repo: str, sha: str) -> str | None:
        """Texto a enviar la primera vez que un repo tiene el despliegue en cola; None si ya estaba avisado."""
        data = self._load()
        if repo in data:
            return None
        data[repo] = {"sha": sha, "desde": self._now()}
        self._save(data)
        return texto(self.aj, "incidencia_abre", repo=repo, sha=sha[:9])

    def cerrar(self, repo: str) -> str | None:
        """Texto a enviar cuando Railway responde de nuevo y había incidencia abierta; None si no la había."""
        data = self._load()
        abierta = data.pop(repo, None)
        if abierta is None:
            return None
        self._save(data)
        return texto(self.aj, "incidencia_cierra", repo=repo, sha=str(abierta.get("sha", ""))[:9])
