"""Tema de Integración (28-09): fichas con cabecera por riesgo en el tema 398 + copia espejo en el DM de Oscar,
línea corta con enlace en el tema del carril y resumen fijado que el runner edita en cada pasada.
Todo con dobles: nada toca Telegram, GitHub, Railway ni el .state real."""
from __future__ import annotations

import json

import pytest

from agent_lanes.config import Lane, load_telegram_settings
from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.integration import (SUMMARY_EMPTY, IntegrationRoute, PinnedSummary, age_label, header, link_line,
                                     render_ficha, risk_of, risks_from_files, summary_state, summary_text, topic_link)
from agent_lanes.integrator import Integrator, IntegratorSettings, Policy, load_integrator_settings
from agent_lanes.notices import MessageStore, TaskNotices
from agent_lanes.review import ReviewRunner
from tests.test_integrator import (GH, HEAD, MGT_POLICY, MGT_SLUG, OHQ_POLICY, OHQ_SLUG, RW, Clock, FakeHermes,
                                   FakeLinks, World, approved, lane, record)

GESTION = "-1003530490339"
INTEG = (GESTION, "398")
OSCAR = "6744452215"
NOW = 1_000_000.0
FOR_OSCAR = "Arregla el login con Google, que fallaba desde ayer."


class Tg:
    """Doble de TelegramNotifier con send (resuelve lane_target), send_to, edit, pin."""
    bot_id = "42"

    def __init__(self, *, edit_ok=True, edit_raises=False):
        self.sent, self.edits, self.markups, self.pins, self.answers, self.deleted = [], [], [], [], [], []
        self.edit_ok, self.edit_raises = edit_ok, edit_raises
        self._n = 800

    def _msg(self, chat, thread):
        self._n += 1
        return {"chat_id": str(chat), "thread_id": str(thread or "0"), "message_id": self._n}

    def send(self, text, target=None, lane_target=None, *, silent=False, reply_markup=None):
        chat, thread = target or lane_target or (GESTION, "5")
        m = self._msg(chat, thread)
        self.sent.append({**m, "text": text, "markup": reply_markup, "silent": silent})
        return m

    def send_to(self, chat_id, thread_id, text, *, html=True, silent=False, reply_markup=None, reply_to=None):
        m = self._msg(chat_id, thread_id)
        self.sent.append({**m, "text": text, "markup": reply_markup, "silent": silent})
        return m

    def edit(self, chat_id, message_id, text, *, html=True, reply_markup=None):
        if self.edit_raises:
            raise RuntimeError("telegram no alcanzable")
        self.edits.append({"chat": str(chat_id), "mid": message_id, "text": text, "markup": reply_markup})
        return self.edit_ok

    def edit_markup(self, chat_id, message_id, markup):
        self.markups.append(markup)
        return True

    def pin(self, chat_id, message_id):
        self.pins.append((str(chat_id), message_id))
        return True

    def delete(self, chat_id, message_id):
        self.deleted.append(message_id)
        return True

    def answer_callback(self, cid, text=None, *, alert=False):
        self.answers.append(text)
        return True

    def in_topic(self, thread="398"):
        return [m for m in self.sent if (m["chat_id"], m["thread_id"]) == (GESTION, thread)]

    def in_dm(self):
        return [m for m in self.sent if m["chat_id"] == OSCAR]


def build(tmp_path, *, name="claude-oscarhq", policy=None, world=None, tg=None, body="## Objetivo\nLogin",
          for_oscar=FOR_OSCAR, integration=INTEG, messages=None):
    record(tmp_path)
    slug = OHQ_SLUG if name == "claude-oscarhq" else MGT_SLUG
    w = world or World(slug)
    ln = lane(name, tmp_path)
    policy = policy or (OHQ_POLICY if name == "claude-oscarhq" else MGT_POLICY)
    settings = IntegratorSettings(enabled=True, interval_seconds=300, worktree_root=str(tmp_path / "wt"),
                                  deploy_timeout_seconds=900, poll_seconds=20, policies={name: policy},
                                  integration_telegram=integration)
    h = FakeHermes([{"id": "t_1", "title": "Arreglar login", "assignee": name, "body": body}],
                   {"t_1": [approved(slug)]})
    if for_oscar:
        orig = h.show
        h.show = lambda tid: {**orig(tid), "runs": [{"metadata": {"branch": f"lane/{tid}", "head_sha": HEAD,
                                                                  "for_oscar": for_oscar}}]}
    tg = tg or Tg()
    messages = messages or MessageStore()
    clock = Clock()
    desk = DecisionDesk(tg, CallbackStore(tmp_path / "cb"), lanes={name: ln}, hermes_for=lambda b: h,
                        links=FakeLinks(slug), owner_id=OSCAR, runner=w, spawn=lambda fn: fn(), messages=messages)
    integ = Integrator(settings, {name: ln}, hermes_for=lambda b: h, links=FakeLinks(slug), notifier=tg,
                       messages=messages, desk=desk, runner=w, gh_exe=GH, railway_exe=RW, clock=clock,
                       sleep=clock.sleep, http_get=lambda url: (200, "{}"), state_dir=tmp_path / "state",
                       now=lambda: NOW)
    desk.integrator = integ
    return integ, w, h, tg, desk, messages


def press(desk, msg, n=0):
    data = [b["callback_data"] for row in msg["markup"]["inline_keyboard"] for b in row][n]
    desk.handle_update({"callback_query": {"id": "cq", "from": {"id": int(OSCAR)}, "data": data, "message": {
        "chat": {"id": int(msg["chat_id"])}, "message_id": msg["message_id"],
        "message_thread_id": int(msg["thread_id"])}}})


def fichas(tg):
    """Avisos de tarea (no el resumen fijado)."""
    return [m for m in tg.sent if "PENDIENTE DE INTEGRAR" not in m["text"] and m["text"] != SUMMARY_EMPTY]


# --- configuración ---------------------------------------------------------------------------------------

def test_lanes_yaml_declares_the_integration_topic_and_risks():
    assert load_telegram_settings()["integration"] == INTEG
    s = load_integrator_settings(env={})
    assert s.integration_telegram == INTEG
    assert risk_of(s.policies["claude-oscarhq"]) == risk_of(Policy(lane="x", deploy="railway_up", risk="manual",
                                                                   risk_label="Oscar HQ"))
    assert header(risk_of(s.policies["claude-oscarhq"]), "listo") == "🟠 OSCAR HQ · LISTO"
    assert header(risk_of(s.policies["claude-migrateam"]), "listo") == "🟢 STAGING · LISTO"


def test_without_integration_key_the_topic_is_off(tmp_path):
    y = tmp_path / "l.yaml"
    y.write_text("lanes: {}\n", encoding="utf-8")
    assert load_telegram_settings(y)["integration"] is None
    assert load_integrator_settings(y, env={}).integration_telegram is None


# --- cabecera por riesgo -----------------------------------------------------------------------------------

@pytest.mark.parametrize("policy,expected", [
    (Policy(lane="m", deploy="on_merge", risk="staging"), "🟢 STAGING"),
    (Policy(lane="o", deploy="railway_up", risk="manual", risk_label="oscar hq"), "🟠 OSCAR HQ"),
    (Policy(lane="o", deploy="railway_up"), "🟠 DEPLOY MANUAL"),
    (Policy(lane="p", deploy="on_merge"), "🔴 PRODUCCIÓN"),          # el merge despliega y nadie dijo staging
    (Policy(lane="p", deploy="on_merge", risk="production"), "🔴 PRODUCCIÓN"),
    (Policy(lane="p", deploy="on_merge", risk="loquesea"), "🔴 PRODUCCIÓN"),  # valor desconocido: lo peor
    (None, "🟢 SIN DEPLOY"),                                         # carril sin política (scraper, nextjobs)
])
def test_risk_header(policy, expected):
    r = risk_of(policy)
    assert f"{r.emoji} {r.label}" == expected


def test_waiting_and_failed_override_the_risk_emoji():
    r = risk_of(Policy(lane="p", deploy="on_merge"))
    assert header(r, "en espera", pr=7, override="⏸") == "⏸ PRODUCCIÓN · EN ESPERA · PR #7"
    assert header(r, "no se puede integrar", pr=7, override="⛔") == "⛔ PRODUCCIÓN · NO SE PUEDE INTEGRAR · PR #7"


# --- ficha ------------------------------------------------------------------------------------------------

def _ficha(**kw):
    base = dict(risk=risk_of(OHQ_POLICY), phase="listo para integrar", tid="t_1", title="Arreglar <login>",
                repo="oscar-hq", base="master", status="🚦 Listo para integrar PR #7 · pulsa para integrar", pr=7,
                gates=["✔ tests OK"], links=[("🗂 Tarjeta", "https://k/t_1"), ("PR #7", "https://gh/pull/7")])
    return render_ficha(**{**base, **kw})


def test_ficha_with_for_oscar():
    text = _ficha(for_oscar="Arregla <b>el</b> login", risks=["⚠️ requiere migración manual"],
                  deploy="fusionar NO despliega", deps=["t_9 · Otra · sin integrar"])
    lines = text.split("\n")
    assert lines[0] == "<b>🟠 DEPLOY MANUAL · LISTO PARA INTEGRAR · PR #7</b>"
    assert lines[1] == "t_1 · Arreglar &lt;login&gt;"
    assert lines[2] == "oscar-hq · lane/t_1 → master"
    assert lines[3] == "Para ti: Arregla &lt;b&gt;el&lt;/b&gt; login"
    assert "Gates: ✔ tests OK" in lines and "Riesgos: ⚠️ requiere migración manual" in lines
    assert "Deploy: fusionar NO despliega" in lines and "Depende de: t_9 · Otra · sin integrar" in lines
    assert lines[-1] == '<a href="https://k/t_1">🗂 Tarjeta</a> · <a href="https://gh/pull/7">PR #7</a>'


def test_ficha_without_for_oscar_uses_the_title_and_says_no_risks():
    text = _ficha()
    assert "Para ti: Arreglar &lt;login&gt;" in text and "Riesgos: ninguno" in text
    assert "Depende de" not in text and "Deploy:" not in text


def test_ficha_never_exceeds_telegram_limit_nor_cuts_html():
    text = _ficha(gates=[f"✔ gate {i} " + "x" * 80 for i in range(80)])
    assert len(text) <= 4000 and text.endswith("</a>") and text.startswith("<b>")


def test_risks_from_changed_files():
    p = Policy(lane="m", alembic_versions="backend/alembic/versions/", manual_migrations=("supabase/migrations/",),
               sensitive_paths=("Procfile",))
    assert risks_from_files(p, ["backend/alembic/versions/1.py", "supabase/migrations/x.sql", "backend/Procfile"]) == [
        "⚠️ migración de Alembic", "⚠️ migración manual", "⚠️ infraestructura de deploy: backend/Procfile"]
    assert risks_from_files(None, ["a.py"]) == []


def test_topic_link_and_link_line():
    assert topic_link(GESTION, "398", 12) == "https://t.me/c/3530490339/398/12"
    assert topic_link(OSCAR, "0", 12) is None
    line = link_line("🚦 PR #7 listo para integrar", {"chat_id": GESTION, "thread_id": "398", "message_id": 12})
    assert line == '🚦 PR #7 listo para integrar → <a href="https://t.me/c/3530490339/398/12">ver en Integración</a>'


# --- enrutado del integrador -------------------------------------------------------------------------------

def test_offer_goes_to_topic_398_with_dm_mirror_even_if_the_task_came_from_another_topic(tmp_path):
    body = f"Origen-Telegram: chat={GESTION} thread=230\n\n## Objetivo\nLogin"
    integ, w, h, tg, desk, messages = build(tmp_path, body=body)
    assert integ.run_pass() == {"t_1": "offered"}
    topic, dm = fichas(tg)
    assert (topic["chat_id"], topic["thread_id"]) == INTEG and not topic["silent"]
    assert dm["chat_id"] == OSCAR and dm["text"] == topic["text"] and dm["markup"] == topic["markup"]
    assert not [m for m in tg.sent if m["thread_id"] == "230"]  # nada en el tema de origen
    assert [m["message_id"] for m in messages.all_messages("t_1")] == [topic["message_id"], dm["message_id"]]
    lines = topic["text"].split("\n")
    assert lines[0] == "<b>🟠 DEPLOY MANUAL · LISTO PARA INTEGRAR · PR #7</b>"
    assert f"Para ti: {FOR_OSCAR}" in lines
    assert "Gates: ✔ sin conflictos con master · ✔ sin secretos · ✔ tests OK" in lines
    assert "Riesgos: ninguno" in lines and "Deploy: fusionar NO despliega; el deploy es otro botón" in lines
    assert "🚦 Listo para integrar PR #7 · pulsa para integrar" in lines


def test_ficha_without_for_oscar_shows_the_title(tmp_path):
    integ, w, h, tg, *_ = build(tmp_path, for_oscar=None)
    integ.run_pass()
    assert "Para ti: Arreglar login" in fichas(tg)[0]["text"]


def test_lane_topic_keeps_only_a_short_link_line(tmp_path):
    messages = MessageStore()
    tg = Tg()
    # Aviso previo de la tarea en el tema del carril (p. ej. "🔍 en review").
    TaskNotices(tg, messages).publish("t_1", "🔍 t_1 · en review", None, (GESTION, "231"))
    old = tg.sent[0]
    integ, w, h, tg, desk, messages = build(tmp_path, tg=tg, messages=messages)
    integ.run_pass()
    ficha = next(m for m in fichas(tg) if m["thread_id"] == "398")
    link = next(e for e in tg.edits if e["mid"] == old["message_id"])
    assert link["text"] == (f'🚦 PR #7 listo para integrar → <a href="https://t.me/c/3530490339/398/'
                            f'{ficha["message_id"]}">ver en Integración</a>')
    assert old["message_id"] not in tg.deleted  # no se borra: queda como enlace
    assert len([m for m in tg.sent if m["thread_id"] == "231"]) == 1  # ningún mensaje nuevo en el tema del carril


def test_link_line_follows_the_latest_ficha(tmp_path):
    messages = MessageStore()
    tg = Tg()
    TaskNotices(tg, messages).publish("t_1", "🔍 t_1 · en review", None, (GESTION, "231"))
    old = tg.sent[0]
    w = World()
    w.test_rc = 1
    integ, w, h, tg, desk, messages = build(tmp_path, tg=tg, messages=messages, world=w)
    integ.run_pass()  # ⛔ gates fallidos
    w.test_rc = 0
    integ._save("t_1", status="stale", head_sha="")  # nuevos commits: se repiten los gates
    integ.run_pass(force=True)  # 🚦 listo
    last = [m for m in fichas(tg) if m["thread_id"] == "398"][-1]
    link = [e for e in tg.edits if e["mid"] == old["message_id"]][-1]
    assert f"/398/{last['message_id']}" in link["text"] and link["text"].startswith("🚦 PR #7 listo")


def test_failed_gates_ficha(tmp_path):
    w = World()
    w.test_rc = 1
    integ, w, h, tg, *_ = build(tmp_path, world=w)
    assert integ.run_pass() == {"t_1": "gates_failed"}
    text = fichas(tg)[0]["text"]
    assert text.startswith("<b>⛔ DEPLOY MANUAL · NO SE PUEDE INTEGRAR · PR #7</b>")
    assert "✔ sin conflictos con master" in text and "⛔ los tests del carril fallan con el PR fusionado" in text
    assert fichas(tg)[0]["markup"] is None


def test_waiting_ficha(tmp_path):
    integ, w, h, tg, *_ = build(tmp_path)
    import agent_lanes.integrator as mod
    orig = mod.pending_parents
    mod.pending_parents = lambda *a, **k: [{"id": "t_9", "title": "Base de datos", "status": "review",
                                            "reason": "en review"}]
    try:
        assert integ.run_pass() == {"t_1": "waiting_deps"}
    finally:
        mod.pending_parents = orig
    text = fichas(tg)[0]["text"]
    assert text.startswith("<b>⏸ DEPLOY MANUAL · EN ESPERA · PR #7</b>")
    assert "Depende de: t_9 · Base de datos · en review" in text


def test_migrateam_staging_and_unlabelled_on_merge_is_production(tmp_path):
    staging = Policy(**{**MGT_POLICY.__dict__, "risk": "staging"})
    integ, w, h, tg, *_ = build(tmp_path, name="claude-migrateam", policy=staging, world=World(MGT_SLUG))
    integ.run_pass()
    assert fichas(tg)[0]["text"].startswith("<b>🟢 STAGING · LISTO PARA INTEGRAR · PR #7</b>")
    integ2, w2, h2, tg2, *_ = build(tmp_path / "b", name="claude-migrateam", world=World(MGT_SLUG))
    integ2.run_pass()
    assert fichas(tg2)[0]["text"].startswith("<b>🔴 PRODUCCIÓN · LISTO PARA INTEGRAR · PR #7</b>")


def test_button_outcomes_edit_every_copy(tmp_path):
    integ, w, h, tg, desk, messages = build(tmp_path)
    integ.run_pass()
    topic, dm = fichas(tg)
    press(desk, dm)  # 🔀 Fusionar desde el DM
    for mid in (topic["message_id"], dm["message_id"]):
        last = [e for e in tg.edits if e["mid"] == mid][-1]
        assert last["text"].startswith("<b>🟠 DEPLOY MANUAL · FUSIONADO · PR #7</b>")
        assert "✅ fusionado · ddddddd · sin desplegar" in last["text"] and "Deploy:" not in last["text"]
        assert [b["text"] for row in last["markup"]["inline_keyboard"] for b in row] == ["🚀 Desplegar"]


def test_without_owner_desk_no_dm_copy(tmp_path):
    integ, w, h, tg, desk, messages = build(tmp_path)
    integ.desk = None
    integ.run_pass()
    assert tg.in_dm() == [] and len(fichas(tg)) == 1


# --- resumen fijado ----------------------------------------------------------------------------------------

def test_summary_created_and_pinned_then_edited_only_when_it_changes(tmp_path):
    integ, w, h, tg, desk, messages = build(tmp_path)
    integ.run_pass()
    pinned = [m for m in tg.sent if "PENDIENTE DE INTEGRAR" in m["text"]]
    assert len(pinned) == 1 and (pinned[0]["chat_id"], pinned[0]["thread_id"]) == INTEG and pinned[0]["silent"]
    assert tg.pins == [(GESTION, pinned[0]["message_id"])]
    assert pinned[0]["text"] == ("<b>🚦 PENDIENTE DE INTEGRAR (1)</b>\n"
                                 "🟠 PR #7 · Arreglar login · 🚦 listo, pulsa Fusionar · &lt;1 h")
    st = json.loads((tmp_path / "state" / "pinned.json").read_text(encoding="utf-8"))
    assert st["message_id"] == pinned[0]["message_id"] and st["thread_id"] == "398"
    n_edits = len(tg.edits)
    integ.run_pass(force=True)  # nada cambió: ni mensaje nuevo ni edición
    assert len([m for m in tg.sent if "PENDIENTE" in m["text"]]) == 1 and len(tg.edits) == n_edits
    integ._now = lambda: NOW + 5 * 3600  # 5 h después: cambia la antigüedad -> se edita el mismo mensaje
    integ.run_pass(force=True)
    assert tg.edits[-1]["mid"] == pinned[0]["message_id"] and tg.edits[-1]["text"].endswith("· 5 h")
    assert len(tg.pins) == 1


def test_summary_after_merge_button_shows_pending_deploy_without_waiting_a_pass(tmp_path):
    integ, w, h, tg, desk, messages = build(tmp_path)
    integ.run_pass()
    press(desk, fichas(tg)[0])
    assert tg.edits[-1]["text"].endswith("🟠 PR #7 · Arreglar login · 🚀 fusionado, falta desplegar · &lt;1 h")


def test_summary_empty(tmp_path):
    integ, w, h, tg, *_ = build(tmp_path)
    h.tasks = []
    integ.run_pass()
    assert [m["text"] for m in tg.sent] == [SUMMARY_EMPTY] and len(tg.pins) == 1


def test_summary_drops_integrated_tasks(tmp_path):
    integ, w, h, tg, *_ = build(tmp_path)
    integ.run_pass()
    integ._save("t_1", status="deployed")
    integ.run_pass(force=True)
    assert tg.edits[-1]["text"] == SUMMARY_EMPTY


def test_summary_is_off_without_topic_or_in_dry_run(tmp_path):
    integ, w, h, tg, *_ = build(tmp_path, integration=None)
    integ.run_pass()
    assert tg.pins == [] and not (tmp_path / "state" / "pinned.json").exists()
    assert fichas(tg)[0]["thread_id"] == "231"  # sin tema de Integración: el del carril, como siempre


def test_pinned_summary_recreates_when_the_message_is_gone(tmp_path):
    tg = Tg(edit_ok=False)
    p = PinnedSummary(tg, INTEG, tmp_path / "pinned.json")
    assert p.update("a") == "created"
    assert p.update("a") == "unchanged"
    assert p.update("b") == "created" and len(tg.pins) == 2


def test_pinned_summary_network_error_does_not_duplicate(tmp_path):
    tg = Tg()
    p = PinnedSummary(tg, INTEG, tmp_path / "pinned.json")
    p.update("a")
    tg.edit_raises = True
    assert p.update("b") == "error"
    assert len(tg.sent) == 1 and len(tg.pins) == 1


def test_pinned_summary_recreated_if_topic_changes(tmp_path):
    tg = Tg()
    PinnedSummary(tg, INTEG, tmp_path / "pinned.json").update("a")
    assert PinnedSummary(tg, (GESTION, "400"), tmp_path / "pinned.json").update("a") == "created"


def test_summary_text_and_states():
    rows = [{"emoji": "🟢", "pr": 9, "title": "B" * 80, "state": "⛔ gates fallidos", "since": NOW - 3 * 86400},
            {"emoji": "🟠", "pr": 7, "title": "A", "state": "🚦 listo, pulsa Fusionar", "since": NOW - 3600}]
    text = summary_text(rows, NOW).split("\n")
    assert text[0] == "<b>🚦 PENDIENTE DE INTEGRAR (2)</b>"
    assert text[1].startswith("🟢 PR #9 · " + "B" * 39 + "…") and text[1].endswith("· 3 d")  # la más antigua primero
    assert text[2] == "🟠 PR #7 · A · 🚦 listo, pulsa Fusionar · 1 h"
    assert summary_text([], NOW) == SUMMARY_EMPTY
    assert age_label(NOW - 30 * 3600, NOW) == "30 h" and age_label(None, NOW) == "?"
    assert summary_state("deployed") is None and summary_state("integrated") is None
    assert summary_state("merged", deploy="on_merge") == "🚀 fusionado, deploy sin confirmar"
    assert summary_state("offered", deploy="on_merge", migration="alembic") == "⏸ migración pendiente"


# --- tarjeta done (✅ Aprobar) del carril review -------------------------------------------------------------

def test_review_done_card_goes_to_integration_topic_with_dm_mirror(tmp_path):
    ohq = Lane(name="claude-oscarhq", board="oscarhq", repo="C:/Users/oscar/dev/oscar-hq", base="master",
               telegram=(GESTION, "231"))
    review = Lane(name="review", kind="review", reviews=("claude-oscarhq",))
    lanes = {"claude-oscarhq": ohq, "review": review}
    bot, messages = Tg(), MessageStore()
    desk = DecisionDesk(bot, CallbackStore(tmp_path / "cb"), lanes=lanes, hermes_for=lambda b: None, links=None,
                        owner_id=OSCAR, spawn=lambda fn: fn(), messages=messages)
    TaskNotices(bot, messages).publish("t_1", "🔍 t_1 · en review", None, (GESTION, "231"))
    old = bot.sent[0]
    rr = ReviewRunner(review, lanes, hermes_for=lambda b: None, git=None, reviewer=None, verifier=None, notify=bot,
                      messages=messages, decisions=desk, integration=IntegrationRoute(INTEG, {"claude-oscarhq": Policy(
                          lane="claude-oscarhq", deploy="railway_up", risk="manual", risk_label="OSCAR HQ",
                          manual_migrations=("supabase/migrations/",))}))
    body = f"Origen-Telegram: chat={GESTION} thread=231\n\n## Objetivo\nLogin"
    rr.notify("done", {"id": "t_1", "title": "Arreglar login", "body": body, "assignee": "claude-oscarhq"},
              "review aprobada · 2 archivos · tests OK · lista para merge", alert=True, buttons=True,
              changed_files=["supabase/migrations/1.sql", "a.py"], for_oscar=None, test_exit=0,
              deps=["t_9 · Base · en review"])
    topic, dm = bot.sent[1:]
    assert (topic["chat_id"], topic["thread_id"]) == INTEG and dm["chat_id"] == OSCAR
    assert dm["text"] == topic["text"] and dm["markup"] == topic["markup"]
    assert [b["text"] for row in topic["markup"]["inline_keyboard"] for b in row][0] == "✅ Aprobar"
    lines = topic["text"].split("\n")
    assert lines[0] == "<b>🟠 OSCAR HQ · PARA APROBAR</b>"
    assert lines[2] == "oscar-hq · lane/t_1 → master" and "Para ti: Arreglar login" in lines
    assert "Gates: ✔ revisión aprobada · ✔ tests OK" in lines and "Riesgos: ⚠️ migración manual" in lines
    assert "Depende de: t_9 · Base · en review" in lines
    link = next(e for e in bot.edits if e["mid"] == old["message_id"])
    assert link["text"].startswith("✅ t_1 · Arreglar login · lista para aprobar → <a href=\"https://t.me/c/")
    assert messages.get("t_1")["channel"] == "integration"


def test_later_lane_notice_does_not_overwrite_the_integration_ficha(tmp_path):
    bot, messages = Tg(), MessageStore()
    n = TaskNotices(bot, messages)
    n.publish("t_1", "ficha", None, INTEG, alert=True, channel="integration")
    n.publish("t_1", "▶️ t_1 · en curso (reabierta)", None, (GESTION, "231"))
    assert bot.edits == [] and bot.sent[-1]["thread_id"] == "231"


def test_review_done_without_integration_route_stays_as_before(tmp_path):
    ohq = Lane(name="claude-oscarhq", board="oscarhq", repo="C:/ohq", base="master", telegram=(GESTION, "231"))
    review = Lane(name="review", kind="review", reviews=("claude-oscarhq",))
    bot = Tg()
    rr = ReviewRunner(review, {"claude-oscarhq": ohq, "review": review}, hermes_for=lambda b: None, git=None,
                      reviewer=None, verifier=None, notify=bot, messages=MessageStore())
    rr.notify("done", {"id": "t_1", "title": "T", "body": "", "assignee": "claude-oscarhq"}, "lista", alert=True)
    assert bot.sent[0]["thread_id"] == "231" and bot.sent[0]["text"].startswith("✅ t_1 · T")


def test_notifier_pin_payload():
    from agent_lanes.telegram import TelegramNotifier
    n = TelegramNotifier("123:abc", GESTION)
    calls = []
    n._api = lambda method, payload, timeout=None: calls.append((method, payload)) or True
    assert n.pin(GESTION, 55) is True
    assert calls == [("pinChatMessage", {"chat_id": GESTION, "message_id": 55, "disable_notification": True})]
