"""Auditor diario del sistema de carriles (plan D, 28-09): detecta lo que hoy detecta un humano mirando el kanban.

Determinista (sin LLM) y de SOLO LECTURA, salvo una cosa: con hallazgos crea UNA tarjeta en el board `default`
asignada a `oscar`, titulada `[DECISIÓN] Auditoría diaria <fecha>: N hallazgos`, con el informe y acciones
SUGERIDAS (nada se ejecuta solo). `--idempotency-key auditoria-diaria-<fecha>`: dos ejecuciones el mismo día no
duplican la tarjeta. Sin hallazgos no crea nada.

    py -3.12 auditor.py --dry-run                 # imprime el informe, no crea tarjeta
    py -3.12 auditor.py                           # crea la tarjeta si hay hallazgos (tarea programada L-V 07:40)
    py -3.12 auditor.py --dry-run --runner-log <ruta> --hermes-log <ruta>   # otros logs (p. ej. desde un worktree)

Qué mira (cada regla es una función pura `check_*`; los umbrales, arriba):
- Duplicados probables: títulos parecidos (SequenceMatcher >= DUP_RATIO tras normalizar) creados el mismo día, en el
  mismo o distinto board, ambos vivos (ni done ni archivados).
- Carril equivocado (heurística por palabras, ver CODE_WORDS/OPS_WORDS): tarjeta de un carril de código que solo
  habla de discos, calendario, accesos... (parece de claude-ops) y tarjeta de claude-ops que habla de commits, PR,
  endpoints o archivos .py/.ts (parece de un carril de código). Y tarjetas de carril en un board que no es el suyo
  (el runner no las ve).
- Tarjetas en `triage`; bloqueos técnicos repetidos (>= 2 bloqueos que no son needs_input en su historia); `ready`
  sin assignee o con uno inexistente (conocidos = carriles de lanes.yaml + `oscar` + perfiles de `hermes profile
  list`, con HERMES_PROFILES de reserva).
- Tarjetas de Oscar sin etiqueta de decisión ([DECISIÓN, [SEMANA, [IDEA...) con más de 7 días; comentarios de Hermes
  en tarjetas de carril, en la ventana, con pinta de respuesta de Oscar pero sin el prefijo `RESPUESTA-OSCAR:` (no le
  llegan al worker).
- Ramas `lane/*` remotas cuya tarea está archivada, fusionada (done + INTEGRADO) o no existe; aparte, las de tareas
  done sin integrar. Desfase develop↔master de MigraTeam (GitHub compare, sin tocar el checkout).
- Errores repetidos (>= 2) en la ventana en `hermes/logs/errors.log` y `.state/runner.log`, agrupados por tipo.
Ventana: desde la ejecución anterior (lunes: desde el viernes).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

if __package__ in (None, ""):  # ejecutado como script: agent-lanes/ al path para importar agent_lanes
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_lanes import proc as _proc  # noqa: E402
from agent_lanes.config import ROOT  # noqa: E402
from agent_lanes.notices import DECISION_TAGS, OSCAR_ASSIGNEE, truncate  # noqa: E402

log = logging.getLogger("auditor")

BOARDS = ("oscarhq", "migrateam", "default", "personal")
CARD_BOARD = "default"
DAY = 24 * 3600
DUP_RATIO = 0.85
OSCAR_STALE_DAYS = 7
DEVELOP_STALE_DAYS = 7
REPEAT_MIN = 2
MAX_IDS = 3
TITLE_PREFIX = "[DECISIÓN] Auditoría diaria"
HERMES_ERRORS_LOG = Path.home() / "AppData/Local/hermes/logs/errors.log"
RUNNER_LOG = ROOT / ".state" / "runner.log"
# Perfiles de Hermes a 28-09 (`hermes profile list`): de reserva si la CLI no responde. Son assignees válidos y los
# autores de los comentarios de Hermes.
HERMES_PROFILES = frozenset({"default", "pildora-feedback"})
# Prefijo con el que Hermes debe pasar una respuesta de Oscar al worker. Lo define feat/respuestas-y-arbol (la versión
# de main de hermes.py usa "Respuesta de Oscar:" solo para los botones): si cambia allí, cambiar aquí.
ANSWER_PREFIX = "RESPUESTA-OSCAR:"
MIGRATEAM_LANE = "claude-migrateam"
ACTIVE = ("triage", "todo", "ready", "running", "blocked", "review", "scheduled")

# Heurística de carril: palabras (sin tildes, minúsculas) que sugieren código o trabajo operativo.
# Se buscan como palabra entera (\b): "api" no casa con "rápido" ni "rama" con "programa".
CODE_WORDS = ("codigo", "bug", "fix", "test", "tests", "endpoint", "componente", "refactor", "commit", "pull request", "pr",
              "rama", "migracion", "funcion", "backend", "frontend", "api", "script", "deploy", "repo", "pytest",
              "typescript", "python", "schema")
CODE_FILE_RE = re.compile(r"\b[\w/-]+\.(py|ts|tsx|js|jsx|sql|yaml|yml|toml|json|md)\b")
OPS_WORDS = ("vault", "obsidian", "disco", "calendario", "google drive", "drive", "clickup", "carpeta", "copiar",
             r"inventari\w*", "tunel", "cloudflared", r"accesos?", "cuenta de", "invitar", "compartir", "buzon", "backup",
             "correo", "gmail")
ANSWER_RE = re.compile(r"^\s*(oscar\b|respuesta\b|decisi[oó]n de oscar|opci[oó]n\s*\d|s[ií],?\s+adelante|"
                       r"aprobad[oa]\b|ok de oscar|oscar (dice|responde|elige|prefiere|ha (respondido|elegido|decidido)))",
                       re.I)
LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)? (\w+) (.*)$")
TID_RE = re.compile(r"t_[0-9a-f]{6,}")


@dataclass
class Finding:
    kind: str
    title: str
    action: str
    ids: list[str] = field(default_factory=list)
    detail: str = ""

    def render(self) -> str:
        n = f" ({len(self.ids)})" if self.ids else ""
        ids = ", ".join(self.ids[:MAX_IDS]) + (f" +{len(self.ids) - MAX_IDS}" if len(self.ids) > MAX_IDS else "")
        head = f"- **{self.title}**{n}" + (f": {ids}" if ids else "") + (f" · {self.detail}" if self.detail else "")
        return f"{head}\n  → {self.action}"


# --- utilidades puras ---------------------------------------------------------------------------------

def norm(text: str | None) -> str:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(s.split())


def norm_title(title: str | None) -> str:
    s = re.sub(r"\[[^\]]*\]", " ", title or "")  # [DECISIÓN], [SEMANA 40]...
    return " ".join(re.sub(r"[^\w\s]", " ", norm(s)).split())


def day_of(ts) -> str:
    return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d") if ts else ""


DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def when(ts: float) -> str:
    dt = datetime.fromtimestamp(ts)
    return f"{DIAS[dt.weekday()]} {dt.day} {dt:%H:%M}"


def window_start(now: float) -> float:
    """Desde la ejecución anterior: el lunes, desde el viernes (L-V)."""
    return now - DAY * (3 if datetime.fromtimestamp(now).weekday() == 0 else 1)


def _task(it: dict) -> dict:
    return it.get("task") or {}


def _alive(t: dict) -> bool:
    return t.get("status") in ACTIVE


def _kind_of(lanes: dict, assignee: str | None) -> str | None:
    lane = lanes.get(assignee or "")
    return getattr(lane, "kind", None) if lane else None


def _comments(it: dict) -> list[dict]:
    return (it.get("detail") or {}).get("comments") or []


def _group(kind: str, title: str, action: str, ids: list[str], detail: str = "") -> list[Finding]:
    return [Finding(kind, title, action, sorted(dict.fromkeys(ids)), detail)] if ids else []


# --- reglas (puras) -------------------------------------------------------------------------------------

def check_duplicates(items: list[dict]) -> list[Finding]:
    live = [it for it in items if _alive(_task(it)) and not (_task(it).get("title") or "").startswith(TITLE_PREFIX)]
    by_day: dict[str, list[dict]] = {}
    for it in live:
        by_day.setdefault(day_of(_task(it).get("created_at")), []).append(it)
    out = []
    for day, group in sorted(by_day.items()):
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                ta, tb = norm_title(_task(a).get("title")), norm_title(_task(b).get("title"))
                if ta and tb and SequenceMatcher(None, ta, tb).ratio() >= DUP_RATIO:
                    boards = a["board"] if a["board"] == b["board"] else f"{a['board']} / {b['board']}"
                    out.append(Finding("duplicados", "Duplicado probable", "archiva una de las dos o fusiónalas",
                                       [_task(a)["id"], _task(b)["id"]],
                                       f"{day} · {boards} · «{truncate(_task(a).get('title'), 50)}»"))
    return out


def _has_word(words, text: str) -> bool:
    t = norm(text)
    return any(re.search(rf"\b(?:{w})\b", t) for w in words)


def has_ops_words(text: str) -> bool:
    return _has_word(OPS_WORDS, text)


def looks_code(text: str) -> bool:
    return _has_word(CODE_WORDS, text) or bool(CODE_FILE_RE.search(text or ""))


def looks_ops(text: str) -> bool:
    return has_ops_words(text) and not looks_code(text)


def check_lane_fit(items: list[dict], lanes: dict) -> list[Finding]:
    ops_in_code, code_in_ops, wrong_board = [], [], []
    for it in items:
        t = _task(it)
        if t.get("status") not in ACTIVE or t.get("status") == "running":
            continue
        lane = lanes.get(t.get("assignee") or "")
        if not lane or lane.kind == "review":
            continue
        text = f"{t.get('title') or ''}\n{t.get('body') or ''}"
        if lane.kind == "implement" and looks_ops(text):
            ops_in_code.append(t["id"])
        elif lane.kind == "ops" and looks_code(text) and not has_ops_words(text):
            code_in_ops.append(t["id"])
        if lane.board and it["board"] != lane.board:
            wrong_board.append(t["id"])
    return (_group("carril", "Tarea de carril de código que parece operativa (sin repo)",
                   "reasígnala a claude-ops si no hay que tocar código", ops_in_code)
            + _group("carril", "Tarea de claude-ops que parece de código",
                     "reasígnala al carril claude-<repo> que corresponda", code_in_ops)
            + _group("carril", "Tarea de carril en un board que no es el de su carril (el runner no la ve)",
                     "muévela al board del carril o recréala allí", wrong_board))


def check_triage(items: list[dict]) -> list[Finding]:
    ids = [_task(it)["id"] for it in items if _task(it).get("status") == "triage"]
    return _group("triage", "Tarjetas en triage", "complétalas y pásalas a ready, o archívalas", ids)


def transient_blocks(detail: dict | None) -> int:
    """Bloqueos que no son preguntas a Oscar. Hermes registra la repetición como `block_loop_detected` (y pasa la
    tarjeta a triage) con `recurrences`: se toma el mayor de los dos recuentos."""
    evs = [e for e in (detail or {}).get("events") or ()
           if e.get("kind") in ("blocked", "block_loop_detected")
           and (e.get("payload") or {}).get("kind") != "needs_input"]
    recurrences = max((int((e.get("payload") or {}).get("recurrences") or 0) for e in evs), default=0)
    return max(len(evs), recurrences)


def check_repeated_blocks(items: list[dict]) -> list[Finding]:
    ids = [_task(it)["id"] for it in items
           if _alive(_task(it)) and transient_blocks(it.get("detail")) >= REPEAT_MIN]
    return _group("bloqueos", "Bloqueos técnicos repetidos (>= 2 en su historia)",
                  "mira el último motivo en la tarjeta: suele ser el mismo fallo del gate o del entorno", ids)


def check_assignees(items: list[dict], known: set[str]) -> list[Finding]:
    none, unknown = [], {}
    for it in items:
        t = _task(it)
        if t.get("status") != "ready":
            continue
        a = t.get("assignee")
        if not a:
            none.append(t["id"])
        elif a not in known:
            unknown.setdefault(a, []).append(t["id"])
    out = _group("assignee", "Ready sin assignee (nadie la cogerá)", "asígnala a un carril, a oscar o archívala", none)
    for a, ids in sorted(unknown.items()):
        out += _group("assignee", f"Ready asignadas a '{a}', que no es carril ni perfil de Hermes",
                      "reasígnalas a un carril (claude-*) o a oscar; si el agente está en pausa, archívalas", ids)
    return out


def check_oscar_untagged(items: list[dict], now: float) -> list[Finding]:
    limit = now - OSCAR_STALE_DAYS * DAY
    old = [it for it in items if _task(it).get("assignee") == OSCAR_ASSIGNEE and _task(it).get("status") in (
        "ready", "blocked", "todo") and (_task(it).get("created_at") or now) < limit
        and not any(tag in (_task(it).get("title") or "") for tag in DECISION_TAGS)]
    old.sort(key=lambda it: _task(it).get("created_at") or 0)
    return _group("oscar", f"Tarjetas tuyas sin etiqueta de decisión con más de {OSCAR_STALE_DAYS} días",
                  "etiquétalas ([DECISIÓN], [SEMANA], [IDEA]) o archívalas; sin etiqueta no salen en /decisiones",
                  [_task(it)["id"] for it in old])


def check_unprefixed_answers(items: list[dict], lanes: dict, since: float, authors: set[str]) -> list[Finding]:
    ids = []
    for it in items:
        t = _task(it)
        if _kind_of(lanes, t.get("assignee")) not in ("implement", "ops") or t.get("status") not in ACTIVE:
            continue
        for c in _comments(it):
            body = c.get("body") or ""
            if (c.get("author") in authors and (c.get("created_at") or 0) >= since
                    and not body.lstrip().startswith(ANSWER_PREFIX) and ANSWER_RE.search(body)):
                ids.append(t["id"])
                break
    return _group("respuestas", f"Comentarios de Hermes con pinta de respuesta tuya sin `{ANSWER_PREFIX}`",
                  f"repítela empezando por `{ANSWER_PREFIX}` (si no, el worker no la ve al retomar)", ids)


def _integrated(it: dict) -> bool:
    return any((c.get("body") or "").lstrip().startswith("INTEGRADO") for c in _comments(it))


def check_lane_branches(branches: dict[str, list[str]], by_id: dict[str, dict]) -> list[Finding]:
    stale, orphan, pending = [], [], []
    for repo, tids in sorted(branches.items()):
        for tid in tids:
            it = by_id.get(tid)
            label = f"{repo}:lane/{tid}"
            if it is None:
                orphan.append(label)
            elif _task(it).get("status") == "archived" or (_task(it).get("status") == "done" and _integrated(it)):
                stale.append(label)
            elif _task(it).get("status") == "done":
                pending.append(label)
    return (_group("ramas", "Ramas lane/* remotas de tareas fusionadas o archivadas",
                   "bórralas en GitHub (git push origin --delete lane/<id>) cuando lo confirmes", stale)
            + _group("ramas", "Ramas lane/* remotas sin tarjeta en ningún board",
                     "comprueba si el trabajo está en la base y bórralas", orphan)
            + _group("ramas", "Ramas lane/* de tareas done sin integrar",
                     "decide: aprobar e integrar el PR, o descartar la rama", pending))


def check_drift(drift: dict | None, now: float) -> list[Finding]:
    if not drift:
        return []
    out = []
    behind, ahead = int(drift.get("behind") or 0), int(drift.get("ahead") or 0)
    if behind:
        out.append(Finding("migrateam", "MigraTeam: master tiene commits que no están en develop",
                           "haz el back-merge master→develop (hotfix sin bajar a staging)", detail=f"{behind} commit(s)"))
    oldest = drift.get("oldest")
    if ahead and oldest and now - oldest > DEVELOP_STALE_DAYS * DAY:
        days = int((now - oldest) // DAY)
        out.append(Finding("migrateam", "MigraTeam: develop lleva cambios sin promover a producción",
                           "valora un release/<fecha> (promoción con tu OK)",
                           detail=f"{ahead} commit(s), el más antiguo de hace {days} días"))
    return out


def parse_log(lines, since: float) -> list[tuple[float, str, str]]:
    """[(ts, nivel, texto)] con ts >= since; las líneas sin fecha (trazas) se pegan a la anterior."""
    out: list[list] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        m = LOG_TS_RE.match(line)
        if m:
            try:
                ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                continue
            out.append([ts, m.group(2).upper(), m.group(3)])
        elif out and line.strip():
            out[-1][2] += "\n" + line
    return [(ts, lvl, txt) for ts, lvl, txt in out if ts >= since]


def hermes_error_type(level: str, text: str) -> str | None:
    if re.search(r"kanban_\w+ returned error.*(not found|unknown task|or unknown)", text, re.S):
        return "Hermes busca tarjetas que no existen en su board (kanban_* task not found → board equivocado)"
    m = re.search(r"Unrecognized slash command (/\w+)", text)
    if m:
        return f"Comando {m.group(1)} enviado al bot equivocado (Unrecognized slash command)"
    if "OAuth" in text:
        return "MCP de Hermes sin autorización OAuth (hermes mcp login)"
    if "Tool loop warning" in text or "same_tool_failure" in text:
        return "Hermes en bucle repitiendo una herramienta que falla"
    if re.search(r"\[Telegram\].*(failed|ConnectError|stuck)", text):
        return None  # 28-09 (Oscar): cortes de red transitorios que Hermes recupera solo; solo meten ruido
    m = re.search(r"Tool (\w+) returned error", text)
    if m:
        return f"Hermes: la herramienta {m.group(1)} devuelve error"
    return None


def runner_error_type(level: str, text: str) -> str | None:
    if "bloqueada (needs_input)" in text:
        return None  # es el flujo normal (pregunta a Oscar), no un error
    if "test_cmd falló" in text:
        return "Runner: el test_cmd de un carril falla en la verificación"
    if "runner reiniciado" in text:
        return "Runner reiniciado con tareas en curso"
    if "limpieza pendiente" in text or "worktree no eliminado" in text:
        return "Runner: worktrees sin poder limpiar"
    if "worktree add falló" in text:
        return "Runner: no puede crear el worktree de una tarea"
    m = re.search(r"job falló: (.+)", text)
    if m:
        return "Runner: excepción en un job: " + truncate(m.group(1).splitlines()[0], 60)
    if level in ("ERROR", "CRITICAL"):
        first = TID_RE.sub("t_…", text.splitlines()[0])
        return "Runner: " + truncate(re.sub(r"\b[0-9a-f]{7,40}\b|\d+", "…", first), 70)
    return None


def check_logs(entries: list[tuple[float, str, str]] | None, classify, source: str,
               missing_note: str | None = None) -> list[Finding]:
    if entries is None:
        return [Finding("logs", f"No se pudo leer {source}", "comprueba la ruta del log", detail=missing_note or "")]
    counts: dict[str, list[str]] = {}
    for _, lvl, txt in entries:
        kind = classify(lvl, txt)
        if kind:
            counts.setdefault(kind, []).extend(TID_RE.findall(txt)[:1] or [""])
    out = []
    for kind, hits in sorted(counts.items(), key=lambda kv: -len(kv[1])):
        if len(hits) >= REPEAT_MIN:
            ids = sorted({h for h in hits if h})
            out.append(Finding("logs", kind, "revisa la causa: se repite", ids, f"{len(hits)} veces en {source}"))
    return out


# --- informe ----------------------------------------------------------------------------------------------

def audit(data: dict) -> list[Finding]:
    items, lanes, now = data["items"], data["lanes"], data["now"]
    since = data.get("since") or window_start(now)
    by_id = {_task(it)["id"]: it for it in items if _task(it).get("id")}
    known = set(lanes) | {OSCAR_ASSIGNEE} | set(data.get("profiles") or HERMES_PROFILES)
    return (check_duplicates(items) + check_lane_fit(items, lanes) + check_triage(items)
            + check_repeated_blocks(items) + check_assignees(items, known) + check_oscar_untagged(items, now)
            + check_unprefixed_answers(items, lanes, since, set(data.get("profiles") or HERMES_PROFILES))
            + check_lane_branches(data.get("branches") or {}, by_id) + check_drift(data.get("drift"), now)
            + check_logs(data.get("hermes_log"), hermes_error_type, "errors.log de Hermes", data.get("hermes_log_path"))
            + check_logs(data.get("runner_log"), runner_error_type, "runner.log", data.get("runner_log_path")))


def title_for(findings: list[Finding], now: float) -> str:
    return f"{TITLE_PREFIX} {day_of(now)}: {len(findings)} hallazgos"


def report(findings: list[Finding], now: float, notes: list[str] = ()) -> str:
    since = window_start(now)
    head = [f"Auditoría determinista del sistema de carriles ({day_of(now)}; ventana desde "
            f"{when(since)}). Solo SUGERENCIAS: nada se ejecuta solo.", ""]
    if not findings:
        return "\n".join(head + ["Sin hallazgos."] + [f"_Nota: {n}_" for n in notes])
    sections: dict[str, list[Finding]] = {}
    for f in findings:
        sections.setdefault(f.kind, []).append(f)
    names = {"duplicados": "Duplicados", "carril": "Carril equivocado", "triage": "Triage", "bloqueos": "Bloqueos",
             "assignee": "Assignees", "oscar": "Tus tarjetas", "respuestas": "Respuestas que no llegan al worker",
             "ramas": "Ramas", "migrateam": "MigraTeam develop↔master", "logs": "Errores repetidos"}
    body = []
    for kind, fs in sections.items():
        body.append(f"### {names.get(kind, kind)}")
        body += [f.render() for f in fs]
        body.append("")
    tail = [f"_Nota: {n}_" for n in notes]
    tail.append("Generado por tools/agent-lanes/auditor.py (reglas y umbrales en su docstring). Archiva esta tarjeta "
                "cuando lo hayas revisado.")
    return "\n".join(head + body + tail)


# --- IO -----------------------------------------------------------------------------------------------------

def _run(args: list[str], timeout: int = 120, cwd: str | None = None) -> subprocess.CompletedProcess:
    return _proc.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                     stdin=subprocess.DEVNULL, **({"cwd": cwd} if cwd else {}))


def collect_tasks(hermes_for, boards=BOARDS) -> list[dict]:
    items = []
    for b in boards:
        cp = hermes_for(b)._call("list", "--json", "--archived")
        if cp.returncode != 0:
            log.warning("list del board %s falló: %s", b, (cp.stderr or "")[:200])
            continue
        items += [{"board": b, "task": t, "detail": None} for t in json.loads(cp.stdout or "[]")]
    return items


def needs_detail(it: dict, lanes: dict, branch_ids: set[str]) -> bool:
    t = _task(it)
    lane_kind = _kind_of(lanes, t.get("assignee"))
    if t.get("id") in branch_ids and t.get("status") == "done":
        return True  # ¿INTEGRADO?
    if lane_kind in ("implement", "ops") and t.get("status") in ACTIVE:
        return True  # bloqueos repetidos y comentarios de Hermes
    return t.get("status") == "blocked"


def fetch_details(items: list[dict], hermes_for, lanes: dict, branch_ids: set[str], workers: int = 4) -> None:
    todo = [it for it in items if needs_detail(it, lanes, branch_ids)]

    def fetch(it):
        try:
            it["detail"] = hermes_for(it["board"]).show(_task(it)["id"])
        except Exception as exc:  # una tarjeta ilegible no tumba la auditoría
            log.warning("show %s falló: %s", _task(it).get("id"), exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(fetch, todo))


def hermes_profiles(exe: Path) -> set[str]:
    try:
        cp = _run([str(exe), "profile", "list"], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set(HERMES_PROFILES)
    names, started = set(), False
    for line in (cp.stdout or "").splitlines():
        if "─" in line:
            started = True
            continue
        if started and line.strip():
            names.add(line.strip().lstrip("◆*").split()[0])
    return names | set(HERMES_PROFILES)


def lane_branches(lanes: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for repo, remote in dict.fromkeys((l.repo, l.remote) for l in lanes.values() if l.kind == "implement" and l.repo):
        try:
            cp = _run(["git", "-C", repo, "ls-remote", "--heads", remote, "lane/*"], timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("ls-remote %s falló: %s", repo, exc)
            continue
        if cp.returncode != 0:
            log.warning("ls-remote %s falló: %s", repo, (cp.stderr or "")[:200])
            continue
        tids = [ref.rsplit("lane/", 1)[1] for ref in re.findall(r"refs/heads/(lane/\S+)", cp.stdout or "")]
        if tids:
            out[Path(repo).name] = tids
    return out


def migrateam_drift(lanes: dict, gh_exe: str) -> dict | None:
    """ahead = develop por delante de master; behind = master con commits que develop no tiene. GitHub compare: no
    toca el checkout (ni fetch)."""
    from agent_lanes.notices import LinkBuilder
    lane = lanes.get(MIGRATEAM_LANE)
    slug = LinkBuilder(None).repo_slug(lane) if lane else None
    if not slug:
        return None
    try:
        cp = _run([gh_exe, "api", f"repos/{slug}/compare/master...develop", "--jq",
                   "{ahead: .ahead_by, behind: .behind_by, oldest: .commits[0].commit.committer.date}"],
                  cwd=tempfile.gettempdir())
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("gh compare falló: %s", exc)
        return None
    if cp.returncode != 0:
        log.warning("gh compare falló: %s", (cp.stderr or "")[:200])
        return None
    d = json.loads(cp.stdout or "{}")
    oldest = d.get("oldest")
    if oldest:
        oldest = datetime.fromisoformat(oldest.replace("Z", "+00:00")).timestamp()
    return {"ahead": d.get("ahead") or 0, "behind": d.get("behind") or 0, "oldest": oldest}


def read_log(path: Path, since: float, max_bytes: int = 4_000_000) -> list[tuple[float, str, str]] | None:
    """Solo el final del archivo (errors.log de Hermes pesa MB); None si no existe o no se puede leer."""
    try:
        with open(path, "rb") as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - max_bytes))
            data = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    return parse_log(data.splitlines(), since)


def gather(now: float, runner_log: Path, hermes_log: Path) -> dict:
    from agent_lanes.config import load_lanes
    from agent_lanes.hermes import HERMES_EXE, HermesCLI
    from agent_lanes.integrator import GH_EXE
    lanes = load_lanes()
    cache: dict[str, HermesCLI] = {}
    hermes_for = lambda b: cache.setdefault(b, HermesCLI(b, author="auditor"))  # noqa: E731
    since = window_start(now)
    items = collect_tasks(hermes_for)
    branches = lane_branches(lanes)
    fetch_details(items, hermes_for, lanes, {t for ts in branches.values() for t in ts})
    return {"items": items, "lanes": lanes, "now": now, "since": since, "profiles": hermes_profiles(HERMES_EXE),
            "branches": branches, "drift": migrateam_drift(lanes, GH_EXE),
            "hermes_log": read_log(hermes_log, since), "hermes_log_path": str(hermes_log),
            "runner_log": read_log(runner_log, since), "runner_log_path": str(runner_log)}


def create_card(title: str, body: str, key: str) -> str:
    """Una tarjeta en default para oscar. --body-file: un cuerpo que empieza por "- " no se toma como flag."""
    from agent_lanes.hermes import HermesCLI
    h = HermesCLI(CARD_BOARD, author="auditor")
    fd, tmp = tempfile.mkstemp(prefix="auditoria-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        cp = h._call("create", "--assignee", OSCAR_ASSIGNEE, "--idempotency-key", key, "--body-file", tmp,
                     "--created-by", "auditor", "--json", title)
    finally:
        os.remove(tmp)
    if cp.returncode != 0:
        raise RuntimeError(f"kanban create falló: {(cp.stderr or '').strip()[:300]}")
    try:
        return str(json.loads(cp.stdout).get("id") or cp.stdout.strip())
    except (json.JSONDecodeError, AttributeError):
        return (cp.stdout or "").strip()[:80]


AUDIT_TITLE_PREFIX = "[DECISIÓN] Auditoría diaria"


def archive_previous(new_id: str) -> list[str]:
    """Archiva las auditorías anteriores aún abiertas: solo vale la del día (28-09, Oscar: sin acumular tarjetas)."""
    from agent_lanes.hermes import HermesCLI
    h = HermesCLI(CARD_BOARD, author="auditor")
    cp = h._call("list", "--json")
    try:
        tasks = json.loads(cp.stdout or "[]") if cp.returncode == 0 else []
    except json.JSONDecodeError:
        tasks = []
    done = []
    for t in tasks:
        if (t.get("id") != new_id and str(t.get("title") or "").startswith(AUDIT_TITLE_PREFIX)
                and t.get("status") not in ("done", "archived")):
            if h._call("archive", t["id"]).returncode == 0:
                done.append(t["id"])
    if done:
        log.info("auditorías anteriores archivadas: %s", done)
    return done


def _print(text: str) -> None:
    if sys.stdout is None:  # pyw: sin consola
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Auditor diario del sistema de carriles (sin LLM)")
    ap.add_argument("--dry-run", action="store_true", help="imprime el informe; no crea tarjeta")
    ap.add_argument("--runner-log", type=Path, default=RUNNER_LOG)
    ap.add_argument("--hermes-log", type=Path, default=HERMES_ERRORS_LOG)
    args = ap.parse_args(argv)
    state = ROOT / ".state"
    if not args.dry_run:
        state.mkdir(exist_ok=True)
        logging.basicConfig(filename=str(state / "auditor.log"), level=logging.INFO, encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        now = time.time()
        findings = audit(gather(now, args.runner_log, args.hermes_log))
        title, body = title_for(findings, now), report(findings, now)
        if args.dry_run:
            _print(f"{title}\n\n{body}" if findings else f"Sin hallazgos: no se crearía tarjeta.\n\n{body}")
            return 0
        if not findings:
            log.info("sin hallazgos: no se crea tarjeta")
            return 0
        tid = create_card(title, body, f"auditoria-diaria-{day_of(now)}")
        log.info("tarjeta %s: %s", tid, title)
        archive_previous(tid)
        _print(f"{tid}: {title}")
        return 0
    except Exception:
        log.exception("auditoría fallida")
        raise


if __name__ == "__main__":
    sys.exit(main())
