"""Auditor diario (auditor.py): reglas puras con fixtures, informe, ventana y creación idempotente de la tarjeta."""
from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

import auditor as A
from agent_lanes.config import Lane

DAY = 24 * 3600
# martes 29-09-2026 07:40 (hora local): ventana de un día
NOW = time.mktime((2026, 9, 29, 7, 40, 0, 0, 0, -1))
MONDAY = time.mktime((2026, 9, 28, 7, 40, 0, 0, 0, -1))

LANES = {
    "claude-oscarhq": Lane(name="claude-oscarhq", board="oscarhq", repo="C:/r/oscar-hq", base="master"),
    "claude-migrateam": Lane(name="claude-migrateam", board="migrateam", repo="C:/r/migrateam", base="develop"),
    "claude-ops": Lane(name="claude-ops", board="oscarhq", kind="ops"),
    "review": Lane(name="review", kind="review"),
}


def it(tid, title="Tarea", *, board="oscarhq", status="ready", assignee="claude-oscarhq", created=None, body="",
       comments=None, events=None):
    return {"board": board, "task": {"id": tid, "title": title, "status": status, "assignee": assignee,
                                     "created_at": NOW - 3600 if created is None else created, "body": body},
            "detail": {"comments": comments or [], "events": events or []}}


def data(items, **kw):
    # logs leídos y vacíos (None = no se pudo leer, que es un hallazgo)
    return {"items": items, "lanes": LANES, "now": NOW, "since": NOW - DAY, "profiles": {"default"},
            "hermes_log": [], "runner_log": [], **kw}


def kinds(findings):
    return [f.kind for f in findings]


def titles(findings):
    return " | ".join(f.title for f in findings)


# --- duplicados --------------------------------------------------------------------------------------------

def test_duplicates_same_day_across_boards():
    items = [it("t_aaaaaa01", "Arreglar login de Oscar HQ"), it("t_aaaaaa02", "[DECISIÓN] Arreglar el login de Oscar HQ",
                                                                 board="default", assignee="oscar")]
    f = A.check_duplicates(items)
    assert len(f) == 1 and f[0].ids == ["t_aaaaaa01", "t_aaaaaa02"] and "oscarhq / default" in f[0].detail


def test_duplicates_ignore_other_day_done_and_different_titles():
    items = [it("t_aaaaaa01", "Arreglar login de Oscar HQ"),
             it("t_aaaaaa02", "Arreglar login de Oscar HQ", created=NOW - 3 * DAY),   # otro día
             it("t_aaaaaa03", "Arreglar login de Oscar HQ", status="done"),            # ya cerrada
             it("t_aaaaaa04", "Migrar vault de Obsidian")]
    assert A.check_duplicates(items) == []


def test_previous_audit_cards_are_not_duplicates():
    items = [it("t_aaaaaa01", f"{A.TITLE_PREFIX} 2026-09-28: 3 hallazgos", assignee="oscar"),
             it("t_aaaaaa02", f"{A.TITLE_PREFIX} 2026-09-28: 4 hallazgos", assignee="oscar")]
    assert A.check_duplicates(items) == []


# --- carril equivocado -------------------------------------------------------------------------------------

def test_code_lane_task_that_looks_operational():
    items = [it("t_bbbbbb01", "Copiar la carpeta del vault de Obsidian del disco E: a C:"),
             it("t_bbbbbb02", "Arreglar bug del endpoint /api/login", body="falla el test de backend")]
    f = A.check_lane_fit(items, LANES)
    assert [x.ids for x in f] == [["t_bbbbbb01"]] and "operativa" in f[0].title


def test_ops_task_that_looks_like_code():
    items = [it("t_bbbbbb03", "Refactor de services/crm.py y abrir PR", assignee="claude-ops"),
             it("t_bbbbbb04", "Inventariar carpeta del disco E:", assignee="claude-ops")]
    f = A.check_lane_fit(items, LANES)
    assert [x.ids for x in f] == [["t_bbbbbb03"]] and "claude-ops" in f[0].title


def test_lane_task_on_another_board():
    f = A.check_lane_fit([it("t_bbbbbb05", "Arreglar bug en api.py", board="default")], LANES)
    assert f and f[0].ids == ["t_bbbbbb05"] and "board" in f[0].title


def test_lane_fit_ignores_done_and_non_lane_tasks():
    items = [it("t_bbbbbb06", "Copiar carpeta del vault", status="done"),
             it("t_bbbbbb07", "Copiar carpeta del vault", assignee="oscar")]
    assert A.check_lane_fit(items, LANES) == []


# --- triage, bloqueos, assignees ---------------------------------------------------------------------------

def test_triage():
    f = A.check_triage([it("t_cccccc01", status="triage"), it("t_cccccc02")])
    assert f[0].ids == ["t_cccccc01"]


def test_repeated_technical_blocks_not_needs_input():
    ev = [{"kind": "blocked", "payload": {"kind": "transient"}}, {"kind": "blocked", "payload": {"kind": "transient"}}]
    asks = [{"kind": "blocked", "payload": {"kind": "needs_input"}}] * 3
    items = [it("t_cccccc03", status="blocked", events=ev), it("t_cccccc04", status="blocked", events=asks),
             it("t_cccccc05", status="done", events=ev), it("t_cccccc06", status="blocked", events=ev[:1])]
    assert A.check_repeated_blocks(items)[0].ids == ["t_cccccc03"]


def test_ready_without_or_with_unknown_assignee():
    items = [it("t_dddddd01", assignee=None), it("t_dddddd02", assignee="codex"), it("t_dddddd03", assignee="codex"),
             it("t_dddddd04", assignee="claude-oscarhq"), it("t_dddddd05", assignee="default"),
             it("t_dddddd06", assignee="oscar"), it("t_dddddd07", assignee="codex", status="done")]
    known = set(LANES) | {"oscar", "default"}
    f = A.check_assignees(items, known)
    assert [x.ids for x in f] == [["t_dddddd01"], ["t_dddddd02", "t_dddddd03"]]
    assert "'codex'" in f[1].title  # una línea por assignee, no una por tarjeta


# --- tarjetas de Oscar y respuestas -----------------------------------------------------------------------

def test_oscar_cards_without_decision_tag_older_than_7_days():
    old = NOW - 8 * DAY
    items = [it("t_eeeeee01", "Revisar contrato", assignee="oscar", created=old),
             it("t_eeeeee02", "[DECISIÓN] Precio", assignee="oscar", created=old),
             it("t_eeeeee03", "Revisar web", assignee="oscar", created=NOW - 2 * DAY),
             it("t_eeeeee04", "Revisar algo", assignee="oscar", created=old, status="done")]
    assert A.check_oscar_untagged(items, NOW)[0].ids == ["t_eeeeee01"]


def test_hermes_comment_that_looks_like_oscar_answer_without_prefix():
    c = lambda body, author="default", at=NOW - 600: {"author": author, "body": body, "created_at": at}  # noqa: E731
    items = [
        it("t_ffffff01", status="blocked", comments=[c("Oscar (28-09): la sesión de Codex ya terminó, reintenta")]),
        it("t_ffffff02", status="blocked", comments=[c(f"{A.ANSWER_PREFIX} opción 2")]),              # bien
        it("t_ffffff03", status="blocked", comments=[c("Oscar dice que sí", author="oscar-telegram")]),  # no es Hermes
        it("t_ffffff04", status="blocked", comments=[c("Oscar elige la B", at=NOW - 3 * DAY)]),        # fuera de ventana
        it("t_ffffff05", status="blocked", comments=[c("BLOCKED: verificación mecánica fallida")]),     # no es respuesta
        it("t_ffffff06", status="blocked", assignee="oscar", comments=[c("Oscar: hecho")]),              # no es de carril
    ]
    f = A.check_unprefixed_answers(items, LANES, NOW - DAY, {"default"})
    assert f[0].ids == ["t_ffffff01"] and A.ANSWER_PREFIX in f[0].action


# --- ramas y MigraTeam ------------------------------------------------------------------------------------

def test_lane_branches_classified():
    integ = [{"author": "lane-integrator", "body": "INTEGRADO abc · PR x"}]
    items = [it("t_111111", status="done", comments=integ), it("t_222222", status="archived"),
             it("t_333333", status="done"), it("t_444444", status="review")]
    by_id = {x["task"]["id"]: x for x in items}
    f = A.check_lane_branches({"oscar-hq": ["t_111111", "t_222222", "t_333333", "t_444444", "t_555555"]}, by_id)
    got = {x.title: x.ids for x in f}
    assert got["Ramas lane/* remotas de tareas fusionadas o archivadas"] == ["oscar-hq:lane/t_111111",
                                                                              "oscar-hq:lane/t_222222"]
    assert got["Ramas lane/* remotas sin tarjeta en ningún board"] == ["oscar-hq:lane/t_555555"]
    assert got["Ramas lane/* de tareas done sin integrar"] == ["oscar-hq:lane/t_333333"]
    assert all("t_444444" not in i for ids in got.values() for i in ids)  # en curso: rama viva


def test_migrateam_drift():
    assert A.check_drift({"ahead": 3, "behind": 0, "oldest": NOW - 2 * DAY}, NOW) == []  # reciente: normal
    f = A.check_drift({"ahead": 5, "behind": 2, "oldest": NOW - 10 * DAY}, NOW)
    assert [x.title for x in f] == ["MigraTeam: master tiene commits que no están en develop",
                                    "MigraTeam: develop lleva cambios sin promover a producción"]
    assert "10 días" in f[1].detail
    assert A.check_drift(None, NOW) == []


# --- logs ---------------------------------------------------------------------------------------------------

HERMES_LOG = """\
2026-09-28 12:21:56,053 WARNING [s1] agent.tool_executor: Tool kanban_comment returned error (0.00s): {"error": "kanban_comment: unknown task t_b889c49a"}
2026-09-28 12:36:12,596 WARNING [s2] agent.tool_executor: Tool kanban_show returned error (0.00s): {"error": "task t_c70e4221 not found"}
2026-09-28 13:15:25,642 WARNING [s1] agent.tool_executor: Tool kanban_unblock returned error (0.01s): {"error": "could not unblock t_985109b9 (not blocked or unknown)"}
2026-09-28 14:58:28,447 WARNING [s1] gateway.run: Unrecognized slash command /decisiones from telegram — replying with unknown-command notice
2026-09-28 15:01:00,000 WARNING [s1] gateway.run: Unrecognized slash command /decisiones from telegram — replying with unknown-command notice
2026-09-28 15:02:00,000 ERROR mcp: auth failed
Traceback (most recent call last):
tools.mcp_oauth.OAuthNonInteractiveError: MCP OAuth requires browser authorization
2026-09-28 15:03:00,000 WARNING gateway.run: Unrecognized slash command /hoy from telegram
2026-09-20 10:00:00,000 WARNING agent.tool_executor: Tool kanban_show returned error (0.00s): {"error": "task t_old not found"}
"""

RUNNER_LOG = """\
2026-09-28 09:34:37,756 ERROR MainThread t_dc1e9f0c: job falló: 'dict' object has no attribute 'split'
2026-09-28 10:34:37,756 ERROR MainThread t_aa1e9f0c: job falló: 'dict' object has no attribute 'split'
2026-09-28 12:53:39,562 WARNING lane-job_1 t_985109b9 bloqueada (transient): verificación mecánica fallida: test_cmd falló (exit 1): x
2026-09-28 13:45:57,101 WARNING lane-job_1 t_985109b9 bloqueada (transient): verificación mecánica fallida: test_cmd falló (exit 1): y
2026-09-28 13:07:27,446 WARNING lane-job_0 t_102f03b6 bloqueada (needs_input): El worker necesita decisión:
2026-09-28 13:20:15,123 WARNING lane-job_0 t_a0790dfc bloqueada (needs_input): El worker necesita decisión:
2026-09-28 13:21:00,000 INFO MainThread pasada: {}
"""


def _since():
    return time.mktime((2026, 9, 28, 0, 0, 0, 0, 0, -1))


def test_hermes_errors_grouped_by_type():
    entries = A.parse_log(HERMES_LOG.splitlines(), _since())
    assert all(ts >= _since() for ts, _, _ in entries)  # la línea del 20-09 queda fuera
    f = {x.title: x for x in A.check_logs(entries, A.hermes_error_type, "errors.log")}
    board = next(x for t, x in f.items() if "board equivocado" in t)
    assert board.ids == ["t_985109b9", "t_b889c49a", "t_c70e4221"] and "3 veces" in board.detail
    assert any("/decisiones" in t and "bot equivocado" in t for t in f)
    assert not any("/hoy" in t for t in f)          # una sola vez: no es repetido
    assert not any("OAuth" in t for t in f)         # una sola vez (la traza se pega a su línea)


def test_traceback_lines_join_their_stamped_line():
    entries = A.parse_log(HERMES_LOG.splitlines(), _since())
    oauth = [txt for _, lvl, txt in entries if lvl == "ERROR"]
    assert len(oauth) == 1 and "OAuthNonInteractiveError" in oauth[0]
    assert A.hermes_error_type("ERROR", oauth[0]).startswith("MCP de Hermes sin autorización")


def test_runner_errors_grouped_and_needs_input_ignored():
    f = A.check_logs(A.parse_log(RUNNER_LOG.splitlines(), _since()), A.runner_error_type, "runner.log")
    got = {x.title: x.ids for x in f}
    assert got == {"Runner: excepción en un job: 'dict' object has no attribute 'split'": ["t_aa1e9f0c", "t_dc1e9f0c"],
                   "Runner: el test_cmd de un carril falla en la verificación": ["t_985109b9"]}


def test_missing_log_is_a_visible_finding():
    f = A.check_logs(None, A.runner_error_type, "runner.log", "C:/x/.state/runner.log")
    assert f[0].title == "No se pudo leer runner.log" and "C:/x" in f[0].detail


def test_read_log_tail_and_missing(tmp_path):
    p = tmp_path / "e.log"
    p.write_bytes(HERMES_LOG.encode("utf-8") + b"\xff\xfe basura no utf-8\n")
    assert A.read_log(p, _since())
    assert A.read_log(tmp_path / "no.log", _since()) is None


# --- ventana, informe, tarjeta ------------------------------------------------------------------------------

def test_window_monday_reaches_friday():
    assert datetime.fromtimestamp(A.window_start(MONDAY)).weekday() == 4  # viernes
    assert NOW - A.window_start(NOW) == DAY


def test_report_and_title():
    items = [it("t_cccccc01", "Comandos de marketing", status="triage"), it("t_dddddd01", "Otra cosa", assignee=None)]
    f = A.audit(data(items))
    assert kinds(f) == ["triage", "assignee"]
    assert A.title_for(f, NOW) == "[DECISIÓN] Auditoría diaria 2026-09-29: 2 hallazgos"
    text = A.report(f, NOW)
    assert "### Triage" in text and "→ complétalas" in text and "nada se ejecuta solo" in text
    assert "lunes 28 07:40" in text  # ventana: desde la ejecución anterior


def test_title_is_a_decision_card_for_the_desk():
    from agent_lanes.notices import is_decision_card
    title = A.title_for([A.Finding("x", "y", "z")], NOW)
    assert is_decision_card({"assignee": "oscar", "status": "ready", "title": title})


def test_no_findings():
    assert A.audit(data([it("t_1234567")])) == []
    assert "Sin hallazgos" in A.report([], NOW)


class FakeCP:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_create_card_uses_body_file_idempotency_and_oscar(monkeypatch):
    calls = []

    def fake_call(self, *args, timeout=120):
        calls.append((self.board, args))
        body_file = args[args.index("--body-file") + 1]
        assert Path(body_file).read_text(encoding="utf-8").startswith("- **cuerpo")  # "- " no llega como flag
        return FakeCP(0, json.dumps({"id": "t_new"}))
    monkeypatch.setattr("agent_lanes.hermes.HermesCLI._call", fake_call)
    tid = A.create_card("[DECISIÓN] Auditoría diaria 2026-09-29: 1 hallazgos", "- **cuerpo**", "auditoria-diaria-2026-09-29")
    board, args = calls[0]
    assert tid == "t_new" and board == "default" and args[0] == "create"
    assert args[args.index("--assignee") + 1] == "oscar"
    assert args[args.index("--idempotency-key") + 1] == "auditoria-diaria-2026-09-29"
    assert args[-1].startswith("[DECISIÓN] Auditoría diaria") and "--body" not in args
    assert not Path(args[args.index("--body-file") + 1]).exists()  # temporal borrado


def test_create_card_failure_raises(monkeypatch):
    monkeypatch.setattr("agent_lanes.hermes.HermesCLI._call", lambda self, *a, **k: FakeCP(1, "", "boom"))
    with pytest.raises(RuntimeError, match="kanban create"):
        A.create_card("t", "b", "k")


def _main(monkeypatch, tmp_path, items, argv):
    created, printed = [], []
    monkeypatch.setattr(A, "gather", lambda now, r, h: data(items, now=now))
    monkeypatch.setattr(A, "create_card", lambda *a: created.append(a) or "t_new")
    monkeypatch.setattr(A, "_print", printed.append)
    monkeypatch.setattr(A, "ROOT", tmp_path)
    assert A.main(argv) == 0
    return created, printed


def test_main_dry_run_prints_and_never_creates(monkeypatch, tmp_path):
    created, printed = _main(monkeypatch, tmp_path, [it("t_cccccc01", status="triage")], ["--dry-run"])
    assert created == [] and "Auditoría diaria" in printed[0] and "Tarjetas en triage" in printed[0]


def test_main_creates_one_card_with_date_key(monkeypatch, tmp_path):
    created, _ = _main(monkeypatch, tmp_path, [it("t_cccccc01", status="triage")], [])
    (title, body, key), = created
    assert key == f"auditoria-diaria-{A.day_of(time.time())}" and "1 hallazgos" in title and "triage" in body


def test_main_without_findings_creates_nothing(monkeypatch, tmp_path):
    created, _ = _main(monkeypatch, tmp_path, [it("t_1234567")], [])
    assert created == []


def test_print_without_console_under_pyw(monkeypatch):
    monkeypatch.setattr("sys.stdout", None)
    A._print("sin consola")  # pyw: sys.stdout es None y no debe romper


def test_profiles_parsed_from_hermes_table(monkeypatch):
    table = ("\r\n Profile   Model   Gateway\r\n ─────── ─────── ───────\r\n ◆default   claude   running\r\n"
             "  pildora-feedback deepseek running\r\n  nuevo  x  stopped\r\n")
    monkeypatch.setattr(A, "_run", lambda *a, **k: subprocess.CompletedProcess([], 0, table, ""))
    assert A.hermes_profiles(Path("hermes.exe")) >= {"default", "pildora-feedback", "nuevo"}
