"""[ATASCOS RÁPIDOS] Que los PR aprobados no esperen días (t_86c09458).

Tres piezas, todas del integrador:
  * `PendingNotice`: aviso diario «🚦 N PR aprobados esperando lote» con [🔀 Fusionar lote] y una línea por PR.
  * `AutoResolver`: conflicto SOLO en ficheros append-only (CHANGELOG, ledger…) -> fusiona la base en la RAMA DEL PR
    con el driver `union`, pasa los gates y empuja a esa rama (nunca a la base, nunca con force). Mezclado con
    código no toca nada: devuelve la rama a su carril.
  * `pr_branch_card_body`: texto de las tarjetas de rebase/arreglo, que trabajan sobre la rama del PR original
    (`Rama-origen` + `Rama-push` acotados a esa tarjeta; el guard sigue prohibiendo main/master y el force).
"""
from __future__ import annotations

import fnmatch
import html
import logging
import re
import threading
from pathlib import Path

from .notices import truncate

log = logging.getLogger("agent_lanes")

# Ficheros que solo crecen por el final: un conflicto aquí se resuelve juntando las dos partes (union).
DEFAULT_APPEND_ONLY = ("docs/kb/CHANGELOG.md", "docs/kb/LEDGER_ESTATUS.md", "docs/kb/archivo/CHANGELOG.md",
                       "CHANGELOG.md")
NOTICE_HOUR = 9           # hora local desde la que sale el aviso diario
NOTICE_TITLE_MAX = 50
TID_BRANCH_RE = re.compile(r"t_[0-9A-Za-z]{1,40}")


def is_append_only(path: str, patterns) -> bool:
    return any(path == p or fnmatch.fnmatchcase(path, p) for p in patterns or ())


def pr_branch_card_body(branch: str, number: int, base: str, lote: str, *, rebase: bool, reason: str = "",
                        origin: str = "") -> str:
    """Cuerpo de una tarjeta que trabaja sobre la rama del PR original (`branch`): Rama-origen = la rama del PR y
    Rama-push = esa rama. El worker empuja con `git push origin HEAD:<branch>` (sin force: solo fast-forward)."""
    what = (f"Rebasar la rama `{branch}` (PR #{number}) sobre `{lote}` y la base `{base}`, resolviendo los conflictos"
            if rebase else f"Arreglar la rama `{branch}` (PR #{number}): {reason}")
    how = (f"- Después `git merge origin/{lote}` (si existe; si no, `origin/{base}`) y resuelve los conflictos "
           "conservando la intención de ambas partes.\n" if rebase else
           "- Si es un falso positivo del escáner de secretos (un dato de prueba), cámbialo para que no parezca una "
           "clave real; si es un test o build roto, arréglalo de verdad.\n")
    return (f"## Objetivo\n{what}.\n\n## Cómo\n- Trabaja SOBRE LA RAMA DEL PR: `git fetch origin` y "
            f"`git merge origin/{branch}` en tu rama (trae su trabajo; no la reescribas).\n{how}"
            f"- Al terminar empuja a la rama del PR y a la tuya: `git push origin HEAD:{branch}` y "
            f"`git push -u origin HEAD`. Sin force. El PR #{number} se actualiza solo; dilo en `for_oscar`.\n"
            f"- Cambios mínimos.\n\nRama-origen: {branch}\nRama-push: {branch}\n"
            + (f"\nOrigen: {origin}\n" if origin else ""))


class PendingNotice:
    """Aviso diario de PR aprobados que esperan lote. Sin PR esperando no manda nada."""

    def __init__(self, integrator):
        self.i = integrator
        self._building = threading.Lock()

    def _sent_path(self, lane_name: str) -> Path:
        return self.i.batches.dir / f"notice-{lane_name}.json"

    def _sent_day(self, lane_name: str) -> str:
        try:
            import json
            return json.loads(self._sent_path(lane_name).read_text(encoding="utf-8")).get("day") or ""
        except (OSError, ValueError):
            return ""

    def entries(self, lane) -> list[dict]:
        i, now = self.i, self.i._now()
        out = []
        for c in i.batches._candidates(lane):
            since = float(i._state(c["tid"]).get("pending_since") or c["at"] or now)
            out.append({**c, "days": max(0, int((now - since) // 86400))})
        return out

    @staticmethod
    def render(repo: str, entries: list[dict]) -> str:
        n = len(entries)
        head = f"🚦 {n} PR aprobado{'s' if n != 1 else ''} esperando lote · {html.escape(repo)}"
        rows = [f"• #{e['pr']} · {html.escape(repo)} · {html.escape(truncate(e['title'], NOTICE_TITLE_MAX))} · "
                f"{e['days']} día{'s' if e['days'] != 1 else ''}" for e in entries]
        return "\n".join([head, *rows, "Pulsa el botón para montar el lote ahora (o espera al automático)."])

    def tick(self) -> list[str]:
        """Una comprobación por pasada. Devuelve los carriles avisados."""
        i = self.i
        if i.dry_run or not i.settings.enabled or not i.desk:
            return []
        now = i.batches.clock()
        if now.hour < NOTICE_HOUR:
            return []
        today, sent = now.strftime("%Y-%m-%d"), []
        for name, policy in i.settings.policies.items():
            lane = i.lanes.get(name)
            if not policy.batch or not lane or self._sent_day(name) == today:
                continue
            entries = self.entries(lane)
            if not entries:
                continue
            import json
            i.batches.dir.mkdir(parents=True, exist_ok=True)
            self._sent_path(name).write_text(json.dumps({"day": today}), encoding="utf-8")  # antes: sin bucles
            try:
                self._publish(lane, entries)
                sent.append(name)
            except Exception as exc:
                log.warning("aviso de lote pendiente %s falló: %s", name, exc)
        return sent

    def _publish(self, lane, entries: list[dict]) -> None:
        from .batch import BATCH_LABEL
        from .integrator import INT_BATCH_BUILD, repo_name
        i = self.i
        first = entries[0]
        rec = {"kind": "integrate", "task_id": first["tid"], "board": lane.board, "lane": lane.name,
               "title": first["title"], "pr_number": first["pr"], "pr_url": first["url"], "batch_build": True}
        markup = i.desk.store.issue(rec, [[{"text": BATCH_LABEL, "action": INT_BATCH_BUILD}]])[1]
        i._publish(lane, {"id": first["tid"], "title": f"lote {lane.name}", "body": None},
                   self.render(repo_name(lane), entries), markup, link=f"🚦 {len(entries)} PR esperando lote")

    def on_build_button(self, lane, policy, rec: dict, where: dict, desk) -> bool:
        """[🔀 Fusionar lote] del aviso diario: monta ya el lote (lo mismo que /lote). True = hecho."""
        if not self._building.acquire(blocking=False):
            return False
        try:
            desk.notifier.edit(where["chat_id"], where["message_id"],
                               "⏳ Montando el lote… pasa los gates y los tests; te llegará su ficha.", html=False)
        except Exception as exc:
            log.warning("aviso de lote: no se pudo editar el mensaje: %s", exc)
        self._spawn(lambda: self._build(lane, policy, where, desk))
        return True

    def _spawn(self, fn) -> None:  # aparte para que los tests lo ejecuten en línea
        threading.Thread(target=fn, name="lote-aviso", daemon=True).start()

    def _build(self, lane, policy, where: dict, desk) -> None:
        try:
            try:
                text = self.i.batches.assemble(lane, policy, manual=True)
            except Exception as exc:
                log.warning("lote desde el aviso diario falló: %s", exc)
                text = "No pude montar el lote; detalle en el log."
            try:
                desk.notifier.edit(where["chat_id"], where["message_id"], text, html=False)
            except Exception as exc:
                log.warning("aviso de lote: no se pudo editar el resultado: %s", exc)
        finally:
            self._building.release()


class AutoResolver:
    """Conflicto SOLO en ficheros append-only: la base se fusiona en la rama del PR con `merge=union`, se pasan los
    gates (diff, migraciones, tests del carril) y se empuja a `lane/<tid>`. Sin force, sin tocar la base."""

    def __init__(self, integrator):
        self.i = integrator

    def run(self, lane, policy, tid: str, number: int, pr: dict, task: dict) -> tuple[str, list[str]]:
        """('pushed'|'code'|'gates'|'push_failed'|'skipped', detalle). 'code' = hay conflicto de código."""
        i, head = self.i, pr["headRefOid"]
        patterns = tuple(policy.append_only_paths or ())
        if i.dry_run or not patterns or i._state(tid).get("autoresolve_try") == head:
            return "skipped", []
        i._save(tid, autoresolve_try=head)  # antes: un fallo (o un rechazo del push) no se reintenta en bucle
        with i._lock:
            return self._attempt(lane, policy, tid, number, head, task, patterns)

    def _attempt(self, lane, policy, tid: str, number: int, head: str, task: dict, patterns) -> tuple[str, list[str]]:
        from .integrator import GateResult
        i = self.i
        repo, remote, base = lane.repo, lane.remote, lane.base
        pr_ref = f"refs/integrator/pr-{number}"
        if i._git(repo, "fetch", remote, f"+refs/heads/{base}:refs/remotes/{remote}/{base}",
                  f"+refs/pull/{number}/head:{pr_ref}").returncode != 0 \
                or (i._git(repo, "rev-parse", pr_ref).stdout or "").strip() != head:
            return "skipped", ["no se pudo descargar el PR o cambió mientras tanto"]
        base_sha = (i._git(repo, "rev-parse", f"{remote}/{base}").stdout or "").strip()
        wt = Path(i.settings.worktree_root) / f"int-{tid}"
        i._remove_worktree(repo, wt)
        if i._git(repo, "-c", f"core.hooksPath={i._nohooks()}", "worktree", "add", "--detach", str(wt),
                  head).returncode != 0:
            return "skipped", ["no se pudo preparar la copia de trabajo"]
        try:
            return self._merge_and_push(wt, lane, policy, tid, number, head, base_sha, task, patterns, GateResult)
        finally:
            i._remove_worktree(repo, wt)

    def _merge_and_push(self, wt: Path, lane, policy, tid, number, head, base_sha, task, patterns, GateResult):
        i, w = self.i, str(wt)
        attrs = i.state_dir / "union.attributes"
        i.state_dir.mkdir(parents=True, exist_ok=True)
        attrs.write_text("".join(f"{p} merge=union\n" for p in patterns), encoding="utf-8")
        nohooks = ["-c", f"core.hooksPath={i._nohooks()}"]
        merge = i._git(w, *nohooks, "-c", "user.name=agent-lanes-integrator", "-c", "user.email=agent-lanes@localhost",
                       "-c", "commit.gpgsign=false", "-c", f"core.attributesFile={attrs.as_posix()}",
                       "merge", "--no-ff", "--no-edit", base_sha)
        if merge.returncode != 0:  # lo que sigue en conflicto NO es append-only (esos los resolvió union)
            files = (i._git(w, "diff", "--name-only", "--diff-filter=U").stdout or "").split()
            i._git(w, *nohooks, "merge", "--abort")
            return "code", [f for f in files if not is_append_only(f, patterns)] or files
        new_head = (i._git(w, "rev-parse", "HEAD").stdout or "").strip()
        res = GateResult(ok=False, head_sha=new_head, base_sha=base_sha)
        rng = f"{base_sha}...{new_head}"
        res.changed_files = (i._git(w, "diff", "--no-renames", "--name-only", rng).stdout or "").split()
        i._diff_gates(w, lane, policy, res, rng)
        i._migration_gates(wt, lane, policy, res)
        if res.reasons:
            self._tell(lane, tid, number, task, "🛑 Fusión automática NO empujada: fallan los gates tras unir "
                       f"los ficheros de solo-añadir ({'; '.join(res.reasons)[:300]}). Lo da: el carril {lane.name}.")
            return "gates", res.reasons
        cp = i._push_pr_branch(w, lane.remote, tid)
        if cp.returncode != 0:
            err = (cp.stderr or "").strip()[-300:]
            protected = "protected" in err.lower() or "GH006" in err
            self._tell(lane, tid, number, task, "🛑 Fusión automática NO empujada: " + (
                "la rama del PR está protegida" if protected else "GitHub rechazó el push (¿la rama avanzó?)")
                + f". Detalle: {err}. Lo da: Oscar o el carril {lane.name}.")
            return "push_failed", [err]
        i.rebind_approval(tid, new_head)
        i._save(tid, autoresolve_head=new_head, autoresolve_from=head, head_sha=None)
        why = f"unión automática de {', '.join(sorted(patterns)[:3])}"
        msg = (f"INTEGRADOR: la base ({lane.base}) se fusionó en la rama del PR #{number} con `merge=union` "
               f"(conflicto solo en ficheros de solo-añadir), gates OK; {head[:7]} → {new_head[:7]}. {why}.")
        i._comment(lane, tid, msg)
        slug = i.links.repo_slug(lane) if i.links else None
        if slug:
            i._gh("pr", "comment", str(number), "--repo", slug, "--body", msg)
        return "pushed", [new_head]

    def _tell(self, lane, tid: str, number: int, task: dict, text: str) -> None:
        i = self.i
        i._comment(lane, tid, f"INTEGRADOR: PR #{number} · " + text)
        i._publish(lane, {"id": tid, "title": task.get("title") or tid, "body": None},
                   f"⚠️ PR #{number} · {html.escape(truncate(task.get('title'), 50))}\n{html.escape(text)}",
                   link=f"⚠️ PR #{number} sin fusión automática")
