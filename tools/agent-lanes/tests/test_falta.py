"""Fase A de los bots del equipo: exclusiones del lote en lenguaje llano con botones, /falta y texto libre contestado.
Mismo montaje que test_batch (git real en tmp_path; gh, Hermes y Telegram simulados). Sin red ni `claude` real."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from agent_lanes import explain
from agent_lanes.commands import CommandCenter
from agent_lanes.decisions import CallbackStore, DecisionDesk
from agent_lanes.falta import classify, env_pending, falta_message, plain_reason
from agent_lanes.notices import MessageStore
from tests.test_batch import SECRET, Env, press
from tests.test_integrator import OSCAR, buttons


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    e.created_cards, e.bodies = [], {}
    base_show = e.h.show

    def create(title, body, assignee, *, priority=None, key=None, created_by=None, parents=None):
        e.created_cards.append({"title": title, "body": body, "assignee": assignee, "key": key, "by": created_by})
        return f"t_{len(e.created_cards):08x}"

    def show(tid):
        out = base_show(tid)
        out["task"] = {**out["task"], "body": e.bodies.get(tid, "")}
        return out

    e.h.create, e.h.show = create, show
    return e


class Out:
    """Notificador del CommandCenter: guarda texto y teclado."""

    def __init__(self):
        self.sent = []

    def send_to(self, chat, thread, text, html=True, **kw):
        self.sent.append({"text": text, "markup": kw.get("reply_markup"), "reply_to": kw.get("reply_to")})


def center(env, asker=None):
    out = Out()
    cc = CommandCenter(out, env.desk, lanes={"claude-oscarhq": env.lane}, hermes_for=lambda b: env.h,
                       messages=MessageStore(), owner_id=str(OSCAR), asker=asker,
                       renotifier=SimpleNamespace(collect=lambda statuses=(): []))
    cc._spawn = lambda fn: fn()
    env.desk.commands = cc
    return cc, out


def say(cc, text, uid=OSCAR):
    cc.handle({"text": text, "from": {"id": uid}, "chat": {"id": -100}})


def dm(env, text, uid=OSCAR, **extra):
    env.desk.handle_update({"message": {"text": text, "from": {"id": uid}, "chat": {"id": uid, "type": "private"},
                                        "message_id": 9, **extra}})


def notices(env):
    return [m for m in env.tg.sent if m["text"].startswith("⏭ <b>Fuera del lote") and m["markup"]]


def lote_with_exclusions(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.branch("t_conf", {"src/a.py": "a = 3\n"})              # choca con t_a
    env.branch("t_sec", {"src/c.py": f'KEY = "{SECRET}"\n'})    # secreto
    env.queue()
    env.integ.batches.assemble(env.lane, env.policy)


# --- motivos en lenguaje llano -----------------------------------------------------------------------------

def test_classify_and_plain_reason():
    sec = "posible secreto en el código: tests/test_telegram_tools.py:543 (clave sk-)"
    assert classify(sec) == "secret" and "tests/test_telegram_tools.py:543" in plain_reason(sec)
    assert "falso positivo" in plain_reason(sec)
    conf = "choca con main o con otra rama del lote (src/services/telegram_bot.py)"
    assert classify(conf) == "conflict" and "src/services/telegram_bot.py" in plain_reason(conf)
    assert classify("rompe los tests o el build del lote (probada sola)") == "tests"
    assert classify("toca rutas vetadas del carril: x") == "other" and plain_reason("toca rutas vetadas") == "toca rutas vetadas"
    assert "…" not in plain_reason(conf + " " + "z" * 300)


def test_env_pending_parses_and_dedups():
    body = "x\nEnv-Pendiente: SUPABASE_URL=https://ejemplo.supabase.co\nEnv-Pendiente: API_KEY\nEnv-Pendiente: API_KEY=otra\n"
    assert env_pending(body) == [("SUPABASE_URL", "https://ejemplo.supabase.co"), ("API_KEY", "")]
    assert env_pending(None) == []


def test_falta_message_puts_next_step_first():
    text = falta_message("Píldora", ["uno", "dos", "tres"])
    assert text.index("Siguiente") < text.index("uno") < text.index("2. dos") < text.index("3. tres")
    assert "Nada te frena" in falta_message(None, [])


# --- aviso «Fuera del lote» ---------------------------------------------------------------------------------

def test_excluded_notices_explain_in_plain_words_with_a_button_per_cause(env):
    lote_with_exclusions(env)
    ns = notices(env)
    by_pr = {("secreto" if "clave" in n["text"] else "choca"): n for n in ns}
    sec, conf = by_pr["secreto"], by_pr["choca"]
    assert buttons(sec["markup"]) == ["🔧 Arreglar", "⏭ Dejar para el siguiente lote", "💬 Explícame más"]
    assert buttons(conf["markup"]) == ["🔁 Rebasar", "⏭ Dejar para el siguiente lote", "💬 Explícame más"]
    assert "falso positivo" in sec["text"] and SECRET not in sec["text"]
    assert "src/a.py" in conf["text"] and "rebasarla" in conf["text"]
    # el teclado del lote sigue intacto y aparte
    assert any(buttons(m["markup"]) == ["🔀 Fusionar lote"] for m in env.tg.sent)


def test_ficha_shows_full_plain_reason_without_120_char_cut():
    from agent_lanes.batch import render_batch_ficha
    from agent_lanes.integration import risk_of
    reason = "choca con main o con otra rama del lote (" + ", ".join(f"src/m{i}.py" for i in range(3)) + ")"
    rec = {"pr_number": 9, "batch_brand": "x", "batch_date": "d", "batch_branch": "b", "batch_members": [[1, "ok"]],
           "batch_excluded": [[2, "rama", reason + " " + "w" * 100]], "gates": [], "risks": []}
    text = render_batch_ficha(risk=risk_of(None), phase="P", rec=rec, status="", repo="r", base="main")
    assert "rebasarla antes" in text and "…" not in text.split("Fuera del lote")[1]


def test_arreglar_creates_fix_card_and_takes_branch_out_of_the_queue(env):
    lote_with_exclusions(env)
    sec = next(n for n in notices(env) if "clave" in n["text"])
    press(env.desk, env.tg, markup=sec["markup"], n=0)
    [card] = env.created_cards
    assert card["assignee"] == "claude-oscarhq" and card["by"] == "oscar" and card["title"].startswith("Arreglar #")
    assert "origin/lane/t_sec" in card["body"] and "falso positivo" in card["body"]
    assert env.integ._state("t_sec")["status"] == "batch_replaced"
    assert any("sustituido por la tarjeta" in c[1] for c in env.h.comments if c[0] == "t_sec")
    assert "Creada" in env.tg.edits[-1]["text"]
    # el lote sigue ofrecido y los demás excluidos siguen en cola
    assert env.integ._state("t_conf")["status"] == "batch_pending"


def test_rebasar_card_points_at_the_lote_branch(env):
    lote_with_exclusions(env)
    conf = next(n for n in notices(env) if "rebasarla" in n["text"])
    press(env.desk, env.tg, markup=conf["markup"], n=0)
    [card] = env.created_cards
    assert card["title"].startswith("Rebasar #") and "release/lote-oscarhq-" in card["body"]
    assert env.integ._state("t_conf")["status"] == "batch_replaced"


def test_skip_leaves_the_branch_for_the_next_lote_and_failure_keeps_buttons(env):
    lote_with_exclusions(env)
    sec = next(n for n in notices(env) if "clave" in n["text"])
    skip = next(b for row in sec["markup"]["inline_keyboard"] for b in row if b["text"].startswith("⏭"))
    press(env.desk, env.tg, markup={"inline_keyboard": [[skip]]})
    assert env.created_cards == [] and env.integ._state("t_sec")["status"] == "batch_pending"
    assert "siguiente lote" in env.tg.edits[-1]["text"]
    conf = next(n for n in notices(env) if "rebasarla" in n["text"])

    def boom(*a, **k):
        raise RuntimeError("hermes caído")

    env.h.create = boom
    press(env.desk, env.tg, markup=conf["markup"], n=0)
    assert env.integ._state("t_conf")["status"] == "batch_pending"  # nada cambió
    env.h.create = lambda title, body, assignee, **kw: env.created_cards.append(title) or "t_0000abcd"
    press(env.desk, env.tg, markup=conf["markup"], n=0)  # los botones volvieron: se puede reintentar
    assert len(env.created_cards) == 1 and env.integ._state("t_conf")["status"] == "batch_replaced"


# --- /falta -------------------------------------------------------------------------------------------------

def test_falta_with_open_lote_excluded_branch_migration_and_env(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.branch("t_b", {"supabase/migrations/001.sql": "select 1;\n"})
    env.branch("t_sec", {"src/c.py": f'KEY = "{SECRET}"\n'})
    env.bodies["t_a"] = "Env-Pendiente: OPENAI_KEY=sk-ejemplo\n"
    env.queue()
    env.integ.batches.assemble(env.lane, env.policy)
    cc, out = center(env)
    say(cc, "/falta")
    msg = out.sent[-1]
    text = msg["text"]
    assert "Siguiente:</b> 🔀 Fusionar el lote PR #100" in text
    assert "fuera del lote" in text and "falso positivo" in text
    assert "SQL/migración (supabase)" in text
    assert "OPENAI_KEY" in text and "sk-ejemplo" in text
    assert buttons(msg["markup"]) == ["🔀 Fusionar lote #100"]
    # el botón hace lo de siempre: fusiona el lote
    env.desk.commands = cc
    press(env.desk, env.tg, markup=msg["markup"], n=0)
    assert env.gh.named("merge")
    cc2, out2 = center(env)
    say(cc2, "/falta")
    after = out2.sent[-1]
    assert "Aplicar el SQL/migración (supabase)" in after["text"] and "✅ Migración aplicada" in buttons(after["markup"])[0]
    press(env.desk, env.tg, markup=after["markup"], n=0)
    assert env.integ._state("t_a")["migration_applied"]


def test_falta_deploy_button_asks_for_confirmation(env):
    e = env
    e.policy = type(e.policy)(**{**e.policy.__dict__, "deploy": "railway_up"})
    e.integ.settings.policies["claude-oscarhq"] = e.policy
    e.branch("t_a", {"src/a.py": "a = 2\n"})
    e.queue()
    e.integ.batches.assemble(e.lane, e.policy)
    press(e.desk, e.tg)  # fusiona el lote (sin migración)
    cc, out = center(e)
    say(cc, "/falta")
    last = out.sent[-1]
    assert "Desplegar PR" in last["text"] and buttons(last["markup"]) == ["🚀 Desplegar #101"] or "Desplegar" in last["text"]


def test_falta_with_nothing_pending(env):
    cc, out = center(env)
    say(cc, "/falta")
    assert "Nada te frena" in out.sent[-1]["text"] and out.sent[-1]["markup"] is None
    say(cc, "/falta nadie")
    assert "Marca desconocida" in out.sent[-1]["text"]
    say(cc, "/falta", uid=1)
    assert out.sent[-1]["text"] == "Solo Oscar"


def test_falta_waiting_branches_point_to_lote(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.queue()
    cc, out = center(env)
    say(cc, "/falta")
    assert "esperando lote" in out.sent[-1]["text"] and "/lote oscarhq" in out.sent[-1]["text"]


# --- texto libre del DM -------------------------------------------------------------------------------------

def test_free_text_question_gets_an_answer_fed_with_the_real_state(env):
    env.branch("t_a", {"src/a.py": "a = 2\n"})
    env.queue()
    seen = []
    cc, out = center(env, asker=lambda q, ctx: seen.append((q, ctx)) or "Falta montar el lote: pulsa /lote.")
    dm(env, "¿qué falta para sacar la versión?")
    assert seen and seen[0][0] == "¿qué falta para sacar la versión?"
    assert "esperando lote" in seen[0][1] and "<b>" not in seen[0][1]  # contexto de /falta y /estado, sin HTML
    reply = out.sent[-1]["text"]
    assert reply.startswith("💬 Falta montar el lote") and "Solo información" in reply
    assert env.gh.created == []  # informar no ejecuta nada


def test_free_text_failure_says_so(env):
    cc, out = center(env, asker=lambda q, ctx: None)
    dm(env, "¿cómo vamos?")
    assert "No pude generar la explicación" in out.sent[-1]["text"]

    def boom(q, ctx):
        raise RuntimeError("x")

    cc._asker = boom
    dm(env, "¿y ahora?")
    assert "No pude generar la explicación" in out.sent[-1]["text"]


def test_free_text_does_not_interfere_with_commands_or_open_session(env):
    asked = []
    cc, out = center(env, asker=lambda q, ctx: asked.append(q) or "x")
    dm(env, "/falta")
    assert asked == [] and "Qué falta" in out.sent[-1]["text"]
    dm(env, "/sesion prueba")
    dm(env, "esto es un punto de la sesión")
    assert asked == [] and "Punto 1 anotado" in out.sent[-1]["text"]


def test_free_text_from_others_or_groups_is_not_answered(env):
    asked = []
    cc, out = center(env, asker=lambda q, ctx: asked.append(q) or "x")
    env.desk.handle_update({"message": {"text": "hola", "from": {"id": OSCAR}, "chat": {"id": -100, "type": "supergroup"}}})
    assert asked == []
    env.desk.collab = SimpleNamespace(claims=lambda msg, owner: True, handle=lambda msg: None)
    dm(env, "hola", uid=777)
    assert asked == []


def test_without_asker_free_text_keeps_the_old_help(env):
    cc, out = center(env, asker=None)
    sent = []
    env.desk.notifier = SimpleNamespace(send_to=lambda *a, **k: sent.append(a[2]), bot_id="42")
    dm(env, "hola")
    assert sent and "Comandos del bot" in sent[0]


def test_answer_is_cheap_isolated_and_capped():
    args = explain.claude_args(explain.ASK_SYSTEM)
    assert "--model" in args and args[args.index("--model") + 1] == "haiku"
    assert args[args.index("--tools") + 1] == "" and "--strict-mcp-config" in args
    assert float(args[args.index("--max-budget-usd") + 1]) <= 0.10
    calls = []

    def run(a, **kw):
        calls.append((a, kw))
        return subprocess.CompletedProcess(a, 0, '{"result": "Falta el lote.", "is_error": false}\n', "")

    assert explain.answer("¿qué falta?", "x" * 20000, runner=run) == "Falta el lote."
    assert len(calls[0][1]["input"]) < explain.ASK_CONTEXT_MAX + explain.ASK_QUESTION_MAX + 200
    assert explain.answer("q", "c", runner=lambda a, **kw: subprocess.CompletedProcess(a, 1, "no json", "")) is None
    assert explain.answer("q", "c", runner=lambda a, **kw: subprocess.CompletedProcess(
        a, 0, '{"is_error": true, "subtype": "error_max_budget_usd"}', "")) is None


# --- aviso al equipo ----------------------------------------------------------------------------------------

def group_msg(text, uid=555, **extra):
    return {"message": {"text": text, "from": {"id": uid}, "chat": {"id": -900, "type": "supergroup"},
                        "message_id": 4, **extra}}


def test_team_notice_is_off_by_default_and_ignores_group_text(env):
    cc, out = center(env)
    cc.bot_username = "pildora_carriles_bot"
    sent = []
    env.desk.notifier = SimpleNamespace(send_to=lambda *a, **k: sent.append(a[2]), bot_id="42")
    env.desk.handle_update(group_msg("@pildora_carriles_bot hola"))
    assert sent == []


def test_team_notice_answers_only_when_addressed_and_once_per_minute(env):
    cc, out = center(env)
    cc.bot_username = "pildora_carriles_bot"
    sent = []
    env.desk.notifier = SimpleNamespace(send_to=lambda *a, **k: sent.append(a[2]), bot_id="42")
    env.desk.team_notice = {"enabled": True, "chats": [-900]}
    env.desk.handle_update(group_msg("lo que hablamos entre nosotros"))
    assert sent == []
    env.desk.handle_update(group_msg("@pildora_carriles_bot ¿me ayudas?"))
    assert len(sent) == 1 and "asistente del equipo" in sent[0]
    env.desk.handle_update(group_msg("otra vez @pildora_carriles_bot"))
    assert len(sent) == 1  # enfriamiento
    env.desk._help_at.clear()
    env.desk.handle_update(group_msg("responde", reply_to_message={"from": {"id": 42}}))
    assert len(sent) == 2
    env.desk.team_notice = {"enabled": True, "chats": [-111]}  # otro grupo: nada
    env.desk._help_at.clear()
    env.desk.handle_update(group_msg("@pildora_carriles_bot"))
    assert len(sent) == 2
