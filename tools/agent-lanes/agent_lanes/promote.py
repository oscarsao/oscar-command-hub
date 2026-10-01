"""Promover MigraTeam a producción desde Telegram (01-10): develop → master con doble confirmación.

Flujo (`/promover migrateam` o [🚀 Promover a producción]; solo Oscar):
  1. abre o actualiza el PR develop → master (`gh pr create/edit`);
  2. espera a los checks obligatorios de la protección de rama (nombres leídos de la API, no hardcodeados);
  3. SOLO LECTURA de producción: `alembic_version` del /health de producción contra el head del PR. Si no cuadra
     (o /health no lo expone) NO hay botón: se escala al coordinador;
  4. resumen (commits, PR incluidos, migraciones, seeds, riesgos) con [✅ Revisado];
  5. segunda confirmación [🚀 Sí, promover] (nombra las migraciones una a una);
  6. `gh pr merge --merge --match-head-commit` (sin --admin, --auto ni --force) y sondeo de /health hasta ver el
     commit_sha del merge; aviso final con cómo revertir.

Seguridad: nada del PR se ejecuta (solo metadatos y texto de las migraciones leído con `gh api` y analizado con `ast`);
los botones llevan una firma HMAC (acción, promoción, SHA del PR, paso, caducidad) que se comprueba al pulsar, además
del dueño que ya valida el DecisionDesk; cada paso caduca (30 min) y el flujo entero también (2 h); si el PR cambia
tras el resumen la confirmación no vale y se regenera. El estado vive en `.state/promote/`, nunca en el repo.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import json
import logging
import re
import secrets
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import proc as _proc
from .config import ROOT, _load
from .integrator import FORBIDDEN_FLAGS, GH_EXE, _http_get, parse_revision

log = logging.getLogger("agent_lanes")

STATE_DIR = ROOT / ".state" / "promote"
PROMO_REVIEW, PROMO_CONFIRM, PROMO_CANCEL = "promo_review", "promo_confirm", "promo_cancel"
PROMO_ACTIONS = (PROMO_REVIEW, PROMO_CONFIRM, PROMO_CANCEL)
PROMOTE_BUTTON = {"text": "🚀 Promover a producción", "action": "promo_start"}
STEP_TTL, FLOW_TTL = 1800, 7200   # 30 min por paso, 2 h de flujo (decisión de Oscar 01-10)
RED = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "STALE"}
MAX_MIGRATIONS = 400
SHA_RE = re.compile(r"[0-9a-f]{40}")
PR_REF_RE = re.compile(r"\(#(\d+)\)")
ESCALATE = "escala al coordinador (no se ofrece el botón)"


@dataclass(frozen=True)
class PromoteConfig:
    key: str
    repo: str                          # owner/repo de GitHub
    head: str = "develop"
    base: str = "master"
    health_url: str = ""               # /health de PRODUCCIÓN (https)
    health_commit_key: str = "commit_sha"
    health_alembic_key: str = "alembic_version"
    alembic_versions: str = "backend/alembic/versions/"
    seeds: tuple[str, ...] = ()
    sensitive_paths: tuple[str, ...] = ()
    checks_timeout: int = 1800
    health_timeout: int = 900
    poll_seconds: int = 20


def load_promote_configs(path: Path | None = None) -> dict[str, PromoteConfig]:
    """`promote:` de lanes.yaml -> {clave: PromoteConfig}. Sin sección: ninguna promoción disponible."""
    out = {}
    for key, p in ((_load(path) or {}).get("promote") or {}).items():
        p = dict(p or {})
        for f in ("seeds", "sensitive_paths"):
            p[f] = tuple(p.get(f) or ())
        if p.get("health_url") and not str(p["health_url"]).startswith("https://"):
            raise ValueError(f"promote.{key}.health_url debe ser https://")
        out[str(key).lower()] = PromoteConfig(key=str(key).lower(), **{k: v for k, v in p.items() if v is not None})
    return out


def sign(secret: bytes, *parts) -> str:
    return hmac.new(secret, "|".join(str(x) for x in parts).encode(), hashlib.sha256).hexdigest()[:32]


def _docline(source: str) -> str:
    try:
        doc = ast.get_docstring(ast.parse(source)) or ""
    except SyntaxError:
        return ""
    return doc.strip().splitlines()[0][:100] if doc.strip() else ""


class Promoter:
    def __init__(self, configs: dict[str, PromoteConfig], *, notifier, desk, runner=_proc.run, gh_exe: str = GH_EXE,
                 http_get: Callable[[str], tuple[int, str]] | None = None, state_dir: Path = STATE_DIR,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], float] = time.time, out: Callable[[str], None] = print):
        self.configs, self.notifier, self.desk = configs, notifier, desk
        self._run, self.gh_exe, self._http_get = runner, gh_exe, http_get or _http_get
        self.state_dir = Path(state_dir)
        self._clock, self._sleep, self._now = clock, sleep, now
        self._lock = threading.RLock()  # _merge guarda estado dentro del candado
        self.busy_keys: set[str] = set()

    # --- utilidades ----------------------------------------------------------------------------------

    def _gh(self, *args: str, timeout: int = 120):
        if any(a in FORBIDDEN_FLAGS for a in args):
            raise PermissionError(f"flag prohibido en gh: {[a for a in args if a in FORBIDDEN_FLAGS]}")
        return self._run([self.gh_exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=timeout, stdin=-3, cwd=tempfile.gettempdir())  # -3 = subprocess.DEVNULL

    def _json(self, *args: str):
        cp = self._gh(*args)
        if cp.returncode != 0:
            return None
        try:
            return json.loads(cp.stdout or "null")
        except json.JSONDecodeError:
            return None

    def _secret(self) -> bytes:
        """Clave de las firmas: aleatoria, local a `.state/promote/` (no es el token del bot ni sale de aquí)."""
        path = self.state_dir / "secret.key"
        try:
            return bytes.fromhex(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            self.state_dir.mkdir(parents=True, exist_ok=True)
            key = secrets.token_bytes(32)
            path.write_text(key.hex(), encoding="ascii")
            return key

    def _state(self, pid: str) -> dict:
        try:
            return json.loads((self.state_dir / f"{pid}.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, pid: str, **data) -> dict:
        with self._lock:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            cur = {**self._state(pid), **data}
            tmp = self.state_dir / f"{pid}.tmp"
            tmp.write_text(json.dumps(cur), encoding="utf-8")
            tmp.replace(self.state_dir / f"{pid}.json")
            return cur

    def _log(self, pid: str, text: str) -> None:
        """Log de decisiones: cada paso y cada rechazo quedan en runner.log y en el estado de la promoción."""
        log.info("promoción %s: %s", pid, text)
        self._save(pid, log=[*self._state(pid).get("log", [])[-30:], f"{time.strftime('%H:%M:%S')} {text}"])

    def _send(self, dest: dict, text: str, markup: dict | None = None) -> dict | None:
        try:
            thread = dest.get("thread_id")
            return self.notifier.send_to(str(dest["chat_id"]), None if str(thread or 0) == "0" else thread,
                                         text[:3900], html=False, reply_markup=markup)
        except Exception as exc:
            log.warning("promoción: no se pudo enviar el aviso: %s", exc)
            return None

    def _unbutton(self, st: dict, note: str) -> None:
        if st.get("message_id"):
            try:
                self.notifier.edit(st["chat_id"], st["message_id"], note[:3900], reply_markup={"inline_keyboard": []})
            except Exception as exc:
                log.info("promoción: no se pudo editar el aviso: %s", exc)

    def _buttons(self, st: dict, step: str, spec: list[dict]) -> dict:
        expires = int(self._now()) + STEP_TTL
        flow_end = int(st["started"]) + FLOW_TTL
        expires = min(expires, flow_end)
        rec = {"kind": "promote", "task_id": f"promo-{st['id']}", "board": "", "lane": "", "promo": st["id"],
               "step": step, "head": st["head"], "expires": expires}
        rec["sig"] = sign(self._secret(), "v1", st["id"], st["head"], step, expires)
        return self.desk.store.issue(rec, [[dict(b)] for b in spec])[1]

    # --- 1-4: preparar y resumir -------------------------------------------------------------------------

    def start(self, key: str, dest: dict) -> str:
        """Entrada de /promover y del botón. Devuelve una línea para el log; todo el aviso va por Telegram."""
        cfg = self.configs.get((key or "").strip().lower())
        if not cfg:
            self._send(dest, "No hay promoción configurada para ese proyecto: /promover " + "|".join(self.configs))
            return "sin config"
        with self._lock:
            if cfg.key in self.busy_keys:
                self._send(dest, "Ya hay una promoción de MigraTeam en curso; espera a que termine.")
                return "ocupado"
            self.busy_keys.add(cfg.key)
        try:
            return self._prepare(cfg, dest)
        finally:
            self.busy_keys.discard(cfg.key)

    def _prepare(self, cfg: PromoteConfig, dest: dict, pid: str | None = None) -> str:
        pid = pid or secrets.token_hex(4)
        self._save(pid, id=pid, key=cfg.key, started=self._state(pid).get("started") or int(self._now()),
                   chat_id=str(dest["chat_id"]), thread_id=dest.get("thread_id"), status="preparing")
        if not cfg.health_url:
            self._fail(pid, dest, "falta `health_url` de producción en la config de promoción")
            return "sin health_url"
        pr = self._open_pr(cfg)
        if not pr:
            self._fail(pid, dest, "no pude abrir ni actualizar el PR develop → master (¿gh o permisos?)")
            return "sin PR"
        head = pr["headRefOid"]
        self._save(pid, pr=pr["number"], pr_url=pr["url"], head=head)
        self._log(pid, f"PR #{pr['number']} @ {head[:12]}")
        self._send(dest, f"⏳ Promoción {pid}: PR #{pr['number']} listo. Esperando los checks obligatorios de {cfg.base}…")
        bad = self._wait_checks(cfg, pr["number"], head)
        if bad:
            self._fail(pid, dest, bad)
            return "checks"
        info = self._compare(cfg, head)
        if info is None:
            self._fail(pid, dest, "no pude leer la comparación develop → master")
            return "sin comparación"
        verdict = self._migrations(cfg, head)
        if verdict.get("problem"):
            self._fail(pid, dest, verdict["problem"] + " · " + ESCALATE)
            return "alembic"
        text = self._summary(cfg, pr, info, verdict)
        st = self._save(pid, status="await_review", migrations=verdict["pending"], reviewed=False, text=text)
        markup = self._buttons(st, "review", [{"text": "✅ Revisado", "action": PROMO_REVIEW},
                                              {"text": "❌ Cancelar", "action": PROMO_CANCEL}])
        sent = self._send(dest, text, markup)
        self._save(pid, message_id=(sent or {}).get("message_id"))
        return f"resumen enviado ({pid})"

    def _fail(self, pid: str, dest: dict, why: str) -> None:
        self._save(pid, status="stopped")
        self._log(pid, f"PARADA: {why}")
        self._send(dest, f"⛔ Promoción {pid} parada: {why}\nNo se ha tocado producción.")

    def _open_pr(self, cfg: PromoteConfig) -> dict | None:
        find = ("pr", "list", "--repo", cfg.repo, "--head", cfg.head, "--base", cfg.base, "--state", "open", "--json",
                "number,url,headRefOid", "--limit", "1")
        prs = self._json(*find)
        title = f"Promoción {cfg.head} → {cfg.base} ({time.strftime('%Y-%m-%d')})"
        body = "Promoción a producción lanzada desde Telegram (agent-lanes). Se fusiona solo con la doble confirmación de Oscar."
        if isinstance(prs, list) and prs:
            self._gh("pr", "edit", str(prs[0]["number"]), "--repo", cfg.repo, "--title", title)
        elif isinstance(prs, list):
            self._gh("pr", "create", "--repo", cfg.repo, "--head", cfg.head, "--base", cfg.base, "--title", title,
                     "--body", body)
        else:
            return None
        prs = self._json(*find)
        pr = prs[0] if isinstance(prs, list) and prs else None
        return pr if pr and SHA_RE.fullmatch(str(pr.get("headRefOid") or "")) else None

    def _wait_checks(self, cfg: PromoteConfig, number: int, head: str) -> str | None:
        """None = todos los obligatorios en verde; texto = por qué se para."""
        prot = self._json("api", f"repos/{cfg.repo}/branches/{cfg.base}/protection/required_status_checks")
        required = set()
        if isinstance(prot, dict):
            required = {c for c in prot.get("contexts") or () if isinstance(c, str)}
            required |= {c.get("context") for c in prot.get("checks") or () if isinstance(c, dict) and c.get("context")}
        if not required:
            return f"no pude leer los checks obligatorios de la protección de {cfg.base}"
        deadline = self._clock() + cfg.checks_timeout
        while True:
            view = self._json("pr", "view", str(number), "--repo", cfg.repo, "--json", "statusCheckRollup,headRefOid")
            if not isinstance(view, dict) or view.get("headRefOid") != head:
                return "el PR cambió mientras esperaba los checks"
            got = {}
            for c in view.get("statusCheckRollup") or ():
                got[c.get("name") or c.get("context")] = str(c.get("conclusion") or c.get("state") or "").upper()
            red = sorted(n for n in required if got.get(n) in RED)
            if red:
                return "checks en rojo: " + ", ".join(red)
            if all(got.get(n) == "SUCCESS" for n in required):
                return None
            if self._clock() >= deadline:
                pend = sorted(n for n in required if got.get(n) != "SUCCESS")
                return "los checks no terminaron a tiempo: " + ", ".join(pend)
            self._sleep(cfg.poll_seconds)

    def _compare(self, cfg: PromoteConfig, head: str) -> dict | None:
        data = self._json("api", f"repos/{cfg.repo}/compare/{cfg.base}...{head}?per_page=100")
        return data if isinstance(data, dict) and isinstance(data.get("commits"), list) else None

    def _prod_health(self, cfg: PromoteConfig) -> dict | None:
        try:
            code, body = self._http_get(cfg.health_url)
            return json.loads(body or "{}") if code == 200 else None
        except Exception:
            return None

    def _read(self, cfg: PromoteConfig, path: str, ref: str) -> str | None:
        cp = self._gh("api", "-H", "Accept: application/vnd.github.raw", f"repos/{cfg.repo}/contents/{path}?ref={ref}")
        return cp.stdout if cp.returncode == 0 else None

    def _migrations(self, cfg: PromoteConfig, head: str) -> dict:
        """Solo lectura: revisión de producción (/health) contra el head del PR. {'pending': [...]} o {'problem': str}."""
        health = self._prod_health(cfg)
        prod = str((health or {}).get(cfg.health_alembic_key) or "").strip()
        if not prod:
            return {"problem": f"el /health de producción no da `{cfg.health_alembic_key}`"}
        listing = self._json("api", f"repos/{cfg.repo}/contents/{cfg.alembic_versions.rstrip('/')}?ref={head}")
        if not isinstance(listing, list) or len(listing) > MAX_MIGRATIONS:
            return {"problem": "no pude listar las migraciones del PR"}
        revs: dict[str, tuple[tuple[str, ...], str, str]] = {}
        for item in listing:
            name = str(item.get("name") or "")
            if not name.endswith(".py") or name.startswith("__"):
                continue
            if not re.fullmatch(r"[\w.-]+", name):  # el nombre va en la ruta de `gh api`: nada de ? # / ..
                return {"problem": f"nombre de migración no válido en el PR: {name[:40]}"}
            src = self._read(cfg, f"{cfg.alembic_versions.rstrip('/')}/{name}", head)
            try:
                rev, down = parse_revision(src or "")
            except (SyntaxError, ValueError):
                rev, down = None, ()
            if not rev:
                return {"problem": f"migración ilegible en el PR: {name}"}
            revs[rev] = (down, name, _docline(src or ""))
        children = {d: r for r, (downs, _, _) in revs.items() for d in downs}
        heads = [r for r in revs if r not in children]
        if len(heads) != 1:
            return {"problem": f"el PR tiene {len(heads)} heads de Alembic"}
        if prod not in revs:
            return {"problem": f"alembic_version de producción ({prod}) no está en el PR"}
        chain, cur = [], prod
        while cur != heads[0]:
            nxt = [r for r, (downs, _, _) in revs.items() if cur in downs]
            if len(nxt) != 1:
                return {"problem": f"alembic_version de producción ({prod}) no enlaza con el head del PR"}
            cur = nxt[0]
            chain.append(f"{revs[cur][1]}" + (f" — {revs[cur][2]}" if revs[cur][2] else ""))
        return {"pending": chain, "prod": prod, "head_rev": heads[0]}

    def _summary(self, cfg: PromoteConfig, pr: dict, info: dict, verdict: dict) -> str:
        commits = info["commits"]
        total = int(info.get("total_commits") or len(commits))
        subjects = [str((c.get("commit") or {}).get("message") or "").splitlines()[0][:80] for c in commits]
        prs = sorted({int(n) for s in subjects for n in PR_REF_RE.findall(s)})
        files = [str(f.get("filename")) for f in info.get("files") or () if isinstance(f, dict)]
        seeds = [f for f in files if any(f.startswith(p) for p in cfg.seeds)]
        risky = [f for f in files if any(f.startswith(p) for p in cfg.sensitive_paths)]
        mig = verdict["pending"]
        lines = [f"🚀 PROMOVER {cfg.key.upper()} A PRODUCCIÓN · PR #{pr['number']}", pr["url"],
                 f"{cfg.head} → {cfg.base} · {total} commit(s)" + (" (lista recortada)" if total > len(commits) else ""),
                 f"PR incluidos: {', '.join('#' + str(n) for n in prs) or 'ninguno detectado'}",
                 f"Alembic: producción en {verdict['prod']} · " + (f"{len(mig)} migración(es) nueva(s)" if mig
                                                                   else "sin migraciones nuevas"),
                 f"Seeds: {', '.join(seeds[:6]) if seeds else 'ninguno'}",
                 "Riesgos: " + ("; ".join(["⚠️ " + f for f in risky[:5]] + (["⚠️ migraciones de Alembic"] if mig else []))
                                or "ninguno detectado"),
                 "Checks obligatorios: en verde", "", "Últimos commits:"]
        lines += [f"· {s}" for s in subjects[-8:]]
        lines += ["", "Paso 1 de 2: pulsa Revisado cuando lo hayas mirado. Caduca en 30 min."]
        return "\n".join(lines)

    # --- 5-8: confirmaciones, merge y verificación --------------------------------------------------------

    def on_button(self, action: str, rec: dict, where: dict, desk) -> bool:
        """Pulsación ya autenticada como Oscar por el DecisionDesk. Siempre devuelve True: lo gastado, gastado
        (cada paso es de un solo uso); si algo no cuadra se avisa y se regenera con /promover."""
        try:
            self._on_button(action, rec, where)
        except Exception as exc:
            log.warning("promoción: fallo inesperado en %s: %s", action, exc)
            self._send(where, "⛔ Fallo inesperado en la promoción; no se ha fusionado nada. Detalle en el log.")
        return True

    def _reject(self, st: dict, where: dict, why: str) -> None:
        self._log(st.get("id", "?"), f"RECHAZADO: {why}")
        self._send(where, f"⛔ Esa confirmación no vale: {why}. Vuelve a lanzar /promover migrateam.")

    def _on_button(self, action: str, rec: dict, where: dict) -> None:
        pid, step = str(rec.get("promo") or ""), str(rec.get("step") or "")
        st = self._state(pid)
        cfg = self.configs.get(st.get("key", ""))
        expected = sign(self._secret(), "v1", pid, rec.get("head"), step, rec.get("expires"))
        if not st or not cfg or action not in PROMO_ACTIONS or not hmac.compare_digest(str(rec.get("sig")), expected):
            self._reject(st, where, "firma no válida")
            return
        now = self._now()
        if now > float(rec["expires"]) or now > int(st["started"]) + FLOW_TTL:
            self._reject(st, where, "caducó")
            return
        if action == PROMO_CANCEL:
            self._save(pid, status="cancelled")
            self._log(pid, "cancelada por Oscar")
            self._unbutton(st, "❌ Promoción cancelada. No se ha tocado producción.")
            return
        want = {PROMO_REVIEW: ("review", "await_review"), PROMO_CONFIRM: ("confirm", "await_confirm")}[action]
        if step != want[0] or st.get("status") != want[1] or rec.get("head") != st.get("head"):
            self._reject(st, where, "el paso ya no corresponde")
            return
        view = self._json("pr", "view", str(st["pr"]), "--repo", cfg.repo, "--json", "headRefOid,state")
        if not isinstance(view, dict) or view.get("state") != "OPEN" or view.get("headRefOid") != st["head"]:
            self._save(pid, status="stale")
            self._unbutton(st, "⚠️ El PR cambió desde el resumen: esta confirmación ya no vale.")
            self._reject(st, where, "el PR cambió desde el resumen")
            return
        if action == PROMO_REVIEW:
            self._second_confirmation(cfg, st, where)
        else:
            self._merge(cfg, st, where)

    def _second_confirmation(self, cfg: PromoteConfig, st: dict, where: dict) -> None:
        pid = st["id"]
        fresh = self._migrations(cfg, st["head"])  # producción pudo moverse desde el resumen
        if fresh.get("problem") or fresh.get("pending") != (st.get("migrations") or []):
            self._save(pid, status="stale")
            self._unbutton(st, "⚠️ Las migraciones cambiaron desde el resumen: esta confirmación ya no vale.")
            self._log(pid, "PARADA: la lista de migraciones cambió antes del último paso")
            self._send(where, "⛔ Las migraciones pendientes han cambiado desde el resumen"
                              + (f" ({fresh['problem']})" if fresh.get("problem") else "")
                              + ". No se ha tocado producción. Vuelve a lanzar /promover migrateam.")
            return
        mig = fresh["pending"]
        text = [f"🚀 ÚLTIMO PASO · promover {cfg.key.upper()} a producción (PR #{st['pr']})",
                f"Se fusionará {cfg.head} en {cfg.base} sin bypass y Railway/Vercel desplegarán solos."]
        if mig:
            text += [f"Se aplicarán {len(mig)} migración(es) de Alembic, una a una:"] + [f"  {i}. {m}" for i, m in
                                                                                       enumerate(mig, 1)]
        else:
            text.append("Sin migraciones de Alembic.")
        text.append("Paso 2 de 2. Caduca en 30 min.")
        self._save(pid, status="await_confirm", reviewed=True)
        self._log(pid, "Revisado por Oscar")
        self._unbutton(st, st.get("text", "") + "\n\n✅ Revisado")
        markup = self._buttons(self._state(pid), "confirm", [{"text": "🚀 Sí, promover", "action": PROMO_CONFIRM},
                                                              {"text": "❌ Cancelar", "action": PROMO_CANCEL}])
        sent = self._send(where, "\n".join(text), markup)
        self._save(pid, message_id=(sent or {}).get("message_id"))

    def _merge(self, cfg: PromoteConfig, st: dict, where: dict) -> None:
        pid = st["id"]
        with self._lock:  # un solo merge por promoción aunque lleguen dos pulsaciones
            if self._state(pid).get("status") != "await_confirm":
                return
            self._save(pid, status="merging")
        self._log(pid, f"fusionando PR #{st['pr']} @ {st['head'][:12]}")
        self._unbutton(st, f"🔀 Fusionando PR #{st['pr']}…")
        cp = self._gh("pr", "merge", str(st["pr"]), "--repo", cfg.repo, "--merge", "--match-head-commit", st["head"],
                      timeout=180)
        if cp.returncode != 0:
            self._save(pid, status="stopped")
            self._log(pid, f"gh pr merge rechazado: {(cp.stderr or '').strip()[:200]}")
            self._send(where, "⛔ GitHub no ha aceptado la fusión (protección de rama o PR cambiado). No se fuerza nada: "
                              "revisa el PR y vuelve a lanzar /promover migrateam.")
            return
        view = self._json("pr", "view", str(st["pr"]), "--repo", cfg.repo, "--json", "mergeCommit") or {}
        sha = str((view.get("mergeCommit") or {}).get("oid") or "").lower()
        self._save(pid, status="merged", merge_sha=sha)
        self._log(pid, f"fusionado {sha[:12] or '?'}; verificando /health")
        revert = (f"Para revertir: abre un PR con `git revert -m 1 {sha[:12]}` sobre {cfg.base}. "
                  "Las migraciones nunca se deshacen solas: lo escala el coordinador.")
        if not SHA_RE.fullmatch(sha):
            self._send(where, "✅ Fusionado, pero no pude leer el commit del merge: comprueba /health a mano.\n" + revert)
            return
        deadline, last = self._clock() + cfg.health_timeout, "sin respuesta"
        while self._clock() < deadline:
            health = self._prod_health(cfg)
            served = str((health or {}).get(cfg.health_commit_key) or "").lower()
            if health and health.get("status") == "healthy" and served == sha:
                self._save(pid, status="deployed")
                self._log(pid, "producción sirve el commit nuevo")
                self._send(where, f"🚀 Promoción hecha: producción sirve {sha[:7]} (health OK).\n" + revert)
                return
            last = f"sirve {served[:7]}" if served else "sin respuesta"
            self._sleep(cfg.poll_seconds)
        self._save(pid, status="unconfirmed")
        self._log(pid, f"/health sin el commit nuevo: {last}")
        self._send(where, f"⚠️ Fusionado ({sha[:7]}) pero /health no confirma el despliegue en "
                          f"{cfg.health_timeout // 60} min ({last}). Revisa Railway y Vercel; no reintento nada.\n" + revert)
