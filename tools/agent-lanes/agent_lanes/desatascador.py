"""Desatascador (t_bca8ab62; diseño: integrador-docs/DISENO_CARRILES_SIEMPRE_ANDANDO.markdown §4.1).

Cada `minutos` (15) recorre las tarjetas `blocked` de los tableros de los carriles y aplica SOLO lo estándar, dejando un
comentario de una línea en la tarjeta (y una línea en el resumen de la mañana):

1. El worker pide `Rama-origen: lane/t_xxx`: añade esa línea al cuerpo (la rama sale del mensaje, empieza por `lane/t_`,
   existe en el remoto y no es la base) y reencola.
2. Fallo transitorio (timeout, runner reiniciado, base de datos de solo lectura): reintento con espera creciente, como
   máximo `max_reintentos` (2) POR TARJETA. El contador vive en los comentarios de la tarjeta («Desatascador: reintento
   1/2»), así que no se pierde con el estado local. Al agotarse avisa a la Integradora, una sola vez.
3. Espera a una rama sin fusionar: condición «el SHA de la rama del padre ya está en la rama base». Si el backend lo
   permite pasa a `scheduled`; si no, sigue `blocked` con la condición en `.state/desatascador.json`. Al cumplirse, reencola.

Nunca toca una tarjeta cuyo motivo dependa de algo fuera del repo (producción, variables de entorno, migraciones,
workflows de GitHub, guard/rutas vetadas) ni una clasificada como estructural en bloqueos.yaml: esas son de la Integradora.
Siempre reasigna antes de desbloquear (el runner reclama al instante).

APAGADO por defecto y en seco: bloque `desatascador:` de lanes.yaml (`enabled: false, seco: true`). En seco solo calcula y
guarda el informe (`.state/desatascador.json`); no comenta, ni reasigna, ni desbloquea, ni envía. A mano:
`python -m agent_lanes.desatascador --dry-run` imprime lo que haría sobre las bloqueadas reales sin modificar nada.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from . import proc as _proc
from .bloqueos import ESTRUCTURAL, clasificar
from .config import ROOT

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "desatascador.json"
AUTHOR = "desatascador"
PREFIJO = "Desatascador:"
RAMA_RE = re.compile(r"^lane/t_[0-9a-f]{8}$")
RAMA_EN_TEXTO = re.compile(r"Rama-origen:?\s*`?(lane/t_[0-9a-f]{8})\b", re.I)
RAMA_PADRE = re.compile(r"\blane/(t_[0-9a-f]{8})\b")
LINEA_ORIGEN = re.compile(r"^\s*Rama-origen:", re.M | re.I)
REINTENTO_RE = re.compile(rf"^{PREFIJO} reintento (\d+)/(\d+)")

# Todo es configuración (regla «nada hardcodeado»): lanes.yaml; lo que falte vale esto.
TRANSITORIOS = (r"timed out|timeout|max_runtime|el worker no terminó|tiempo o presupuesto|runner reiniciado|"
                r"read-?only|solo lectura|readonly database")
ESPERA = (r"esper\w+\b.{0,60}\b(lane/t_[0-9a-f]{8}|rama|PR|padre)\b|sin fusionar|"
          r"depende de .{0,60}lane/t_[0-9a-f]{8}|padre .{0,40}\b(master|main)\b")
FUERA_DEL_REPO = (r"producci[oó]n|variables? de entorno|env vars?|despliegue|deploy|migraci[oó]n|alembic|"
                  r"\.github/workflows|rutas vetadas|LIMITACION-GUARD|credencial|secret|token|dokploy|vercel|"
                  r"\bnavegador\b|\b390 ?px\b")


@dataclass(frozen=True)
class Ajustes:
    activo: bool = False
    seco: bool = True
    minutos: float = 15
    max_reintentos: int = 2
    espera_base_min: float = 10      # espera antes del reintento n (0-based): base * 2**n minutos desde el bloqueo
    max_por_pasada: int = 5          # tope de acciones reales por pasada (freno ante un bucle)
    resumen_hora: int = 8            # hora Madrid del resumen de la mañana
    transitorios: str = TRANSITORIOS
    espera: str = ESPERA
    fuera_del_repo: str = FUERA_DEL_REPO


def _num(raw, default, lo=0.0):
    return raw if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= lo else default


def load(path: Path | str | None = None) -> Ajustes:
    """Bloque `desatascador:` de lanes.yaml; lo que falte o no valga queda en su defecto (nunca lanza)."""
    d = Ajustes()
    try:
        raw = (yaml.safe_load(Path(path or ROOT / "lanes.yaml").read_text(encoding="utf-8")) or {}).get("desatascador")
    except (OSError, yaml.YAMLError, AttributeError):
        return d
    if not isinstance(raw, dict):
        return d

    def rx(key, default):
        v = raw.get(key)
        try:
            return v if isinstance(v, str) and v and re.compile(v) else default
        except re.error:
            return default

    hora = raw.get("resumen_hora")
    return Ajustes(
        activo=raw["enabled"] if isinstance(raw.get("enabled"), bool) else d.activo,
        seco=raw["seco"] if isinstance(raw.get("seco"), bool) else d.seco,
        minutos=_num(raw.get("minutos"), d.minutos, 1),
        max_reintentos=int(_num(raw.get("max_reintentos"), d.max_reintentos, 0)),
        espera_base_min=_num(raw.get("espera_base_min"), d.espera_base_min, 0),
        max_por_pasada=int(_num(raw.get("max_por_pasada"), d.max_por_pasada, 1)),
        resumen_hora=hora if isinstance(hora, int) and not isinstance(hora, bool) and 0 <= hora <= 23 else d.resumen_hora,
        transitorios=rx("transitorios", d.transitorios), espera=rx("espera", d.espera),
        fuera_del_repo=rx("fuera_del_repo", d.fuera_del_repo))


@dataclass(frozen=True)
class Accion:
    tid: str
    board: str
    lane: str
    regla: str          # rama_origen | reintento | espera_sha | espera_cumplida | agotado | nada
    texto: str          # una línea (comentario de la tarjeta y resumen)
    rama: str = ""      # rama_origen: la rama a añadir
    pasa_a_scheduled: bool = False
    espera: dict | None = None   # espera_sha: {"padre", "rama", "base"}

    @property
    def actua(self) -> bool:
        return self.regla != "nada"


def _epoch(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def motivo(show: dict) -> tuple[str, float]:
    """Último motivo de bloqueo (comentario `BLOCKED: …` o evento `blocked`) y cuándo se escribió."""
    best, when = "", 0.0
    for c in (show or {}).get("comments") or []:
        body = str(c.get("body") or "")
        if body.startswith("BLOCKED:") and _epoch(c.get("created_at")) >= when:
            best, when = body[len("BLOCKED:"):].strip(), _epoch(c.get("created_at"))
    if best:
        return best, when
    for e in (show or {}).get("events") or []:
        if e.get("kind") == "blocked" and _epoch(e.get("created_at")) >= when:
            best, when = str((e.get("payload") or {}).get("reason") or ""), _epoch(e.get("created_at"))
    return best, when


def reintentos_hechos(show: dict) -> tuple[int, bool]:
    """(reintentos ya hechos, ¿ya avisó de que se agotaron?) a partir de los comentarios visibles de la tarjeta."""
    n, agotado = 0, False
    for c in (show or {}).get("comments") or []:
        body = str(c.get("body") or "")
        if m := REINTENTO_RE.match(body):
            n = max(n, int(m.group(1)))
        elif body.startswith(f"{PREFIJO} reintentos agotados"):
            agotado = True
    return n, agotado


def decidir(card: dict, show: dict, cfg: Ajustes, *, now: float, board: str = "", lane=None,
            rama_existe: Callable[[str], bool | None] = lambda r: None,
            padre_en_base: Callable[[str, str], bool | None] = lambda p, b: None) -> Accion:
    """Qué hace el desatascador con UNA tarjeta bloqueada. Función pura (git y kanban entran por los parámetros)."""
    task = (show or {}).get("task") or card
    tid, lane_name = task.get("id") or card.get("id"), str(task.get("assignee") or card.get("assignee") or "")

    def nada(por_que: str) -> Accion:
        return Accion(tid, board, lane_name, "nada", por_que)

    razon, desde = motivo(show)
    if not razon:
        return nada("sin motivo de bloqueo legible")
    if re.search(cfg.fuera_del_repo, razon, re.I) or clasificar(razon).clase == ESTRUCTURAL:
        return nada("depende de algo fuera del repo o es estructural: se deja a la Integradora")
    base = getattr(lane, "base", "") or "main"
    # 1. Rama-origen
    if m := RAMA_EN_TEXTO.search(razon):
        rama = m.group(1)
        if rama == f"lane/{tid}" or not RAMA_RE.match(rama):
            return nada(f"Rama-origen {rama} no válida")
        if LINEA_ORIGEN.search(str(task.get("body") or "")):
            return nada("la tarjeta ya lleva Rama-origen (no se repite)")
        if rama_existe(rama) is not True:
            return nada(f"la rama {rama} no existe en el remoto (o no se pudo comprobar)")
        return Accion(tid, board, lane_name, "rama_origen", f"{PREFIJO} añadida «Rama-origen: {rama}» y reencolada",
                      rama=rama)
    # 3. Espera a una rama sin fusionar
    if re.search(cfg.espera, razon, re.I):
        padres = RAMA_PADRE.findall(razon) or list((show or {}).get("parents") or [])
        padres = [p if isinstance(p, str) else str((p or {}).get("id")) for p in padres]
        if not padres:
            return nada("espera a una rama pero no se sabe cuál (sin padre ni lane/t_ en el motivo)")
        padre = padres[0]
        rama = f"lane/{padre}"
        estado = padre_en_base(rama, base)
        n_esperas = sum(1 for c in (show or {}).get("comments") or []
                        if str(c.get("body") or "").startswith(f"{PREFIJO} espera cumplida"))
        if estado is True:
            if n_esperas >= max(cfg.max_reintentos, 1):
                return nada("la espera ya se cumplió y se reencoló antes: no se repite (aviso a la Integradora)")
            return Accion(tid, board, lane_name, "espera_cumplida",
                          f"{PREFIJO} espera cumplida: {rama} ya está en {base}; reencolada")
        if estado is None:
            return nada(f"no se pudo comprobar {rama} en {base}; se reintenta en la próxima pasada")
        return Accion(tid, board, lane_name, "espera_sha",
                      f"{PREFIJO} espera a que el SHA de {rama} esté en {base}; se reencola solo al cumplirse",
                      pasa_a_scheduled=True, espera={"padre": padre, "rama": rama, "base": base})
    # 2. Transitorio
    if re.search(cfg.transitorios, razon, re.I):
        hechos, agotado = reintentos_hechos(show)
        if hechos >= cfg.max_reintentos:
            if agotado:
                return nada("reintentos agotados: ya avisada la Integradora")
            return Accion(tid, board, lane_name, "agotado",
                          f"{PREFIJO} reintentos agotados ({hechos}/{cfg.max_reintentos}); avisada la Integradora")
        espera_s = cfg.espera_base_min * 60 * (2 ** hechos)
        if now - desde < espera_s:
            return nada(f"espera creciente: el reintento {hechos + 1} toca a los {espera_s / 60:g} min del bloqueo")
        return Accion(tid, board, lane_name, "reintento",
                      f"{PREFIJO} reintento {hechos + 1}/{cfg.max_reintentos} del fallo transitorio; reencolada")
    return nada("motivo no estándar")


class _State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            pass
        for k, v in (("esperas", {}), ("acciones", []), ("informe_seco", [])):
            self.data.setdefault(k, v)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


class GitLectura:
    """Comprobaciones de git de SOLO LECTURA sobre el repo del carril (ls-remote y merge-base)."""

    def __init__(self, run=_proc.run):
        self._run = run

    def _out(self, *args: str) -> str | None:
        try:
            cp = self._run(list(args), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except Exception:
            return None
        return cp.stdout if cp.returncode == 0 else None

    def rama_existe(self, lane, rama: str) -> bool | None:
        out = self._out("git", "-C", lane.repo, "ls-remote", lane.remote, f"refs/heads/{rama}")
        return None if out is None else bool(out.split())

    def padre_en_base(self, lane, rama: str, base: str) -> bool | None:
        """¿El SHA de la rama del padre ya es ancestro de <remote>/<base>? None = no se pudo saber."""
        out = self._out("git", "-C", lane.repo, "ls-remote", lane.remote, f"refs/heads/{rama}")
        if out is None:
            return None
        parts = out.split()
        if not parts:
            return None  # sin rama remota: no se puede afirmar; el padre `done` se resuelve en el desatascador
        try:
            cp = self._run(["git", "-C", lane.repo, "merge-base", "--is-ancestor", parts[0], f"{lane.remote}/{base}"],
                           capture_output=True, text=True, timeout=60)
        except Exception:
            return None
        return True if cp.returncode == 0 else (False if cp.returncode == 1 else None)


class Desatascador:
    """`send(texto)` publica al canal (resumen y aviso a la Integradora); `hermes_for(board)` = backend del kanban."""

    def __init__(self, lanes: dict, hermes_for: Callable, send: Callable[..., object] | None = None,
                 git: GitLectura | None = None, state_path: Path | str = STATE_PATH,
                 settings_path: Path | str | None = None, clock: Callable[[], float] = time.time):
        self.lanes, self.hermes_for, self.send = lanes, hermes_for, send or (lambda *a, **k: None)
        self.git, self.state_path, self.settings_path, self.clock = git or GitLectura(), Path(state_path), settings_path, clock

    def ajustes(self) -> Ajustes:
        return load(self.settings_path)

    def _carriles(self):
        vistos = {}
        for name, lane in self.lanes.items():
            if getattr(lane, "board", "") and getattr(lane, "kind", "implement") == "implement":
                vistos[name] = lane
        return vistos

    def planificar(self, cfg: Ajustes | None = None) -> list[Accion]:
        """Solo lectura: lo que haría ahora sobre las bloqueadas (y las `scheduled` con espera de SHA registrada)."""
        cfg, now = cfg or self.ajustes(), self.clock()
        state, out, carriles = _State(self.state_path), [], self._carriles()
        for board in sorted({l.board for l in carriles.values()}):
            h = self.hermes_for(board)
            cards = []
            for status in ("blocked", "scheduled"):
                try:
                    cards += [c for c in h.list_status_all(status)
                              if status == "blocked" or c.get("id") in state.data["esperas"]]
                except Exception as exc:
                    log.warning("desatascador: no se pudo listar %s en %s: %s", status, board, exc)
            for card in cards:
                lane = carriles.get(str(card.get("assignee") or ""))
                if lane is None or lane.board != board:
                    continue
                try:
                    show = h.show(card["id"])
                except Exception as exc:
                    log.info("desatascador: %s no leída: %s", card.get("id"), exc)
                    continue
                if (show.get("task") or {}).get("status", card.get("status")) not in ("blocked", "scheduled"):
                    continue
                acc = decidir(card, show, cfg, now=now, board=board, lane=lane,
                              rama_existe=lambda r, l=lane: self.git.rama_existe(l, r),
                              padre_en_base=lambda r, b, l=lane: self.git.padre_en_base(l, r, b))
                if acc.regla == "espera_sha" and card["id"] in state.data["esperas"]:
                    continue  # ya registrada: no se repite el comentario
                if (show.get("task") or {}).get("status") == "scheduled" and acc.regla == "nada":
                    continue
                if acc.actua:
                    out.append(acc)
        return out

    def _reencolar(self, h, acc: Accion) -> bool:
        """Reasigna ANTES de desbloquear (el runner reclama al instante) y desbloquea. Falla = no cuenta como hecho."""
        return bool(h.reassign(acc.tid, acc.lane, f"desatascador: {acc.regla}") and h.unblock(acc.tid))

    def ejecutar(self, cfg: Ajustes | None = None) -> list[Accion]:
        """Una pasada. En seco guarda el informe y NO modifica nada. Con `enabled` a false no hace nada."""
        cfg = cfg or self.ajustes()
        if not cfg.activo:
            return []
        plan = self.planificar(cfg)
        state = _State(self.state_path)
        if cfg.seco:
            state.data["informe_seco"] = [a.__dict__ for a in plan]
            state.save()
            return []
        hechas: list[Accion] = []
        for acc in plan[:cfg.max_por_pasada]:
            h = self.hermes_for(acc.board)
            try:
                if acc.regla == "rama_origen":
                    if not getattr(h, "append_body_line", None) or not h.append_body_line(acc.tid, f"Rama-origen: {acc.rama}"):
                        log.warning("desatascador: %s: no se pudo añadir Rama-origen; se deja", acc.tid)
                        continue
                    h.comment(acc.tid, acc.texto, author=AUTHOR)
                    ok = self._reencolar(h, acc)
                elif acc.regla == "agotado":
                    h.comment(acc.tid, acc.texto, author=AUTHOR)
                    self.send(f"🛠 Desatascador: {acc.tid} agotó los reintentos del fallo transitorio. Le toca a la "
                              "Integradora (no se vuelve a tocar sola).")
                    ok = True
                elif acc.regla == "espera_sha":
                    h.comment(acc.tid, acc.texto, author=AUTHOR)
                    state.data["esperas"][acc.tid] = acc.espera
                    if getattr(h, "schedule", None):
                        h.schedule(acc.tid)
                    ok = True
                else:  # reintento | espera_cumplida: comentario ANTES (el contador es el comentario), luego reencolar
                    h.comment(acc.tid, acc.texto, author=AUTHOR)
                    ok = self._reencolar(h, acc)
                    if ok:
                        state.data["esperas"].pop(acc.tid, None)
            except Exception as exc:
                log.warning("desatascador: %s falló: %s", acc.tid, exc)
                continue
            if ok:
                hechas.append(acc)
                state.data["acciones"].append({"ts": self.clock(), "tid": acc.tid, "regla": acc.regla,
                                               "texto": acc.texto, "board": acc.board})
        state.save()
        return hechas

    def resumen(self, desde: float | None = None) -> str | None:
        """Resumen de la mañana: acciones de las últimas 24 h. None si no hubo. Vacía el registro enviado."""
        state = _State(self.state_path)
        corte = self.clock() - 86400 if desde is None else desde
        lista = [a for a in state.data["acciones"] if _epoch(a.get("ts")) >= corte]
        if not lista:
            return None
        lineas = [f"🛠 Desatascador, últimas 24 h: {len(lista)} acción(es)"]
        lineas += [f"- {a['tid']} · {a['regla']} · {a['texto']}" for a in lista[:15]]
        if len(lista) > 15:
            lineas.append(f"… y {len(lista) - 15} más")
        return "\n".join(lineas)


class DesatascadorTicker:
    """Bucle del runner: cada `minutos` una pasada y, pasada la hora, un resumen al día. Un fallo no tumba el runner."""

    def __init__(self, des: Desatascador, clock: Callable[[], float] = time.time):
        self.des, self.clock = des, clock
        self._last = 0.0

    def tick(self) -> list[Accion]:
        from .resumen_carriles import MADRID
        now = self.clock()
        try:
            cfg = self.des.ajustes()
            if not cfg.activo or now - self._last < cfg.minutos * 60:
                return []
            self._last = now
            hechas = self.des.ejecutar(cfg)
            local, state = datetime.fromtimestamp(now, MADRID), _State(self.des.state_path)
            dia = local.strftime("%Y-%m-%d")
            if not cfg.seco and local.hour >= cfg.resumen_hora and state.data.get("ultimo_resumen") != dia:
                state.data["ultimo_resumen"] = dia  # antes de enviar: sin bucles si algo falla
                state.save()
                if texto := self.des.resumen():
                    self.des.send(texto)
            return hechas
        except Exception as exc:
            log.warning("desatascador falló: %s", exc)
            return []


def main(argv: list[str], *, des: Desatascador | None = None, out: Callable[[str], None] = print) -> int:
    """`python -m agent_lanes.desatascador --dry-run`: imprime lo que haría. No comenta, reencola ni guarda nada."""
    ap = argparse.ArgumentParser(prog="desatascador")
    ap.add_argument("--dry-run", action="store_true", required=True, help="solo imprime")
    ap.parse_args(argv)
    if des is None:
        from .config import load_lanes
        from .kanban_api import backend_for
        des = Desatascador(load_lanes(), lambda b: backend_for(b))
    plan = des.planificar()
    out(f"{len(plan)} acción(es) posibles (dry-run: no se ha cambiado nada)")
    for n, a in enumerate(plan, 1):
        out(f"{n}) {a.tid} [{a.regla}] {a.lane}: {a.texto}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
