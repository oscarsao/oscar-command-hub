"""/flujo: propuesta de tarjetas encadenadas, validación, tableros, seguridad [CONFIRMAR] y creación con Hermes simulado."""
from __future__ import annotations

import json
from types import SimpleNamespace

from agent_lanes import flujo
from agent_lanes.commands import COMMANDS, CommandCenter
from tests.test_commands import center, command, press

LANES = ["claude-migrateam", "claude-oscarhq", "claude-nextjobs"]


def card(titulo, carril="claude-migrateam", padres=(), cuerpo="Hacer algo.", confirmar=False):
    return {"titulo": titulo, "carril": carril, "padres": list(padres), "cuerpo": cuerpo, "limites": "no tocar main",
            "confirmar": confirmar}


def plan_text(*cards):
    return json.dumps(list(cards))


def parsed(*cards):
    return flujo.parse_plan(plan_text(*cards), LANES)


# --- parser -------------------------------------------------------------------------------------------

def test_parse_ok_and_fenced():
    out = flujo.parse_plan("```json\n" + plan_text(card("A"), card("B", padres=[1])) + "\n```", LANES)
    assert [c["n"] for c in out] == [1, 2] and out[1]["padres"] == [1] and out[0]["confirmar"] is False


def test_parse_rejects_bad_plans():
    assert flujo.parse_plan("no json", LANES) == []
    assert parsed(card("A", carril="claude-inventado")) == []
    assert parsed(card("A", padres=[1])) == []                       # padre = ella misma
    assert parsed(card("A"), card("B", padres=[3])) == []            # padre posterior
    assert parsed(*[card(f"T{i}") for i in range(9)]) == []          # más de 8
    assert parsed(card("")) == []
    assert flujo.parse_plan("[]", LANES) == []


def test_parse_accepts_exactly_eight():
    assert len(parsed(*[card(f"T{i}") for i in range(8)])) == 8


def test_risky_detected_even_if_model_says_no():
    out = parsed(card("Desplegar a producción"), card("Enviar correo a clientes"), card("Escribir tests"))
    assert [c["confirmar"] for c in out] == [True, True, False]


def test_propose_uses_isolated_claude_call():
    seen = {}

    def runner(args, **kw):
        seen.update(args=args, kw=kw)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": plan_text(card("A"))}), stderr="")

    out = flujo.propose("hazme algo " + "x" * 5000, {"claude-migrateam": "MigraTeam"}, runner=runner)
    assert len(out) == 1
    assert seen["args"][:4] == ["claude", "-p", "--model", "sonnet"] and "--tools" in seen["args"]
    assert flujo.OPEN in seen["kw"]["input"] and len(seen["kw"]["input"]) < 4000


def test_propose_failures_return_empty():
    def boom(*a, **k):
        raise OSError("sin claude")

    def bad(*a, **k):
        return SimpleNamespace(returncode=1, stdout=json.dumps({"is_error": True, "result": "x"}), stderr="")

    assert flujo.propose("x", {"claude-migrateam": ""}, runner=boom) == []
    assert flujo.propose("x", {"claude-migrateam": ""}, runner=bad) == []


# --- comando y botones --------------------------------------------------------------------------------

def setup(tmp_path, cards):
    cc, desk, bot, h, _ = center(tmp_path, {})
    creates, counter = [], iter(range(1, 100))

    def create(title, body, assignee, *, priority=None, key=None, created_by=None, parents=None):
        creates.append({"title": title, "body": body, "assignee": assignee, "key": key, "by": created_by,
                        "parents": parents})
        return f"t_{next(counter):08x}"

    h.create = create
    cc._flow_planner = lambda text, lanes: cards
    return cc, desk, bot, creates, create


def test_command_registered_and_in_help():
    assert "flujo" in dict(COMMANDS) and "/flujo" in CommandCenter.help_text()


def test_proposal_creates_nothing_until_button(tmp_path):
    cc, desk, bot, creates, _ = setup(tmp_path, parsed(card("A"), card("B", padres=[1])))
    command(desk, "/flujo haz cosas")
    msg = bot.sent[-1]
    assert "1. A → claude-migrateam" in msg["text"] and "2. B → claude-migrateam · tras #1" in msg["text"]
    assert [b["text"] for r in msg["markup"]["inline_keyboard"] for b in r] == ["✅ Crear todo", "🚫 Cancelar"]
    assert creates == []


def test_cancel_creates_nothing(tmp_path):
    cc, desk, bot, creates, _ = setup(tmp_path, parsed(card("A")))
    command(desk, "/flujo x")
    press(desk, bot.sent[-1], 1)
    assert creates == []


def test_create_all_chain_parents_keys_and_author(tmp_path):
    cc, desk, bot, creates, _ = setup(tmp_path, parsed(card("A"), card("B", padres=[1]), card("C", padres=[1, 2])))
    command(desk, "/flujo x")
    press(desk, bot.sent[-1], 0)
    assert [c["title"] for c in creates] == ["A", "B", "C"]
    assert creates[0]["parents"] is None and creates[1]["parents"] == ["t_00000001"]
    assert creates[2]["parents"] == ["t_00000001", "t_00000002"]
    assert all(c["by"] == "oscar" and c["assignee"] == "claude-migrateam" for c in creates)
    assert len({c["key"] for c in creates}) == 3 and all(c["key"].startswith("flujo-f_") for c in creates)
    assert "## Objetivo" in creates[0]["body"] and "## Límites" in creates[0]["body"]
    assert "Origen-Telegram: chat=" in creates[0]["body"]


def test_risky_card_goes_to_oscar_with_tag(tmp_path):
    cc, desk, bot, creates, _ = setup(tmp_path, parsed(card("A"), card("Desplegar a producción", padres=[1])))
    command(desk, "/flujo x")
    assert "🔒" in bot.sent[-1]["text"]
    press(desk, bot.sent[-1], 0)
    assert creates[0]["assignee"] == "claude-migrateam"
    assert creates[1]["assignee"] == "oscar" and creates[1]["title"].startswith("[CONFIRMAR] ")
    assert "visto bueno" in creates[1]["body"]


def test_board_follows_lane_and_cross_board_parent_is_not_linked(tmp_path):
    cc, desk, bot, creates, create = setup(tmp_path,
                                           parsed(card("A", "claude-migrateam"), card("B", "claude-oscarhq", [1])))
    boards = []
    cc.hermes_for = lambda b: (boards.append(b), SimpleNamespace(create=create))[1]
    command(desk, "/flujo x")
    press(desk, bot.sent[-1], 0)
    assert boards == ["migrateam", "oscarhq"]
    assert creates[1]["parents"] is None and "Depende de (otro tablero" in creates[1]["body"]


def test_partial_failure_skips_descendants(tmp_path):
    cc, desk, bot, creates, create = setup(
        tmp_path, parsed(card("A"), card("B", padres=[1]), card("C", "claude-oscarhq")))

    def flaky(title, *a, **k):
        if title == "A":
            raise RuntimeError("hermes caído")
        return create(title, *a, **k)

    cc.hermes_for = lambda b: SimpleNamespace(create=flaky)
    command(desk, "/flujo x")
    press(desk, bot.sent[-1], 0)
    assert [c["title"] for c in creates] == ["C"]


def test_invalid_plan_and_empty_usage(tmp_path):
    cc, desk, bot, creates, _ = setup(tmp_path, [])
    command(desk, "/flujo")
    assert "Uso: /flujo" in bot.sent[-1]["text"]
    command(desk, "/flujo algo", uid=2)
    assert "No pude proponer" in bot.sent[-1]["text"] and creates == []


def test_non_owner_cannot_use(tmp_path):
    cc, desk, bot, creates, _ = setup(tmp_path, parsed(card("A")))
    command(desk, "/flujo x", user=777, chat=777)
    assert bot.sent[-1]["text"] == "Solo Oscar" and creates == []


def test_hermes_create_passes_parent_flags():
    from agent_lanes.hermes import HermesCLI
    calls = []

    class H(HermesCLI):
        author = "x"

        def __init__(self):
            pass

        def _call(self, *args, timeout=120):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='{"id": "t_0000abcd"}', stderr="")

    tid = H().create("T", "body", "claude-hub", key="k", created_by="oscar", parents=["t_00000001", "t_00000002"])
    assert tid == "t_0000abcd"
    args = calls[0]
    assert [args[i + 1] for i, a in enumerate(args) if a == "--parent"] == ["t_00000001", "t_00000002"]
    assert args[-1] == "T" and "--idempotency-key" in args
