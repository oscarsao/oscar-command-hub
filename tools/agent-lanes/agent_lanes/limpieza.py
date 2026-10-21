"""Limpieza propuesta de tarjetas resueltas (spec: specs/kanban-limpieza-propuesta.md).

Una vez al día el detector marca tarjetas abiertas que ya están resueltas de hecho y las PROPONE por Telegram con
[Cerrar / Mantener / Ver]. Nada se cierra ni se archiva sin el botón de Oscar.

- `Limpieza`: umbrales y reglas, de tools/agent-lanes/limpieza.yaml (se relee en cada pasada; valor inválido = defecto).
- `detectar`: función pura sobre tarjetas ya leídas -> `Candidata` con motivo y evidencia.
- `LimpiezaRunner`: lee los tableros, propone (mensaje + botones + comentario) y atiende los botones.
- `LimpiezaTicker`: para el bucle del runner; una vez al día pasada la hora configurada.
- `main`: `python -m agent_lanes.limpieza --dry-run` imprime las candidatas sin enviar, comentar ni guardar nada.
"""
from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from .bandeja import title_key
from .config import ROOT
from .reconcile import OPEN_STATUSES, pr_outcome, pr_refs

log = logging.getLogger("agent_lanes")

SETTINGS_PATH = ROOT / "limpieza.yaml"
STATE_PATH = ROOT / ".state" / "limpieza.json"
LIM_CLOSE, LIM_KEEP, LIM_VIEW = "lim_close", "lim_keep", "lim_view"
AUTHOR = "limpieza"
OSCAR_AUTHORS = ("oscar", "oscar-telegram")
SYSTEM_AUTHORS = (AUTHOR, "reconciliacion", "agent-lanes")  # no cuentan como movimiento ni como reapertura
QUIET_EVENTS = ("commented", "comment")  # el comentario ya se cuenta por su autor
REOPEN_GRACE = 600  # s: los comentarios/eventos del integrador justo tras el merge no son una reapertura
RULES = ("pr_mergeado", "hermana_hecha", "decidir_respondida", "duplicada", "oscar_parada")
TITLE_MAX = 50


@dataclass(frozen=True)
class Ajustes:
    activo: bool = True
    hora: int = 9
    dias_sin_movimiento: float = 7
    dias_silencio: float = 14
    similitud: float = 0.85
    max_propuestas: int = 8
    tableros: tuple[str, ...] = ()
    reglas: dict = field(default_factory=lambda: {r: True for r in RULES})


def _num(raw, default, lo: float = 0, hi: float | None = None):
    ok = isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= lo and (hi is None or raw <= hi)
    return raw if ok else default


def load(path: Path | str | None = None) -> Ajustes:
    """Ajustes desde limpieza.yaml; lo que falte o no sea válido queda en su valor por defecto (nunca lanza)."""
    d = Ajustes()
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.info("limpieza.yaml no leído (%s): valores por defecto", exc)
        return d
    if not isinstance(raw, dict):
        return d
    reglas = dict(d.reglas)
    if isinstance(raw.get("reglas"), dict):
        reglas.update({k: v for k, v in raw["reglas"].items() if k in RULES and isinstance(v, bool)})
    boards = raw.get("tableros")
    return Ajustes(
        activo=raw["activo"] if isinstance(raw.get("activo"), bool) else d.activo,
        hora=int(_num(raw.get("hora"), d.hora, 0, 23)),
        dias_sin_movimiento=_num(raw.get("dias_sin_movimiento"), d.dias_sin_movimiento, 0.0001),
        dias_silencio=_num(raw.get("dias_silencio"), d.dias_silencio, 0.0001),
        similitud=_num(raw.get("similitud"), d.similitud, 0.0001, 1),
        max_propuestas=int(_num(raw.get("max_propuestas"), d.max_propuestas, 1)),
        tableros=tuple(str(b) for b in boards) if isinstance(boards, list) else d.tableros,
        reglas=reglas)


@dataclass(frozen=True)
class Candidata:
    tid: str
    board: str
    title: str
    regla: str
    motivo: str
    evidencia: str
    accion: str = "complete"  # complete | archive
    assignee: str = ""

    def linea(self) -> str:
        return f"{self.tid} · {self.title[:TITLE_MAX]} · {self.motivo} · {self.evidencia}"


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%d/%m")


def _stamp(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value else None


def _author(c: dict) -> str:
    return str(c.get("author") or "").lower()


def movimiento(show: dict) -> float | None:
    """Último movimiento real: eventos (salvo los de comentario) y comentarios que no son del propio job."""
    stamps = [_stamp(e.get("created_at")) for e in show.get("events") or () if e.get("kind") not in QUIET_EVENTS]
    stamps += [_stamp(c.get("created_at")) for c in show.get("comments") or () if _author(c) not in SYSTEM_AUTHORS]
    stamps.append(_stamp((show.get("task") or {}).get("created_at")))
    nums = [s for s in stamps if s]
    return max(nums) if nums else None


def _epoch(iso) -> float | None:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def reabierta_tras(show: dict, ts: float | None) -> bool:
    """¿Se movió la tarjeta después de `ts` (salvo el margen del integrador)? Sin hora fiable, por prudencia sí."""
    if ts is None:
        return True
    last = movimiento(show)
    return bool(last and last > ts + REOPEN_GRACE)


def _regla_pr(c: dict, prs: list[dict]) -> tuple[str, str] | None:
    for pr in prs:
        if pr_outcome(pr) != "merged" or c["id"] not in pr_refs(pr):
            continue
        if reabierta_tras(c["show"], _epoch(pr.get("mergedAt"))):
            continue  # mergeado pero reabierta después: sigue viva
        return "su PR está mergeado", f"PR #{pr.get('number')} {pr.get('url') or ''}".strip()
    return None


def _regla_hermana(c: dict, hechas: list[dict]) -> tuple[str, str] | None:
    key = title_key(c.get("title"))
    for d in hechas:
        if key and d["id"] != c["id"] and title_key(d.get("title")) == key:
            return "una tarjeta equivalente ya está hecha", f"{d['id']} (done)"
    return None


def _regla_respondida(c: dict) -> tuple[str, str] | None:
    comments = [x for x in c["show"].get("comments") or () if _author(x) not in SYSTEM_AUTHORS]
    if c.get("status") not in ("blocked", "triage") or len(comments) < 2 or _author(comments[-1]) not in OSCAR_AUTHORS:
        return None
    if all(_author(x) in OSCAR_AUTHORS for x in comments):
        return None  # no hay pregunta de nadie a la que Oscar respondiera
    at = _stamp(comments[-1].get("created_at"))
    return "la pregunta ya tiene respuesta", f"comentario de {_author(comments[-1])}" + (f" el {_day(at)}" if at else "")


def _regla_parada(c: dict, cfg: Ajustes, now: float) -> tuple[str, str] | None:
    last = movimiento(c["show"])
    if c.get("assignee") != "oscar" or not last or now - last < cfg.dias_sin_movimiento * 86400:
        return None
    dias = int((now - last) // 86400)
    return f"sin movimiento {dias} días", f"asignada a oscar; último movimiento {_day(last)}"


def _duplicadas(cards: list[dict], cfg: Ajustes) -> dict[str, tuple[str, str]]:
    """tid de la más nueva -> (motivo, evidencia), por pares del mismo carril con títulos parecidos."""
    out: dict[str, tuple[str, str]] = {}
    for i, a in enumerate(cards):
        for b in cards[i + 1:]:
            if a.get("assignee") != b.get("assignee") or not (ka := title_key(a.get("title"))) \
                    or not (kb := title_key(b.get("title"))):
                continue
            ratio = difflib.SequenceMatcher(None, ka, kb).ratio()
            if ratio < cfg.similitud:
                continue
            old, new = sorted((a, b), key=lambda c: (_stamp((c["show"].get("task") or {}).get("created_at")) or 0, c["id"]))
            out.setdefault(new["id"], ("duplicada de otra abierta", f"{old['id']} ({round(ratio * 100)} % parecida)"))
    return out


def detectar(cards: list[dict], hechas: list[dict], prs: list[dict], cfg: Ajustes, now: float) -> list[Candidata]:
    """Candidatas a cierre. `cards`: abiertas {board, id, title, status, assignee, show}; `hechas`: las `done` ({id, title}).
    Una tarjeta sale una sola vez, por la primera regla activa que casa (orden de RULES). Nunca `running`."""
    live = [c for c in cards if c.get("status") != "running"]
    dups = _duplicadas(live, cfg) if cfg.reglas.get("duplicada") else {}
    out = []
    for c in live:
        checks = (("pr_mergeado", lambda: _regla_pr(c, prs)), ("hermana_hecha", lambda: _regla_hermana(c, hechas)),
                  ("decidir_respondida", lambda: _regla_respondida(c)), ("duplicada", lambda: dups.get(c["id"])),
                  ("oscar_parada", lambda: _regla_parada(c, cfg, now)))
        found = next(((name, *r) for name, check in checks if cfg.reglas.get(name) and (r := check())), None)
        if found:
            regla, motivo, evidencia = found
            out.append(Candidata(c["id"], c["board"], c.get("title") or "", regla, motivo, evidencia,
                                 "archive" if regla == "duplicada" else "complete", c.get("assignee") or ""))
    return out


class _State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            pass
        for key in ("kept", "proposed"):
            self.data.setdefault(key, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(tmp, self.path)


class LimpiezaRunner:
    """`send(text, markup)` publica al canal de Oscar; `issue(rec, spec)` -> (token, reply_markup) (CallbackStore)."""

    def __init__(self, hermes_for: Callable, boards: list[str], prs: Callable[[], list[dict]],
                 send: Callable[[str, dict | None], object], issue: Callable | None = None,
                 state_path: Path | str = STATE_PATH, settings_path: Path | str | None = None,
                 clock: Callable[[], float] = time.time):
        self.hermes_for, self.boards, self.prs, self.send, self.issue = hermes_for, boards, prs, send, issue
        self.state_path, self.settings_path, self.clock = Path(state_path), settings_path, clock

    def ajustes(self) -> Ajustes:
        return load(self.settings_path)

    def _read(self, boards) -> tuple[list[dict], list[dict]]:
        cards, hechas = [], []
        for board in boards:
            h = self.hermes_for(board)
            try:
                hechas += [{"id": d["id"], "title": d.get("title")} for d in h.list_status_all("done")]
            except Exception as exc:
                log.warning("limpieza: no se pudo listar done en %s: %s", board, exc)
            for status in OPEN_STATUSES:
                try:
                    listed = h.list_status_all(status)
                except Exception as exc:
                    log.warning("limpieza: no se pudo listar %s en %s: %s", status, board, exc)
                    continue
                for card in listed:
                    try:
                        show = h.show(card["id"])
                    except Exception:
                        continue
                    task = show.get("task") or {}
                    cards.append({"board": board, "id": card["id"], "title": task.get("title") or card.get("title"),
                                  "status": task.get("status") or status,
                                  "assignee": task.get("assignee") or card.get("assignee"), "show": show})
        return cards, hechas

    def candidatas(self, cfg: Ajustes | None = None) -> list[Candidata]:
        """Detecta y descarta las silenciadas o ya propuestas hace menos de `dias_silencio` (solo lectura)."""
        cfg, now = cfg or self.ajustes(), self.clock()
        state = _State(self.state_path)
        cards, hechas = self._read(cfg.tableros or self.boards)
        found = detectar(cards, hechas, self.prs(), cfg, now)
        return [c for c in found if state.data["kept"].get(c.tid, 0) <= now
                and now - state.data["proposed"].get(c.tid, 0) >= cfg.dias_silencio * 86400]

    def proponer(self, cfg: Ajustes | None = None) -> list[str]:
        """Manda la propuesta (una línea por candidata, fila de botones cada una) y deja el comentario en la tarjeta."""
        cfg = cfg or self.ajustes()
        found = self.candidatas(cfg)[:cfg.max_propuestas]
        if not found:
            return []
        rows, lines = [], []
        for n, c in enumerate(found, 1):
            lines.append(f"{n}) {c.linea()}")
            if self.issue:
                rec = {"task_id": c.tid, "board": c.board, "lane": c.assignee, "title": c.title, "motivo": c.motivo,
                       "evidencia": c.evidencia, "accion": c.accion, "limpieza": True}
                rows += self.issue(rec, [[{"text": f"✅ Cerrar {n}", "action": LIM_CLOSE},
                                          {"text": f"⏳ Mantener {n}", "action": LIM_KEEP},
                                          {"text": f"👁 Ver {n}", "action": LIM_VIEW}]])[1]["inline_keyboard"]
        text = (f"🧹 {len(found)} tarjeta{'s' if len(found) != 1 else ''} que parece{'n' if len(found) != 1 else ''} "
                "resuelta" + ("s" if len(found) != 1 else "") + ":\n" + "\n".join(lines)
                + "\n➡️ Próximo paso: Cerrar, Mantener o Ver cada una · Lo da: Oscar (si no contestas, no pasa nada)")
        self.send(text, {"inline_keyboard": rows} if rows else None)
        state = _State(self.state_path)
        for c in found:
            state.data["proposed"][c.tid] = self.clock()
            try:
                self.hermes_for(c.board).comment(c.tid, f"propuesta de cierre: {c.motivo} ({c.evidencia})", author=AUTHOR)
            except Exception as exc:
                log.warning("limpieza: no se pudo comentar %s: %s", c.tid, exc)
        state.save()
        return [c.tid for c in found]

    # --- botones -------------------------------------------------------------------------------------

    @staticmethod
    def _say(desk, where: dict | None, text: str) -> None:
        if where and getattr(desk, "notifier", None):
            try:
                desk.notifier.send_to(where["chat_id"], where.get("thread_id"), text, html=False)
            except Exception as exc:
                log.warning("limpieza: no se pudo responder: %s", exc)

    def on_button(self, action: str, rec: dict, where: dict | None, desk) -> bool:
        tid, h = rec["task_id"], self.hermes_for(rec["board"])
        who = desk._who() if hasattr(desk, "_who") else "Oscar"
        try:
            show = h.show(tid)
        except Exception as exc:
            log.info("limpieza: %s no leída: %s", tid, exc)
            self._say(desk, where, f"{tid}: no pude leer la tarjeta ahora")
            return False
        status = (show.get("task") or {}).get("status")
        if action == LIM_VIEW:
            self._say(desk, where, self._ver(tid, show))
            return True
        if status in ("done", "archived"):
            self._say(desk, where, f"{tid}: ya estaba cerrada")
            return True
        if action == LIM_KEEP:
            cfg = self.ajustes()
            until = self.clock() + cfg.dias_silencio * 86400
            state = _State(self.state_path)
            state.data["kept"][tid] = until
            state.data["proposed"].pop(tid, None)
            state.save()
            h.comment(tid, f"{who}: mantener hasta {_day(until)} (no se vuelve a proponer antes)", author=AUTHOR)
            self._say(desk, where, f"⏳ {tid} se mantiene hasta {_day(until)}")
            return True
        if status == "running":
            self._say(desk, where, f"{tid}: está en curso; no la cierro")
            return False
        motivo = f"{rec.get('motivo', '')} ({rec.get('evidencia', '')})".strip()
        if rec.get("accion") == "archive":
            ok = h.archive(tid)
        else:
            ok, _ = h.complete(tid, motivo, {"limpieza": True, "regla_motivo": rec.get("motivo", "")})
        if not ok:
            self._say(desk, where, f"{tid}: no se pudo cerrar (¿estado incompatible?)")
            return False
        h.comment(tid, f"{who}: {'archivada' if rec.get('accion') == 'archive' else 'cerrada'} desde la propuesta "
                       f"de limpieza · {motivo}", author=AUTHOR)
        self._say(desk, where, f"✅ {tid} {'archivada' if rec.get('accion') == 'archive' else 'cerrada'}")
        return True

    @staticmethod
    def _ver(tid: str, show: dict) -> str:
        t = show.get("task") or {}
        last = [f"- {c.get('author')}: {str(c.get('body') or '')[:200]}" for c in (show.get("comments") or ())[-3:]]
        return "\n".join([f"👁 {tid} · {t.get('title')}", f"Estado: {t.get('status')} · asignada a {t.get('assignee')}",
                          *(["Últimos comentarios:", *last] if last else ["Sin comentarios."])])[:3500]


class LimpiezaTicker:
    """Bucle del runner: una vez al día, pasada la hora de `limpieza.yaml` (Madrid), propone. Un fallo no tumba el runner."""

    def __init__(self, runner: LimpiezaRunner, minutes: float = 10, clock: Callable[[], float] = time.time):
        self.runner, self.minutes, self.clock = runner, minutes, clock
        self._last = 0.0

    def tick(self) -> list[str]:
        from .resumen_carriles import MADRID
        now = self.clock()
        if now - self._last < self.minutes * 60:
            return []
        self._last = now
        try:
            cfg = self.runner.ajustes()
            local = datetime.fromtimestamp(now, MADRID)
            state = _State(self.runner.state_path)
            day = local.strftime("%Y-%m-%d")
            if not cfg.activo or local.hour < cfg.hora or state.data.get("last_day") == day:
                return []
            state.data["last_day"] = day  # antes de enviar: sin bucles si algo falla
            state.save()
            return self.runner.proponer(cfg)
        except Exception as exc:
            log.warning("limpieza falló: %s", exc)
            return []


def main(argv: list[str], *, runner: LimpiezaRunner | None = None, out: Callable[[str], None] = print) -> int:
    """`python -m agent_lanes.limpieza --dry-run`: imprime las candidatas de hoy. No envía, comenta ni guarda nada."""
    ap = argparse.ArgumentParser(prog="limpieza")
    ap.add_argument("--dry-run", action="store_true", required=True, help="solo imprime (el envío lo hace el runner)")
    ap.parse_args(argv)
    if runner is None:
        from .config import load_lanes
        from .hermes import HermesCLI
        from .reconcile import gh_prs
        lanes = load_lanes()
        boards = sorted({l.board for l in lanes.values() if l.board})
        slugs = sorted({l.github for l in lanes.values() if l.github})
        runner = LimpiezaRunner(lambda b: HermesCLI(b), boards, lambda: gh_prs(slugs), lambda *a: None)
    found = runner.candidatas()
    out(f"{len(found)} candidata(s) a cierre (dry-run: no se ha enviado ni cambiado nada)")
    for n, c in enumerate(found, 1):
        out(f"{n}) [{c.regla}] {c.linea()}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
