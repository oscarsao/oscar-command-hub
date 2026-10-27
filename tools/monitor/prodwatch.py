"""Avisos de producción del monitor (22-10, «apagar ruido + avisos importantes por Telegram»).

Dos vigilancias que el monitor headless evalúa cada 5 min:
  - Despliegues de PRODUCCIÓN fallidos en Vercel (API de Vercel, solo `target=production`: las vistas previas de las
    ramas lane/* no avisan). Cada despliegue avisa UNA vez (los ya avisados se guardan en .state/prodwatch.json) y los
    anteriores a FRESH_S se ignoran, para que el primer arranque no inunde el DM.
  - Salud de los servicios del servidor: `health_url` de cada carril (integrator.lanes de lanes.yaml) y el estado de su
    aplicación en Dokploy (application.one → applicationStatus). El aviso/«resuelto» lo gestiona Monitor.condition.

Configuración (lanes.yaml, datos, no código):
    monitor_alerts:
      vercel: {token_env: VERCEL_TOKEN, team_id: "", projects: [recursos, website]}
      skip_health: [claude-migrateam]     # carriles cuyo health_url no se vigila (p. ej. Railway pausado)
Los tokens se leen por NOMBRE de variable (entorno o agent-lanes/.env); nunca van en lanes.yaml ni se imprimen.
Solo LEE: GET a las APIs.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VERCEL_API = "https://api.vercel.com"
FRESH_S = 6 * 3600     # un fallo más viejo que esto no avisa (primer arranque, o fallo antiguo ya conocido)
SEEN_MAX = 200


def http_get(url: str, headers: dict, timeout: float = 15) -> tuple[int, object]:
    """(código, JSON o None); 0 si no hay red. Sin redirecciones a otros hosts."""
    req = urllib.request.Request(url, headers={"User-Agent": "oscar-monitor", "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, code = r.read(2_000_000).decode("utf-8", "replace"), r.status
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None
    try:
        return code, json.loads(raw) if raw.strip() else None
    except ValueError:
        return code, None


def read_env(name: str, files=()) -> str:
    """Valor de la variable `name`: entorno del proceso y, si no, los .env dados. Vacío si no existe."""
    if not name:
        return ""
    if os.environ.get(name):
        return os.environ[name]
    for f in files:
        try:
            for line in Path(f).read_text(encoding="utf-8").splitlines():
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip().strip("\"'")
        except OSError:
            continue
    return ""


class ProdWatch:
    def __init__(self, vercel: dict | None, policies: dict, *, env_files=(), http=http_get,
                 state_path: Path | None = None, now=time.time, skip_health=(), skip_urls=()):
        self.vercel, self.policies = vercel or {}, policies or {}
        # skip_health: carriles sin vigilar (p. ej. Railway pausado); skip_urls: ya cubiertas por el panel de servicios.
        self.skip_health, self.skip_urls = set(skip_health), set(skip_urls)
        self.env_files, self.http, self.now = env_files, http, now
        self.state_path = state_path

    # --- Vercel -----------------------------------------------------------------------------------------
    def _seen(self) -> list[str]:
        try:
            return list(json.loads(self.state_path.read_text(encoding="utf-8")).get("seen", []))
        except (OSError, ValueError, AttributeError):
            return []

    def _save_seen(self, seen: list[str]) -> None:
        if self.state_path:
            try:
                self.state_path.write_text(json.dumps({"seen": seen[-SEEN_MAX:]}), encoding="utf-8")
            except OSError:
                pass

    def vercel_failures(self) -> list[dict]:
        """Despliegues de PRODUCCIÓN en error, recientes y aún no avisados. Los marca como avisados."""
        projects = self.vercel.get("projects") or []
        token = read_env(self.vercel.get("token_env") or "", self.env_files)
        if not projects or not token:
            return []
        seen, new = self._seen(), []
        for project in projects:
            q = {"projectId": project, "target": "production", "state": "ERROR", "limit": 5}
            if self.vercel.get("team_id"):
                q["teamId"] = self.vercel["team_id"]
            code, data = self.http(f"{VERCEL_API}/v6/deployments?" + urllib.parse.urlencode(q),
                                   {"Authorization": f"Bearer {token}"})
            if code != 200 or not isinstance(data, dict):
                continue
            for d in data.get("deployments") or []:
                uid = str(d.get("uid") or d.get("id") or "")
                state = str(d.get("state") or d.get("readyState") or "").upper()
                if not uid or uid in seen or state != "ERROR" or d.get("target") != "production":
                    continue  # una vista previa (target null) nunca avisa
                if self.now() - float(d.get("created") or 0) / 1000 > FRESH_S:
                    continue
                meta = d.get("meta") or {}
                new.append({"id": uid, "project": d.get("name") or project,
                            "sha": str(meta.get("githubCommitSha") or "")[:7] or "?",
                            "message": str(meta.get("githubCommitMessage") or "").splitlines()[0][:80]
                            if meta.get("githubCommitMessage") else "", "url": d.get("url") or ""})
                seen.append(uid)
        if new:
            self._save_seen(seen)
        return new

    def estado_vigilancias(self) -> list[str]:
        """Una entrada por vigilancia con su estado (F1-02, DISP-06/CF-05): solo NOMBRES de lo que falta, nunca valores.
        La registra el monitor al arrancar para que una vigilancia apagada no pase desapercibida."""
        out = []
        token_env = self.vercel.get("token_env") or ""
        if not self.vercel:
            out.append("Vercel: sin configurar (monitor_alerts.vercel)")
        elif not self.vercel.get("projects") or not read_env(token_env, self.env_files):
            out.append(f"Vercel: desactivado (falta {token_env or 'monitor_alerts.vercel.token_env'})")
        else:
            out.append(f"Vercel: activo ({len(self.vercel['projects'])} proyectos)")
        n_health = sum(1 for lane, p in self.policies.items() if p.get("health_url") and lane not in self.skip_health
                       and p.get("health_url") not in self.skip_urls)
        out.append(f"health_url: {n_health} activos")
        for lane, p in self.policies.items():
            if not p.get("dokploy_url"):
                continue
            faltan = [k for k in ("dokploy_application_id",) if not p.get(k)]
            token_name = p.get("dokploy_token_env") or ""
            if not token_name or not read_env(token_name, self.env_files):
                faltan.append(token_name or "dokploy_token_env")
            name = lane.removeprefix("claude-")
            out.append(f"Dokploy {name}: " + (f"desactivado (falta {', '.join(faltan)})" if faltan else "activo"))
        return out

    # --- servidor (health_url + Dokploy) ----------------------------------------------------------------
    def server_checks(self) -> list[tuple[str, bool, str, str]]:
        """[(clave, activa, texto, qué hacer)] para Monitor.condition. Las claves empiezan por `health-`."""
        out = []
        for lane, p in self.policies.items():
            name = lane.removeprefix("claude-")
            url = p.get("health_url")
            if url and lane not in self.skip_health and url not in self.skip_urls:
                code, _ = self.http(url, {})
                bad = not (200 <= code < 400 or code == 429)  # 429 = vivo pero limitando: no es una caída
                out.append((f"health-srv-{name}", bad, f"{name}: {url} no responde bien ({code or 'sin respuesta'}).",
                            "entra al servidor pildora-01 → Dokploy → la aplicación → Logs / Deployments; si el último "
                            "deploy falló, pulsa Redeploy del anterior."))
            base, app_id = str(p.get("dokploy_url") or "").rstrip("/"), p.get("dokploy_application_id")
            token = read_env(p.get("dokploy_token_env") or "", self.env_files)
            if base and app_id and token:
                code, data = self.http(f"{base}/api/application.one?" + urllib.parse.urlencode({"applicationId": app_id}),
                                       {"x-api-key": token})
                if code == 200 and isinstance(data, dict):
                    status = str(data.get("applicationStatus") or "").lower()
                    out.append((f"health-dokploy-{name}", status == "error",
                                f"Dokploy: la aplicación de {name} está en estado «error».",
                                "abre el panel de Dokploy → la aplicación → Deployments → Logs, y Redeploy si procede."))
                # Sin respuesta del panel (túnel SSH cerrado) no se avisa: el health_url ya cubre la caída real.
        return out


def from_config(lanes_yaml: Path, env_files=(), state_path: Path | None = None, skip_urls=()) -> ProdWatch | None:
    """ProdWatch desde lanes.yaml (`monitor_alerts` + `integrator.lanes`); None si no hay nada que vigilar."""
    try:
        import yaml
        data = yaml.safe_load(Path(lanes_yaml).read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    cfg = data.get("monitor_alerts") or {}
    vercel = cfg.get("vercel")
    policies = {n: p for n, p in ((data.get("integrator") or {}).get("lanes") or {}).items() if isinstance(p, dict)}
    if not vercel and not any(p.get("health_url") or p.get("dokploy_url") for p in policies.values()):
        return None
    return ProdWatch(vercel, policies, env_files=env_files, state_path=state_path,
                     skip_health=cfg.get("skip_health") or (), skip_urls=skip_urls)
