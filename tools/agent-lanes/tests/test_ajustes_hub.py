"""Ajustes editables del hub (t_9e297c93): por defecto == comportamiento de antes, validación, auditoría, reversión,
recarga en caliente, permisos y pantallas de /ajustes."""
from __future__ import annotations

import os
from datetime import datetime

import pytest

from agent_lanes import ajustes_hub as ah
from agent_lanes import atascos, batch, guard_route, resumen_carriles
from agent_lanes.aviso import REMIND_HOURS, StuckWatcher
from agent_lanes.config import load_lanes, load_notice_limits, load_runner_settings
from agent_lanes.notices import LIMITS
from tests.test_commands import NOT_OWNER, OSCAR, center, command, press, texts


@pytest.fixture
def store(tmp_path):
    return ah.almacen()


# --- por defecto == comportamiento actual -------------------------------------------------------------

def test_defaults_iguales_a_las_constantes_de_antes():
    d = {k: s.default for k, s in ah.SETTINGS.items()}
    assert d["atascos.escalado_guard_horas"] == guard_route.WINDOW_HOURS
    assert d["atascos.aplazar_horas"] == guard_route.SNOOZE_HOURS
    assert d["avisos.resumen_ventana_horas"] == resumen_carriles.WINDOW_HOURS
    assert d["avisos.decisiones_en_resumen"] == resumen_carriles.DECISIONS_SHOWN
    assert d["avisos.hora_parte"] is None and resumen_carriles.SLOT_HOUR == 9
    assert d["atascos.aviso_hora"] == atascos.NOTICE_HOUR
    assert d["atascos.titulo_max"] == atascos.NOTICE_TITLE_MAX
    assert d["atascos.solo_anadir"] == atascos.DEFAULT_APPEND_ONLY
    assert d["lotes.titulo_excluido_max"] == batch.EXCLUDED_TITLE_MAX
    assert d["avisos.recordatorio_atascadas_horas"] == REMIND_HOURS


def test_sin_editar_los_modulos_se_comportan_como_antes():
    assert StuckWatcher(lambda: [], None, None, "x.json").hours == 24
    assert resumen_carriles._hour(None) == 9
    assert "2 h" in guard_route.integrator_text({"tid": "t_1", "veces": 1})
    assert "24 h más" in guard_route.oscar_text({"tid": "t_1", "queued_at": 0}, 3600)
    now = datetime(2026, 10, 21, 10, 59, tzinfo=resumen_carriles.MADRID)
    assert resumen_carriles.due_date(now, set()) == "2026-10-21"
    assert resumen_carriles.due_date(now.replace(hour=11), set()) is None


def test_yaml_manda_si_no_hay_valor_editado(tmp_path):
    y = tmp_path / "l.yaml"
    y.write_text("runner: {max_workers: 3, interval_seconds: 60}\nnotices: {text_max: 4000}\ndefaults: {max_parallel: 1}\n"
                 "lanes: {a: {repo: r, base: main, board: b}}\n", encoding="utf-8")
    assert load_runner_settings(y)["max_workers"] == 3
    assert load_notice_limits(y) == {"text_max": 4000}
    assert load_lanes(y)["a"].max_parallel == 1


# --- edición válida / inválida ------------------------------------------------------------------------

def test_edicion_valida_y_efecto_en_modulos(store):
    store.set("atascos.escalado_guard_horas", "4", "Oscar")
    store.set("atascos.aplazar_horas", 48, "Oscar")
    store.set("avisos.recordatorio_atascadas_horas", 12, "Oscar")
    store.set("avisos.hora_parte", 8, "Oscar")
    store.set("avisos.decisiones_en_resumen", 1, "Oscar")
    assert "4 h" in guard_route.integrator_text({"tid": "t_1", "veces": 1})
    assert "48 h más" in guard_route.oscar_text({"tid": "t_1", "queued_at": 0}, 3600)
    assert StuckWatcher(lambda: [], None, None, "x.json").hours == 12
    assert resumen_carriles._hour(None) == 8
    assert resumen_carriles.due_date(datetime(2026, 10, 21, 8, 5, tzinfo=resumen_carriles.MADRID), set())
    q = guard_route.GuardQueue(store.path.parent / "q.json", now=lambda: 0.0)
    q.update("x", a=1)
    class P:  # pending mínimo
        tid, lane, task, need = "t_9", type("L", (), {"name": "c"})(), {"title": "x"}, "n"
    q.add(P)
    q.snooze("t_9")
    assert q.get("t_9")["snooze_until"] == 48 * 3600


@pytest.mark.parametrize("key,val", [
    ("atascos.escalado_guard_horas", "0.5"),   # escalado >= 1 h
    ("carriles.max_workers", 5), ("carriles.max_workers", 0), ("carriles.max_workers", "dos"),
    ("avisos.hora_parte", 22), ("lotes.hora", "25:00"), ("lotes.hora", "8:30"),
    ("atascos.titulo_max", 3.5), ("atascos.solo_anadir", "../etc/passwd"), ("atascos.solo_anadir", "/abs/x")])
def test_edicion_invalida_se_rechaza_con_mensaje(store, key, val):
    with pytest.raises(ah.AjusteInvalido) as e:
        store.set(key, val, "Oscar")
    assert str(e.value)
    assert not store.editado(key) and store.historial() == []


def test_max_workers_avisa_de_ram_y_de_reinicio(store):
    assert "RAM" in store.set("carriles.max_workers", 3, "Oscar")
    msg = store.set("carriles.max_workers", 1, "Oscar")
    assert "RAM" not in msg and "reiniciar el runner" in msg
    assert "reiniciar" in store.set("carriles.intervalo_segundos", 30, "Oscar")


def test_ajustes_de_arranque_llegan_a_la_config_del_runner(store, tmp_path):
    y = tmp_path / "l.yaml"
    y.write_text("runner: {max_workers: 3}\ndefaults: {max_parallel: 1}\nlanes: {a: {repo: r, base: main, board: b}}\n",
                 encoding="utf-8")
    store.set("carriles.max_workers", 2, "Oscar")
    store.set("carriles.ciego_minutos", 10, "Oscar")
    store.set("carriles.max_parallel", 2, "Oscar")
    store.set("topes.reason_max", 900, "Oscar")
    r = load_runner_settings(y)
    assert (r["max_workers"], r["ciego_minutes"], r["interval_seconds"]) == (2, 10, 60)
    assert load_lanes(y)["a"].max_parallel == 2
    assert load_notice_limits(y)["reason_max"] == 900 and "reason_max" in LIMITS


def test_lista_solo_anadir_y_lote_hora(store):
    assert store.set("atascos.solo_anadir", "CHANGELOG.md, docs/NOTAS.md ;docs/NOTAS.md", "Oscar") == ""
    assert store.valor("atascos.solo_anadir") == ("CHANGELOG.md", "docs/NOTAS.md")
    store.set("lotes.hora", "07:30", "Oscar")
    assert store.valor("lotes.hora") == "07:30"


# --- auditoría, reversión, recarga en caliente ---------------------------------------------------------

def test_auditoria_y_reversion(store):
    store.set("atascos.titulo_max", 80, "María")
    store.set("atascos.titulo_max", 30, "Oscar")
    h = store.historial("atascos.titulo_max")
    assert [(r["quien"], r["antes"], r["despues"], r["accion"]) for r in h] == [
        ("María", 50, 80, "cambio"), ("Oscar", 80, 30, "cambio")]
    assert all(r["ts"] for r in h)
    assert store.reset("atascos.titulo_max", "Oscar") is True
    assert store.valor("atascos.titulo_max") == 50 and not store.editado("atascos.titulo_max")
    assert store.historial()[-1]["accion"] == "restablecer" and store.historial()[-1]["antes"] == 30
    assert store.reset("atascos.titulo_max", "Oscar") is False


def test_recarga_en_caliente_al_editar_el_fichero_a_mano(store):
    assert ah.valor("atascos.aviso_hora") == 9
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"valores": {"atascos.aviso_hora": 11}}', encoding="utf-8")
    os.utime(store.path, ns=(1, 5_000_000_000))
    assert ah.valor("atascos.aviso_hora") == 11   # sin reiniciar nada
    store.path.write_text('{"valores": {"atascos.aviso_hora": 99, "no.existe": 1}}', encoding="utf-8")
    os.utime(store.path, ns=(1, 6_000_000_000))
    assert ah.valor("atascos.aviso_hora") == 9    # valor roto a mano: vale el de fábrica
    store.path.write_text("{no es json", encoding="utf-8")
    os.utime(store.path, ns=(1, 7_000_000_000))
    assert ah.valor("atascos.aviso_hora") == 9


# --- /ajustes en Telegram: botones, permisos ------------------------------------------------------------

def test_flujo_de_botones_guarda_y_restablece(tmp_path):
    cc, desk, bot, h, _ = center(tmp_path, {})
    command(desk, "/ajustes")
    menu = bot.sent[-1]
    assert texts(menu["markup"]) == ["Avisos", "Atascos", "Lotes", "Carriles", "Avanzado"]
    press(desk, menu, 1)   # Atascos
    grupo = bot.sent[-1]
    assert "Escalado a Oscar de un bloqueo de guard: 2" in grupo["text"]
    press(desk, grupo, 0)  # primer ajuste
    det = bot.sent[-1]
    assert texts(det["markup"])[:4] == ["1", "2", "4", "8"]
    press(desk, det, 2)    # 4 h
    assert "✅" in bot.sent[-1]["text"] and ah.valor("atascos.escalado_guard_horas") == 4.0
    assert ah.almacen().historial()[-1]["quien"] == "Oscar"
    command(desk, "/ajustes atascos.escalado_guard_horas")
    det = bot.sent[-1]
    assert "✅ 4" in texts(det["markup"])
    press(desk, det, len(texts(det["markup"])) - 2)   # ↩️ Valor por defecto
    assert "vuelve al valor por defecto" in bot.sent[-1]["text"] and ah.valor("atascos.escalado_guard_horas") == 2.0


def test_ajuste_de_reinicio_lo_dice_en_la_pantalla_y_al_guardar(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    command(desk, "/ajustes carriles.max_workers")
    assert "reiniciar el runner" in bot.sent[-1]["text"] and "RAM" in bot.sent[-1]["text"]
    command(desk, "/ajustes carriles.max_workers 4", uid=2)
    assert "RAM" in bot.sent[-1]["text"] and "reiniciar el runner" in bot.sent[-1]["text"]
    assert ah.almacen().valor("carriles.max_workers") == 4


def test_modo_avanzado_valida_y_historial(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    command(desk, "/ajustes atascos.solo_anadir ../secreto")
    assert bot.sent[-1]["text"].startswith("❌ No lo he guardado") and not ah.almacen().editado("atascos.solo_anadir")
    command(desk, "/ajustes atascos.escalado_guard_horas 0", uid=2)
    assert "❌" in bot.sent[-1]["text"]
    command(desk, "/ajustes atascos.solo_anadir CHANGELOG.md", uid=3)
    command(desk, "/ajustes historial", uid=4)
    assert "atascos.solo_anadir" in bot.sent[-1]["text"]
    command(desk, "/ajustes nada.raro 3", uid=5)
    assert "No existe" in bot.sent[-1]["text"]


def test_solo_operadores_editan(tmp_path):
    cc, desk, bot, *_ = center(tmp_path, {})
    command(desk, "/ajustes atascos.titulo_max 20", user=555, chat=555)
    assert bot.sent[-1]["text"] == NOT_OWNER and not ah.almacen().editado("atascos.titulo_max")
    command(desk, "/ajustes")                           # Oscar abre el menú...
    menu = bot.sent[-1]
    press(desk, menu, 0, user=555)                      # ...y un extraño no puede pulsar sus botones
    assert "solo para operadores" in bot.answers[-1] and len(bot.sent) == 2   # rechazo + menú; nada más
    assert not ah.almacen().editado("atascos.titulo_max")


def test_el_comando_esta_registrado():
    from agent_lanes.commands import COMMANDS
    assert "ajustes" in dict(COMMANDS)
    assert OSCAR
