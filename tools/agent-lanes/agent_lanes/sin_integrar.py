"""Freno por aprobadas sin integrar (t_e4ecacf5): si en un repo se acumulan demasiadas tarjetas `done` cuya rama
`lane/<id>` no está en la base y no tiene PR, los carriles de ese repo dejan de reclamar tarjetas nuevas.

Medido el 21-11-2026: «done» significa «pasó la revisión», no «integrada», y nada frenaba a los carriles cuando la cola
de sin integrar crecía (363 ramas, 272 con >150 commits de retraso, 11 de 32 ya chocaban con lo posterior).

Criterio de «sin integrar» (solo lectura de git y gh), por REPO y no por tablero: tarjeta `done` con rama
`<remote>/lane/<id>` que no es ancestro de `<remote>/<base>`, sin PR abierto ni fusionado y sin comentario que empiece
por `INTEGRADO <sha>`.

- Recuento con caché de 30 min (`cache_minutos`). Si git/gh/Hermes fallan o se pasa `plazo_segundos`, el estado es
  «desconocido» y NO frena (falla abierto).
- Ajustes en `sin_integrar.yaml` (se relee en cada pasada, sin reiniciar): `tope_sin_integrar` (25; 0 = apagado),
  `aviso_en` (20; 0 = sin aviso previo), `cache_minutos`, `plazo_segundos`, `informe`.
- Con el recuento >= tope el carril no reclama; las tarjetas ya en marcha terminan. La tarjeta que se queda esperando
  recibe UN comentario (`SIN-INTEGRAR-FRENO`) con el recuento.
- Avisos por el canal de avisos (tipo `sin_integrar`): una vez al cruzar `aviso_en` y otra al alcanzar el tope; no se
  repite mientras el nivel no cambie. Al bajar se rearma (el siguiente cruce vuelve a avisar).
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import ROOT

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "sin_integrar.yaml"
STATE_PATH = ROOT / ".state" / "sin_integrar.json"
MARCA = "SIN-INTEGRAR-FRENO"
AUTHOR = "agent-lanes"
INFORME = "C:/Users/oscar/integrador-docs/hecha_no_integrada/ultimo.markdown"
NEGATIVO_TTL = 6 * 3600.0  # s: un «sin INTEGRADO» ya leído no se vuelve a leer hasta pasado este tiempo

OK, AVISO, TOPE = 0, 1, 2
_NIVELES = {OK: "ok", AVISO: "aviso", TOPE: "tope"}


@dataclass(frozen=True)
class Ajustes:
    tope: int = 25
    aviso_en: int = 20
    cache_minutos: float = 30.0
    plazo_segundos: float = 90.0
    informe: str = INFORME


def _ent(v) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def load_ajustes(path: Path | str | None = None) -> Ajustes:
    """Ajustes desde el YAML; lo que falte o no sea válido queda en su valor por defecto."""
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return Ajustes()
    if not isinstance(raw, dict):
        return Ajustes()
    d = Ajustes()
    tope = _ent(raw.get("tope_sin_integrar"))
    aviso = _ent(raw.get("aviso_en"))
    tope = d.tope if tope is None else tope
    aviso = d.aviso_en if aviso is None else aviso
    if tope and aviso > tope:
        aviso = tope
    out = {"tope": tope, "aviso_en": aviso}
    for key, campo in (("cache_minutos", "cache_minutos"), ("plazo_segundos", "plazo_segundos")):
        v = raw.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            out[campo] = float(v)
    if isinstance(raw.get("informe"), str) and raw["informe"].strip():
        out["informe"] = raw["informe"].strip()
    return Ajustes(**out)


class Lector:
    """Lectura de git y gh (nunca escribe). Cualquier fallo devuelve None = «desconocido»."""

    def __init__(self, runner=subprocess.run, timeout: float = 60.0):
        self._run, self._timeout = runner, timeout

    def _out(self, args: list[str], cwd: str) -> str | None:
        try:
            cp = self._run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=self._timeout, cwd=cwd)
        except Exception as exc:
            log.info("sin_integrar: %s falló: %s", " ".join(args[:3]), exc)
            return None
        return (cp.stdout or "") if cp.returncode == 0 else None

    def ramas_sin_fusionar(self, repo: str, remote: str, base: str) -> set[str] | None:
        """Ramas `lane/*` de `<remote>` que NO son ancestro de `<remote>/<base>` (sin el prefijo del remote)."""
        out = self._out(["git", "-C", repo, "branch", "-r", "--no-merged", f"{remote}/{base}",
                         "--format=%(refname:short)"], repo)
        if out is None:
            return None
        pref = f"{remote}/"
        return {x.strip()[len(pref):] for x in out.splitlines() if x.strip().startswith(pref + "lane/")}

    def ramas_con_pr(self, repo: str) -> set[str] | None:
        """Ramas con PR abierto o fusionado (los cerrados sin fusionar no cuentan como integrados)."""
        out = self._out(["gh", "pr", "list", "--state", "all", "--json", "headRefName,state", "--limit", "1000"], repo)
        if out is None:
            return None
        try:
            data = json.loads(out)
        except ValueError:
            return None
        if not isinstance(data, list):
            return None
        return {str(p["headRefName"]) for p in data
                if isinstance(p, dict) and p.get("headRefName") and p.get("state") in ("OPEN", "MERGED")}


def _tarjetas_done(lanes, hermes_for, repo: str) -> list[str] | None:
    """Ids de las tarjetas `done` de los carriles de código de ese repo (por repo, no por tablero)."""
    ids: list[str] = []
    for lane in lanes.values():
        if getattr(lane, "kind", "implement") != "implement" or getattr(lane, "repo", "") != repo:
            continue
        for t in hermes_for(lane.board).list_status(lane.name, "done"):
            if t.get("id") and t["id"] not in ids:
                ids.append(t["id"])
    return ids


class SinIntegrar:
    def __init__(self, lanes: dict, hermes_for, *, lector: Lector | None = None, settings_path: Path | str | None = None,
                 state_path: Path | str | None = None, send=None, clock=time.monotonic, wall=time.time):
        self.lanes, self.hermes_for = lanes, hermes_for
        self.lector = lector or Lector()
        self.settings_path = settings_path
        self.state_path = Path(state_path or STATE_PATH)
        self.send = send  # send(text, markup=None) del canal de avisos; None = no avisa (se puede fijar después)
        self._clock, self._wall = clock, wall
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[float, int | None]] = {}   # repo -> (cuándo, recuento o None)
        self._vistas: dict[str, tuple[float, bool]] = {}         # tid -> (cuándo, tiene INTEGRADO)
        self._comentadas: set[str] = set()

    def ajustes(self) -> Ajustes:
        return load_ajustes(self.settings_path)

    # --- recuento ------------------------------------------------------------------------------------------------

    def _integrado(self, hermes, tid: str) -> bool:
        """¿La tarjeta lleva un comentario `INTEGRADO <sha>`? Lanza si Hermes falla."""
        hit = self._vistas.get(tid)
        ahora = self._clock()
        if hit and (hit[1] or ahora - hit[0] < NEGATIVO_TTL):
            return hit[1]
        show = hermes.show(tid) or {}
        val = any(str(c.get("body") or "").lstrip().startswith("INTEGRADO ")
                  for c in show.get("comments") or [] if isinstance(c, dict))
        self._vistas[tid] = (ahora, val)
        return val

    def _calcular(self, repo: str, aj: Ajustes) -> int | None:
        lane = next((l for l in self.lanes.values()
                     if getattr(l, "kind", "implement") == "implement" and getattr(l, "repo", "") == repo), None)
        if lane is None:
            return None
        limite = self._clock() + aj.plazo_segundos
        try:
            done = _tarjetas_done(self.lanes, self.hermes_for, repo)
            sin_fusionar = self.lector.ramas_sin_fusionar(repo, lane.remote or "origin", lane.base)
            con_pr = self.lector.ramas_con_pr(repo)
            if done is None or sin_fusionar is None or con_pr is None:
                return None
            cand = [t for t in done if f"lane/{t}" in sin_fusionar and f"lane/{t}" not in con_pr]
            hermes = self.hermes_for(lane.board)
            n = 0
            for tid in cand:
                if self._clock() > limite:
                    return None  # tarda demasiado: desconocido (lo leído queda en memoria para la próxima vuelta)
                if not self._integrado(hermes, tid):
                    n += 1
            return n
        except Exception as exc:  # falla abierto: nunca frena por un error propio
            log.info("sin_integrar: recuento de %s falló: %s", repo, exc)
            return None

    def recuento(self, repo: str) -> int | None:
        """Sin integrar del repo (caché `cache_minutos`); None = desconocido."""
        aj = self.ajustes()
        with self._lock:
            hit = self._cache.get(repo)
            if hit and self._clock() - hit[0] < aj.cache_minutos * 60:
                return hit[1]
            n = self._calcular(repo, aj)
            self._cache[repo] = (self._clock(), n)
            return n

    def recuentos(self) -> dict[str, int | None]:
        """Recuento de cada repo con carriles de código (para el resumen)."""
        repos = sorted({getattr(l, "repo", "") for l in self.lanes.values()
                        if getattr(l, "kind", "implement") == "implement" and getattr(l, "repo", "")})
        if not self.ajustes().tope and not self.ajustes().aviso_en:
            return {}
        return {r: self.recuento(r) for r in repos}

    # --- decisión y avisos ---------------------------------------------------------------------------------------

    @staticmethod
    def nivel(n: int | None, aj: Ajustes) -> int:
        if n is None or aj.tope == 0:
            return OK
        if n >= aj.tope:
            return TOPE
        if aj.aviso_en and n >= aj.aviso_en:
            return AVISO
        return OK

    def _estado(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _guardar(self, data: dict) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.info("sin_integrar: estado no guardado: %s", exc)

    def _avisar(self, repo: str, niv: int, n: int, aj: Ajustes) -> None:
        """Avisa solo cuando el nivel SUBE respecto al último avisado; al bajar se rearma sin avisar."""
        with self._lock:
            data = self._estado()
            previo = data.get(repo, OK)
            previo = previo if isinstance(previo, int) else OK
            if niv == previo:
                return
            data[repo] = niv
            self._guardar(data)
            if niv < previo or not self.send:
                return
            nombre = Path(repo).name or repo
            detalle = ", ".join(f"{Path(r).name or r}: {c}" for r, c in sorted(self._cache_vista().items()))
            if niv == TOPE:
                cab = (f"🛑 Sin integrar: {nombre} llega al tope ({n} de {aj.tope}). Sus carriles dejan de reclamar "
                       f"tarjetas nuevas hasta que baje; las que están en marcha terminan.")
            else:
                cab = f"⚠️ Sin integrar: {nombre} llega a {n} (aviso en {aj.aviso_en}, tope {aj.tope})."
            texto = f"{cab}\nRecuento por repo: {detalle}\nInforme: {aj.informe}"
            try:
                self.send(texto)
            except Exception as exc:
                log.info("sin_integrar: aviso no enviado: %s", exc)

    def _cache_vista(self) -> dict[str, int]:
        return {r: n for r, (_, n) in self._cache.items() if n is not None}

    def frenado(self, lane) -> tuple[bool, int | None]:
        """(¿este carril NO debe reclamar?, recuento de su repo). Falla abierto. También lanza los avisos de cruce."""
        try:
            aj = self.ajustes()
            repo = getattr(lane, "repo", "")
            if aj.tope == 0 or not repo or getattr(lane, "kind", "implement") != "implement":
                return False, None
            n = self.recuento(repo)
            niv = self.nivel(n, aj)
            if n is not None:
                self._avisar(repo, niv, n, aj)
            return niv == TOPE, n
        except Exception as exc:
            log.info("sin_integrar: freno no evaluado: %s", exc)
            return False, None

    def comentar_espera(self, hermes, tid: str, repo: str, n: int) -> None:
        """Un solo comentario por tarjeta que se queda esperando; sobrevive a reinicios mirando los comentarios."""
        if tid in self._comentadas:
            return
        self._comentadas.add(tid)
        aj = self.ajustes()
        try:
            show = hermes.show(tid) or {}
            if any(str(c.get("body") or "").startswith(MARCA) for c in show.get("comments") or []):
                return
            hermes.comment(tid, f"{MARCA}: el repo {Path(repo).name or repo} tiene {n} tarjetas aprobadas sin integrar "
                                f"(tope {aj.tope}). Esta tarjeta espera en ready; se reclamará sola cuando baje el "
                                f"recuento. Informe: {aj.informe}", author=AUTHOR)
        except Exception as exc:
            log.info("sin_integrar: no se pudo comentar %s: %s", tid, exc)

    def linea(self) -> str:
        """Línea para el resumen de estado de carriles; vacía si está apagado o sin datos."""
        try:
            aj = self.ajustes()
            if not aj.tope and not aj.aviso_en:
                return ""
            partes = []
            for repo, n in self.recuentos().items():
                nombre = Path(repo).name or repo
                if n is None:
                    partes.append(f"{nombre} desconocido")
                    continue
                marca = {TOPE: " 🛑", AVISO: " ⚠️"}.get(self.nivel(n, aj), "")
                partes.append(f"{nombre} {n}/{aj.tope}{marca}")
            return ("📦 Aprobadas sin integrar: " + " · ".join(partes)) if partes else ""
        except Exception as exc:
            log.info("sin_integrar: línea de resumen no calculada: %s", exc)
            return ""


_actual: SinIntegrar | None = None


def instalar(instancia: SinIntegrar | None) -> None:
    global _actual
    _actual = instancia


def linea_resumen() -> str:
    return _actual.linea() if _actual else ""
