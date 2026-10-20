"""Gate de lote con tsc/vitest del dashboard, escáner de secretos con textos de prueba ficticios y aviso inmediato de
los bloqueos de prioridad alta. Todo con dobles: sin red, sin Telegram, sin Hermes ni npm reales."""
from __future__ import annotations

from pathlib import Path

from agent_lanes.aviso import UrgentBlockWatcher
from agent_lanes.integrator import Policy, load_integrator_settings, scan_secrets
from tests.test_batch import Env
from tests.test_commands import OSCAR, Q_REC, center, needs
from tests.test_integrator import diff

LANES_YAML = Path(__file__).resolve().parents[1] / "lanes.yaml"
FRONT_CMD = 'py -3.12 -c "import os,sys;sys.exit(1 if os.path.exists(\'dashboard/TSC_ERR\') else 0)"'


# --- 1) gate del dashboard ---------------------------------------------------------------------------------

def test_lanes_yaml_corre_tsc_y_vitest_del_dashboard_en_oscarhq():
    pol = load_integrator_settings(LANES_YAML, lane_filter=False).policies["claude-oscarhq"]
    assert pol.batch_frontend_paths == ("dashboard/",)
    assert "tsc --noEmit" in pol.batch_frontend_cmd and "vitest run" in pol.batch_frontend_cmd
    assert "npm ci" in pol.batch_frontend_cmd  # el worktree del lote no trae node_modules


def _env(tmp_path):
    pol = Policy(lane="claude-oscarhq", batch="daily", batch_brand="oscarhq", batch_frontend_paths=("dashboard/",),
                 batch_frontend_cmd=FRONT_CMD)
    return Env(tmp_path, pol), pol


def test_lote_con_dashboard_en_rojo_no_dice_tests_ok_ni_se_abre(tmp_path):
    e, pol = _env(tmp_path)
    e.branch("t_front", {"dashboard/TSC_ERR": "x\n"})  # tsc/vitest fallan
    e.queue()
    out = e.integ.batches.assemble(e.lane, pol)
    assert e.gh.created == [] and e.remote_branches() == []
    assert "no se pudo montar" in out or "vacío" in out


def test_dashboard_en_rojo_aparta_a_la_rama_culpable_y_el_resto_pasa(tmp_path):
    e, pol = _env(tmp_path)
    e.branch("t_a", {"src/a.py": "a = 2\n"})
    e.branch("t_front", {"dashboard/TSC_ERR": "x\n"})
    e.branch("t_b", {"dashboard/ok.ts": "export const b = 1\n"})
    e.queue()
    e.integ.batches.assemble(e.lane, pol)
    b = e.integ.batches.all()[0]
    assert [m["tid"] for m in b["members"]] == ["t_a", "t_b"]
    assert [x["tid"] for x in b["excluded"]] == ["t_front"] and "probada sola" in b["excluded"][0]["reason"]
    assert "build/typecheck del frontend OK" in e.gh.created[0]["body"]


def test_lote_sin_tocar_el_dashboard_no_corre_su_gate(tmp_path):
    e, pol = _env(tmp_path)
    e.branch("t_a", {"src/a.py": "a = 2\n"})
    e.queue()
    e.integ.batches.assemble(e.lane, pol)
    assert "frontend" not in e.gh.created[0]["body"]


# --- 2) secret-scan: textos de prueba ficticios -------------------------------------------------------------

def hits(line):
    return scan_secrets(diff("tests/test_error_triage.py", "+" + line))


def test_clave_ficticia_de_prueba_se_admite():
    assert hits('assert redact("sk-ABCD1234efgh5678IJKL9012")') == []
    assert hits('k = "sk-XXXXXXXXXXXXXXXXXXXXXXXX"') == []
    assert hits('k = "sk-ejemplo-de-clave-para-el-test"') == []


def test_marca_allow_en_la_linea_admite_solo_esa_linea():
    body = "+k = 'sk-ant-api03-" + "Z" * 40 + "'  # secret-scan: allow\n+k2 = 'sk-ant-api03-" + "Z" * 40 + "'"
    found = scan_secrets(diff("tests/t.py", body))
    assert [(f[1], f[2]) for f in found] == [(2, "clave sk-")]


def test_clave_con_forma_real_se_detecta():
    assert hits("k = 'sk-ant-api03-" + "Z" * 40 + "'")[0][2] == "clave sk-"
    assert hits("k = 'sk-proj-Qm7Rt2Vx9LpA4sDf8Gh3JkZc5Nb'")[0][2] == "clave sk-"
    assert hits("t = 'ghp_" + "a" * 36 + "'")[0][2] == "token de GitHub"
    # una palabra de ejemplo pegada a la clave no la convierte en ficticia
    assert hits("k = 'sk-Qm7Rt2Vx9LpA4sDf8Gh3JkZexample5Nb'")[0][2] == "clave sk-"


def test_ficticia_en_una_regla_no_tapa_a_otra_real_en_la_misma_linea():
    line = "k = 'sk-ABCD1234efgh5678IJKL9012' ; t = 'ghp_" + "a" * 36 + "'"
    assert hits(line)[0][2] == "token de GitHub"


# --- 3) bloqueos de prioridad alta: aviso inmediato --------------------------------------------------------

def blocked_center(tmp_path, prio):
    show = needs("t_aaaaaaa1", Q_REC, assignee="claude-oscarhq", title="Arreglar el login")
    show["task"]["priority"] = prio
    cc, desk, bot, h, messages = center(tmp_path, {"t_aaaaaaa1": show})
    cc.urgent_state = tmp_path / "urgent.json"
    return cc, bot


def test_prio_8_bloqueada_avisa_al_dm_con_la_accion(tmp_path):
    cc, bot = blocked_center(tmp_path, 8)
    assert cc.urgent_tick() == ["t_aaaaaaa1"]
    msg = bot.sent[-1]
    assert msg["chat_id"] == str(OSCAR) and "Prioridad 8" in msg["text"] and "t_aaaaaaa1" in msg["text"]
    assert "¿Publicar ya?" in msg["text"] and "A) Sí" in msg["text"] and "Acción:" in msg["text"]
    assert msg["markup"] and msg["markup"]["inline_keyboard"]  # con los botones de la tarjeta


def test_prio_5_no_avisa_al_momento(tmp_path):
    cc, bot = blocked_center(tmp_path, 5)
    assert cc.urgent_tick() == [] and bot.sent == []


def test_no_repite_el_aviso_y_un_reinicio_tampoco(tmp_path):
    cc, bot = blocked_center(tmp_path, 10)
    cc.urgent_tick()
    cc._urgent_at = 0
    assert cc.urgent_tick() == [] and len(bot.sent) == 1
    cc2, bot2 = blocked_center(tmp_path, 10)  # mismo fichero de estado
    assert cc2.urgent_tick() == [] and bot2.sent == []


def test_aviso_urgente_tambien_va_al_tema_atencion_con_mencion(tmp_path):
    cc, bot = blocked_center(tmp_path, 9)
    cc.renotifier().attention = {"chat": "-100777", "thread": "12", "mention": "@oscar_e2"}
    cc.urgent_tick()
    assert [(m["chat_id"], m["thread_id"]) for m in bot.sent] == [(str(OSCAR), "0"), ("-100777", "12")]
    assert "@oscar_e2" in bot.sent[-1]["text"] and bot.sent[-1]["markup"] is None


def test_si_el_envio_falla_se_reintenta_en_la_siguiente_vuelta(tmp_path):
    class P:
        tid, state, since, task = "t_x", "needs_input", 5.0, {"priority": 9}

    ok = [False]
    w = UrgentBlockWatcher(lambda: [P()], lambda p: ok[0], tmp_path / "u.json")
    assert w.tick() == []
    ok[0] = True
    assert w.tick() == ["t_x"] and w.tick() == []


def test_sin_estado_configurado_ni_almacen_real_no_hace_nada(tmp_path):
    cc, bot = blocked_center(tmp_path, 10)
    del cc.urgent_state
    assert cc.urgent_tick() == [] and bot.sent == []
