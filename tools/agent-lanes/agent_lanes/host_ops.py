"""Operación de los hosts remotos (E2 + E3 mínimo de specs/worker-remoto-ssh.md): estado por host, sondeo, puerta de
lanzamiento y corte de huérfanos. Todo nace APAGADO: un carril sin `host:` no pasa por aquí, y un carril con `host:` cuyas
claves de E2 están en su valor por defecto tampoco tiene puerta (`HostCfg` y `Lane.host_gasto_dia_usd`).

Una sola fuente de estado en el HUB: `.state/hosts/<destino>.json` (`ultimaSenal` = hora de RECEPCIÓN en el hub del último
sondeo correcto, nunca una hora declarada por el host; disco, SHA, hash de contract/, gasto del día) y
`.state/hosts/<destino>.cuota.json` (pausa por cuota de ESE host; no toca `.state/cuota.json`, la global).

`gate()` solo LEE ese estado: lo llama `LaneRunner.candidates()` bajo el candado del planificador y no puede abrir SSH. El
sondeo (SSH) lo hace `HostMonitor` en su propio hilo, o `probe()` a mano. Solo datos y avisos al log: nada va a Telegram."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import cuota
from . import proc as _proc
from .config import ROOT, HostCfg
from .contrato import contract_hash

log = logging.getLogger("agent_lanes")

HOSTS_DIR = ROOT / ".state" / "hosts"
SONDEO_DEFECTO_S = 60          # cada cuánto se sondea un host con comprobaciones activas pero sin `latido_s`
MAX_REENCOLADOS = 3            # reencolados por fallo de transporte antes de escalar como bloqueo normal
MOTIVO_REENCOLADO = "reencolada sin contar bloqueo"  # prefijo del motivo de la devolución a ready (ver _model_fallback)
_HEX40 = re.compile(r"[0-9a-f]{40}")
_DESTINO_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


class HostStore:
    """Estado por host en `<dir>/<destino>.json`. Lecturas tolerantes (ilegible = vacío); escrituras atómicas."""

    def __init__(self, directory: Path | str = HOSTS_DIR):
        self.dir = Path(directory)
        self._lock = threading.Lock()

    def _path(self, destino: str) -> Path:
        if not _DESTINO_FILE.fullmatch(destino or ""):
            raise ValueError(f"destino de host no válido para un fichero: {destino!r}")
        return self.dir / f"{destino}.json"

    def cuota_path(self, destino: str) -> Path:
        self._path(destino)  # valida el nombre
        return self.dir / f"{destino}.cuota.json"

    def read(self, destino: str) -> dict:
        try:
            data = json.loads(self._path(destino).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def update(self, destino: str, **kw) -> dict:
        with self._lock:
            data = {**self.read(destino), **kw}
            _atomic_write(self._path(destino), data)
            return data

    # gasto declarado/estimado del día (hora local del hub)
    def gasto_hoy(self, destino: str, now: float) -> float:
        g = self.read(destino).get("gasto") or {}
        return float(g.get("usd") or 0.0) if g.get("dia") == _dia(now) else 0.0

    def add_gasto(self, destino: str, usd: float, now: float | None = None) -> float:
        now = time.time() if now is None else now
        with self._lock:
            data = self.read(destino)
            g = data.get("gasto") or {}
            total = (float(g.get("usd") or 0.0) if g.get("dia") == _dia(now) else 0.0) + max(0.0, float(usd or 0.0))
            data["gasto"] = {"dia": _dia(now), "usd": round(total, 4)}
            _atomic_write(self._path(destino), data)
            return total


def _dia(now: float) -> str:
    return datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def default_store(state_dir: Path | str) -> HostStore:
    """Almacén junto al estado de las tareas (`<.state>/hosts`), como el de la pausa de cuota."""
    return HostStore(Path(state_dir).parent / "hosts")


def sondeo_activo(host: HostCfg) -> bool:
    """¿Este host tiene alguna comprobación que necesite datos del host? (todo apagado por defecto)."""
    return bool(host.latido_s > 0 or host.hub_sha_minimo or host.disco_min_gb > 0 or host.contrato_hash)


def intervalo_sondeo(host: HostCfg) -> int:
    return host.latido_s or SONDEO_DEFECTO_S


# --- sondeo (SSH) ---------------------------------------------------------------------------------------------------

def _hub_head(run=_proc.run) -> str | None:
    cp = run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30)
    out = (getattr(cp, "stdout", "") or "").strip().lower()
    return out if cp.returncode == 0 and _HEX40.fullmatch(out) else None


def _contiene(min_sha: str, head: str, run=_proc.run) -> bool:
    """¿`head` (el HEAD del clon del host) contiene `min_sha`? Se mira en el clon del HUB, no con lo que diga el host: un
    SHA que el hub no conoce no se puede comprobar y bloquea."""
    for sha in (min_sha, head):
        if run(["git", "-C", str(ROOT), "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True, text=True,
               timeout=30).returncode != 0:
            return False
    return run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", min_sha, head], capture_output=True, text=True,
               timeout=30).returncode == 0


def probe(host: HostCfg, store: HostStore, *, transport=None, now: float | None = None, run=_proc.run,
          hub_contract=None, ssh_factory=None) -> dict | None:
    """Un sondeo por SSH: guarda en el estado del host disco, SHA, hash y `ultimaSenal`. Devuelve el estado o None si falló
    (el fallo queda anotado y `gate()` no lanza). No decide nada: solo datos."""
    from .ssh_host import SshRunner, SshTransportError  # import tardío: ssh_host importa config y runner de git
    now = time.time() if now is None else now
    try:
        ssh = (ssh_factory or SshRunner)(host, transport)
        data = ssh.op("status", timeout=60, worktree_root=host.worktree_root)
    except (SshTransportError, subprocess.TimeoutExpired) as exc:
        previo = store.read(host.destino)
        store.update(host.destino, ultimoSondeo=now, ultimoError=str(exc)[:300], ultimoErrorAt=now, ok=False)
        if previo.get("ok", True):
            log.warning("host %s: sondeo fallido: %s", host.destino, str(exc)[:200])
        return None
    head = str(data.get("hub_head") or "").lower()
    head = head if _HEX40.fullmatch(head) else ""
    disk = data.get("disk_free_gb")
    disk = float(disk) if isinstance(disk, (int, float)) and not isinstance(disk, bool) else None
    remoto_hash = str(data.get("contract_hash") or "")
    mi_hash = hub_contract if hub_contract is not None else contract_hash(ROOT)
    sha_ok = None
    if host.hub_sha_minimo:
        minimo = _hub_head(run) if host.hub_sha_minimo == "pin" else host.hub_sha_minimo.lower()
        sha_ok = bool(minimo and head and _contiene(minimo, head, run))
    return store.update(
        host.destino, ultimaSenal=now, ultimoSondeo=now, ok=True, ultimoError=None, hostTime=data.get("host_time"),
        diskFreeGb=disk, hubHeadHost=head or None, contratoHost=remoto_hash,
        contratoOk=(remoto_hash == mi_hash and bool(mi_hash)) if host.contrato_hash else None, shaOk=sha_ok)


class HostMonitor:
    """Hilo que sondea cada host con comprobaciones activas cada `latido_s` (o 60 s). Solo datos al estado del hub."""

    def __init__(self, hosts: list[HostCfg], store: HostStore, *, transport=None, clock=time.time, tick_s: float = 5.0):
        vistos: dict[str, HostCfg] = {}
        for h in hosts:
            if sondeo_activo(h):
                vistos.setdefault(h.destino, h)
        self.hosts = list(vistos.values())
        self.store, self._transport, self._clock, self._tick_s = store, transport, clock, tick_s
        self._stop = threading.Event()

    def tick(self) -> list[str]:
        hechos = []
        for h in self.hosts:
            last = float(self.store.read(h.destino).get("ultimoSondeo") or 0)
            if self._clock() - last >= intervalo_sondeo(h):
                try:
                    probe(h, self.store, transport=self._transport, now=self._clock())
                except Exception as exc:  # noqa: BLE001 - el monitor nunca cae
                    log.warning("host %s: sondeo falló: %s", h.destino, exc)
                hechos.append(h.destino)
        return hechos

    def start(self) -> threading.Thread | None:
        if not self.hosts:
            return None

        def loop():
            while not self._stop.wait(self._tick_s):
                self.tick()

        t = threading.Thread(target=loop, name="host-monitor", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()


# --- puerta de lanzamiento (solo lee estado) ------------------------------------------------------------------------

def tope_gasto(lane, lanes) -> float | None:
    """Tope diario del host de `lane`: el menor `host_gasto_dia_usd` declarado por los carriles que comparten destino."""
    destino = lane.host.destino
    topes = [float(l.host_gasto_dia_usd) for l in (lanes or {}).values()
             if getattr(l, "host", None) and l.host.destino == destino and getattr(l, "host_gasto_dia_usd", None)]
    if getattr(lane, "host_gasto_dia_usd", None):
        topes.append(float(lane.host_gasto_dia_usd))
    return min(topes) if topes else None


def gate(lane, lanes, store: HostStore, now: float | None = None) -> str | None:
    """Motivo por el que NO se lanzan tarjetas nuevas de este carril, o None. Sin SSH. Carril sin host o sin claves de E2
    activas: None siempre (paridad con E1)."""
    host = getattr(lane, "host", None)
    if not host:
        return None
    now = time.time() if now is None else now
    destino = host.destino
    pausa = cuota.vigente(store.cuota_path(destino), now)
    if pausa:
        return f"pausa por cuota de Claude del host {destino} hasta {cuota.hora(float(pausa['paused_until']))}"
    tope = tope_gasto(lane, lanes)
    if tope is not None:
        gastado = store.gasto_hoy(destino, now)
        if gastado >= tope:
            return (f"tope diario de gasto del host {destino} alcanzado ({gastado:.2f} de {tope:.2f} USD, declarado/estimado); "
                    "no se lanzan tarjetas nuevas hoy")
    if not sondeo_activo(host):
        return None
    st = store.read(destino)
    senal = float(st.get("ultimaSenal") or 0)
    limite = host.latido_parado_s if host.latido_s > 0 else max(host.latido_parado_s, 3 * intervalo_sondeo(host))
    if not senal or now - senal > limite:
        edad = f"hace {int((now - senal) // 60)} min" if senal else "nunca"
        return f"host {destino} sin señal (último sondeo correcto {edad})"
    if st.get("ok") is False and float(st.get("ultimoErrorAt") or 0) >= senal:
        return f"host {destino} no respondió al último sondeo: {str(st.get('ultimoError') or '')[:120]}"
    if host.disco_min_gb > 0:
        libre = st.get("diskFreeGb")
        if libre is None or float(libre) < host.disco_min_gb:
            return f"host {destino} con poco disco ({libre if libre is not None else '?'} GB libres; mínimo {host.disco_min_gb})"
    if host.hub_sha_minimo and st.get("shaOk") is not True:
        return (f"el clon del hub en {destino} no contiene el SHA mínimo ({host.hub_sha_minimo}); actualízalo a mano "
                "antes de lanzar tarjetas")
    if host.contrato_hash and st.get("contratoOk") is not True:
        return f"el hash de contract/ de {destino} no coincide con el del hub; actualiza su clon del hub"
    return None


def host_estado(host: HostCfg, store: HostStore, now: float | None = None) -> str:
    """Una palabra para paneles: ok | sin_senal | desconocido (sin latido configurado)."""
    now = time.time() if now is None else now
    if host.latido_s <= 0:
        return "desconocido"
    senal = float(store.read(host.destino).get("ultimaSenal") or 0)
    return "ok" if senal and now - senal <= host.latido_parado_s else "sin_senal"


_AVISADOS: dict[tuple[str, str], float] = {}


def avisar_una_vez(destino: str, motivo: str, now: float | None = None, cada_s: float = 3600) -> bool:
    """Warning al log como mucho una vez por (host, motivo) y hora. True si se emitió."""
    now = time.time() if now is None else now
    clave = (destino, re.sub(r"\d+", "#", motivo)[:80])
    if now - _AVISADOS.get(clave, 0.0) < cada_s:
        return False
    _AVISADOS[clave] = now
    log.warning("host %s: no se lanzan tarjetas: %s", destino, motivo)
    return True
