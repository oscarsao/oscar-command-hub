"""Operador acotado por marca: people.yaml `brands` limita dónde puede actuar (comandos, botones, aprobaciones).

Mauredy (MigraTeam) no toca Píldora/OscarHQ/Signal; Angel (OscarHQ, Signal) no toca MigraTeam; Oscar y María (`"*"`)
operan en todo. Un destino sin marca conocida se rechaza para los acotados (falla cerrado).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_lanes.decisions import DEPLOY_NO
from agent_lanes.people import load_people, norm_marca
from tests.test_botones_tarjetas import setup
from tests.test_commands import OSCAR, command, press

MARIA, MAUREDY, ANGEL = 5516823836, 7000000001, 7000000002

YAML = """people:
  maria:
    name: María
    user_id: "5516823836"
    role: coo
    brands: [Píldora, MigraTeam, "*"]
    operador: true
  mauredy:
    name: Mauredy
    user_id: "7000000001"
    role: dev
    brands: [MigraTeam]
    operador: true
  angel:
    name: Angel
    user_id: "7000000002"
    role: dev
    brands: [OscarHQ, Signal]
    operador: true
"""

REC_MIGRATEAM = {"task_id": "t_a0000001", "board": "migrateam", "lane": "claude-migrateam"}
REC_OSCARHQ = {"task_id": "t_a0000002", "board": "oscarhq", "lane": "claude-oscarhq"}
REC_SCRAPER = {"task_id": "t_a0000003", "board": "oscarhq", "lane": "claude-scraper"}
REC_HUB = {"task_id": "t_a0000004", "board": "default", "lane": "claude-hub"}
REC_SIN_MARCA = {"task_id": "bandeja", "board": "", "lane": ""}


def ppl(tmp_path):
    p = Path(tmp_path) / "people.yaml"
    p.write_text(YAML, encoding="utf-8")
    return load_people(str(OSCAR), p)


def test_puede_en_cruza_la_marca_con_brands(tmp_path):
    g = ppl(tmp_path)
    assert g.puede_en(MAUREDY, "MigraTeam") and not g.puede_en(MAUREDY, "Píldora")
    assert not g.puede_en(MAUREDY, "Signal") and not g.puede_en(MAUREDY, None)
    assert g.puede_en(ANGEL, "Píldora") and g.puede_en(ANGEL, "Signal") and not g.puede_en(ANGEL, "MigraTeam")
    for marca in ("MigraTeam", "Píldora", "NextJobs", None):
        assert g.puede_en(OSCAR, marca) and g.puede_en(MARIA, marca)
    assert not g.puede_en(999, "MigraTeam")  # quien no es operador no puede nada
    assert norm_marca("OscarHQ") == norm_marca("Píldora") == norm_marca(" PILDORA ")


def test_operador_sin_brands_no_puede_en_ninguna(tmp_path):
    p = Path(tmp_path) / "p.yaml"
    p.write_text('people:\n  x:\n    name: X\n    user_id: "8000000001"\n    role: dev\n    operador: true\n',
                 encoding="utf-8")
    g = load_people(str(OSCAR), p)
    assert g.es_operador(8000000001) and not g.puede_en(8000000001, "MigraTeam")


def test_el_yaml_real_deja_a_maria_en_todo_y_acota_a_mauredy_y_angel():
    real = load_people(str(OSCAR))
    assert all(real.puede_en(MARIA, m) for m in ("MigraTeam", "Píldora", "NextJobs", None))
    # Mauredy y Angel siguen sin user_id (null): no existen hasta que Oscar lo rellene, pero ya salen acotados.
    import yaml
    raw = yaml.safe_load(Path(__file__).resolve().parents[2].joinpath("telegram", "people.yaml")
                         .read_text(encoding="utf-8"))["people"]
    assert raw["maria"]["brands"][-1] == "*" and raw["maria"]["brands"][0] == "Píldora"  # el tablero de sus ideas no cambia
    assert raw["mauredy"]["operador"] is True and raw["mauredy"]["brands"] == ["MigraTeam"]
    assert raw["angel"]["operador"] is True and raw["angel"]["brands"] == ["Píldora", "Signal"]
    assert not raw["ingerber"].get("operador") and not raw["andrea"].get("operador")


def desk_con(tmp_path):
    cc, desk, bot, h = setup(tmp_path, {})
    desk.people = ppl(tmp_path)
    return cc, desk, bot, h


@pytest.mark.parametrize("user,rec,permitido", [
    (MAUREDY, REC_MIGRATEAM, True), (MAUREDY, REC_OSCARHQ, False), (MAUREDY, REC_SCRAPER, False),
    (MAUREDY, REC_HUB, False), (MAUREDY, REC_SIN_MARCA, False),
    (ANGEL, REC_OSCARHQ, True), (ANGEL, REC_SCRAPER, True), (ANGEL, REC_MIGRATEAM, False), (ANGEL, REC_HUB, False),
    (MARIA, REC_MIGRATEAM, True), (MARIA, REC_OSCARHQ, True), (MARIA, REC_HUB, True), (MARIA, REC_SIN_MARCA, True),
    (OSCAR, REC_MIGRATEAM, True), (OSCAR, REC_OSCARHQ, True), (OSCAR, REC_HUB, True), (OSCAR, REC_SIN_MARCA, True),
])
def test_puede_con_segun_la_marca_del_aviso(tmp_path, user, rec, permitido):
    _, desk, _, _ = desk_con(tmp_path)
    assert desk.puede_con(user, rec) is permitido


def test_un_grupo_o_aceptar_todo_exige_permiso_en_todos_los_miembros(tmp_path):
    _, desk, _, _ = desk_con(tmp_path)
    mixto = {"task_id": "bandeja", "board": "", "lane": "", "accept_all": [REC_MIGRATEAM, REC_OSCARHQ]}
    assert not desk.puede_con(MAUREDY, mixto) and not desk.puede_con(ANGEL, mixto)
    assert desk.puede_con(MARIA, mixto) and desk.puede_con(OSCAR, mixto)
    solo_mt = {**REC_MIGRATEAM, "group": [REC_MIGRATEAM, {**REC_MIGRATEAM, "task_id": "t_a0000009"}]}
    assert desk.puede_con(MAUREDY, solo_mt) and not desk.puede_con(ANGEL, solo_mt)


def pulsar(desk, bot, user, rec):
    markup = desk.store.issue(rec, [[{"text": "No despliegues", "action": DEPLOY_NO}]])[1]
    msg = {"markup": markup, "chat_id": str(user), "thread_id": "0", "message_id": 7}
    bot.answers.clear()
    press(desk, msg, 0, user=user)
    return bot.answers[-1] if bot.answers else None


@pytest.mark.parametrize("user,rec", [
    (MAUREDY, REC_OSCARHQ), (MAUREDY, REC_SCRAPER), (MAUREDY, REC_HUB), (ANGEL, REC_MIGRATEAM), (ANGEL, REC_HUB),
])
def test_boton_de_otra_marca_se_rechaza_con_mensaje_claro(tmp_path, user, rec):
    _, desk, bot, _ = desk_con(tmp_path)
    answer = pulsar(desk, bot, user, {**rec, "kind": "integrate", "merge_sha": "abc"})
    assert answer.startswith("No tienes permiso para actuar en") and "limitado a" in answer
    assert "Oscar o a María" in answer


@pytest.mark.parametrize("user,rec", [
    (MAUREDY, REC_MIGRATEAM), (ANGEL, REC_OSCARHQ), (ANGEL, REC_SCRAPER),
    (MARIA, REC_MIGRATEAM), (MARIA, REC_OSCARHQ), (MARIA, REC_HUB), (OSCAR, REC_MIGRATEAM), (OSCAR, REC_HUB),
])
def test_boton_de_su_marca_no_se_rechaza(tmp_path, user, rec):
    _, desk, bot, _ = desk_con(tmp_path)
    answer = pulsar(desk, bot, user, {**rec, "kind": "integrate", "merge_sha": "abc"})
    assert not (answer or "").startswith("No tienes permiso")


def test_comando_de_otra_marca_se_rechaza_y_el_de_la_suya_no(tmp_path):
    cc, desk, bot, _ = desk_con(tmp_path)
    veto = cc._veto_marca
    # Mauredy: nada de integrador/sesiones ni de proyectos que no sean MigraTeam
    assert "No tienes permiso" in veto(MAUREDY, "integrador", "", None)
    assert "No tienes permiso" in veto(MAUREDY, "sesion", "", None)
    assert "No tienes permiso" in veto(MAUREDY, "promover", "oscarhq", None)
    assert veto(MAUREDY, "promover", "migrateam", None) is None
    assert "No tienes permiso" in veto(ANGEL, "promover", "migrateam", None)
    assert "No tienes permiso" in veto(MAUREDY, "lote", "", None)  # sin marca clara
    # Oscar y María: sin veto
    for user in (OSCAR, MARIA):
        for cmd, args in (("integrador", ""), ("promover", "migrateam"), ("lote", "oscarhq"), ("desplegar", "oscarhq")):
            assert veto(user, cmd, args, None) is None


def test_maria_sigue_operando_por_comando_y_mauredy_no_ejecuta_integrador(tmp_path):
    cc, desk, bot, _ = desk_con(tmp_path)
    command(desk, "/tareas", user=MARIA, chat=MARIA)
    assert "No tienes permiso" not in bot.sent[-1]["text"]
    command(desk, "/integrador", user=MAUREDY, chat=MAUREDY, uid=2)
    assert bot.sent[-1]["text"].startswith("No tienes permiso")


def test_hazlo_de_otra_marca_se_veta_y_el_de_la_suya_no(tmp_path):
    cc, desk, bot, _ = desk_con(tmp_path)
    veto = cc._veto_marca
    assert "No tienes permiso" in veto(MAUREDY, "hazlo", "OscarHQ: algo", None)
    assert "No tienes permiso" in veto(MAUREDY, "hazlo", "scraper: algo", None)
    assert "No tienes permiso" in veto(ANGEL, "hazlo", "MigraTeam: algo", None)
    assert veto(MAUREDY, "hazlo", "MigraTeam: algo", None) is None
    assert veto(ANGEL, "hazlo", "OscarHQ: algo", None) is None
    assert veto(MAUREDY, "hazlo", "sin pistas de carril", None) is None  # irá a botones, que se comprueban al pulsar
    for user in (OSCAR, MARIA):
        assert veto(user, "hazlo", "OscarHQ: algo", None) is None


def test_ajustes_solo_para_quien_opera_en_todas_las_marcas(tmp_path):
    cc, desk, bot, _ = desk_con(tmp_path)
    for user in (MAUREDY, ANGEL):
        assert "No tienes permiso" in cc._veto_marca(user, "ajustes", "clave valor", None)
    for user in (OSCAR, MARIA):
        assert cc._veto_marca(user, "ajustes", "clave valor", None) is None
