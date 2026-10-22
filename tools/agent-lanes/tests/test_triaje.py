"""Entrada inteligente (triaje.py + commands.py): clasificación con modelo simulado, consulta sin tarjeta, preguntas ->
tarjeta con respuestas, «Créala ya», workflow sin crear nada hasta aprobar, NO_CARRIL al integrador y duplicados."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

from agent_lanes import triaje, workflows
from agent_lanes.notices import OSCAR_ASSIGNEE
from tests import test_commands as tc
from tests.test_commands import OSCAR, press, texts
from tests.test_comandos_rapidos import CreatingHermes

TEXT = "MigraTeam: cambia el color del botón de login"


class DupHermes(CreatingHermes):
    def __init__(self, shows, cards=()):
        super().__init__(shows)
        self.cards = list(cards)

    def list_status_all(self, status):
        return [c for c in self.cards if c["status"] == status]


def center(tmp_path, category, *, cards=(), asker=None, planner=None):
    cc, desk, bot, h, messages = tc.center(tmp_path, {})
    h2 = DupHermes(h.shows, cards)
    cc.hermes_for = lambda board: h2
    seen = []
    cc._classifier = lambda text, cfg: seen.append(text) or category
    cc._asker = asker
    cc._triaje_cfg = triaje.load_config(tmp_path / "no-existe.yaml")
    cc._workflow_store = workflows.Store(tmp_path / "wf.json")
    cc._flow_planner = planner
    cc.seen = seen
    return cc, desk, bot, h2


def say(cc, text=TEXT):
    cc.free_text({"text": text, "from": {"id": OSCAR}, "chat": {"id": OSCAR, "type": "private"}})


# --- clasificación ---------------------------------------------------------------------------------------

def _runner(result, *, is_error=False):
    def run(args, **kw):
        run.args, run.kw = args, kw
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": result, "is_error": is_error}), stderr="")
    return run


def test_clasificar_cada_categoria_con_modelo_simulado():
    for cat in triaje.CATEGORIAS:
        assert triaje.clasificar("lo que sea", runner=_runner(cat)) == cat
    run = _runner("CONSULTA")
    triaje.clasificar("¿qué PR hay abiertos?", runner=run)
    assert "--tools" in run.args and run.args[run.args.index("--tools") + 1] == ""
    assert run.args[run.args.index("--max-budget-usd") + 1] == "0.1"  # el tope viene de la config


def test_clasificar_cae_en_trabajo_pequeno_si_falla_o_es_ambiguo():
    assert triaje.clasificar("x", runner=_runner("no sé")) == triaje.PEQUENA
    assert triaje.clasificar("x", runner=_runner("CONSULTA o NO_CARRIL")) == triaje.PEQUENA
    assert triaje.clasificar("x", runner=_runner("CONSULTA", is_error=True)) == triaje.PEQUENA

    def boom(*a, **k):
        raise OSError("sin claude")
    assert triaje.clasificar("x", runner=boom) == triaje.PEQUENA


def test_config_por_defecto_y_valor_invalido(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("max_preguntas: dos\nventana_dias: 3\n", encoding="utf-8")
    cfg = triaje.load_config(p)
    assert cfg["max_preguntas"] == 4 and cfg["ventana_dias"] == 3  # el inválido vale el defecto


# --- consulta --------------------------------------------------------------------------------------------

def test_consulta_responde_y_no_crea_tarjeta(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.CONSULTA, asker=lambda q, ctx: "Hay 2 PR abiertos.")
    say(cc, "¿qué PR hay abiertos?")
    assert h.created == [] and "Hay 2 PR abiertos." in bot.sent[-1]["text"]


def test_consulta_sin_asker_dice_que_no_tiene_el_dato(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.CONSULTA)
    say(cc, "¿cuánto gasté?")
    assert h.created == [] and "No tengo ese dato" in bot.sent[-1]["text"]


def test_triaje_apagado_o_sin_clasificador_no_cambia_nada(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.PEQUENA, asker=lambda q, ctx: "respuesta")
    cc._triaje_cfg["activo"] = False
    say(cc)
    assert cc.seen == [] and h.created == [] and "respuesta" in bot.sent[-1]["text"]


# --- trabajo pequeño: preguntas -> tarjeta ---------------------------------------------------------------

def test_preguntas_con_recomendada_primero_y_tarjeta_con_respuestas(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.PEQUENA)
    say(cc)
    assert h.created == []  # nada se crea antes de contestar
    first = texts(bot.sent[-1]["markup"])
    assert first[0].startswith("⭐") and "⚡ Créala ya" in first and "🚫 Cancelar" in first
    for _ in range(4):  # la recomendada de cada una de las 4 preguntas
        press(desk, bot.sent[-1], 0)
    [card] = h.created
    assert card["assignee"] == "claude-migrateam" and card["created_by"] == "oscar"
    assert "## Respuestas de Oscar" in card["body"] and "¿Qué alcance tiene?" in card["body"]
    assert "Origen-Telegram: chat=6744452215 thread=0" in card["body"]
    assert "Creada" in bot.sent[-1]["text"]


def test_prioridad_alta_se_traduce_con_el_yaml(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.PEQUENA)
    say(cc)
    press(desk, bot.sent[-1], 0)       # alcance
    press(desk, bot.sent[-1], 1)       # prioridad: Alta (hoy)
    press(desk, bot.sent[-1], 0)       # hecho
    press(desk, bot.sent[-1], 0)       # quién
    assert h.created[0]["priority"] == 8


def test_crear_ya_salta_las_preguntas(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.PEQUENA)
    say(cc)
    ya = texts(bot.sent[-1]["markup"]).index("⚡ Créala ya")
    press(desk, bot.sent[-1], ya)
    [card] = h.created
    assert "Créala ya" in card["body"] and TEXT in card["body"]


def test_cancelar_no_crea_nada(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.PEQUENA)
    say(cc)
    press(desk, bot.sent[-1], texts(bot.sent[-1]["markup"]).index("🚫 Cancelar"))
    assert h.created == [] and "Cancelado" in bot.edits[-1]["text"]


def test_sin_marca_la_primera_pregunta_es_el_carril(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.PEQUENA)
    say(cc, "cambia el texto del pie de la home")
    assert "carril" in bot.sent[-1]["text"]
    press(desk, bot.sent[-1], 0)       # carril (la 1.ª opción) y luego 3 preguntas: el máximo sigue siendo 4
    assert h.created == [] and "2/4" in bot.sent[-1]["text"]
    for _ in range(3):
        press(desk, bot.sent[-1], 0)
    assert len(h.created) == 1


# --- duplicados ------------------------------------------------------------------------------------------

def test_duplicado_ofrece_anadir_a_la_existente(tmp_path):
    existing = {"id": "t_0000abcd", "title": "MigraTeam: cambia el color del botón de login", "status": "ready"}
    cc, desk, bot, h = center(tmp_path, triaje.PEQUENA, cards=[existing])
    say(cc)
    assert "t_0000abcd" in bot.sent[-1]["text"] and h.created == []
    press(desk, bot.sent[-1], 0)  # ➕ Añadir a la existente
    assert h.created == [] and ("comment", "t_0000abcd") == h.calls[-1][:2]


def test_duplicado_crear_nueva_igualmente_sigue_con_preguntas(tmp_path):
    existing = {"id": "t_0000abcd", "title": TEXT, "status": "ready"}
    cc, desk, bot, h = center(tmp_path, triaje.PEQUENA, cards=[existing])
    say(cc)
    press(desk, bot.sent[-1], 1)
    assert "1/" in bot.sent[-1]["text"] and h.created == []


def test_hecha_hace_mucho_no_cuenta_como_duplicado():
    cfg = triaje.load_config("no-existe.yaml")
    old = {"id": "t_1", "title": TEXT, "status": "done", "completed_at": time.time() - 40 * 86400}
    new = dict(old, id="t_2", completed_at=time.time() - 86400)
    assert triaje.buscar_duplicado(TEXT, [old], cfg) is None
    assert triaje.buscar_duplicado(TEXT, [old, new], cfg)["id"] == "t_2"
    assert triaje.buscar_duplicado("otra cosa distinta del todo", [new], cfg) is None


# --- trabajo grande: workflow ----------------------------------------------------------------------------

def _plan(text, lanes):
    return [{"n": 1, "titulo": "Modelo", "carril": "claude-migrateam", "padres": [], "cuerpo": "a", "limites": "",
             "confirmar": False},
            {"n": 2, "titulo": "Pantalla", "carril": "claude-migrateam", "padres": [1], "cuerpo": "b", "limites": "",
             "confirmar": False}]


def test_grande_propone_workflow_y_no_crea_nada_sin_aprobar(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.GRANDE, planner=_plan)
    say(cc, "MigraTeam: nuevo módulo de informes con exportación")
    assert "workflow" in bot.sent[-1]["text"] and h.created == []
    press(desk, bot.sent[-1], texts(bot.sent[-1]["markup"]).index("🧩 Hacerlo como workflow"))
    plan = bot.sent[-1]
    assert "Coste estimado: ~3 €" in plan["text"] and "No se crea nada" in plan["text"]
    assert h.created == []  # el plan está propuesto, no aprobado
    [wf] = cc.workflow_store.load().values()
    assert wf["estado"] == "plan" and wf["tarjetas"] == []


def test_workflow_aprobado_crea_solo_la_primera_tanda_con_req(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.GRANDE, planner=_plan)
    tc.command(desk, "/workflow MigraTeam: nuevo módulo de informes")
    assert h.created == []
    press(desk, bot.sent[-1], 0)  # ✅ Aprobar plan y gasto
    [card] = h.created             # «Pantalla» depende de «Modelo»: espera
    assert card["title"] == "Modelo" and "req: req_" in card["body"]
    [wf] = cc.workflow_store.load().values()
    assert wf["estado"] == "en curso"


def test_workflow_cancelar_no_crea_nada(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.GRANDE, planner=_plan)
    tc.command(desk, "/workflow MigraTeam: nuevo módulo de informes")
    press(desk, bot.sent[-1], 1)
    assert h.created == [] and "descartado" in bot.edits[-1]["text"]


def test_workflow_sin_texto_o_sin_plan_no_crea_nada(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.GRANDE, planner=lambda t, l: [])
    tc.command(desk, "/workflow")
    assert bot.sent[-1]["text"].startswith("Uso: /workflow")
    tc.command(desk, "/workflow algo enorme", uid=2)
    assert "No pude proponer" in bot.sent[-1]["text"] and h.created == [] and cc.workflow_store.load() == {}


# --- NO_CARRIL -------------------------------------------------------------------------------------------

def test_no_carril_va_al_integrador_y_no_a_un_carril(tmp_path):
    cc, desk, bot, h = center(tmp_path, triaje.NO_CARRIL)
    say(cc, "cambia la variable DATABASE_URL en Railway")
    assert h.created == [] and "integrador" in bot.sent[-1]["text"]
    press(desk, bot.sent[-1], 0)
    [card] = h.created
    assert card["assignee"] == OSCAR_ASSIGNEE and card["title"].startswith("[INTEGRADOR]")
    assert not card["assignee"].startswith("claude-")
