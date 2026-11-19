"""[VISTA PREVIA] Al cerrar una tarjeta de interfaz, enlace a la vista previa y a staging para Oscar (t_808a4945).

Decisión de Oscar (19-11-2026): cuando se desarrollan interfaces nuevas, que se le abra la web para verla directamente
en staging, con la URL. La regla de la casa pide «cambio -> URL exacta» en el cierre, pero no se comprobaba.

Cada `minutes` el bucle del runner mira las tarjetas en review/done de los carriles con repo en GitHub y, de las que son
de interfaz, hace tres cosas:

  1. Detecta una tarjeta de interfaz: el cuerpo tiene la línea «Tipo: UI», o el PR toca `dashboard/src/app` o
     `dashboard/src/components`.
  2. Busca con `gh` la vista previa de la rama del PR (último despliegue `success` con `environment_url`) y saca la ruta de
     cada pantalla cambiada de `dashboard/src/app/**/page.tsx`. Si el PR apunta a la rama de staging, añade el enlace a
     staging con esa ruta.
  3. Comenta la tarjeta y manda el aviso al canal de Oscar con botón «Abrir». Si aún no hay vista previa lista, reintenta
     cada pasada hasta `espera_min` minutos y, pasado ese tiempo, avisa UNA vez de que falta. NUNCA manda un enlace vacío.

Bloque `vista_previa:` de lanes.yaml. `enabled: false` (por defecto) = MODO SECO: lee de GitHub (solo lectura) pero no
comenta ni avisa; deja lo que haría en `.state/vista_previa.json` (clave `seco`) y en el log.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from . import proc as _proc
from .config import ROOT

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "vista_previa.json"
ESTADOS = ("review", "done")
APP = "dashboard/src/app/"
RUTAS_UI = (APP, "dashboard/src/components/")
TIPO_UI = re.compile(r"^\s*tipo\s*:\s*ui\b", re.IGNORECASE | re.MULTILINE)
MAX_BOTONES = 4
MAX_REGISTRO = 50


@dataclass(frozen=True)
class Ajustes:
    activo: bool = False                 # False = modo seco
    espera_min: float = 15               # minutos que se reintenta la vista previa antes de avisar de que falta
    staging_url: str = "https://stage.pildoradigital.com"
    staging_rama: str = "staging"        # el enlace a staging solo se da si el PR apunta a esta rama


def load(path: Path | str | None = None) -> Ajustes:
    """Bloque `vista_previa:` de lanes.yaml; lo que falte o no sea válido queda en su valor por defecto (nunca lanza)."""
    d = Ajustes()
    try:
        raw = (yaml.safe_load(Path(path or ROOT / "lanes.yaml").read_text(encoding="utf-8")) or {}).get("vista_previa")
    except (OSError, yaml.YAMLError, AttributeError):
        return d
    if not isinstance(raw, dict):
        return d
    espera = raw.get("espera_min")
    return Ajustes(activo=raw["enabled"] if isinstance(raw.get("enabled"), bool) else d.activo,
                   espera_min=float(espera) if isinstance(espera, (int, float)) and not isinstance(espera, bool)
                   and espera >= 0 else d.espera_min,
                   staging_url=str(raw["staging_url"]).strip() if isinstance(raw.get("staging_url"), str) else d.staging_url,
                   staging_rama=str(raw["staging_rama"]).strip() if isinstance(raw.get("staging_rama"), str)
                   and raw["staging_rama"].strip() else d.staging_rama)


def es_tarjeta_ui(body: str | None, files: list[str]) -> bool:
    """«Tipo: UI» en el cuerpo, o el PR toca rutas de pantallas del panel."""
    return bool(TIPO_UI.search(body or "")) or any(f.startswith(RUTAS_UI) for f in files)


def ruta_de(path: str) -> str | None:
    """`dashboard/src/app/(x)/ajustes/[id]/page.tsx` -> `/ajustes/[id]`; None si no es una pantalla."""
    if not path.startswith(APP) or path.rsplit("/", 1)[-1] != "page.tsx":
        return None
    segs = [s for s in path[len(APP):].split("/")[:-1]
            if s and not (s.startswith("(") and s.endswith(")")) and not s.startswith("@")]
    return "/" + "/".join(segs)


def pantallas(files: list[str]) -> list[str]:
    return sorted({r for f in files if (r := ruta_de(f))})


def _url(base: str, ruta: str) -> str:
    return base.rstrip("/") + ("" if ruta == "/" else ruta)


def _https(valor) -> str | None:
    v = str(valor or "").strip()
    return v if v.startswith("https://") and len(v) > len("https://") else None


class VistaPrevia:
    def __init__(self, lanes: dict, hermes_for: Callable[[str], object], enviar: Callable[..., object], *,
                 gh: Callable[..., object] | None = None, ajustes: Callable[[], Ajustes] = load,
                 state_path: Path | str = STATE_PATH, minutes: float = 1, clock: Callable[[], float] = time.time):
        """`enviar(texto, markup)` manda el aviso al canal de Oscar; `gh(*args)` devuelve un CompletedProcess."""
        self.lanes, self.hermes_for, self.enviar = lanes, hermes_for, enviar
        self._gh = gh or self._gh_real
        self.ajustes, self.state_path, self.minutes, self.clock = ajustes, Path(state_path), minutes, clock
        self._last = 0.0

    # --- estado ---------------------------------------------------------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _store(self, data: dict) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            os.replace(tmp, self.state_path)
        except OSError as exc:
            log.info("vista_previa: estado no guardado: %s", exc)

    # --- GitHub (solo lectura) --------------------------------------------------------------------------

    @staticmethod
    def _gh_real(*args: str):
        return _proc.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)

    def _json(self, *args: str):
        try:
            cp = self._gh(*args)
        except Exception as exc:
            log.info("vista_previa: gh %s falló: %s", " ".join(args[:2]), exc)
            return None
        if getattr(cp, "returncode", 1) != 0:
            return None
        try:
            return json.loads(cp.stdout or "null")
        except ValueError:
            return None

    def _pr(self, slug: str, tid: str) -> dict | None:
        pr = self._json("pr", "view", f"lane/{tid}", "--repo", slug, "--json", "number,url,state,baseRefName,files")
        return pr if isinstance(pr, dict) and pr.get("number") else None

    def _preview(self, slug: str, tid: str) -> str | None:
        """URL de la vista previa de la rama: último despliegue de la rama con estado `success` y `environment_url`."""
        deps = self._json("api", f"repos/{slug}/deployments?ref=lane/{tid}&per_page=5")
        for dep in deps if isinstance(deps, list) else []:
            sts = self._json("api", f"repos/{slug}/deployments/{dep.get('id')}/statuses?per_page=5")
            for st in sts if isinstance(sts, list) else []:
                if st.get("state") == "success" and (url := _https(st.get("environment_url"))):
                    return url
        return None

    # --- textos ---------------------------------------------------------------------------------------

    @staticmethod
    def render(tid: str, preview: str, rutas: list[str], staging: str | None) -> tuple[str, list[dict]]:
        """(texto, enlaces [{text, url}]). Cada enlace lleva una URL no vacía."""
        lines = [f"🖥 Vista previa de la rama lane/{tid}: {preview}"]
        enlaces = []
        if rutas:
            lines.append("Pantallas cambiadas:")
            for r in rutas:
                lines.append(f"• {r} → {_url(preview, r)}")
                enlaces.append({"text": "Abrir" if not enlaces else f"Abrir {r}", "url": _url(preview, r)})
        else:
            lines.append("Cambia componentes del panel, sin pantalla propia: abre la vista previa y busca el cambio.")
            enlaces.append({"text": "Abrir", "url": preview})
        if staging:
            lines.append("En staging:" if not rutas else "En staging (misma ruta):")
            lines += [f"• {_url(staging, r)}" for r in rutas] if rutas else [f"• {staging}"]
        return "\n".join(lines), enlaces[:MAX_BOTONES]

    # --- bucle ----------------------------------------------------------------------------------------

    def tick(self) -> int:
        """Una pasada, como mucho cada `minutes`. Devuelve cuántas tarjetas se resolvieron. Nunca lanza."""
        now = self.clock()
        if now - self._last < self.minutes * 60:
            return 0
        self._last = now
        try:
            return self._pasada(self.ajustes(), now)
        except Exception as exc:
            log.warning("vista_previa falló: %s", exc)
            return 0

    def _pasada(self, cfg: Ajustes, now: float) -> int:
        state, hechas = self._load(), 0
        tarjetas = state.setdefault("tarjetas", {})
        registro = state.setdefault("seco", [])
        for lane in self.lanes.values():
            slug = getattr(lane, "github", None)
            if not slug or not getattr(lane, "board", None):
                continue
            h = self.hermes_for(lane.board)
            for estado in ESTADOS:
                try:
                    cards = h.list_status(lane.name, estado)
                except Exception as exc:
                    log.info("vista_previa: no se pudo listar %s/%s: %s", lane.name, estado, exc)
                    continue
                for t in cards:
                    tid = str(t.get("id") or "")
                    rec = tarjetas.get(tid) or {}
                    if not tid or rec.get("fin") or (not cfg.activo and rec.get("seco")):
                        continue
                    if self._tarjeta(cfg, now, h, slug, t, tid, rec, registro):
                        hechas += 1
                    if rec:
                        tarjetas[tid] = rec
        state["seco"] = registro[-MAX_REGISTRO:]
        self._store(state)
        return hechas

    def _tarjeta(self, cfg: Ajustes, now: float, h, slug: str, t: dict, tid: str, rec: dict, registro: list) -> bool:
        pr = self._pr(slug, tid)
        rec.setdefault("desde", now)
        vencido = now - float(rec["desde"]) >= cfg.espera_min * 60
        if not pr:
            if vencido:
                rec["fin"] = "sin_pr"
            return False
        if str(pr.get("state")).upper() != "OPEN":
            rec["fin"] = "pr_cerrado"
            return False
        files = [str(f.get("path") or "") for f in pr.get("files") or [] if isinstance(f, dict)]
        body = t.get("body")
        if body is None:
            try:
                body = (h.show(tid) or {}).get("body")
            except Exception:
                body = None
        if not es_tarjeta_ui(body, files):
            rec["fin"] = "no_ui"
            return False
        preview = self._preview(slug, tid)
        titulo = f"{tid} · {str(t.get('title') or '')[:60]}".rstrip(" ·")
        if not preview:
            if not vencido:
                return False  # se reintenta en la pasada siguiente
            texto = (f"⏳ {titulo}\nEs una tarjeta de interfaz, pero la vista previa de lane/{tid} aún no está lista "
                     f"tras {int(cfg.espera_min)} min. Mira el PR en GitHub cuando Vercel termine.")
            enlaces, comentario = [{"text": "Ver PR", "url": pr.get("url")}], texto
            clave = "falta"
        else:
            rutas = pantallas(files)
            staging = _https(cfg.staging_url) if pr.get("baseRefName") == cfg.staging_rama else None
            comentario, enlaces = self.render(tid, preview, rutas, staging)
            texto, clave = f"🖥 {titulo}\n{comentario}", "enviado"
        enlaces = [e for e in enlaces if _https(e.get("url"))]  # nunca un botón con enlace vacío
        markup = {"inline_keyboard": [[e] for e in enlaces]} if enlaces else None
        if not cfg.activo:
            log.info("vista_previa [seco]: comentaría en %s: %s | avisaría: %s", tid, comentario, texto)
            registro.append({"tid": tid, "comentario": comentario, "aviso": texto, "botones": enlaces, "resultado": clave})
            rec["seco"] = True
            return True
        h.comment(tid, comentario)
        self.enviar(texto, markup)
        rec["fin"] = clave
        return True
