"""F1-04 (3c): resumen diario de avisos INFO. Varios INFO del día -> un solo mensaje; ALTA/URGENTE siguen al momento."""
from __future__ import annotations

from datetime import datetime

import pytest

from agent_lanes import doctor_config, resumen_carriles
from agent_lanes.canal_avisos import CanalAvisos
from agent_lanes.resumen_carriles import MADRID, DiarioInfo
from tests.test_canal_avisos import OWNER, TEMA, TG


class Reloj:
    def __init__(self, h, day=20):
        self.dt = datetime(2026, 10, day, h, 0, tzinfo=MADRID)

    def __call__(self):
        return self.dt


@pytest.fixture
def diario(tmp_path):
    sent, reloj = [], Reloj(8)
    d = DiarioInfo(lambda text: sent.append(text) or True, tmp_path / "resumen_info.json", now=reloj)
    resumen_carriles.instalar(d)
    yield d, sent, reloj
    resumen_carriles.instalar(None)


def test_varios_info_del_dia_salen_en_un_solo_mensaje(diario):
    d, sent, reloj = diario
    assert resumen_carriles.anotar("vigilancia", "prodwatch Vercel: desactivado (falta VERCEL_TOKEN)")
    assert resumen_carriles.anotar("limpieza", "t_1: carpeta sin worktree", clave="t_1")
    assert resumen_carriles.anotar("limpieza", "t_1: carpeta sin worktree (otra vez)", clave="t_1")  # 1 vez al día
    assert resumen_carriles.anotar("workflow_sin_origen", "wf_1 · Secuencias: sin origen", clave="wf_1")
    assert d.tick() is None and sent == []  # antes de las 9:00 no sale
    reloj.dt = reloj.dt.replace(hour=9, minute=5)
    text = d.tick()
    assert len(sent) == 1 and text == sent[0]
    assert "(3)" in text and "VERCEL_TOKEN" in text and "t_1" in text and "wf_1" in text
    assert d.tick() is None and len(sent) == 1  # una vez al día


def test_sin_eventos_no_se_envia_nada(diario):
    d, sent, reloj = diario
    reloj.dt = reloj.dt.replace(hour=10)
    assert d.tick() is None and sent == []


def test_un_envio_fallido_se_reintenta_y_no_pierde_eventos(tmp_path):
    ok = {"v": False}
    sent = []
    d = DiarioInfo(lambda text: (sent.append(text), ok["v"])[1], tmp_path / "r.json", now=Reloj(10))
    d.anotar("bateria", "PC con batería al 30 %")
    assert d.tick() is None
    ok["v"] = True
    assert d.tick() and len(sent) == 2 and "batería" in sent[-1]


def test_tipos_ajenos_no_se_apuntan(diario):
    d, sent, reloj = diario
    assert resumen_carriles.anotar("monitor", "runner parado") is False
    assert resumen_carriles.anotar("needs_input", "x") is False


def test_sin_resumen_instalado_anotar_no_hace_nada():
    resumen_carriles.instalar(None)
    assert resumen_carriles.anotar("vigilancia", "x") is False


def test_canal_pasa_los_info_al_resumen_y_lo_urgente_sale_al_momento(diario, tmp_path):
    d, sent, reloj = diario
    tg = TG()
    c = CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: False)
    assert c.enviar("vigilancia", "Supabase: desactivada") is None
    assert c.enviar("relanzada", "Resumen diario relanzado") is None
    assert tg.sends() == []  # los INFO no salen sueltos
    c.enviar("monitor", "🔴 runner parado")  # URGENTE: inmediato
    assert [p["text"] for p in tg.sends()] == ["🔴 runner parado"]
    reloj.dt = reloj.dt.replace(hour=9)
    assert "Supabase" in d.tick() and len(sent) == 1


def test_un_info_con_botones_no_se_resume(diario, tmp_path):
    tg = TG()
    c = CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: False)
    kb = {"inline_keyboard": [[{"text": "x", "callback_data": "tok:0"}]]}
    c.enviar("vigilancia", "con botón", kb)
    assert len(tg.sends()) == 1


def test_doctor_config_al_arrancar_apunta_las_funciones_apagadas(diario):
    d, sent, reloj = diario
    texto = "🩺 doctor-config · 1 activas\n🟢 tema: activo\n⚪ sync_usuarios: desactivado (falta OHQ_API_KEY)"
    doctor_config.publicar_al_arrancar(lambda t: None, informe_fn=lambda: texto, anotar=resumen_carriles.anotar)
    reloj.dt = reloj.dt.replace(hour=9)
    out = d.tick()
    assert "sync_usuarios: desactivado (falta OHQ_API_KEY)" in out and "tema: activo" not in out
