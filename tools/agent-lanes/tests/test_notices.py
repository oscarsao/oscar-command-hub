"""Un mensaje por tarea (27-09): formato, almacén de message_id, edición vs nuevo, sanitizado, URLs, limpieza."""
from __future__ import annotations

import io
import json
import subprocess
import urllib.error

import pytest

from agent_lanes import notices, telegram
from agent_lanes.config import Lane
from agent_lanes.git_ops import GitOps
from agent_lanes.hermes import HermesCLI
from agent_lanes.notices import (MessageStore, TaskNotices, card_url, compare_url, github_repo_url, questions_block,
                                 render, truncate)
from agent_lanes.review import ReviewRunner
from agent_lanes.runner import LaneRunner
from agent_lanes.verify import VerifyResult
from tests.test_phase2 import META, REVIEW, FakeReviewer, FakeReviewGit, ReviewHermes, verdict
from tests.test_runner_state import (GOOD, LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker,
                                     ok_outcome)

RAW_GIT = ("fatal: 'C:\\Users\\oscar\\dev\\_lanes\\lane-t_5ef35468' contains modified or untracked files, "
           "use --force to delete it")
TRACE = 'Traceback (most recent call last):\n  File "C:\\x\\runner.py", line 1\nRuntimeError: boom'


def assert_clean(text: str) -> None:
    for bad in ("fatal:", "C:\\", "Traceback", "--force", "stderr", "session_id"):
        assert bad not in text, (bad, text)


# --- formato ----------------------------------------------------------------------------------------

def test_truncate_title_to_60_with_ellipsis():
    t = truncate("x" * 80, 60)
    assert len(t) == 60 and t.endswith("…")
    assert truncate("  corto \n título ", 60) == "corto título"


def test_render_card_format_and_escapes_html():
    body = "Modo OASP: Spec-Lite\n\n## Objetivo\n- Que el <aviso> tenga **contexto**\n\n## Criterios\n- x"
    text = render("review", "t_1", "Arregla <script> & " + "y" * 80, "claude-oscarhq", "en review · 2 archivos",
                  [("🗂 Tarjeta", "https://k/t?a=1&b=2"), ("🔀 Cambios", "https://github.com/o/r/compare/m...lane/t_1")],
                  body=body)
    lines = text.split("\n")
    assert len(lines) == 5
    assert lines[0].startswith("🔍 t_1 · Arregla &lt;script&gt; &amp; ") and lines[0].endswith("…")
    assert lines[1] == "Píldora · claude-oscarhq · Spec-Lite"
    assert lines[2] == "Qué: Que el &lt;aviso&gt; tenga contexto"
    assert lines[3] == "en review · 2 archivos"
    assert "<script>" not in text
    assert lines[4].startswith('<a href="https://k/t?a=1&amp;b=2">🗂 Tarjeta</a> · ')
    # sin objetivo ni enlaces: cabecera, contexto y estado
    assert render("running", "t_1", "T", "l", "en curso").split("\n") == ["▶️ t_1 · T", "l", "en curso"]


def test_questions_block_max_5_items_and_limit_total():
    qs = [f"pregunta {i} " + "z" * 200 for i in range(8)]
    block = questions_block(qs)
    assert 2 <= len(block) <= 5 and len("\n".join(block)) <= notices.QUESTIONS_MAX_CHARS
    assert all(b.startswith("• ") for b in block)
    short = questions_block([f"¿{i}?" for i in range(9)])
    assert len(short) == 5
    assert questions_block([]) == []


# --- URLs -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("remote,expected", [
    ("git@github.com:oscarsao/oscar-hq.git", "https://github.com/oscarsao/oscar-hq"),
    ("ssh://git@github.com/oscarsao/oscar-hq.git", "https://github.com/oscarsao/oscar-hq"),
    ("https://github.com/PildoraDigital/OCR-PDF-and-images.git", "https://github.com/PildoraDigital/OCR-PDF-and-images"),
    ("https://github.com/oscarsao/Scraper/", "https://github.com/oscarsao/Scraper"),
    ("https://oscarsao:ghp_SECRET123@github.com/oscarsao/oscar-hq.git", "https://github.com/oscarsao/oscar-hq"),
    ("https://gitlab.com/a/b.git", None),
    ("C:/repos/local", None),
    ("", None),
    (None, None),
])
def test_github_repo_url(remote, expected):
    assert github_repo_url(remote) == expected


def test_compare_url_uses_base_and_lane_branch_and_never_leaks_credentials():
    url = compare_url("https://u:ghp_SECRET@github.com/oscarsao/Scraper.git", "cambio/multiverse-benchmark-harness",
                      "t_5ef35468")
    assert url == "https://github.com/oscarsao/Scraper/compare/cambio/multiverse-benchmark-harness...lane/t_5ef35468"
    assert "SECRET" not in url
    assert compare_url("git@gitlab.com:a/b.git", "master", "t_1") is None


@pytest.mark.parametrize("base,expected", [
    (None, None),
    ("", None),
    ("https://hq.example/kanban/", "https://hq.example/kanban/tasks/oscarhq/t_1"),
    ("https://hq.example/k?board={board}&task={id}", "https://hq.example/k?board=oscarhq&task=t_1"),
])
def test_card_url(base, expected):
    assert card_url(base, "oscarhq", "t_1") == expected


def test_link_builder_reads_origin_once_and_omits_card_without_base():
    calls = []

    def run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "git@github.com:oscarsao/oscar-hq.git\n", "")

    lb = notices.LinkBuilder(None, runner=run)
    assert lb(LANE, "t_1") == [("🔀 Cambios", "https://github.com/oscarsao/oscar-hq/compare/master...lane/t_1")]
    assert lb(LANE, "t_2")[0][1].endswith("...lane/t_2")
    assert calls == [["git", "-C", "C:/repo", "remote", "get-url", "origin"]]
    assert notices.LinkBuilder("https://k", runner=run)(LANE, "t_1", branch=False) == [
        ("🗂 Tarjeta", "https://k/tasks/oscarhq/t_1")]


# --- almacén + edición vs mensaje nuevo ---------------------------------------------------------------

def test_first_publish_sends_silently_then_edits_same_message(tmp_path):
    n = FakeNotifier()
    tn = TaskNotices(n, MessageStore(tmp_path))
    tn.publish("t_1", "a")
    tn.publish("t_1", "b")
    assert n.msgs == ["a"] and n.silent == [True] and n.edits == [(101, "b")]
    assert json.loads((tmp_path / "t_1.json").read_text())["message_id"] == 101


def test_message_id_survives_restart(tmp_path):
    n = FakeNotifier()
    TaskNotices(n, MessageStore(tmp_path)).publish("t_1", "a")
    TaskNotices(n, MessageStore(tmp_path)).publish("t_1", "b")  # new process, same .state
    assert len(n.msgs) == 1 and n.edits == [(101, "b")]


def test_failed_edit_sends_new_message_and_updates_id(tmp_path):
    n = FakeNotifier(edit_ok=False)
    store = MessageStore(tmp_path)
    tn = TaskNotices(n, store)
    tn.publish("t_1", "a")
    tn.publish("t_1", "b")
    assert n.msgs == ["a", "b"] and n.silent == [True, True]
    assert store.get("t_1")["message_id"] == 102


def test_alert_sends_new_notifying_message_and_replaces_the_old_one():
    n = FakeNotifier()
    store = MessageStore()
    tn = TaskNotices(n, store)
    tn.publish("t_1", "progreso")
    tn.publish("t_1", "necesita a Oscar", alert=True)
    assert n.msgs == ["progreso", "necesita a Oscar"] and n.silent == [True, False]
    assert n.edits == [] and n.deleted == [101] and store.get("t_1")["message_id"] == 102


def test_disabled_telegram_stores_nothing():
    class Off(FakeNotifier):
        def send(self, *a, **k):
            return None

    store = MessageStore()
    TaskNotices(Off(), store).publish("t_1", "a")
    assert store.get("t_1") is None


# --- runner: sanitizado de errores y needs_input ---------------------------------------------------

def test_verification_error_never_reaches_telegram_raw():
    class RawVerifier(FakeVerifier):
        def __call__(self, *a):
            return VerifyResult(ok=False, reasons=[RAW_GIT], test_exit=1)

    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    n = FakeNotifier()
    r = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=RawVerifier(), notify=n)
    assert r.run_once() == {"t_1": "blocked:transient"}
    block = [c for c in h.calls if c[0] == "block"][0]
    assert RAW_GIT in block[3]  # detalle completo en la tarjeta
    assert n.silent == [True, False]  # progreso silencioso + alerta que notifica
    assert "⛔" in n.msgs[-1] and "verificación mecánica fallida" in n.msgs[-1]
    for text in n.msgs + [e[1] for e in n.edits]:
        assert_clean(text)


def test_runner_exception_with_trace_is_sanitized():
    class Boom(FakeWorker):
        def run(self, *a, **k):
            raise RuntimeError(TRACE)

    n = FakeNotifier()
    r = LaneRunner(LANE, hermes=FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}]), git=FakeGit(),
                   worker=Boom([]), verifier=FakeVerifier(), notify=n)
    assert r.run_once() == {"t_1": "blocked:transient"}
    assert "error del runner" in n.msgs[-1]
    assert_clean(n.msgs[-1])


def test_needs_input_alert_has_short_bullets_and_reply_hint():
    qs = ["¿A o B? " + "detalle " * 40] + [f"¿q{i}?" for i in range(7)]
    out = ok_outcome({**GOOD, "status": "needs_input", "questions": qs})
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    n = FakeNotifier()
    LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([out]), verifier=FakeVerifier(), notify=n).run_once()
    alert = n.msgs[-1]
    assert n.silent[-1] is False and alert.startswith("❓ t_1")
    assert "responde en este hilo o a Hermes" in alert
    bullets = [l for l in alert.split("\n") if l.startswith("• ")]
    assert 1 <= len(bullets) <= 5 and len("\n".join(bullets)) <= notices.QUESTIONS_MAX_CHARS


def test_review_status_line_has_files_tests_and_cost():
    out = ok_outcome({**GOOD, "changed_files": ["a.py", "b.py"]})
    out = out.__class__(**{**out.__dict__, "cost_usd": 1.72})
    n = FakeNotifier()
    LaneRunner(LANE, hermes=FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}]), git=FakeGit(),
               worker=FakeWorker([out]), verifier=FakeVerifier(), notify=n).run_once()
    assert n.edits[-1][1].split("\n")[2] == "en review · 2 archivos · tests OK · 1,7 $"
    assert "ok" not in n.edits[-1][1].split("\n")[1:]  # el resumen del worker no va a Telegram


def test_runner_drops_hermes_subscription_after_claim_and_survives_failure():
    h = FakeHermes(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
               notify=FakeNotifier()).run_once()
    kinds = h.kinds()
    assert kinds.index("claim") < kinds.index("drop_subs")

    class Broken(FakeHermes):
        def drop_telegram_subs(self, tid):
            raise RuntimeError("hermes caído")

    h2 = Broken(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
    r = LaneRunner(LANE, hermes=h2, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                   notify=FakeNotifier())
    assert r.run_once() == {"t_1": "review"}


# --- una tarea cruza carriles: implementador -> review -> implementador, un solo mensaje ---------------

def test_task_keeps_one_message_across_lanes(tmp_path):
    store = MessageStore(tmp_path)
    n = FakeNotifier()
    task = {"id": "t_1", "title": "T", "body": "B"}
    LaneRunner(LANE, hermes=FakeHermes(tasks=[task]), git=FakeGit(), worker=FakeWorker([ok_outcome()]),
               verifier=FakeVerifier(), notify=n, messages=store).run_once()
    rh = ReviewHermes([{**task, "assignee": "claude-oscarhq", "status": "review"}], meta=META)
    ReviewRunner(REVIEW, {"claude-oscarhq": LANE}, hermes_for=lambda b: rh, git=FakeReviewGit(),
                 reviewer=FakeReviewer([verdict("request_changes", ["añade test"])]), verifier=FakeVerifier(),
                 notify=n, messages=store).run_once()
    LaneRunner(LANE, hermes=FakeHermes(tasks=[task]), git=FakeGit(), worker=FakeWorker([ok_outcome()]),
               verifier=FakeVerifier(), notify=n, messages=store).run_once()
    assert len(n.msgs) == 1  # ▶️ -> 🔍 -> 🔁 -> ▶️ -> 🔍, todo editando el mismo mensaje
    assert [e[1][0] for e in n.edits] == ["🔍", "🔁", "▶", "🔍"]
    assert {e[0] for e in n.edits} == {101}
    changes = n.edits[1][1]
    assert "añade test" not in changes and "cambios pedidos (ronda 1)" in changes  # los cambios van a la tarjeta


def test_review_done_is_a_new_alert_and_cleanup_error_goes_to_card_only(tmp_path):
    class DirtyGit(FakeReviewGit):
        def cleanup(self, lane, tid):
            return False, RAW_GIT

    store = MessageStore(tmp_path)
    store.put("t_1", {"chat_id": "-1", "thread_id": "5", "message_id": 77})
    rh = ReviewHermes([{"id": "t_1", "title": "T", "body": "B", "assignee": "claude-oscarhq", "status": "review"}],
                      meta={**META, "changed_files": ["a", "b"], "cost_usd": 1.2})
    n = FakeNotifier()
    r = ReviewRunner(REVIEW, {"claude-oscarhq": LANE}, hermes_for=lambda b: rh, git=DirtyGit(),
                     reviewer=FakeReviewer([verdict("approve")]), verifier=FakeVerifier(), notify=n, messages=store)
    assert r.run_once() == {"t_1": "done"}
    assert n.silent == [False] and n.deleted == [77]
    text = n.msgs[0]
    assert text.startswith("✅ t_1") and "review aprobada · 2 archivos · tests OK · 1,4 $" in text
    assert "lista para merge" in text and "limpieza local pendiente" in text
    assert_clean(text)
    assert any(c[0] == "comment" and RAW_GIT in c[2] for c in rh.calls)


# --- primitivas de Telegram ------------------------------------------------------------------------

class Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def http_error(code, description):
    return urllib.error.HTTPError("https://api.telegram.org/botSECRET/x", code, "Bad Request", {},
                                  io.BytesIO(json.dumps({"ok": False, "description": description}).encode()))


def test_send_is_html_silent_and_returns_message_id(monkeypatch):
    sent = []

    def urlopen(req, timeout):
        sent.append((req.full_url.rsplit("/", 1)[1], json.loads(req.data)))
        return Resp({"ok": True, "result": {"message_id": 42}})

    monkeypatch.setattr(telegram.urllib.request, "urlopen", urlopen)
    n = telegram.TelegramNotifier("tok", "-1", "5")
    assert n.send("<b>x</b>", silent=True) == {"chat_id": "-1", "thread_id": "5", "message_id": 42}
    method, payload = sent[0]
    assert method == "sendMessage" and payload["parse_mode"] == "HTML" and payload["disable_notification"] is True
    assert n.send("y")["message_id"] == 42 and "disable_notification" not in sent[1][1]


@pytest.mark.parametrize("description,expected", [
    ("Bad Request: message is not modified: specified new message content is the same", True),
    ("Bad Request: message to edit not found", False),
    ("Bad Request: message can't be edited", False),
])
def test_edit_outcomes(monkeypatch, description, expected):
    def urlopen(req, timeout):
        raise http_error(400, description)

    monkeypatch.setattr(telegram.urllib.request, "urlopen", urlopen)
    assert telegram.TelegramNotifier("tok", "-1", "5").edit("-1", 9, "t") is expected


def test_api_error_carries_description_but_never_the_token(monkeypatch):
    def urlopen(req, timeout):
        raise http_error(400, "Bad Request: chat not found")

    monkeypatch.setattr(telegram.urllib.request, "urlopen", urlopen)
    with pytest.raises(telegram.TelegramAPIError) as exc:
        telegram.TelegramNotifier("123:SECRET", "-1").send("x")
    assert "chat not found" in str(exc.value) and "SECRET" not in str(exc.value)


def test_delete_message(monkeypatch):
    seen = []
    monkeypatch.setattr(telegram.urllib.request, "urlopen",
                        lambda req, timeout: (seen.append(req.full_url.rsplit("/", 1)[1]), Resp({"ok": True, "result": True}))[1])
    assert telegram.TelegramNotifier("tok", "-1").delete("-1", 5) and seen == ["deleteMessage"]


# --- hermes: baja de la suscripción del hilo de origen ---------------------------------------------

def test_drop_telegram_subs_argument_order():
    calls = []
    subs = [{"task_id": "t_1", "platform": "telegram", "chat_id": "6744452215", "thread_id": ""},
            {"task_id": "t_1", "platform": "telegram", "chat_id": "-1003530490339", "thread_id": "231"},
            {"task_id": "t_1", "platform": "tui", "chat_id": "sess", "thread_id": ""}]

    def run(args, **kw):
        calls.append(args[4:])
        out = json.dumps(subs) if args[4] == "notify-list" else ""
        return subprocess.CompletedProcess(args, 0, out, "")

    h = HermesCLI("oscarhq", exe="hermes", runner=run)
    assert h.drop_telegram_subs("t_1") == ["6744452215", "-1003530490339:231"]
    assert calls == [
        ["notify-list", "t_1", "--json"],
        ["notify-unsubscribe", "t_1", "--platform", "telegram", "--chat-id", "6744452215"],
        ["notify-unsubscribe", "t_1", "--platform", "telegram", "--chat-id", "-1003530490339", "--thread-id", "231"],
    ]


# --- limpieza de worktrees: --force solo si el remoto tiene el HEAD local ---------------------------

class CleanupRun:
    def __init__(self, head, remote, remove_rc=1, branch_exists=False):
        self.head, self.remote, self.remove_rc, self.branch_exists = head, remote, remove_rc, branch_exists
        self.cmds = []

    def __call__(self, args, **kw):
        self.cmds.append(args)
        sub = args[3]
        if sub == "worktree":
            rc = 0 if "--force" in args else self.remove_rc
            return subprocess.CompletedProcess(args, rc, "", "" if rc == 0 else RAW_GIT)
        if sub == "rev-parse" and args[4] == "HEAD":
            return subprocess.CompletedProcess(args, 0, (self.head or "") + "\n", "")
        if sub == "rev-parse" and args[4] == "--verify":
            return subprocess.CompletedProcess(args, 0 if self.branch_exists else 1, "", "")
        if sub == "ls-remote":
            return subprocess.CompletedProcess(args, 0, f"{self.remote}\trefs/heads/lane/t_1\n" if self.remote else "", "")
        if sub == "status":
            return subprocess.CompletedProcess(args, 0, "?? notas.txt\n M a.py\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")


def _lane(tmp_path):
    (tmp_path / "lane-t_1").mkdir()
    return Lane(**{**LANE.__dict__, "worktree_root": str(tmp_path)})


def test_cleanup_forces_when_remote_has_local_head_and_logs_discarded_files(tmp_path, caplog):
    run = CleanupRun("a" * 40, "a" * 40)
    with caplog.at_level("WARNING", logger="agent_lanes"):
        ok, _ = GitOps(runner=run).cleanup(_lane(tmp_path), "t_1")
    assert ok
    assert any("--force" in c for c in run.cmds if c[3] == "worktree")
    assert "notas.txt" in caplog.text and "a.py" in caplog.text


@pytest.mark.parametrize("head,remote", [("a" * 40, "b" * 40), ("a" * 40, None), ("", "a" * 40)])
def test_cleanup_never_forces_when_remote_differs_or_is_unknown(tmp_path, head, remote):
    run = CleanupRun(head, remote)
    ok, msg = GitOps(runner=run).cleanup(_lane(tmp_path), "t_1")
    assert not ok and "no se fuerza" in msg
    assert not any("--force" in c for c in run.cmds)


def test_cleanup_clean_worktree_needs_no_force(tmp_path):
    run = CleanupRun("a" * 40, "a" * 40, remove_rc=0)
    assert GitOps(runner=run).cleanup(_lane(tmp_path), "t_1")[0]
    assert not any("--force" in c for c in run.cmds)
