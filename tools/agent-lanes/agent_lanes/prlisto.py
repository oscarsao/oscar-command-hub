"""[PR LISTOS] Recordatorio periódico de PR de carriles abiertos que esperan a Oscar (t_2795df37).

El aviso diario de `atascos.PendingNotice` solo ve tarjetas aprobadas de carriles con lote (`batch: daily`), una vez
al día. Un PR sin aprobar, de un carril sin lote, en `pr_problem`, o que simplemente llega después de las 09:00, se
quedaba horas sin que nadie avisara. Este repaso mira GitHub directamente (no el kanban): todos los repos de los
carriles, PR abiertos de ramas `lane/*` con más de `pr_watch_hours` horas, y manda UN mensaje agrupado por estado:

  * MERGEABLE   -> listo para fusionar ya.
  * CONFLICTING -> necesita rebase.
  * UNKNOWN     -> GitHub aún no lo ha calculado: no avisa por sí solo, se vuelve a mirar en la pasada siguiente.

No repite el mismo conjunto antes de `pr_watch_repeat_hours`; si aparece un PR nuevo o cambia de estado, avisa ya.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
from datetime import datetime, timezone

from .notices import truncate

log = logging.getLogger("agent_lanes")

BRANCH_PREFIX = "lane/"
TITLE_MAX = 50
MAX_PER_SECTION = 15
STATE_FILE = "prwatch.json"
NOTICE_ID = "prwatch"
SKIP_STATUS = ("integrated", "merged", "deployed", "batch_included")  # ya fusionado o dentro de un lote abierto


def _age_hours(created: str, now: float) -> float | None:
    try:
        dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt.timestamp()) / 3600)


def _ago(hours: float) -> str:
    return f"{int(hours)} h" if hours < 48 else f"{int(hours // 24)} días"


class ReadyPRWatcher:
    def __init__(self, integrator):
        self.i = integrator
        self._lock = threading.Lock()

    # --- estado ---------------------------------------------------------------------------------------

    @property
    def _path(self):
        return self.i.state_dir / STATE_FILE

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _store(self, data: dict) -> None:
        self.i.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, self._path)

    # --- lectura de GitHub ----------------------------------------------------------------------------

    def _repos(self) -> dict[str, object]:
        """slug -> primer carril de ese repo (todos los carriles con repo en GitHub, con o sin política)."""
        out: dict[str, object] = {}
        links = self.i.links
        for lane in self.i.lanes.values():
            slug = links.repo_slug(lane) if links else None
            if slug and slug.lower() not in {s.lower() for s in out}:
                out[slug] = lane
        return out

    def _list(self, slug: str) -> list[dict] | None:
        cp = self.i._gh("pr", "list", "--repo", slug, "--state", "open", "--limit", "100", "--json",
                        "number,title,url,mergeable,isDraft,headRefName,createdAt,isCrossRepository")
        if cp.returncode != 0:
            log.warning("pr_watch: gh pr list %s falló: %s", slug, (cp.stderr or "").strip()[:200])
            return None
        try:
            data = json.loads(cp.stdout or "[]")
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, list) else None

    def entries(self) -> tuple[list[dict], object | None]:
        """PR de carriles con más de `pr_watch_hours` horas, ya clasificados. También devuelve el primer carril."""
        i, now = self.i, self.i._now()
        hours = float(i.settings.pr_watch_hours)
        found, first = [], None
        for slug, lane in self._repos().items():
            for pr in self._list(slug) or []:
                head = str(pr.get("headRefName") or "")
                if not head.startswith(BRANCH_PREFIX) or pr.get("isDraft") or pr.get("isCrossRepository"):
                    continue
                tid = head[len(BRANCH_PREFIX):]
                if i._state(tid).get("status") in SKIP_STATUS:
                    continue
                age = _age_hours(pr.get("createdAt") or "", now)
                if age is None or age < hours:
                    continue
                state = str(pr.get("mergeable") or "UNKNOWN").upper()
                found.append({"repo": slug.split("/")[-1], "slug": slug, "number": int(pr["number"]),
                              "title": pr.get("title") or head, "url": pr.get("url") or "", "age": age,
                              "state": state if state in ("MERGEABLE", "CONFLICTING") else "UNKNOWN"})
                first = first or lane
        found.sort(key=lambda e: (e["repo"], e["number"]))
        return found, first

    # --- aviso ----------------------------------------------------------------------------------------

    @staticmethod
    def _section(title: str, rows: list[dict]) -> list[str]:
        if not rows:
            return []
        lines = [title]
        for e in rows[:MAX_PER_SECTION]:
            link = f'<a href="{html.escape(e["url"], quote=True)}">#{e["number"]}</a>' if e["url"] else f"#{e['number']}"
            lines.append(f"• {html.escape(e['repo'])} {link} · {html.escape(truncate(e['title'], TITLE_MAX))} · "
                         f"{_ago(e['age'])}")
        if len(rows) > MAX_PER_SECTION:
            lines.append(f"… y {len(rows) - MAX_PER_SECTION} más")
        return lines

    @classmethod
    def render(cls, entries: list[dict]) -> str:
        by = {s: [e for e in entries if e["state"] == s] for s in ("MERGEABLE", "CONFLICTING", "UNKNOWN")}
        parts = ["🔔 PR de carriles esperando por ti"]
        for lines in (
                cls._section(f"✅ Listos para fusionar ({len(by['MERGEABLE'])}) · revísalos y fusiónalos:",
                             by["MERGEABLE"]),
                cls._section(f"⚠️ Con conflictos ({len(by['CONFLICTING'])}) · necesitan rebase antes de fusionar:",
                             by["CONFLICTING"]),
                cls._section(f"⏳ Pendientes de GitHub ({len(by['UNKNOWN'])}) · aún calcula si hay conflictos; "
                             "vuelvo a mirar luego:", by["UNKNOWN"])):
            if lines:
                parts += ["", *lines]
        return "\n".join(parts)

    def tick(self) -> bool:
        """Una comprobación por pasada del integrador. True = se mandó un aviso. Nunca lanza."""
        i = self.i
        if i.dry_run or not i.settings.enabled or float(i.settings.pr_watch_hours) <= 0:
            return False
        if not self._lock.acquire(blocking=False):
            return False
        try:
            entries, lane = self.entries()
            # UNKNOWN solo acompaña: por sí solo no merece un mensaje (se reintenta en la pasada siguiente).
            if not lane or not any(e["state"] != "UNKNOWN" for e in entries):
                return False
            sig = sorted(f"{e['slug']}#{e['number']}:{e['state']}" for e in entries)
            prev, now = self._load(), i._now()
            repeat = float(i.settings.pr_watch_repeat_hours) * 3600
            if prev.get("sig") == sig and now - float(prev.get("at") or 0) < repeat:
                return False
            self._store({"sig": sig, "at": now})  # antes de publicar: un fallo no se repite en bucle
            i._publish(lane, {"id": NOTICE_ID, "title": "PR esperando", "body": None}, self.render(entries),
                       link=f"🔔 {len(entries)} PR de carriles esperando")
            return True
        except Exception as exc:
            log.warning("pr_watch falló: %s", exc)
            return False
        finally:
            self._lock.release()
