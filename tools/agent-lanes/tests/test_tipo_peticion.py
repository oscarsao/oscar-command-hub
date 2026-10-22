"""/pedir: tipo (bug | feature) propuesto y confirmado con botón, preguntas por tipo y modo OASP (entrega B del pitch)."""
from __future__ import annotations

import yaml

from agent_lanes import tipo_peticion as tp
from tests import test_commands as tc
from tests.test_commands import OSCAR, command, press, texts
from tests.test_comandos_rapidos import center


class Registro:
    """Doble de la interfaz mínima del registro de A1."""

    def __init__(self):
        self.creadas, self.updates = [], []

    def crear(self, proyecto, titulo, origen):
        self.creadas.append({"proyecto": proyecto, "titulo": titulo, "origen": origen})
        return f"req_{len(self.creadas)}"

    def actualizar(self, req_id, **campos):
        self.updates.append((req_id, campos))


def env(tmp_path, registro=None):
    cc, desk, bot, h = center(tmp_path)
    cc.peticiones = registro
    return cc, desk, bot, h


def reply(desk, bot, prompt_msg, text, uid=70):
    desk.handle_update({"update_id": uid, "message": {
        "message_id": uid, "from": {"id": OSCAR}, "chat": {"id": OSCAR}, "text": text,
        "reply_to_message": {"message_id": prompt_msg["message_id"]}}})


# --- B1: proponer, nunca fijar --------------------------------------------------------------------------------

def test_detect_proposes_but_only_the_button_fixes_the_type(tmp_path):
    reg = Registro()
    cc, desk, bot, h = env(tmp_path, reg)
    command(desk, "/pedir MigraTeam: el login falla con Google")
    ask = bot.sent[-1]
    assert "Parece algo que falla" in ask["text"]
    assert texts(ask["markup"]) == ["🐞 Arreglar algo que falla", "✨ Crear algo nuevo", "❓ Solo una pregunta",
                                   "🚫 Cancelar"]
    assert h.created == [] and reg.creadas == [] and reg.updates == []  # sin pulsar no hay tipo, ni tarjeta, ni registro


def test_no_hint_asks_without_guessing(tmp_path):
    cc, desk, bot, h = env(tmp_path)
    command(desk, "/pedir hablemos del precio")
    assert bot.sent[-1]["text"].startswith("¿Qué es esto?") and h.created == []


def test_tie_between_hints_does_not_propose():
    cfg = tp.load_config(None)
    assert tp.detect_tipo("crear un informe que no funciona", cfg) is None
    assert tp.detect_tipo("el botón no funciona", cfg) == "bug"
    assert tp.detect_tipo("añade un botón nuevo", cfg) == "feature"


def test_can_confirm_the_other_type_than_the_proposed(tmp_path):
    reg = Registro()
    cc, desk, bot, h = env(tmp_path, reg)
    command(desk, "/pedir el informe falla al exportar")  # propone bug
    press(desk, bot.sent[-1], 1)  # pero la persona dice feature
    assert reg.updates[0] == ("req_1", {"tipo": "feature", "tipo_confirmado_por": "Oscar"})
    assert bot.sent[-1]["text"].startswith("Pregunta 1/3")


def test_pedir_without_text_explains_usage(tmp_path):
    cc, desk, bot, h = env(tmp_path)
    command(desk, "/pedir")
    assert bot.sent[-1]["text"].startswith("Uso: /pedir") and h.created == []


def test_cancel_creates_nothing(tmp_path):
    reg = Registro()
    cc, desk, bot, h = env(tmp_path, reg)
    command(desk, "/pedir el login falla")
    press(desk, bot.sent[-1], 3)  # 0 bug, 1 feature, 2 consulta, 3 cancelar
    assert h.created == [] and reg.creadas == [] and "Cancelado" in bot.edits[-1]["text"]


# --- B2: bug --------------------------------------------------------------------------------------------------

def test_bug_flow_asks_repro_where_expected_urgency_and_prioritizes(tmp_path):
    reg = Registro()
    cc, desk, bot, h = env(tmp_path, reg)
    command(desk, "/pedir MigraTeam: el login falla con Google")
    press(desk, bot.sent[-1], 0)
    assert bot.sent[-1]["text"].startswith("Pregunta 1/4: ¿Cómo se reproduce?")
    press(desk, bot.sent[-1], 0)  # ✍️ Responder: pide el texto con force_reply
    prompt = bot.sent[-1]
    assert prompt["markup"]["force_reply"] is True and "reproduce" in prompt["text"]
    reply(desk, bot, prompt, "Pulso «Entrar con Google» y vuelve a la pantalla de login")
    assert bot.sent[-1]["text"].startswith("Pregunta 2/4: ¿Dónde falla?")
    press(desk, bot.sent[-1], 1)  # ⏭ Sin dato
    assert bot.sent[-1]["text"].startswith("Pregunta 3/4: ¿Qué esperabas")
    press(desk, bot.sent[-1], 1)
    q = bot.sent[-1]["text"]
    assert q.startswith("Pregunta 4/4: ¿Cuánto urge?") and "P0 · Producción rota" in q and "C) P2" in q and "C) P2 · Molesta pero hay rodeo ⭐" in q
    press(desk, bot.sent[-1], 1)  # B = P1
    [card] = h.created
    assert card["assignee"] == "claude-migrateam" and card["priority"] == 8
    assert card["title"].startswith("[Bug P1] MigraTeam: el login falla")
    body = card["body"]
    assert "req: req_1" in body and "Tipo: bug · Urgencia: P1" in body
    assert "Pulso «Entrar con Google»" in body and "sin dato" in body and "Tipo confirmado por: Oscar" in body
    assert reg.creadas[0]["proyecto"] == "MigraTeam" and reg.creadas[0]["origen"] == "telegram"
    assert reg.updates[-1] == ("req_1", {"urgencia": "P1", "modo_oasp": None})
    assert "Creada" in bot.sent[-1]["text"]


def test_questions_already_answered_in_the_message_are_not_repeated(tmp_path):
    cc, desk, bot, h = env(tmp_path)
    command(desk, "/pedir El login falla. Pasos: al pulsar Entrar nada. Debería entrar. Es urgente, P1, en https://x.com/login")
    press(desk, bot.sent[-1], 0)
    [card] = h.created  # no queda ninguna pregunta: tarjeta directa, con la urgencia que ya dijo
    assert "Tipo: bug · Urgencia: P1" in card["body"] and card["priority"] == 8


def test_urgency_skipped_gets_the_recommended_one_and_says_so(tmp_path):
    cc, desk, bot, h = env(tmp_path)
    command(desk, "/pedir El login falla. Pasos: al pulsar Entrar. Debería entrar. En https://x.com/login")
    press(desk, bot.sent[-1], 0)
    assert bot.sent[-1]["text"].startswith("Pregunta 1/1: ¿Cuánto urge?")
    press(desk, bot.sent[-1], 6)  # ⏭ Sin dato
    [card] = h.created
    assert "Urgencia: P2" in card["body"] and "sin dato" in card["body"]


def test_max_questions_cap(tmp_path):
    cc, desk, bot, h = env(tmp_path)
    cc.tipo.cfg["max_preguntas"]["bug"] = 2
    command(desk, "/pedir el login falla")
    press(desk, bot.sent[-1], 0)
    assert bot.sent[-1]["text"].startswith("Pregunta 1/2")


# --- B2: feature y modo OASP ----------------------------------------------------------------------------------

def test_feature_flow_asks_three_questions_then_proposes_mode_and_confirms(tmp_path):
    reg = Registro()
    cc, desk, bot, h = env(tmp_path, reg)
    command(desk, "/pedir nextjobs: añade una exportación a CSV, es un cambio de varias semanas")
    press(desk, bot.sent[-1], 1)
    assert bot.sent[-1]["text"].startswith("Pregunta 1/3: ¿Qué quieres conseguir")
    press(desk, bot.sent[-1], 1)  # sin dato
    assert "A) Solo Oscar ⭐" in bot.sent[-1]["text"]
    press(desk, bot.sent[-1], 3)  # ⭐ Recomendada
    press(desk, bot.sent[-1], 1)  # criterio sin dato
    mode = bot.sent[-1]
    assert "Te propongo Pitch" in mode["text"]
    assert texts(mode["markup"]) == ["Fast-Track (menos de 1 h)", "Spec-Lite (1-3 días)", "⭐ Pitch (1-3 semanas)", "🚫 Cancelar"]
    assert h.created == []  # hasta confirmar el modo no hay tarjeta
    press(desk, mode, 1)  # la persona elige Spec-Lite, no el propuesto
    [card] = h.created
    assert "Tipo: feature · Modo OASP: spec-lite" in card["body"] and "Solo Oscar" in card["body"]
    assert card["priority"] is None and card["title"].startswith("[Feature] nextjobs:")
    assert reg.updates[-1] == ("req_1", {"urgencia": None, "modo_oasp": "spec-lite"})


def test_mode_proposal_rules():
    cfg = tp.load_config(None)
    assert tp.propose_modo("cambia el texto del botón", {}, cfg) == "fast-track"
    assert tp.propose_modo("un módulo nuevo de facturas", {}, cfg) == "pitch"
    assert tp.propose_modo("pantalla de ajustes", {"objetivo": "ordenar"}, cfg) == "spec-lite"
    assert tp.propose_modo("cambia el texto " + "palabra " * 130, {}, cfg) == "spec-lite"  # largo: ya no es Fast-Track


def test_accept_all_recommended_only_when_every_pending_has_one(tmp_path):
    cc, desk, bot, h = env(tmp_path)
    command(desk, "/pedir el login falla")
    press(desk, bot.sent[-1], 0)
    assert "✅ Acepto todas las recomendadas" not in texts(bot.sent[-1]["markup"])  # repro es libre
    cc.tipo.cfg["preguntas"]["feature"] = [
        {"id": "a", "texto": "¿A?", "opciones": ["x", "y"], "recomendada": 1, "pistas": []},
        {"id": "b", "texto": "¿B?", "opciones": ["u", "v"], "recomendada": 0, "pistas": []}]
    command(desk, "/pedir crea un informe", uid=2)
    press(desk, bot.sent[-1], 1)
    assert "✅ Acepto todas las recomendadas" in texts(bot.sent[-1]["markup"])
    press(desk, bot.sent[-1], texts(bot.sent[-1]["markup"]).index("✅ Acepto todas las recomendadas"))
    assert "Te propongo" in bot.sent[-1]["text"]  # contestadas las dos de golpe, falta confirmar el modo


# --- registro: solo 4 campos ----------------------------------------------------------------------------------

def test_registry_only_gets_the_four_contract_fields(tmp_path):
    reg = Registro()
    cc, desk, bot, h = env(tmp_path, reg)
    command(desk, "/pedir El login falla. Pasos: al pulsar Entrar. Debería entrar. En https://x.com/login. P0")
    press(desk, bot.sent[-1], 0)
    assert reg.updates and all(set(c) <= set(tp.REGISTRO_CAMPOS) for _, c in reg.updates)
    assert reg.updates[0][1]["tipo_confirmado_por"] == "Oscar" and reg.updates[-1][1]["urgencia"] == "P0"


def test_works_without_registry_and_without_req_line(tmp_path):
    cc, desk, bot, h = env(tmp_path, None)
    command(desk, "/pedir El login falla. Pasos: al pulsar Entrar. Debería entrar. En https://x.com/login. P3")
    press(desk, bot.sent[-1], 0)
    [card] = h.created
    assert "req:" not in card["body"] and "Tipo: bug · Urgencia: P3" in card["body"]


def test_unplaced_request_becomes_idea_card_for_oscar(tmp_path):
    cc, desk, bot, h = env(tmp_path)
    command(desk, "/pedir hablemos: el listado falla. Pasos: al abrir. Debería cargar. Pantalla de pagos. P2")
    press(desk, bot.sent[-1], 0)
    [card] = h.created
    assert card["assignee"] == "oscar" and card["key"].startswith("pedir-")


# --- configuración editable -----------------------------------------------------------------------------------

def test_yaml_overrides_defaults_and_bad_values_are_ignored(tmp_path):
    f = tmp_path / "tp.yaml"
    f.write_text(yaml.safe_dump({"urgencias": [{"id": "P0", "texto": "Todo roto", "prioridad": 99}],
                                 "max_preguntas": {"bug": 1}, "modo_por_defecto": 7}), encoding="utf-8")
    cfg = tp.load_config(f)
    assert cfg["urgencias"][0]["prioridad"] == 99 and cfg["max_preguntas"] == {"bug": 1, "feature": 3, "consulta": 0}
    assert cfg["modo_por_defecto"] == "spec-lite"  # tipo inválido: se queda el valor por defecto
    assert tp.load_config(tmp_path / "no-existe.yaml") == tp.load_config(None)
    f.write_text("[: no yaml", encoding="utf-8")
    assert tp.load_config(f) == tp.load_config(None)
