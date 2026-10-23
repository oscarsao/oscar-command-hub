"""Prevención de choques al lanzar (23-10): evitar el choque en origen en vez de solo avisarlo.

Antes de que el planificador lance una tarjeta `ready` de un carril de IMPLEMENTACIÓN (LaneRunner.candidates), se mira
si su zona choca con trabajo que ya está en marcha en el mismo repo:

- tarjetas en `prevenir_estados` (running, review) del mismo repo, con la zona de su rama `lane/<id>` o de su texto;
- ramas `lane/*` sin fusionar (su PR ni fusionado ni cerrado, `Git.ramas_cerradas`), recientes o con PR abierto;
- tarjetas que se acaban de admitir en esta misma pasada (dos `ready` que tocan lo mismo no salen a la vez).

La zona de la tarjeta sale de `solapes.extraer_rutas(título + cuerpo)` (y de su rama si ya existe de un intento
anterior). Choca si comparten fichero (`prevenir_nivel: archivo`; `modulo` también carpeta). Si las dos tienen rama,
`git merge-tree` manda: fusión limpia = no se frena. Una tarjeta `ready` casi nunca tiene rama, así que normalmente se
compara su texto con el diff de la otra: no es «conflicto real» probado, es la mejor señal antes de escribir código.

Si choca, la tarjeta NO se lanza en paralelo: se queda en ready, encadenada detrás de la otra, con un comentario
`CHOQUE-AL-LANZAR <otra>` (una vez) que dice con cuál choca y qué comparte. Se re-evalúa en cada pasada, así que se
libera sola en cuanto la otra termina, se fusiona o se cierra (comentario `CHOQUE-LIBERADA`), y como mucho tras
`prevenir_max_horas`. No se usa el `ESPERA-A` de fusion.py «Encadenar»: `deps.pending_ids` sigue contando como pendiente
a una tarjeta archivada con rama y sin INTEGRADO, y entonces la espera sería para siempre.

Respeta las decisiones de Oscar: pares «Se complementan» (solapes_descartados.json), pares «🙈 Ignorar» y ramas
«🔇 Silenciar» de los avisos de choque. Ajustes en choques.yaml (`prevenir_*`, relectura en caliente). Cualquier fallo
(git, gh, Hermes, YAML) = se lanza como siempre: la prevención nunca deja un carril parado por un error suyo.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import threading
import time
from pathlib import Path

from . import choques, solapes, telegram_lane
from .config import ROOT

log = logging.getLogger("agent_lanes")

MARCA = "CHOQUE-AL-LANZAR"
LIBERADA = "CHOQUE-LIBERADA"
AUTHOR = "agent-lanes"
STATE_PATH = ROOT / ".state" / "choques_prevencion.json"  # {tid: {"con": uid, "desde": epoch, "caducada": bool}}


class _GitCache:
    """Memo de las lecturas de git/gh con caducidad: el planificador pregunta en cada vuelta y gh es lento."""

    def __init__(self, git, clock=time.monotonic):
        self._git, self._clock, self.ttl = git, clock, 120.0
        self._memo: dict = {}

    def _get(self, key, fn):
        hit = self._memo.get(key)
        now = self._clock()
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        val = fn()
        self._memo[key] = (now, val)
        return val

    def ramas_remotas(self, repo, remote, base):
        return self._get(("ramas", repo, remote, base), lambda: self._git.ramas_remotas(repo, remote, base))

    def ficheros(self, repo, remote, base, rama):
        return self._get(("fich", repo, remote, base, rama), lambda: self._git.ficheros(repo, remote, base, rama))

    def prs(self, repo):
        return self._get(("prs", repo), lambda: self._git.prs(repo))

    def ramas_cerradas(self, repo):
        return self._get(("cerradas", repo), lambda: getattr(self._git, "ramas_cerradas", lambda _r: set())(repo))

    def choque_real(self, repo, remote, a, b):
        return self._get(("real", repo, remote) + tuple(sorted((a, b))),
                         lambda: self._git.choque_real(repo, remote, a, b))


def _texto(t: dict) -> str:
    return f"{t.get('title') or ''}\n{t.get('body') or ''}"


class Prevencion:
    def __init__(self, lanes: dict, hermes_for, *, git=None, settings_path: Path | str | None = None,
                 silenciadas_path: Path | None = None, ignorados_path: Path | None = None,
                 descartes_path: Path | None = None, state_path: Path | None = None,
                 wall=time.time, clock=time.monotonic):
        self.lanes, self.hermes_for = lanes, hermes_for
        self.git = _GitCache(git or choques.Git(), clock)
        self.settings_path = settings_path
        self.silenciadas_path, self.ignorados_path = silenciadas_path, ignorados_path
        self.descartes_path = descartes_path
        self.state_path = Path(state_path or STATE_PATH)
        self._wall = wall
        self._lock = threading.Lock()
        self._comentadas: set[tuple[str, str]] = set()  # (tid, marca+uid) ya comentadas en este proceso

    # --- estado persistente (cuánto lleva esperando cada tarjeta) ------------------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.info("prevención: estado no guardado: %s", exc)

    # --- lo que ya está en marcha en un repo ---------------------------------------------------------------------

    def _ocupadas(self, eff, aj: choques.Ajustes, ready_ids: set[str], ahora: float) -> tuple[list, set[str]]:
        """(unidades en marcha del repo, ramas remotas vivas). Tarjetas en `prevenir_estados` y ramas lane/*."""
        key = solapes._norm_repo(eff.repo)
        remote = getattr(eff, "remote", "origin") or "origin"
        tarjetas, vistos = [], set()
        boards = list(dict.fromkeys(getattr(l, "board", "") for l in self.lanes.values()
                                    if getattr(l, "board", "") and getattr(l, "kind", "") == "implement"))
        for board in boards:
            for status in aj.prevenir_estados:
                try:
                    tasks = self.hermes_for(board).list_status_all(status)
                except Exception as exc:
                    log.info("prevención: %s/%s no leído: %s", board, status, exc)
                    continue
                for t in tasks:
                    tid = t.get("id")
                    lane = telegram_lane.lane_de_tarjeta(self.lanes, t) if tid else None
                    if lane is None or solapes._norm_repo(getattr(lane, "repo", "")) != key or tid in vistos:
                        continue
                    vistos.add(tid)
                    tarjetas.append({**t, "_board": board})
        fuentes = ("tarjetas", "ramas_lane", "pr") if aj.prevenir_ramas_lane else ("tarjetas", "pr")
        aj2 = dataclasses.replace(aj, nivel=aj.prevenir_nivel, fuentes=fuentes, max_unidades=100_000)
        silenciadas = frozenset(choques.load_silenciadas(self.silenciadas_path))
        ramas = {r: ts for r, ts, _ in self.git.ramas_remotas(eff.repo, remote, eff.base)}
        prs = {p.get("headRefName") for p in self.git.prs(eff.repo)}
        us = choques.unidades_repo(eff.repo, remote, eff.base, tarjetas, self.git, aj2, ahora, silenciadas)
        out = []
        for u in us:
            if u.origen == "persona":  # las ramas de personas solo avisan; frenar es para trabajo de carriles
                continue
            if u.origen == "lane":
                if u.rama[len("lane/"):] in ready_ids:  # rama de un intento anterior de otra ready: la ordena la pasada
                    continue
                viejo = ahora - (ramas.get(u.rama) or 0) > aj.ramas_personas_dias * 86400
                if viejo and u.rama not in prs:  # rama abandonada sin PR: no frena a nadie
                    continue
            out.append(u)
        return out, set(ramas) - silenciadas

    # --- decisión por tarjeta ------------------------------------------------------------------------------------

    def _choca(self, t: dict, eff, aj, ocupadas: list, vivas: set[str], admitidas: list, ahora: float):
        """(unidad con la que choca, qué comparten) o None. También devuelve la Unidad de la propia tarjeta."""
        tid = str(t["id"])
        sol = solapes.Ajustes(nivel=aj.prevenir_nivel, modulo_min_profundidad=aj.modulo_min_profundidad)
        propia = choques.Unidad(tid, choques._etiq(tid, t.get("title") or ""),
                                rutas=solapes.extraer_rutas(_texto(t), sol, eff.repo), texto=_texto(t))
        remote = getattr(eff, "remote", "origin") or "origin"
        if f"lane/{tid}" in vivas:  # intento anterior: su diff también es su zona
            diff = self.git.ficheros(eff.repo, remote, eff.base, f"lane/{tid}")
            if diff:
                propia.rama, propia.rutas = f"lane/{tid}", propia.rutas | diff
        if not propia.rutas:
            return None, propia
        descartes = solapes.load_descartes(self.descartes_path)
        ignorados = choques.load_ignorados(self.ignorados_path, ahora=ahora, dias=aj.recordar_dias)
        for u in ocupadas + admitidas:
            if u.uid == tid or u.rama == f"lane/{tid}":
                continue
            comun = solapes.compartido(propia.rutas, u.rutas, sol)
            if not comun:
                continue
            par = solapes.par(tid, u.uid)
            if par in descartes or par in ignorados:
                continue
            if propia.rama and u.rama and self.git.choque_real(eff.repo, remote, propia.rama, u.rama) is False:
                continue
            return (u, comun), propia
        return None, propia

    def _comentar(self, hermes, tid: str, marca: str, texto: str) -> None:
        """Una vez por (tarjeta, marca): sobrevive a reinicios mirando los comentarios que ya tiene."""
        if (tid, marca) in self._comentadas:
            return
        self._comentadas.add((tid, marca))
        try:
            show = hermes.show(tid) or {}
            if any((c.get("body") or "").startswith(marca) for c in show.get("comments") or []):
                return
            hermes.comment(tid, texto, author=AUTHOR)
        except Exception as exc:
            log.info("prevención: no se pudo comentar %s: %s", tid, exc)

    def filtrar(self, lane, ready: list[dict], hermes) -> list[dict]:
        """Las `ready` que pueden lanzarse ya (en el mismo orden). Las que chocan se quedan en ready, comentadas."""
        if getattr(lane, "kind", "") != "implement" or not ready:
            return ready
        aj = choques.load_ajustes(self.settings_path)
        if not aj.prevenir_al_lanzar:
            return ready
        with self._lock:
            self.git.ttl = aj.prevenir_cache_minutos * 60
            ahora = self._wall()
            estado = self._load()
            cambiado = False
            ready_ids = {str(t.get("id")) for t in ready}
            cache: dict[str, tuple] = {}
            admitidas: dict[str, list] = {}
            out = []
            for t in ready:
                tid = str(t.get("id") or "")
                try:
                    eff = lane
                    if getattr(lane, "repos", None):
                        eff, err = telegram_lane.para_tarjeta(lane, t)
                        if err:
                            out.append(t)
                            continue
                    if not getattr(eff, "repo", "") or not getattr(eff, "base", ""):
                        out.append(t)
                        continue
                    key = solapes._norm_repo(eff.repo)
                    if key not in cache:
                        cache[key] = self._ocupadas(eff, aj, ready_ids, ahora)
                    ocupadas, vivas = cache[key]
                    hit, propia = self._choca(t, eff, aj, ocupadas, vivas, admitidas.get(key, []), ahora)
                except Exception as exc:
                    log.info("prevención: %s sin comprobar (%s); se lanza", tid, exc)
                    out.append(t)
                    continue
                previo = estado.get(tid)
                if hit is None:
                    if previo:
                        estado.pop(tid)
                        cambiado = True
                        self._comentar(hermes, tid, f"{LIBERADA} {previo.get('con')}",
                                       f"{LIBERADA} {previo.get('con')}: ya no choca con lo que está en marcha; "
                                       "vuelve a la cola y se lanza cuando haya hueco")
                    admitidas.setdefault(key, []).append(propia)
                    out.append(t)
                    continue
                u, comun = hit
                if not previo:
                    previo = estado[tid] = {"con": u.uid, "desde": ahora, "lane": lane.name}
                    cambiado = True
                elif previo.get("con") != u.uid:
                    previo["con"] = u.uid
                    cambiado = True
                if previo.get("caducada") or ahora - float(previo.get("desde") or ahora) > aj.prevenir_max_horas * 3600:
                    if not previo.get("caducada"):
                        previo["caducada"] = True
                        cambiado = True
                        self._comentar(hermes, tid, f"{LIBERADA} (tope)",
                                       f"{LIBERADA} (tope): lleva más de {aj.prevenir_max_horas} h esperando a "
                                       f"{u.uid}; se lanza igualmente (choques.yaml → prevenir_max_horas)")
                    admitidas.setdefault(key, []).append(propia)
                    out.append(t)
                    continue
                log.info("%s: no se lanza en paralelo, choca con %s (%s)", tid, u.uid, "; ".join(comun[:3]))
                estado_otra = f"PR #{u.pr}" if u.pr else ("rama sin fusionar" if u.origen == "lane" else "en curso")
                self._comentar(hermes, tid, f"{MARCA} {u.uid}",
                               f"{MARCA} {u.uid}: no se lanza en paralelo porque choca con {u.etiqueta} ({estado_otra}) "
                               f"en {'; '.join(comun[:aj.comparten_max])}. Queda encadenada detrás: se lanza sola cuando "
                               f"{u.uid} termine, se fusione o se cierre (como mucho {aj.prevenir_max_horas} h). Para "
                               "lanzarla ya: 🙈 Ignorar el par en el aviso de choque, o prevenir_al_lanzar: false en "
                               "choques.yaml.")
            # Ya no está en ready (lanzada a mano, archivada…). Solo las de ESTE carril: el estado es compartido.
            for tid in [k for k, v in estado.items() if v.get("lane") == lane.name and k not in ready_ids]:
                estado.pop(tid)
                cambiado = True
            if cambiado:
                self._save(estado)
            return out
