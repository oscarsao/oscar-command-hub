"""/hazlo (tarea pequeña desde el DM, carril inferido o elegido con botones) y /estado (en curso, espera, desplegar)."""
from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from agent_lanes.commands import HAZLO_PRIORITY, CommandCenter
from agent_lanes.hermes import HermesCLI, HermesError
from tests import test_commands as tc
from tests.test_commands import GESTION, OSCAR, command, needs, press, texts


class CreatingHermes(tc.KanbanHermes):
    def __init__(self, shows):
        super().__init__(shows)
        self.created = []

    def create(self, title, body, assignee, *, priority=None, key=None, created_by=None):
        self.created.append({"title": title, "body": body, "assignee": assignee, "priority": priority, "key": key,
                             "created_by": created_by})
        return f"t_{len(self.created):08x}"


def center(tmp_path, shows=None):
    cc, desk, bot, h, messages = tc.center(tmp_path, shows or {})
    h2 = CreatingHermes(h.shows)
    cc.hermes_for = lambda board: h2
    return cc, desk, bot, h2


# --- /hazlo ----------------------------------------------------------------------------------------------

def test_hazlo_infers_lane_from_text_and_creates_card(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    command(desk, "/hazlo MigraTeam: corrige el typo del pie de la home")
    [card] = h.created
    assert card["assignee"] == "claude-migrateam" and card["priority"] == HAZLO_PRIORITY
    assert card["title"].startswith("MigraTeam: corrige el typo")
    assert "Origen-Telegram: chat=6744452215 thread=0" in card["body"]
    assert "15 minutos" in card["body"] and "corrige el typo del pie" in card["body"]
    assert "Creada" in bot.sent[-1]["text"] and "t_00000001" in bot.sent[-1]["text"]
    assert "Te aviso al terminar" in bot.sent[-1]["text"]


def test_hazlo_origin_is_the_topic_where_it_was_asked(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    command(desk, "/hazlo@pildora_carriles_bot revisa el footer", chat=GESTION, thread=230)
    [card] = h.created  # sin palabra clave: el tema de MigraTeam fija marca y carril
    assert card["assignee"] == "claude-migrateam"
    assert f"Origen-Telegram: chat={GESTION} thread=230" in card["body"]


def test_hazlo_same_text_twice_in_a_row_is_idempotent_key(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    command(desk, "/hazlo nextjobs: arregla el log")
    command(desk, "/hazlo nextjobs: arregla el log", uid=2)
    assert h.created[0]["key"] == h.created[1]["key"] and h.created[0]["key"].startswith("hazlo-")


def test_hazlo_without_text_explains_usage(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    command(desk, "/hazlo")
    assert h.created == [] and bot.sent[-1]["text"].startswith("Uso: /hazlo")


def test_hazlo_in_doubt_asks_with_buttons_and_press_creates(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    command(desk, "/hazlo cambia el color del botón")  # DM: sin marca ni palabras clave
    assert h.created == []
    ask = bot.sent[-1]
    labels = texts(ask["markup"])
    assert "Píldora · claude-oscarhq" in labels and "MigraTeam · claude-migrateam" in labels and "🚫 Cancelar" in labels
    press(desk, ask, labels.index("Píldora · claude-oscarhq"))
    [card] = h.created
    assert card["assignee"] == "claude-oscarhq" and f"Origen-Telegram: chat={OSCAR} thread=0" in card["body"]
    assert "Creada" in bot.edits[-1]["text"]
    press(desk, ask, 0, cid="cq2")  # segundo toque: ya consumido
    assert len(h.created) == 1 and bot.answers[-1] in ("Ya está en marcha", "Esta decisión ya no está activa")


def test_hazlo_cancel_creates_nothing(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    command(desk, "/hazlo algo ambiguo")
    ask = bot.sent[-1]
    press(desk, ask, len(texts(ask["markup"])) - 1)
    assert h.created == [] and "Cancelado" in bot.edits[-1]["text"]


def test_hazlo_pildora_topic_offers_only_its_two_lanes(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    command(desk, "/hazlo mejora el prompt", chat=GESTION, thread=231)
    assert texts(bot.sent[-1]["markup"]) == ["Píldora · claude-oscarhq", "Píldora · claude-scraper", "🚫 Cancelar"]


def test_hazlo_two_lane_keywords_asks(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    assert cc.infer_lane("migrateam y nextjobs: unifica el logo", None) is None
    assert cc.infer_lane("ajusta el runner del hub", None) is None  # el carril del hub no está en estos LANES


def test_hazlo_only_oscar(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    command(desk, "/hazlo migrateam: x", user=1234)
    assert h.created == [] and [m["text"] for m in bot.sent] == ["Solo Oscar"]


def test_hazlo_creation_failure_is_reported_not_raised(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    h.create = lambda *a, **k: (_ for _ in ()).throw(HermesError("boom"))
    command(desk, "/hazlo migrateam: x")
    assert bot.sent[-1]["text"] == "No pude completar /hazlo; detalle en el log"


# --- HermesCLI.create ------------------------------------------------------------------------------------

def test_hermes_create_builds_cli_call_and_returns_id():
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        body = open(cmd[cmd.index("--body-file") + 1], encoding="utf-8").read()
        assert body == "- cuerpo\n"
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"id": "t_abcdef12"}), "")

    tid = HermesCLI("default", exe="hermes", runner=run).create("Título", "- cuerpo\n", "claude-hub", priority=10,
                                                                key="k1", created_by="oscar")
    assert tid == "t_abcdef12"
    cmd = calls[0]
    assert cmd[1:4] == ["kanban", "--board", "default"] and cmd[-1] == "Título"
    assert cmd[cmd.index("--assignee") + 1] == "claude-hub" and cmd[cmd.index("--priority") + 1] == "10"
    assert cmd[cmd.index("--idempotency-key") + 1] == "k1" and cmd[cmd.index("--created-by") + 1] == "oscar"


def test_hermes_create_failure_raises():
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "no")  # noqa: E731
    with pytest.raises(HermesError):
        HermesCLI("default", exe="hermes", runner=run).create("T", "b", "claude-hub")


# --- /estado ---------------------------------------------------------------------------------------------

def running(tid, assignee, title="En marcha"):
    return {"task": {"id": tid, "title": title, "body": "", "assignee": assignee, "status": "running",
                     "created_at": 10}, "runs": [], "events": [], "comments": []}


def fake_integrator(merged):
    pol = {"claude-oscarhq": SimpleNamespace(deploy="railway_up"), "claude-nextjobs": SimpleNamespace(deploy="none")}
    return SimpleNamespace(settings=SimpleNamespace(policies=pol),
                           _merged_states=lambda lane, ex: merged.get(lane.name, []))


def test_estado_one_message_with_three_sections(tmp_path):
    shows = {"t_aaaaaaa1": running("t_aaaaaaa1", "claude-migrateam"),
             "t_bbbbbbb2": needs("t_bbbbbbb2", tc.Q_REC, assignee="claude-oscarhq")}
    cc, desk, bot, h = center(tmp_path, shows)
    desk.integrator = fake_integrator({"claude-oscarhq": [("t_ccccccc3", {"pr": 41})]})
    command(desk, "/estado")
    [msg] = bot.sent
    text = msg["text"]
    assert text.index("En curso (1)") < text.index("Esperan a Oscar") < text.index("Pendiente de desplegar")
    assert "t_aaaaaaa1" in text and "1 decisiones de agentes" in text
    assert "claude-oscarhq · PR #41 (t_ccccccc3) fusionado, sin desplegar" in text


def test_estado_filters_by_brand_argument_and_topic(tmp_path):
    shows = {"t_aaaaaaa1": running("t_aaaaaaa1", "claude-migrateam"),
             "t_ddddddd4": running("t_ddddddd4", "claude-oscarhq")}
    cc, desk, bot, h = center(tmp_path, shows)
    command(desk, "/estado pildora")
    assert "t_ddddddd4" in bot.sent[-1]["text"] and "t_aaaaaaa1" not in bot.sent[-1]["text"]
    command(desk, "/estado", chat=GESTION, thread=230, uid=2)
    assert "t_aaaaaaa1" in bot.sent[-1]["text"] and "t_ddddddd4" not in bot.sent[-1]["text"]
    command(desk, "/estado nada", uid=3)
    assert bot.sent[-1]["text"].startswith("Marca desconocida")


def test_estado_empty_and_without_integrator(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    command(desk, "/estado nextjobs")
    text = bot.sent[-1]["text"]
    assert "nada en curso" in text and "nada 🎉" in text and "sin integrador activo" in text


# --- ayuda -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["/start", "/ayuda", "/help"])
def test_help_lists_new_commands(tmp_path, cmd):
    cc, desk, bot, h = center(tmp_path)
    command(desk, cmd)
    text = bot.sent[-1]["text"]
    assert "/hazlo" in text and "/estado" in text and "prioridad alta" in text
    assert isinstance(cc, CommandCenter)
    assert "/desplegar" in text and "/promover" in text and "/lote" in text and "dos toques" in text


# --- /desplegar (doble toque) y /promover (solo prepara la tarjeta) ----------------------------------------

class DeployIntegrator:
    def __init__(self, merged, enabled=True):
        pol = {"claude-oscarhq": SimpleNamespace(deploy="railway_up"),
               "claude-migrateam": SimpleNamespace(deploy="on_merge")}
        self.settings = SimpleNamespace(enabled=enabled, policies=pol)
        self.merged, self.pressed = merged, []

    def _merged_states(self, lane, exclude):
        return self.merged.get(lane.name, [])

    def on_button(self, action, rec, where, desk):
        self.pressed.append((action, rec["task_id"]))
        return True


MERGED = ("t_ccccccc3", {"pr": 41, "title": "Arregla el health", "merge_sha": "a" * 40, "pr_url": "u"})


def test_desplegar_needs_two_taps_before_int_deploy(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    desk.integrator = DeployIntegrator({"claude-oscarhq": [MERGED]})
    command(desk, "/desplegar oscarhq")
    ask = bot.sent[-1]
    assert texts(ask["markup"]) == ["🚀 Desplegar"] and "PR #41" in ask["text"]
    press(desk, ask, 0)
    assert desk.integrator.pressed == []  # el 1.er toque no despliega
    confirm = bot.edits[-1]
    assert "¿Seguro?" in confirm["text"] and texts(confirm["markup"]) == ["✅ Sí, desplegar", "✖ No"]
    press(desk, {**ask, "markup": confirm["markup"]}, 0, cid="cq2")
    assert desk.integrator.pressed == [("int_deploy", "t_ccccccc3")]


def test_desplegar_no_cancels_and_never_deploys(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    desk.integrator = DeployIntegrator({"claude-oscarhq": [MERGED]})
    command(desk, "/desplegar oscarhq")
    ask = bot.sent[-1]
    press(desk, ask, 0)
    press(desk, {**ask, "markup": bot.edits[-1]["markup"]}, 1, cid="cq2")
    assert desk.integrator.pressed == [] and "No despliego" in bot.edits[-1]["text"]


def test_desplegar_without_args_unknown_nothing_pending_or_integrator_off(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    command(desk, "/desplegar oscarhq")
    assert "apagado" in bot.sent[-1]["text"]
    desk.integrator = DeployIntegrator({})
    command(desk, "/desplegar", uid=2)
    assert "Dime el proyecto: /desplegar oscarhq" in bot.sent[-1]["text"]
    command(desk, "/desplegar migrateam", uid=3)  # on_merge: no se despliega desde aquí
    assert "Dime el proyecto" in bot.sent[-1]["text"]
    command(desk, "/desplegar oscarhq", uid=4)
    assert "Nada que desplegar" in bot.sent[-1]["text"] and not bot.sent[-1].get("markup")


def test_desplegar_skips_merges_with_pending_migration(tmp_path):
    cc, desk, bot, h = center(tmp_path)
    mig = ("t_ddddddd4", {"pr": 42, "merge_sha": "b" * 40, "migration": "alembic"})
    desk.integrator = DeployIntegrator({"claude-oscarhq": [mig]})
    command(desk, "/desplegar oscarhq")
    assert "migración pendiente" in bot.sent[-1]["text"] and not bot.sent[-1].get("markup")
