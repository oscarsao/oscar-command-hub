"""/al_dia [proyecto|todos] [dev|prod]: diagnóstico, poner al día las ramas de carril e integrar lo listo (specs/al-dia.md).

Esta entrega (tarjeta a) cubre SOLO `dev`: nada de despliegues. `prod`, «🛠 Resolver» y la oferta proactiva son de la
tarjeta hija (specs/al-dia-prod.md). Todo se apoya en piezas que ya existen y no las duplica:
- PR y ramas: `gh` vía el integrador (`_gh`), igual que `prlisto.ReadyPRWatcher`.
- Poner al día una rama atrasada: `gh api …/update-branch` (GitHub fusiona la base en `lane/<id>`; nunca se toca la base);
  la aprobación de Oscar se re-ata a la nueva cabeza con `rebind_approval`, como ya hace la fusión automática.
- Conflicto: `Integrator._conflict_pass` (atascos.AutoResolver para ficheros solo-añadir; si es código, la tarjeta de
  rebase con Rama-push).
- Integrar: `batch.BatchPlanner.assemble` (lote + gate combinado). MigraTeam sigue develop→master por /promover.
- Migraciones y variables: solo se listan (`falta.py` lo explica); nunca se aplican desde aquí.
Los ajustes viven en `al_dia.yaml` (se relee en cada uso)."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger("agent-lanes.al_dia")

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_PATH = ROOT / "al_dia.yaml"
DEFAULTS = {"rama_atras_commits": 10, "max_prs": 20}
DESTINOS = ("dev", "prod")
AD_INTEGRAR, AD_PR = "ad_integrar", "ad_pr"   # botones: «✅ Integrar todo lo listo» / uno por PR
AD_ACTIONS = (AD_INTEGRAR, AD_PR)
BRANCH_PREFIX = "lane/"
CI_ROJO = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED"}
CI_EN_CURSO = {"PENDING", "IN_PROGRESS", "QUEUED", "WAITING", "EXPECTED"}


def load_ajustes(path: Path | str | None = None) -> dict:
    """Lo que falte o no sea un entero positivo vale el default."""
    try:
        raw = yaml.safe_load(Path(path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    out = dict(DEFAULTS)
    for k in DEFAULTS:
        v = raw.get(k) if isinstance(raw, dict) else None
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            out[k] = v
    return out


def parse_args(args: str, proyectos) -> tuple[str | None, str, str | None]:
    """('todos'|clave, destino, error). Por defecto todos y dev; el orden de las dos palabras da igual."""
    proyecto, destino = "todos", "dev"
    for tok in re.split(r"\s+", (args or "").strip().lower()):
        if not tok:
            continue
        if tok in DESTINOS:
            destino = tok
        elif tok in ("todos", "todo", "all"):
            proyecto = "todos"
        elif tok in proyectos:
            proyecto = tok
        else:
            return None, destino, f"No conozco «{tok}». Uso: /al_dia [{'|'.join(['todos', *proyectos])}] [dev|prod]"
    return proyecto, destino, None


@dataclass
class PRInfo:
    number: int
    tid: str
    title: str
    url: str
    estado: str            # listo | ci_rojo | conflicto | ci_en_curso | sin_aprobar | borrador
    atras: int = 0         # commits que le faltan de la base
    head: str = ""


@dataclass
class ProjectState:
    key: str
    lane: object
    policy: object
    slug: str = ""
    prs: list[PRInfo] = field(default_factory=list)
    sin_desplegar: list[str] = field(default_factory=list)   # tarjetas fusionadas y sin desplegar
    aviso: str = ""                                          # p. ej. «no pude listar los PR»

    def con(self, estado: str) -> list[PRInfo]:
        return [p for p in self.prs if p.estado == estado]


class AlDia:
    def __init__(self, integrator, ajustes_path: Path | str | None = None):
        self.i = integrator
        self.ajustes_path = ajustes_path

    # --- proyectos ---------------------------------------------------------------------------------------------

    def proyectos(self) -> dict[str, tuple]:
        """clave (`oscarhq`, `migrateam`…) -> (carril, política), solo carriles con integrador activo."""
        i = self.i
        if not i or not i.settings.enabled:
            return {}
        return {name.removeprefix("claude-"): (i.lanes[name], pol)
                for name, pol in i.settings.policies.items() if name in i.lanes}

    # --- 1. diagnóstico (solo lectura) -----------------------------------------------------------------------------

    def diagnose(self, key: str) -> ProjectState:
        lane, policy = self.proyectos()[key]
        i = self.i
        slug = i.links.repo_slug(lane) if i.links else None
        st = ProjectState(key, lane, policy, slug or "")
        st.sin_desplegar = [tid for tid, _ in i._merged_states(lane, "")] if policy.deploy != "none" else []
        if not slug:
            st.aviso = "sin repo en GitHub: no se pueden listar los PR"
            return st
        cp = i._gh("pr", "list", "--repo", slug, "--state", "open", "--limit", "100", "--json",
                   "number,title,url,mergeable,isDraft,headRefName,headRefOid,isCrossRepository,statusCheckRollup")
        try:
            data = json.loads(cp.stdout or "[]") if cp.returncode == 0 else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, list):
            st.aviso = "no pude listar los PR (gh falló)"
            return st
        for pr in data:
            head = str(pr.get("headRefName") or "")
            if not head.startswith(BRANCH_PREFIX) or pr.get("isCrossRepository"):
                continue
            tid = head[len(BRANCH_PREFIX):]
            st.prs.append(PRInfo(int(pr["number"]), tid, pr.get("title") or head, pr.get("url") or "",
                                 self._estado(tid, pr), self._atras(slug, lane.base, head), str(pr.get("headRefOid") or "")))
        st.prs.sort(key=lambda p: p.number)
        return st

    def _estado(self, tid: str, pr: dict) -> str:
        if pr.get("isDraft"):
            return "borrador"
        if str(pr.get("mergeable") or "").upper() == "CONFLICTING":
            return "conflicto"
        estados = set()
        for c in pr.get("statusCheckRollup") or []:
            estados.add(str(c.get("conclusion") or c.get("state") or c.get("status") or "").upper())
        if estados & CI_ROJO:
            return "ci_rojo"
        if estados & CI_EN_CURSO:
            return "ci_en_curso"
        if not self.i._approved_record(tid):
            return "sin_aprobar"
        return "listo"

    def _atras(self, slug: str, base: str, head: str) -> int:
        cp = self.i._gh("api", f"repos/{slug}/compare/{base}...{head}", "--jq", ".behind_by")
        try:
            return int((cp.stdout or "0").strip()) if cp.returncode == 0 else 0
        except ValueError:
            return 0

    # --- 2. poner al día (solo ramas lane/*) -------------------------------------------------------------------------

    def poner_al_dia(self, st: ProjectState) -> list[str]:
        """Una línea por rama tocada. Nunca toca la base. Limpio → update-branch y re-ata la aprobación; conflicto →
        resolución trivial o tarjeta de rebase (`_conflict_pass`)."""
        i, umbral, out = self.i, load_ajustes(self.ajustes_path)["rama_atras_commits"], []
        for p in st.prs:
            if p.estado == "conflicto":
                out.append(self._conflicto(st, p))
            elif p.estado != "borrador" and p.atras >= umbral:
                out.append(self._actualizar(st, p))
        return out

    def _actualizar(self, st: ProjectState, p: PRInfo) -> str:
        i = self.i
        cp = i._gh("api", "-X", "PUT", f"repos/{st.slug}/pulls/{p.number}/update-branch", "-f",
                   f"expected_head_sha={p.head}")
        if cp.returncode != 0:
            log.warning("al_dia: update-branch #%s falló: %s", p.number, (cp.stderr or "").strip()[:200])
            return f"⚠️ #{p.number}: no pude ponerla al día ({p.atras} detrás); sigue como estaba"
        new = (i._pr_view(st.slug, p.number) or {}).get("headRefOid")
        if new and new != p.head:
            i.rebind_approval(p.tid, new)
            p.head = new
        p.atras = 0
        return f"🔄 #{p.number}: al día con {st.lane.base}; CI y revisión se repiten sobre la cabeza nueva"

    def _conflicto(self, st: ProjectState, p: PRInfo) -> str:
        i = self.i
        pr = i._pr_view(st.slug, p.number)
        if not pr or not pr.get("headRefOid"):
            return f"⚠️ #{p.number}: conflicto, pero no pude leer el PR"
        task = {"id": p.tid, "title": p.title}
        res = i._conflict_pass(st.lane, st.policy, p.tid, p.number, p.url, pr, task, {})
        if res == "autoresolved":
            return f"🔄 #{p.number}: conflicto trivial (solo-añadir) resuelto y empujado a lane/{p.tid}"
        if res == "sent_back":
            return f"🧩 #{p.number}: conflicto de código; tarjeta de rebase creada en {st.lane.name}"
        return f"🧩 #{p.number}: conflicto con {st.lane.base} que no se resuelve solo; revisa la ficha"

    # --- 3. integrar ------------------------------------------------------------------------------------------------

    def integrar_todo(self, key: str) -> str:
        """Monta el lote del proyecto (BatchPlanner) y devuelve el texto llano. Sin `batch` en su política no hay
        lote: se dice y no se fusiona nada a escondidas."""
        found = self.proyectos().get(key)
        batches = getattr(self.i, "batches", None)
        if not found or not batches or not found[1].batch:
            return (f"{key} no usa lote: sus PR aprobados se fusionan con el botón de cada ficha "
                    "(🔀 Fusionar), no desde aquí.")
        return batches.assemble(found[0], found[1], manual=True)

    # --- presentación -------------------------------------------------------------------------------------------------

    def resumen(self, st: ProjectState, destino: str) -> str:
        listos = st.con("listo")
        out = [f"📁 {st.key} · {destino}"]
        if st.aviso:
            out.append(f"⚠️ {st.aviso}")
        if st.prs:
            partes = [(n, len(st.con(e))) for e, n in (("listo", "listos"), ("ci_rojo", "CI rojo"),
                      ("conflicto", "conflicto"), ("ci_en_curso", "CI en curso"), ("sin_aprobar", "sin aprobar"),
                      ("borrador", "borrador"))]
            out.append(f"PR de carril: {len(st.prs)} · " + ", ".join(f"{c} {n}" for n, c in partes if c))
            umbral = load_ajustes(self.ajustes_path)["rama_atras_commits"]
            atras = [p for p in st.prs if p.atras >= umbral]
            if atras:
                out.append(f"Atrasadas (≥{umbral} commits): " + ", ".join(f"#{p.number} (−{p.atras})" for p in atras))
        elif not st.aviso:
            out.append("Sin PR abiertos de carril.")
        if st.sin_desplegar:
            out.append(f"🚀 Fusionado sin desplegar: {len(st.sin_desplegar)} (el despliegue llega con `prod`)")
        return "\n".join(out)

    def botones(self, desk, st: ProjectState) -> dict | None:
        """«✅ Integrar todo lo listo» + un botón por PR listo. None si no hay nada listo."""
        listos = st.con("listo")[:load_ajustes(self.ajustes_path)["max_prs"]]
        if not listos:
            return None
        base = {"kind": "al_dia", "lane": st.lane.name, "board": st.lane.board, "key": st.key}
        # el escritorio de decisiones exige `task_id` en todo registro: el de «integrar todo» lleva el del primer PR
        filas = [(f"✅ Integrar todo lo listo ({len(st.con('listo'))})", AD_INTEGRAR, dict(base, task_id=listos[0].tid))]
        filas += [(f"🔎 #{p.number} {p.title[:30]}", AD_PR, dict(base, task_id=p.tid)) for p in listos]
        # un token por botón: el registro de cada PR lleva su id
        return {"inline_keyboard": [desk.store.issue(rec, [[{"text": t, "action": a}]])[1]["inline_keyboard"][0]
                                    for t, a, rec in filas]}
