"""Reconciliación del kanban con lo que pasa fuera de él (spec: specs/reconciliacion.md).

- `Reconciler.close_from_prs()`: PR mergeado/cerrado -> cierra o archiva las tarjetas vinculadas y avisa UNA vez.
- `Reconciler.obsolete_pass()`: una vez al día propone archivar tarjetas obsoletas con un botón (nunca archiva sola).
- `record_decision()` / `main()`: `lanes.py tarjeta ...`, para que el integrador registre en la tarjeta lo decidido en
  otro canal. Todo queda comentado en la tarjeta (con autor) y en .state/reconcile-audit.jsonl.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Callable

from . import proc as _proc
from .config import ROOT

log = logging.getLogger("agent_lanes")

STATE_PATH = ROOT / ".state" / "reconcile.json"
AUDIT_PATH = ROOT / ".state" / "reconcile-audit.jsonl"
TID_RE = re.compile(r"\bt_[0-9a-f]{8}\b")
# El id en el cuerpo solo vincula tras una palabra que lo declara: «se apoya en t_x» no cierra t_x.
BODY_REF_RE = re.compile(r"\b(?:cierra|resuelve|closes|fixes|tarjeta)\s*:?\s*(t_[0-9a-f]{8})\b", re.I)
COVERED_RE = re.compile(r"\b(?:cubierta|duplicada|resuelta|hecha)\s+(?:por|en|de)\s+(t_[0-9a-f]{8})\b", re.I)
OPEN_STATUSES = ("ready", "todo", "blocked", "triage", "review")
CLOSED_STATUSES = ("done", "archived")
STALE_HOURS = 72
MAX_PROPOSALS = 5
KEEP_DAYS = 7
INTEGRATOR_PROFILE = "oscar-e2"
GH_EXE = r"C:\Program Files\GitHub CLI\gh.exe"
ACTIONS = ("comentar", "archivar", "reasignar", "resuelta")

REC_ARCHIVE, REC_KEEP = "rec_archive", "rec_keep"


def pr_refs(pr: dict) -> list[str]:
    """Ids de tarjeta con vínculo fuerte a un PR: rama lane/<id>, título o «Cierra: t_x» en el cuerpo."""
    found: list[str] = []
    branch = re.match(r"lane/(t_[0-9a-f]{8})$", str(pr.get("headRefName") or ""))
    candidates = ([branch.group(1)] if branch else []) + TID_RE.findall(str(pr.get("title") or "")) \
        + BODY_REF_RE.findall(str(pr.get("body") or ""))
    for tid in candidates:
        if tid not in found:
            found.append(tid)
    return found


def pr_outcome(pr: dict) -> str | None:
    """'merged' | 'closed' | None (abierto)."""
    if pr.get("mergedAt") or str(pr.get("state")).upper() == "MERGED":
        return "merged"
    return "closed" if str(pr.get("state")).upper() == "CLOSED" else None


def gh_prs(slugs: list[str], run=_proc.run, limit: int = 40) -> list[dict]:
    """PR recientes (abiertos, mergeados y cerrados) de cada repo. Solo lectura; un repo que falla se salta."""
    out: list[dict] = []
    for slug in slugs:
        try:
            cp = run([GH_EXE, "pr", "list", "--repo", slug, "--state", "all", "--limit", str(limit), "--json",
                      "number,title,body,headRefName,state,mergedAt,url"], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", timeout=120)
            if cp.returncode == 0:
                out += json.loads(cp.stdout or "[]")
            else:
                log.warning("reconciliación: gh pr list %s falló: %s", slug, (cp.stderr or "").strip()[:200])
        except Exception as exc:
            log.warning("reconciliación: gh pr list %s: %s", slug, exc)
    return out


def last_movement(show: dict) -> float | None:
    """Último movimiento de una tarjeta: el más reciente entre sus eventos, comentarios y su creación."""
    stamps = [e.get("created_at") for e in show.get("events") or ()] \
        + [c.get("created_at") for c in show.get("comments") or ()] + [(show.get("task") or {}).get("created_at")]
    nums = [float(s) for s in stamps if isinstance(s, (int, float)) and not isinstance(s, bool) and s]
    return max(nums) if nums else None


class _State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            pass
        for key in ("resolved", "kept", "proposed"):
            self.data.setdefault(key, {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(tmp, self.path)


class Reconciler:
    """`send(text, markup)` publica al canal de Oscar; `issue(rec, spec)` -> reply_markup con botones (CallbackStore)."""

    def __init__(self, hermes_for: Callable, boards: list[str], prs: Callable[[], list[dict]],
                 send: Callable[[str, dict | None], None], issue: Callable | None = None,
                 state_path: Path | str = STATE_PATH, clock: Callable[[], float] = time.time,
                 lane_for: Callable[[str], str] | None = None):
        self.hermes_for, self.boards, self.prs, self.send, self.issue = hermes_for, boards, prs, send, issue
        self.state = _State(Path(state_path))
        self.clock = clock
        self.lane_for = lane_for or (lambda board: "")

    def _find(self, tid: str) -> tuple[str, dict] | None:
        for board in self.boards:
            try:
                show = self.hermes_for(board).show(tid)
            except Exception:
                continue
            if show.get("task"):
                return board, show
        return None

    # --- (a) cierre automático -----------------------------------------------------------------------

    def close_from_prs(self) -> list[str]:
        """Cierra/archiva las tarjetas de PR ya resueltos. Devuelve los ids tratados (cada uno se avisa una vez)."""
        done: list[str] = []
        for pr in self.prs():
            outcome = pr_outcome(pr)
            if not outcome:
                continue
            for tid in pr_refs(pr):
                key = f"{tid}:{pr.get('number')}"
                if key in self.state.data["resolved"]:
                    continue
                found = self._find(tid)
                if not found:
                    continue
                board, show = found
                status = (show.get("task") or {}).get("status")
                self.state.data["resolved"][key] = self.clock()  # aunque ya estuviera cerrada: no se reevalúa
                if status in CLOSED_STATUSES or status == "running":
                    continue
                self._resolve(self.hermes_for(board), tid, show, pr, outcome, board)
                done.append(tid)
            self.state.save()
        return done

    def _resolve(self, h, tid: str, show: dict, pr: dict, outcome: str, board: str = "default") -> None:
        n, url = pr.get("number"), pr.get("url") or ""
        title = (show.get("task") or {}).get("title") or ""
        if outcome == "merged":
            where = f"PR #{n} (mergeado)"
            h.comment(tid, f"RECONCILIACIÓN: resuelta en {where} {url}".strip(), author="reconciliacion")
            ok, _ = h.complete(tid, f"Resuelta en {where}", {"reconciled": True, "pr": n})
            step = "ninguno: tarjeta cerrada" if ok else "ciérrala o archívala (no pude cerrarla sola)"
        else:
            where = f"PR #{n} (cerrado sin merge)"
            h.comment(tid, f"RECONCILIACIÓN: su {where} está cerrado; propongo archivarla {url}".strip(),
                      author="reconciliacion")
            self._propose(tid, board, title, f"su PR #{n} está cerrado sin merge")  # nunca archiva sola
            return
        self.send(f"🔗 {tid} · {title[:60]}\nResuelta en {where}.\n➡️ Próximo paso: {step} · Lo da: el sistema", None)

    # --- (b) pasada diaria de obsolescencia ----------------------------------------------------------

    def obsolete_pass(self) -> list[str]:
        """Propone archivar (con botón) las tarjetas abiertas obsoletas. Máx. MAX_PROPOSALS por pasada."""
        now = self.clock()
        by_pr: dict[str, tuple[dict, str]] = {}
        for pr in self.prs():
            if outcome := pr_outcome(pr):
                for tid in pr_refs(pr):
                    by_pr.setdefault(tid, (pr, outcome))
        proposals: list[tuple[str, str, str, dict]] = []  # tid, board, motivo, show
        for board in self.boards:
            h = self.hermes_for(board)
            for status in OPEN_STATUSES:
                try:
                    cards = h.list_status_all(status)
                except Exception as exc:
                    log.warning("reconciliación: no se pudo listar %s en %s: %s", status, board, exc)
                    continue
                for card in cards:
                    tid = card["id"]
                    if self.state.data["kept"].get(tid, 0) > now or tid in self.state.data["proposed"]:
                        continue
                    try:
                        show = h.show(tid)
                    except Exception:
                        continue
                    reason = self._obsolete_reason(h, tid, show, by_pr, now)
                    if reason:
                        proposals.append((tid, board, reason, show))
        sent = []
        for tid, board, reason, show in proposals[:MAX_PROPOSALS]:
            self._propose(tid, board, (show.get("task") or {}).get("title") or "", reason)
            sent.append(tid)
        if len(proposals) > MAX_PROPOSALS:
            self.send(f"🧹 Hay {len(proposals) - MAX_PROPOSALS} tarjetas obsoletas más; las propongo mañana.", None)
        self.state.save()
        return sent

    def _propose(self, tid: str, board: str, title: str, reason: str) -> None:
        """Avisa con [🗄 Archivar]/[⏳ Mantener] y la marca como propuesta (no se repite)."""
        text = (f"🧹 {tid} · {title[:60]}\nPosiblemente obsoleta: {reason}.\n"
                "➡️ Próximo paso: archivarla o mantenerla (un toque) · Lo da: Oscar")
        markup = None
        if self.issue:
            rec = {"task_id": tid, "board": board, "lane": self.lane_for(board), "title": title, "recon": True}
            markup = self.issue(rec, [[{"text": "🗄 Archivar", "action": REC_ARCHIVE},
                                       {"text": f"⏳ Mantener {KEEP_DAYS} d", "action": REC_KEEP}]])[1]
        self.send(text, markup)
        self.state.data["proposed"][tid] = self.clock()

    def _obsolete_reason(self, h, tid: str, show: dict, by_pr: dict, now: float) -> str | None:
        if tid in by_pr:
            pr, outcome = by_pr[tid]
            return f"su PR #{pr.get('number')} está {'mergeado' if outcome == 'merged' else 'cerrado sin merge'}"
        text = " ".join([(show.get("task") or {}).get("body") or ""] + [c.get("body") or ""
                                                                         for c in show.get("comments") or ()])
        for other in COVERED_RE.findall(text):
            if other == tid:
                continue
            found = self._find(other)
            if found and (found[1].get("task") or {}).get("status") == "done":
                return f"su alcance ya lo cubre {other} (hecha)"
        moved = last_movement(show)
        if moved and now - moved > STALE_HOURS * 3600:
            return f"lleva {int((now - moved) // 3600)} h sin movimiento"
        return None

    # --- botones -------------------------------------------------------------------------------------

    def on_button(self, action: str, rec: dict, where: dict | None, desk) -> bool:
        tid, h = rec["task_id"], self.hermes_for(rec["board"])
        who = desk._who()
        if action == REC_ARCHIVE:
            if not h.archive(tid):
                desk._edit_text(where, f"{tid}: no se pudo archivar (¿está en curso?)")
                return False
            h.comment(tid, f"Archivada por {who} (propuesta de obsolescencia)", author="oscar-telegram")
            desk._edit_text(where, f"🗄 {tid} archivada")
            return True
        self.state.data["kept"][tid] = self.clock() + KEEP_DAYS * 86400
        self.state.data["proposed"].pop(tid, None)
        self.state.save()
        h.comment(tid, f"Mantenida {KEEP_DAYS} d por {who} (no se vuelve a proponer antes)", author="oscar-telegram")
        desk._edit_text(where, f"⏳ {tid} se mantiene {KEEP_DAYS} d")
        return True


class ReconcileTicker:
    """Para el bucle del runner: cierre por PR cada `minutes`; obsolescencia una vez al día pasada la hora `hour`."""

    def __init__(self, rec: Reconciler, minutes: float = 10, hour: int = 9, clock: Callable[[], float] = time.time):
        self.rec, self.minutes, self.hour, self.clock = rec, minutes, hour, clock
        self._last = 0.0

    def tick(self) -> None:
        now = self.clock()
        if now - self._last < self.minutes * 60:
            return
        self._last = now
        try:
            self.rec.close_from_prs()
            day = time.strftime("%Y-%m-%d", time.localtime(now))
            if time.localtime(now).tm_hour >= self.hour and self.rec.state.data.get("last_sweep") != day:
                self.rec.state.data["last_sweep"] = day
                self.rec.obsolete_pass()
                self.rec.state.save()
        except Exception as exc:
            log.warning("reconciliación falló: %s", exc)


# --- (d) registro de decisiones del integrador -----------------------------------------------------------

def record_decision(h, action: str, tid: str, author: str, text: str = "", *, profile: str | None = None,
                    audit: Path | str | None = None, clock: Callable[[], float] = time.time) -> tuple[bool, str]:
    """Aplica una decisión tomada fuera del kanban y la deja auditada con su autor."""
    author = (author or "").strip()
    if action not in ACTIONS or not TID_RE.fullmatch(tid or "") or not author:
        return False, f"uso: tarjeta <{'|'.join(ACTIONS)}> <t_id> --autor <quien> [texto]"
    if action == "reasignar" and not profile:
        return False, "reasignar necesita --a <perfil>"
    if action == "comentar" and not text.strip():
        return False, "comentar necesita texto"
    note = f"REGISTRO por {author}: {action}" + (f" → {profile}" if profile else "") + (f" · {text.strip()}"
                                                                                      if text.strip() else "")
    if action == "archivar":
        ok = h.archive(tid)
    elif action == "reasignar":
        ok = h.assign(tid, profile)
    elif action == "resuelta":
        ok, _ = h.complete(tid, text.strip() or f"Resuelta fuera del kanban (registrada por {author})",
                           {"registered_by": author})
    else:
        ok = h.comment(tid, note, author=author)
    if not ok:
        return False, f"{tid}: el kanban rechazó «{action}» (¿estado incompatible?)"
    if action != "comentar":
        h.comment(tid, note, author=author)
    path = Path(audit or AUDIT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": clock(), "author": author, "action": action, "task": tid, "profile": profile,
                             "text": text.strip()}, ensure_ascii=False) + "\n")
    return True, f"{tid}: {action} registrado por {author}"


def main(argv: list[str], *, hermes_for: Callable | None = None, out: Callable[[str], None] = print) -> int:
    """`lanes.py tarjeta <accion> <t_id> --autor <quien> [--a perfil] [--board b] [texto]`."""
    ap = argparse.ArgumentParser(prog="lanes.py tarjeta")
    ap.add_argument("action", choices=ACTIONS)
    ap.add_argument("task_id")
    ap.add_argument("text", nargs="*")
    ap.add_argument("--autor", required=True)
    ap.add_argument("--a", dest="profile")
    ap.add_argument("--board", default="default")
    args = ap.parse_args(argv)
    if hermes_for is None:
        from .hermes import HermesCLI
        hermes_for = lambda board: HermesCLI(board, author=args.autor)  # noqa: E731
    ok, msg = record_decision(hermes_for(args.board), args.action, args.task_id, args.autor, " ".join(args.text),
                              profile=args.profile)
    out(msg)
    return 0 if ok else 1
