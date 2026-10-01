"""Lote diario por proyecto del Integrador (`batch: daily` en la política del carril, lanes.yaml).

En vez de una ficha 🚦 y un botón por PR, las ramas lane/* aprobadas por Oscar se acumulan (estado `batch_pending`,
sin gates sueltos) y a la hora configurable (`batch_time`, 08:30) —o con `/lote <marca>`— se monta UN lote:

  1. rama `release/lote-<marca>-<fecha>` desde <remote>/<base>, en un worktree temporal `int-lote-*`;
  2. se fusionan en ella, EN ORDEN DE APROBACIÓN, las ramas aprobadas. Cada rama pasa sus gates (conflicto, rutas
     vetadas, ejecutables, secretos, un solo head de Alembic); la que choca o falla queda FUERA, con su motivo, y no
     bloquea a las demás;
  3. gates del lote combinado: test_cmd del carril y, si el lote toca `batch_frontend_paths`, `batch_frontend_cmd`
     (build/typecheck). Si falla, cada rama se prueba sola: la culpable sale del lote y se reprueba el resto;
  4. se empuja SOLO esa rama `release/lote-*` y se abre un único PR hacia la base;
  5. UNA ficha en 🚦 con la tabla (incluye / fuera y por qué) y un botón [🔀 Fusionar lote]. Al pulsarlo se fusiona
     el PR del lote (`--squash --match-head-commit`), cada rama recibe `INTEGRADO <sha>`, sus PR sueltos se cierran y
     sigue el flujo de siempre del carril (deploy/aplicar/verificar) a nivel de lote. La migración de cualquier rama
     del lote se agrega: [✅ Migración aplicada] vale para todo el lote.

Nunca: --force, --admin, push a la base ni a ninguna rama que no sea `release/lote-*` (el push va sin hooks).
Sin `batch` en la política, nada de esto actúa: el integrador sigue PR a PR.

Prueba en seco (sin push, sin PR, sin Telegram, sin comentarios):
    py -3.12 -m agent_lanes.integrator --dry-run --lane claude-oscarhq --batch 12,15
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from .integration import header, phase_for, repo_name, risk_of
from .integrator import (INT_BATCH_MERGE, INTEGRATED_PREFIX, MERGE_RETRY_WAITS, NEEDS_MIGRATION, PR_URL_RE, TID_RE,
                         GateResult, Policy, alembic_heads, safe_env)
from .notices import TEXT_MAX, status_line, truncate
from .verify import render_test_cmd

log = logging.getLogger("agent_lanes")

BATCH_LABEL = "🔀 Fusionar lote"
BATCH_BRANCH_RE = re.compile(r"release/lote-[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}-\d{2}-\d{2}(?:-\d+)?")
OPEN_STATUSES = ("offered", "stale")  # lote con PR abierto pendiente del botón
IDENT = ("-c", "user.name=agent-lanes-integrator", "-c", "user.email=agent-lanes@localhost", "-c", "commit.gpgsign=false")


def brand_of(policy: Policy) -> str:
    return re.sub(r"[^a-z0-9]+", "", (policy.batch_brand or policy.lane.removeprefix("claude-")).lower()) or "lote"


def render_batch_ficha(*, risk, phase: str, rec: dict, status: str, repo: str, base: str,
                       override: str | None = None, links=()) -> str:
    """Ficha única del lote (HTML): qué incluye, qué queda fuera y por qué. Se recortan listas, nunca el HTML."""
    e = html.escape
    members = [list(m) for m in rec.get("batch_members") or ()]
    excluded = [list(x) for x in rec.get("batch_excluded") or ()]
    head = [f"<b>{e(header(risk, phase, pr=rec.get('pr_number'), override=override))}</b>",
            f"Lote {e(rec.get('batch_brand') or '')} · {e(rec.get('batch_date') or '')}",
            f"{e(repo or '?')} · {e(rec.get('batch_branch') or '')} → {e(base or '?')}"]
    gates, risks = list(rec.get("gates") or ()), list(rec.get("risks") or ())
    tail = [e(status)] if status else []
    if links:
        tail.append(" · ".join(f'<a href="{e(url, quote=True)}">{e(label)}</a>' for label, url in links))

    def body() -> list[str]:
        out = [f"Incluye ({len(members)}):"] + [f"• #{e(str(n))} · {e(truncate(t, 60))}" for n, t in members]
        if excluded:
            out.append(f"Fuera del lote ({len(excluded)}):")
            out += [f"• #{e(str(n))} · {e(truncate(t, 40))} — {e(truncate(r, 120))}" for n, t, r in excluded]
        if gates:
            out.append("Gates: " + e(" · ".join(gates)))
        out.append("Riesgos: " + (e(" · ".join(risks)) if risks else "ninguno"))
        if rec.get("deploy_note"):
            out.append("Deploy: " + e(rec["deploy_note"]))
        return out

    while len("\n".join(head + body() + tail)) > TEXT_MAX and (members or excluded or gates or risks):
        for lst in (gates, risks, excluded, members):
            if len(lst) > (1 if lst is members else 0):
                lst.pop()
                break
        else:
            break
    return "\n".join(head + body() + tail)


class BatchPlanner:
    """Montaje y fusión de lotes. Usa los métodos privados del Integrator (`self.i`) para git/gh/estado/avisos."""

    def __init__(self, integrator, clock: Callable[[], datetime] = datetime.now):
        self.i = integrator
        self.clock = clock  # hora LOCAL de pared (batch_time)
        self._asm = threading.Lock()

    # --- estado ----------------------------------------------------------------------------------------

    @property
    def dir(self) -> Path:
        return self.i.state_dir / "batches"

    def _state(self, bid: str) -> dict:
        try:
            return json.loads((self.dir / f"lote-{bid}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, bid: str, **data) -> None:
        if self.i.dry_run:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"lote-{bid}.json"
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({**self._state(bid), **data, "id": bid, "updated": time.time()}), encoding="utf-8")
        os.replace(tmp, path)

    def all(self) -> list[dict]:
        out = []
        for path in sorted(self.dir.glob("lote-*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return out

    def _day(self, lane_name: str) -> str:
        try:
            return json.loads((self.dir / f"day-{lane_name}.json").read_text(encoding="utf-8")).get("day") or ""
        except (OSError, json.JSONDecodeError):
            return ""

    def _mark_day(self, lane_name: str, day: str) -> None:
        if self.i.dry_run:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"day-{lane_name}.json").write_text(json.dumps({"day": day}), encoding="utf-8")

    def resolve(self, arg: str) -> tuple | None:
        """(carril, política) con `batch` cuya marca o nombre de carril es `arg` (/lote migrateam)."""
        want = re.sub(r"[^a-z0-9-]+", "", (arg or "").lower())
        for name, policy in self.i.settings.policies.items():
            if policy.batch and want in (brand_of(policy), name) and self.i.lanes.get(name):
                return self.i.lanes[name], policy
        return None

    def brands(self) -> list[str]:
        return sorted(brand_of(p) for p in self.i.settings.policies.values() if p.batch)

    # --- pasada ----------------------------------------------------------------------------------------

    def run_due(self) -> None:
        """Cada pasada: concilia lotes abiertos y monta el del día si ya pasó `batch_time`."""
        i = self.i
        if not (i.settings.enabled or i.dry_run):
            return
        self.reconcile()
        now = self.clock()
        for name, policy in i.settings.policies.items():
            lane = i.lanes.get(name)
            if not policy.batch or not lane:
                continue
            hh, mm = (int(x) for x in policy.batch_time.split(":"))
            today = now.strftime("%Y-%m-%d")
            if (now.hour, now.minute) < (hh, mm) or self._day(name) == today:
                continue
            self._mark_day(name, today)  # antes de montar: un fallo no se repite cada minuto (queda /lote)
            try:
                log.info("lote diario %s: %s", name, self.assemble(lane, policy))
            except Exception as exc:
                log.warning("lote diario %s falló: %s", name, exc)

    def reconcile(self) -> None:
        """Lotes con PR abierto: cerrado sin fusionar -> sus ramas vuelven a la cola; fusionado fuera del integrador
        (a mano en GitHub) -> se dan por integradas."""
        i = self.i
        for b in self.all():
            lane, policy = i.lanes.get(b.get("lane")), i.settings.policies.get(b.get("lane"))
            if b.get("status") not in OPEN_STATUSES or not lane or not policy:
                continue
            slug = i.links.repo_slug(lane) if i.links else None
            pr = i._pr_view(slug, int(b["pr"])) if slug else None
            if not pr:
                continue
            if pr.get("state") == "MERGED":
                sha = ((pr.get("mergeCommit") or {}).get("oid") or "").strip()
                self._finish(lane, policy, b, sha, outside=True)
            elif pr.get("state") == "CLOSED":
                self._release(b, "closed")

    def _release(self, b: dict, status: str) -> None:
        """Las ramas de un lote que no se fusionó vuelven a la cola del siguiente."""
        self._save(b["id"], status=status)
        for m in b.get("members") or ():
            if self.i._state(m["tid"]).get("status") == "batch_included":
                self.i._save(m["tid"], status="batch_pending", batch=None)

    # --- montaje ---------------------------------------------------------------------------------------

    def _candidates(self, lane) -> list[dict]:
        out = []
        for path in sorted(self.i.state_dir.glob("t_*.json")):
            tid = path.stem
            st = self.i._state(tid)
            if TID_RE.fullmatch(tid) and st.get("lane") == lane.name and st.get("status") == "batch_pending" \
                    and st.get("pr") and re.fullmatch(r"[0-9a-f]{40}", st.get("head_sha") or ""):
                out.append({"tid": tid, "pr": int(st["pr"]), "url": st.get("pr_url") or "", "title": st.get("title") or tid,
                            "head_sha": st["head_sha"], "at": float(self.i._approved_record(tid).get("at") or 0)})
        return sorted(out, key=lambda c: (c["at"], c["tid"]))

    def _validate(self, lane, slug: str, cands: list[dict], excluded: list[dict]) -> list[dict]:
        """El PR sigue abierto, es de su rama y su cabeza es la que aprobó Oscar y revisó el carril review."""
        i, ok = self.i, []
        for c in cands:
            pr = i._pr_view(slug, c["pr"])
            try:
                show = i.hermes_for(lane.board).show(c["tid"])
            except Exception as exc:
                show = {}
                log.warning("%s: no se pudo leer la tarjeta: %s", c["tid"], exc)
            problem = i._pr_problem(lane, c["tid"], pr) or i._approval_problem(c["tid"], c["pr"], pr, show)
            if problem:
                excluded.append({**c, "reason": problem})
            else:
                ok.append(c)
        return ok

    def _new_id(self, lane, policy: Policy, day: str) -> tuple[str, str]:
        used = {b.get("id") for b in self.all()}
        for n in range(1, 30):
            bid = f"{brand_of(policy)}-{day}" + (f"-{n}" if n > 1 else "")
            branch = f"release/lote-{bid}"
            if bid in used:
                continue
            if self.i._git(lane.repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0:
                continue
            if (self.i._remote_sha(lane, branch) or ""):
                continue
            return bid, branch
        raise RuntimeError("demasiados lotes en un día")

    def assemble(self, lane, policy: Policy, *, manual: bool = False) -> str:
        """Monta el lote del carril y lo ofrece. Devuelve un texto llano (lo usa /lote y el log)."""
        if not self._asm.acquire(blocking=False):  # la hora y /lote a la vez: uno solo monta
            return "Ya se está montando un lote; espera a que termine."
        try:
            return self._assemble(lane, policy)
        finally:
            self._asm.release()

    def _assemble(self, lane, policy: Policy) -> str:
        i = self.i
        slug = i.links.repo_slug(lane) if i.links else None
        if not slug:
            return "No sé a qué repositorio de GitHub corresponde este carril."
        brand = brand_of(policy)
        opened = [b for b in self.all() if b.get("lane") == lane.name and b.get("status") in OPEN_STATUSES]
        for b in opened:
            fresh = i._remote_sha(lane, lane.base) == b.get("base_sha") and b.get("status") == "offered"
            if fresh and not self._candidates(lane):
                return f"Ya hay un lote abierto de {brand} (PR #{b['pr']}); fusiónalo o ciérralo antes de montar otro."
            # Hay ramas nuevas o la base se movió: el lote abierto ya no es lo que se probaría hoy. Se cierra el PR
            # (no se borra la rama) y sus ramas vuelven a la cola para montar uno nuevo.
            self._supersede(lane, slug, b)
        excluded: list[dict] = []
        cands = self._validate(lane, slug, self._candidates(lane), excluded)
        if not cands:
            return f"No hay nada aprobado esperando lote de {brand}."
        day = self.clock().strftime("%Y-%m-%d")
        bid, branch = self._new_id(lane, policy, day)
        with i._lock:  # worktrees temporales y fetch: una operación git a la vez
            build = self._build(lane, policy, cands, excluded, bid, branch)
        if not build["ok"]:
            for x in build["excluded"]:
                self._note_excluded(lane, x, bid)
            return (f"El lote de {brand} no se pudo montar: " + "; ".join(build["reasons"])) if build["reasons"] \
                else f"El lote de {brand} quedó vacío: ninguna rama pasó los gates."
        return self._publish(lane, policy, slug, bid, branch, day, build)

    def _supersede(self, lane, slug: str, b: dict) -> None:
        if not self.i.dry_run:
            self.i._gh("pr", "close", str(b["pr"]), "--repo", slug, "--comment",
                       "Sustituido por un lote nuevo (ramas nuevas o la base se movió).")
        self._release(b, "superseded")

    def _build(self, lane, policy: Policy, cands: list[dict], excluded: list[dict], bid: str, branch: str) -> dict:
        i = self.i
        out = {"ok": False, "members": [], "excluded": list(excluded), "reasons": [], "passed": [], "files": [],
               "migration": None, "sensitive": [], "base_sha": "", "head_sha": "", "branch": branch}
        repo, remote, base = lane.repo, lane.remote, lane.base
        refs = [f"+refs/pull/{c['pr']}/head:refs/integrator/pr-{c['pr']}" for c in cands]
        cp = i._git(repo, "fetch", remote, f"+refs/heads/{base}:refs/remotes/{remote}/{base}", *refs)
        if cp.returncode != 0:
            out["reasons"].append("no se pudieron descargar los PR de GitHub")
            log.warning("lote %s: git fetch: %s", bid, (cp.stderr or "").strip()[:300])
            return out
        live = []
        for c in cands:
            if (i._git(repo, "rev-parse", f"refs/integrator/pr-{c['pr']}").stdout or "").strip() != c["head_sha"]:
                out["excluded"].append({**c, "reason": "cambió mientras se montaba el lote"})
            else:
                live.append(c)
        out["base_sha"] = base_sha = (i._git(repo, "rev-parse", f"{remote}/{base}").stdout or "").strip()
        wt = Path(i.settings.worktree_root) / f"int-lote-{bid}"
        i._remove_worktree(repo, wt)
        cp = i._git(repo, "-c", f"core.hooksPath={i._nohooks()}", "worktree", "add", "-b", branch, str(wt), base_sha)
        if cp.returncode != 0:
            out["reasons"].append("no se pudo preparar la copia de trabajo del lote")
            log.warning("lote %s: worktree add: %s", bid, (cp.stderr or "").strip()[:300])
            return out
        try:
            self._build_in(str(wt), wt, lane, policy, live, out)
        finally:
            i._remove_worktree(repo, wt)
        return out

    def _merge(self, w: str, sha: str) -> subprocess.CompletedProcess:
        return self.i._git(w, "-c", f"core.hooksPath={self.i._nohooks()}", *IDENT, "merge", "--no-ff", "--no-edit", sha)

    def _abort(self, w: str) -> None:
        self.i._git(w, "-c", f"core.hooksPath={self.i._nohooks()}", "merge", "--abort")

    def _head(self, w: str) -> str:
        return (self.i._git(w, "rev-parse", "HEAD").stdout or "").strip()

    def _files(self, w: str, a: str, b: str, three: bool = True) -> list[str]:
        rng = f"{a}...{b}" if three else f"{a}..{b}"
        return (self.i._git(w, "diff", "--no-renames", "--name-only", rng).stdout or "").split()

    def _build_in(self, w: str, wt: Path, lane, policy: Policy, cands: list[dict], out: dict) -> None:
        i, base_sha = self.i, out["base_sha"]
        members: list[dict] = []
        for c in cands:
            prev = self._head(w)
            m = self._merge(w, c["head_sha"])
            if m.returncode != 0:
                conflicts = (i._git(w, "diff", "--name-only", "--diff-filter=U").stdout or "").split()
                self._abort(w)
                out["excluded"].append({**c, "reason": f"choca con {lane.base} o con otra rama del lote" +
                                        (f" ({', '.join(conflicts[:3])})" if conflicts else "")})
                continue
            res = GateResult(ok=False, head_sha=c["head_sha"], base_sha=base_sha)
            res.changed_files = self._files(w, base_sha, c["head_sha"])
            i._diff_gates(w, lane, policy, res, f"{base_sha}...{c['head_sha']}")
            reasons = list(res.reasons)
            if not reasons and policy.alembic_versions and any(f.startswith(policy.alembic_versions)
                                                              for f in res.changed_files):
                heads, problems = alembic_heads(wt / policy.alembic_versions)
                if len(heads) != 1 or problems:
                    reasons.append(f"su migración de Alembic choca con otra del lote ({len(heads)} heads)")
            if reasons:
                i._git(w, "reset", "--hard", prev)
                out["excluded"].append({**c, "reason": "; ".join(reasons)})
                continue
            members.append({**c, "files": res.changed_files})
        if not members:
            return
        reasons = self._verify(w, lane, policy, self._files(w, base_sha, "HEAD", three=False))
        if reasons and len(members) > 1:
            members, reasons = self._cull(w, wt, lane, policy, members, out, reasons)
        out["files"] = self._files(w, base_sha, "HEAD", three=False)
        out["head_sha"] = self._head(w)
        if reasons or not members:
            out["reasons"] += reasons or ["ninguna rama pasa los gates"]
            return
        files = out["files"]
        out["sensitive"] = [f for f in files if any(f.startswith(p) or Path(f).name == p for p in policy.sensitive_paths)]
        if policy.alembic_versions and any(f.startswith(policy.alembic_versions) for f in files):
            out["migration"] = "alembic"
        if any(f.startswith(p) for p in policy.manual_migrations for f in files):
            out["migration"] = out["migration"] or "supabase"
        if out["sensitive"]:
            out["migration"] = out["migration"] or "infra"
        out["passed"] = [f"sin conflictos con {lane.base}", "sin secretos"] + (["tests OK"] if lane.test_cmd else []) \
            + (["Alembic con un solo head"] if out["migration"] == "alembic" else []) \
            + (["build/typecheck del frontend OK"] if self._frontend(policy, files) else [])
        out["members"], out["ok"] = members, True

    def _frontend(self, policy: Policy, files: list[str]) -> bool:
        return bool(policy.batch_frontend_cmd and any(f.startswith(p) for p in policy.batch_frontend_paths
                                                      for f in files))

    def _verify(self, w: str, lane, policy: Policy, files: list[str]) -> list[str]:
        """test_cmd del carril y, si se tocó el frontend, su build/typecheck. Motivos en lenguaje llano ([] = OK)."""
        i, reasons = self.i, []
        checks = [("los tests del carril fallan con el lote", render_test_cmd(lane))] if lane.test_cmd else []
        if self._frontend(policy, files):
            checks.append(("el build/typecheck del frontend falla con el lote", policy.batch_frontend_cmd))
        for why, cmd in checks:
            try:
                t = i._exec(cmd, cwd=w, timeout=1800, shell=True, env=safe_env())
            except subprocess.TimeoutExpired:
                reasons.append(why.replace("falla", "tarda más de 30 min").replace("fallan", "tardan más de 30 min"))
                continue
            if t.returncode != 0:
                reasons.append(why)
                log.warning("lote: %s (exit %s): %s", cmd[:80], t.returncode, ((t.stdout or "") + (t.stderr or ""))[-1500:])
        return reasons

    def _cull(self, w: str, wt: Path, lane, policy: Policy, members: list[dict], out: dict, reasons: list[str]):
        """El lote combinado falla: cada rama se prueba sola sobre la base; la que falla sola sale del lote y se
        vuelve a probar el resto. Si aun así falla, no hay lote (cada rama pasa sola pero juntas no)."""
        i, base_sha = self.i, out["base_sha"]
        bad = []
        for m in members:
            i._git(w, "reset", "--hard", base_sha)
            if self._merge(w, m["head_sha"]).returncode != 0:
                self._abort(w)
                continue
            if self._verify(w, lane, policy, m["files"]):
                bad.append(m)
        good = [m for m in members if m not in bad]
        for m in bad:
            out["excluded"].append({**m, "reason": "rompe los tests o el build del lote (probada sola)"})
        i._git(w, "reset", "--hard", base_sha)
        for m in good:
            if self._merge(w, m["head_sha"]).returncode != 0:
                self._abort(w)
                return [], ["no se pudo recomponer el lote tras apartar ramas"]
        if not good:
            return [], reasons
        return good, (self._verify(w, lane, policy, self._files(w, base_sha, "HEAD", three=False)) if bad else reasons)

    # --- publicación -----------------------------------------------------------------------------------

    def _push(self, lane, branch: str) -> subprocess.CompletedProcess:
        """Único push del integrador: una rama `release/lote-*`, sin --force y sin hooks."""
        if not BATCH_BRANCH_RE.fullmatch(branch):
            raise PermissionError(f"el integrador solo empuja ramas release/lote-*: {branch}")
        return self.i._run(["git", "-C", lane.repo, "-c", f"core.hooksPath={self.i._nohooks()}", "push", "--no-verify",
                            lane.remote, f"refs/heads/{branch}:refs/heads/{branch}"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=300, stdin=subprocess.DEVNULL)

    def _table(self, build: dict) -> str:
        rows = [f"- #{m['pr']} · {m['title']} (`{m['tid']}`)" for m in build["members"]]
        out = ["## Incluye", *rows]
        if build["excluded"]:
            out += ["", "## Fuera del lote", *[f"- #{x['pr']} · {x['title']} — {x['reason']}" for x in build["excluded"]]]
        out += ["", "Gates del lote combinado: " + ", ".join(build["passed"])]
        return "\n".join(out)

    def _publish(self, lane, policy: Policy, slug: str, bid: str, branch: str, day: str, build: dict) -> str:
        i, brand = self.i, brand_of(policy)
        members, excluded = build["members"], build["excluded"]
        pushed = self._push(lane, branch)
        i._git(lane.repo, "branch", "-D", branch)  # la rama local ya no hace falta (el worktree ya no existe)
        if pushed.returncode != 0:
            log.warning("lote %s: push falló: %s", bid, (pushed.stderr or "").strip()[:300])
            return f"El lote de {brand} está montado pero no se pudo subir a GitHub; detalle en el log."
        cp = i._gh("pr", "create", "--repo", slug, "--base", lane.base, "--head", branch,
                   "--title", f"Lote {brand} {day} ({len(members)} PR)", "--body", self._table(build))
        m = PR_URL_RE.search(cp.stdout or "") if cp.returncode == 0 else None
        if not m:
            log.warning("lote %s: gh pr create falló: %s", bid, (cp.stderr or "").strip()[:300])
            return f"El lote de {brand} se subió ({branch}) pero no se pudo abrir el PR; detalle en el log."
        number, url = int(m.group(2)), m.group(0)
        tids = [x["tid"] for x in members]
        rec = {"kind": "integrate", "task_id": tids[0], "board": lane.board, "lane": lane.name,
               "title": f"Lote {brand} {day}", "pr_number": number, "pr_url": url, "head_sha": build["head_sha"],
               "base_sha": build["base_sha"], "migration": build["migration"], "batch": bid, "batch_brand": brand,
               "batch_date": day, "batch_branch": branch, "batch_tids": tids,
               "batch_members": [[x["pr"], x["title"]] for x in members],
               "batch_excluded": [[x["pr"], x["title"], x["reason"]] for x in excluded],
               "gates": [f"✔ {p}" for p in build["passed"]],
               "risks": i._risk_lines(GateResult(ok=True, migration=build["migration"], sensitive=build["sensitive"])),
               "deploy_note": i._deploy_note(policy, GateResult(ok=True, migration=build["migration"]))}
        self._save(bid, lane=lane.name, brand=brand, date=day, branch=branch, pr=number, pr_url=url, status="offered",
                   head_sha=build["head_sha"], base_sha=build["base_sha"], migration=build["migration"],
                   members=[{k: x[k] for k in ("tid", "pr", "url", "title", "head_sha")} for x in members],
                   excluded=[{k: x[k] for k in ("tid", "pr", "title", "reason")} for x in excluded], rec=rec)
        for x in members:
            i._save(x["tid"], status="batch_included", batch=bid, batch_pr=number)
        for x in excluded:
            self._note_excluded(lane, x, bid)
        i._comment(lane, tids[0], f"INTEGRADOR: lote {bid} montado · PR #{number} · incluye "
                   + ", ".join(f"#{x['pr']}" for x in members) + " · esperando a Oscar")
        for x in members[1:]:
            i._comment(lane, x["tid"], f"INTEGRADOR: va en el lote {bid} (PR #{number}) · esperando a Oscar")
        markup = None
        if i.desk and not (policy.deploy == "on_merge" and build["migration"]):
            markup = i.desk.store.issue(rec, [[{"text": BATCH_LABEL, "action": INT_BATCH_MERGE}]])[1]
        hint = "pulsa para fusionar el lote" if markup else (
            "sin botón: " + NEEDS_MIGRATION if i.desk else "sin bot de carriles: fusiona a mano")
        task = {"id": tids[0], "title": rec["title"], "body": None}
        i._publish(lane, task, i._text("done", lane, rec, status_line(f"🚦 Lote listo · PR #{number}", hint),
                                       phase="LISTO PARA INTEGRAR"), markup, link=f"🚦 Lote {brand} · PR #{number}")
        return f"Lote de {brand} montado: PR #{number} con {len(members)} rama(s)" + (
            f"; {len(excluded)} fuera" if excluded else "")

    def _note_excluded(self, lane, x: dict, bid: str) -> None:
        if not self.i.dry_run:
            self.i._comment(lane, x["tid"], f"INTEGRADOR: PR #{x['pr']} queda FUERA del lote {bid}: {x['reason']}")

    def ficha(self, lane, rec: dict, state: str, status: str, *, phase: str | None = None,
              override: str | None = None) -> str:
        i = self.i
        phase = phase or phase_for(state, status)
        return render_batch_ficha(risk=risk_of(i.settings.policies.get(lane.name)), phase=phase, rec=rec,
                                  status=status, repo=repo_name(lane), base=lane.base, override=override or ("⛔" if state == "blocked" else None),
                                  links=[(f"PR lote #{rec['pr_number']}", rec["pr_url"])])

    # --- botón -----------------------------------------------------------------------------------------

    def on_merge_button(self, lane, policy: Policy, rec: dict, where: dict, desk) -> bool:
        """[🔀 Fusionar lote]. True = acción terminada (con su aviso); False = no se hizo nada y vuelven los botones."""
        i, bid = self.i, rec.get("batch")
        b = self._state(bid or "")
        slug = i.links.repo_slug(lane) if i.links else None
        if not b or b.get("status") != "offered" or not slug:
            i._edit(desk, lane, rec, where, "blocked", status_line("⛔ este lote ya no está abierto",
                                                                  f"/lote {b.get('brand') or ''} monta uno nuevo".strip()))
            return True
        number = int(b["pr"])
        pr = i._pr_view(slug, number) or {}
        problem = None
        if pr.get("state") != "OPEN" or pr.get("isCrossRepository") or pr.get("headRefName") != b["branch"] \
                or pr.get("baseRefName") != lane.base:
            problem = "el PR del lote ya no es el que se montó"
        elif pr.get("headRefOid") != b["head_sha"]:
            problem = "el PR del lote tiene commits nuevos desde los gates"
        elif i._remote_sha(lane, lane.base) != b["base_sha"]:
            problem = f"{lane.base} cambió desde que se montó el lote"
        if problem:
            self._save(bid, status="stale")
            i._edit(desk, lane, rec, where, "blocked", status_line(f"⛔ lote no fusionado PR #{number}", problem,
                                                                  f"/lote {b.get('brand')} lo monta de nuevo"))
            return True
        i._edit(desk, lane, rec, where, "running", status_line(f"🔀 fusionando lote PR #{number}…"))
        self._save(bid, status="merging")
        args = ["pr", "merge", str(number), "--repo", slug, "--squash", "--match-head-commit", b["head_sha"],
                "--delete-branch"]
        cp = i._gh(*args, timeout=180)
        for wait in MERGE_RETRY_WAITS:  # otro PR se fusionó entre medias ("Base branch was modified")
            if cp.returncode == 0 or "base branch was modified" not in (cp.stderr or "").lower():
                break
            i._sleep(wait)
            cp = i._gh(*args, timeout=180)
        after = i._pr_view(slug, number) or {}
        if cp.returncode != 0 and after.get("state") != "MERGED":
            log.warning("lote %s: gh pr merge falló: %s", bid, (cp.stderr or "").strip()[:300])
            self._save(bid, status="offered")
            i._edit(desk, lane, rec, where, "blocked", status_line(
                f"⛔ GitHub no aceptó la fusión del lote PR #{number}", "detalle en el log", "puedes reintentar"))
            return False
        sha = ((after.get("mergeCommit") or {}).get("oid") or "").strip()
        rec = {**rec, "merge_sha": sha, "migration": b.get("migration")}
        self._finish(lane, policy, b, sha, outside=cp.returncode != 0)
        return i._after_merge(lane, policy, rec, where, desk, sha)

    def _finish(self, lane, policy: Policy, b: dict, sha: str, *, outside: bool) -> None:
        """Lote fusionado: cada rama recibe INTEGRADO y su estado; sus PR sueltos se cierran como integrados."""
        i = self.i
        slug = i.links.repo_slug(lane) if i.links else None
        tids = [m["tid"] for m in b.get("members") or ()]
        self._save(b["id"], status="merged", merge_sha=sha)
        for m in b.get("members") or ():
            i._save(m["tid"], status="merged", merge_sha=sha, migration=b.get("migration"), batch=b["id"],
                    batch_tids=tids)
            i._comment(lane, m["tid"], f"{INTEGRATED_PREFIX} {sha or '?'} · PR {m.get('url') or m['pr']} · en el lote "
                       f"{b['id']} (PR #{b['pr']})" + (" (fusionado fuera del integrador)" if outside else ""))
            if slug and not i.dry_run:
                cp = i._gh("pr", "close", str(m["pr"]), "--repo", slug, "--comment",
                           f"Integrado en el lote {b['id']} (PR #{b['pr']}, {sha[:7] or '?'}).")
                if cp.returncode != 0:
                    log.warning("%s: no se pudo cerrar el PR #%s tras el lote: %s", m["tid"], m["pr"],
                                (cp.stderr or "").strip()[:200])

    # --- prueba en seco --------------------------------------------------------------------------------

    def dry_run(self, lane, policy: Policy, numbers: list[int]) -> int:
        """Monta el lote con estos PR (en este orden) y cuenta qué incluiría. Sin push, PR, estado ni Telegram."""
        i, out = self.i, self.i._out
        slug = i.links.repo_slug(lane) if i.links else None
        cands, excluded = [], []
        for n in numbers:
            pr = i._pr_view(slug, n) if slug else None
            if not pr:
                excluded.append({"tid": f"pr{n}", "pr": n, "title": f"PR #{n}", "reason": "no se pudo leer el PR",
                                 "url": "", "head_sha": "", "at": 0})
                continue
            tid = (pr.get("headRefName") or "").removeprefix("lane/") or f"pr{n}"
            cands.append({"tid": re.sub(r"[^\w-]", "_", tid), "pr": n, "url": pr.get("url") or "", "title": f"PR #{n}",
                          "head_sha": pr["headRefOid"], "at": 0})
        bid, branch = f"{brand_of(policy)}-dryrun", f"release/lote-{brand_of(policy)}-dryrun-0000-00-00"
        with i._lock:
            build = self._build(lane, policy, cands, excluded, bid, branch)
        i._git(lane.repo, "branch", "-D", branch)
        out(f"[dry-run] lote {brand_of(policy)} sobre {lane.base} @ {build['base_sha'][:12]}: "
            + ("OK" if build["ok"] else "SIN LOTE"))
        for m in build["members"]:
            out(f"  ✔ incluye #{m['pr']} ({m['tid']})")
        for x in build["excluded"]:
            out(f"  ⛔ fuera #{x['pr']}: {x['reason']}")
        for r in build["reasons"]:
            out(f"  ⛔ {r}")
        if build["ok"]:
            out("  gates: " + ", ".join(build["passed"]) + (f" · migración: {build['migration']}" if build["migration"] else ""))
            out("  botón: " + BATCH_LABEL)
        return 0 if build["ok"] else 1
