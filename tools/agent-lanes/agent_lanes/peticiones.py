"""Registro de peticiones `req_<n>` (pitch «Seguimiento de peticiones», entrega A1 · t_211c9902).

Contrato COMPARTIDO con las entregas B (tipo de petición) y C (workflows): no se cambia sin comentarlo en la tarjeta de A.

- El registro es un JSON propio del hub (patrón `resumen_carriles`: fichero pequeño, sin tocar el kanban). Solo guarda
  lo que el kanban no tiene: id, proyecto, título, tipo (y quién lo confirmó), urgencia/modo, origen y `req -> [tarjetas]`.
- El estado de la petición (`hecha | en curso | esperándote | atascada`), el paso, el próximo paso y QUIÉN lo da son
  DERIVADOS de las tarjetas y sus comentarios (`derivar`): nunca se guardan, así no hay dos fuentes de verdad.
- El vínculo viaja en la tarjeta: línea `req: req_<n>` en el cuerpo o en un comentario; el tipo, en el cuerpo como
  `Tipo: bug · Urgencia: P1`.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

PROYECTOS = ("Hub", "OHQ", "MigraTeam", "Pildora", "Signal", "NextJobs", "Scraper", "Personal")
TIPOS = ("bug", "feature", "workflow")
URGENCIAS = ("P0", "P1", "P2", "P3")
MODOS_OASP = ("fast-track", "spec-lite", "pitch")
ORIGENES = ("telegram", "claude-code")
ESTADOS = ("hecha", "en curso", "esperándote", "atascada")

REQ_RE = re.compile(r"\breq:\s*(req_\d+)\b", re.I)
TIPO_RE = re.compile(r"\bTipo:\s*(bug|feature|workflow)\b(?:\s*·\s*Urgencia:\s*(P[0-3])\b)?", re.I)
INTEGRADO_RE = re.compile(r"\bINTEGRADO\s+([0-9a-f]{7,40})\b")
ESPERA_ATASCO_H = 24   # una pregunta sin respuesta de Oscar más de 24 h cuenta como atasco
TOTAL_PASOS = 6        # petición · tarjeta · carril · PR · integración · despliegue


# --- vínculo y tipo en la tarjeta --------------------------------------------------------------------------

def linea_vinculo(req_id: str) -> str:
    return f"req: {req_id}"


def vinculo_de(texto: str | None) -> str | None:
    """`req_<n>` de un cuerpo/comentario de tarjeta (el primero), o None."""
    found = REQ_RE.search(texto or "")
    return found.group(1).lower() if found else None


def linea_tipo(tipo: str, urgencia: str | None = None) -> str:
    return f"Tipo: {tipo}" + (f" · Urgencia: {urgencia}" if tipo == "bug" and urgencia else "")


def tipo_de(texto: str | None) -> tuple[str | None, str | None]:
    """(tipo, urgencia) de la línea `Tipo: bug · Urgencia: P1` del cuerpo; (None, None) si no hay."""
    found = TIPO_RE.search(texto or "")
    if not found:
        return None, None
    tipo = found.group(1).lower()
    return tipo, (found.group(2).upper() if found.group(2) and tipo == "bug" else None)


# --- registro ----------------------------------------------------------------------------------------------

class Peticiones:
    """Almacén de peticiones. Escritura atómica (tmp + replace); un fichero ilegible se trata como vacío."""

    def __init__(self, path: Path | str, now=lambda: datetime.now().timestamp()):
        self.path, self._now, self._lock = Path(path), now, threading.RLock()

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("peticiones"), dict):
            data["peticiones"] = {}
        ids = [int(k[4:]) for k in data["peticiones"] if re.fullmatch(r"req_\d+", k)]
        siguiente = data.get("siguiente")
        data["siguiente"] = max(siguiente if isinstance(siguiente, int) else 1, max(ids, default=0) + 1)
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _validar_tipo(tipo, por, urgencia, modo_oasp) -> None:
        if tipo is None:
            if urgencia or modo_oasp:
                raise ValueError("urgencia/modo_oasp requieren tipo")
            return
        if tipo not in TIPOS:
            raise ValueError(f"tipo desconocido: {tipo!r}")
        if not (por or "").strip():
            raise ValueError("el tipo solo se fija con tipo_confirmado_por: el bot propone, la persona confirma")
        if urgencia is not None and (tipo != "bug" or urgencia not in URGENCIAS):
            raise ValueError(f"urgencia inválida: {urgencia!r} (solo bug, {'/'.join(URGENCIAS)})")
        if modo_oasp is not None and (tipo != "feature" or modo_oasp not in MODOS_OASP):
            raise ValueError(f"modo_oasp inválido: {modo_oasp!r} (solo feature, {'/'.join(MODOS_OASP)})")

    def crear(self, proyecto: str, titulo: str, origen: str, *, tipo: str | None = None,
              tipo_confirmado_por: str | None = None, urgencia: str | None = None, modo_oasp: str | None = None,
              tarjetas: Iterable[str] = ()) -> dict:
        if proyecto not in PROYECTOS:
            raise ValueError(f"proyecto fuera de la lista cerrada: {proyecto!r}")
        if origen not in ORIGENES:
            raise ValueError(f"origen desconocido: {origen!r}")
        if not (titulo or "").strip():
            raise ValueError("la petición necesita título")
        self._validar_tipo(tipo, tipo_confirmado_por, urgencia, modo_oasp)
        with self._lock:
            data = self._load()
            rid = f"req_{data['siguiente']}"
            now = self._now()
            data["siguiente"] += 1
            data["peticiones"][rid] = {
                "id": rid, "proyecto": proyecto, "titulo": titulo.strip(), "tipo": tipo,
                "tipo_confirmado_por": tipo_confirmado_por if tipo else None, "urgencia": urgencia,
                "modo_oasp": modo_oasp, "origen": origen, "tarjetas": list(dict.fromkeys(tarjetas)),
                "creado": now, "ultimo_movimiento": now}
            self._save(data)
            return dict(data["peticiones"][rid])

    def get(self, rid: str) -> dict | None:
        found = self._load()["peticiones"].get(rid)
        return dict(found) if found else None

    def listar(self, proyecto: str | None = None) -> list[dict]:
        """Peticiones por orden de id (la más reciente al final); `proyecto` filtra sin distinguir mayúsculas."""
        todas = sorted(self._load()["peticiones"].values(), key=lambda r: int(r["id"][4:]))
        return [dict(r) for r in todas if not proyecto or r["proyecto"].lower() == proyecto.lower()]

    def _editar(self, rid: str, cambio) -> dict:
        with self._lock:
            data = self._load()
            rec = data["peticiones"].get(rid)
            if rec is None:
                raise KeyError(rid)
            cambio(rec)
            rec["ultimo_movimiento"] = self._now()
            self._save(data)
            return dict(rec)

    def vincular(self, rid: str, tid: str) -> dict:
        """Añade una tarjeta a la petición (idempotente)."""
        def cambio(rec):
            if tid not in rec["tarjetas"]:
                rec["tarjetas"].append(tid)
        return self._editar(rid, cambio)

    def fijar_tipo(self, rid: str, tipo: str, por: str, *, urgencia: str | None = None,
                   modo_oasp: str | None = None) -> dict:
        self._validar_tipo(tipo, por, urgencia, modo_oasp)

        def cambio(rec):
            rec.update(tipo=tipo, tipo_confirmado_por=por, urgencia=urgencia, modo_oasp=modo_oasp)
        return self._editar(rid, cambio)

    def tocar(self, rid: str) -> dict:
        return self._editar(rid, lambda rec: None)


# --- estado derivado ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Estado:
    estado: str            # uno de ESTADOS
    motivo: str = ""       # solo en `atascada`
    paso: int = 1          # 1..TOTAL_PASOS de la cadena
    proximo: str = ""      # próximo paso
    quien: str = ""        # quién lo da
    desde: float | None = None   # desde cuándo está así (epoch), si se sabe

    @property
    def etiqueta(self) -> str:
        return f"{self.estado} ({self.motivo})" if self.motivo else self.estado


def _comentarios(show: dict) -> list[dict]:
    return [c for c in (show.get("comments") or []) if isinstance(c, dict) and isinstance(c.get("body"), str)]


def _num(valor) -> float | None:
    try:
        return float(valor) if valor is not None and not isinstance(valor, bool) else None
    except (TypeError, ValueError):
        return None


def _ultimo_bloqueo(show: dict) -> dict | None:
    return next((e for e in reversed(show.get("events") or []) if isinstance(e, dict) and e.get("kind") == "blocked"),
                None)


def integrada(show: dict) -> bool:
    return any(INTEGRADO_RE.search(c["body"]) for c in _comentarios(show))


def derivar(req: dict, shows: dict[str, dict | None], ahora: float) -> Estado:
    """Estado de una petición a partir de sus tarjetas (`shows[tid]` = salida de `hermes show`, None si no se leyó).
    Prioridad: atascada > esperándote > en curso > hecha. Sin tarjeta -> `atascada (sin tarjeta)`."""
    tids = list(req.get("tarjetas") or ())
    if not tids:
        return Estado("atascada", "sin tarjeta", 1, "enlazar una tarjeta con «req: " + str(req.get("id")) + "»",
                      "Oscar", req.get("creado"))
    activas, atasco, espera, paso = [], None, None, 2
    for tid in tids:
        show = shows.get(tid)
        if not show:
            atasco = atasco or Estado("atascada", f"tarjeta {tid} no legible", 2, "revisar la tarjeta en Hermes",
                                      "el integrador", None)
            continue
        task = show.get("task") or {}
        status = task.get("status")
        if status == "archived":
            continue
        activas.append((tid, show, task, status))
        if integrada(show):
            paso = max(paso, 5)
        elif status == "running":
            paso = max(paso, 3)
        elif any(re.search(r"\bPR #\d+", c["body"]) for c in _comentarios(show)):
            paso = max(paso, 4)
        bloqueo = _ultimo_bloqueo(show)
        desde = _num((bloqueo or {}).get("created_at"))
        razon = str(((bloqueo or {}).get("payload") or {}).get("reason") or "")
        tipo = ((bloqueo or {}).get("payload") or {}).get("kind")
        ultimo = _comentarios(show)[-1]["body"] if _comentarios(show) else ""
        guard = "LIMITACION-GUARD" in razon   # va como needs_input, pero nadie puede responderla: es del integrador
        if status in ("blocked", "triage") and (status == "triage" or tipo != "needs_input" or guard):
            if guard:
                motivo, quien = "guard bloqueado", "el integrador"
            elif status == "triage":
                motivo, quien = "bloqueada dos veces por lo mismo", "el integrador"
            else:
                motivo, quien = "carril caído", "el integrador"
            atasco = atasco or Estado("atascada", motivo, paso, f"desbloquear {tid}", quien, desde)
        elif status == "blocked" or (task.get("assignee") == "oscar" and status in ("ready", "blocked")):
            if desde is not None and ahora - desde > ESPERA_ATASCO_H * 3600:
                atasco = atasco or Estado("atascada", f"sin respuesta tuya > {ESPERA_ATASCO_H} h", paso,
                                          f"responder la pregunta de {tid}", "Oscar", desde)
            espera = espera or Estado("esperándote", "", paso, f"responder la pregunta de {tid}", "Oscar", desde)
        elif re.search(r"queda FUERA del lote|conflicto", ultimo, re.I) and not integrada(show):
            atasco = atasco or Estado("atascada", "PR sin salida", 4, f"resolver el PR de {tid}", "el integrador",
                                      None)
    if atasco:
        return atasco
    if espera:
        return espera
    if activas and all(integrada(s) for _, s, _, _ in activas):
        return Estado("hecha", "", TOTAL_PASOS, "ninguno", "nadie", None)
    pendientes = [t for t, s, task, st in activas if not integrada(s) and st in ("done", "review")]
    if pendientes and len(pendientes) == len([a for a in activas if not integrada(a[1])]):
        return Estado("en curso", "", max(paso, 4), "integrar la rama en el lote", "el integrador", None)
    return Estado("en curso", "", paso, "terminar la tarjeta", "el worker", None)
