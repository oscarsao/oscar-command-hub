"""Visibilidad del integrador en Telegram (t_5624611d): estado, /encolar con OK atado al commit, incidencia de Railway."""
import json
import subprocess
from types import SimpleNamespace

from agent_lanes import integrador_visibilidad as iv
from agent_lanes.commands import CommandCenter
from agent_lanes.decisions import EXT_DROP, EXT_OK, DecisionDesk
from agent_lanes.notices import MessageStore

NOW = 1_000_000.0
SHA = "a" * 40
SHA2 = "b" * 40
ESTADO = """Actualizado: 2026-10-21T10:00:00+00:00
linea suelta sin sentido
## Fusionar PR de oscar-hq
repo: oscar-hq
pr: 12
sha: abcdef1234
estado: hecho
siguiente: aplicar migración
color: azul
## Semilla
estado: en curso
"""


def test_parse_estado_tolera_lo_desconocido():
    est = iv.parse_estado(ESTADO)
    assert [p["paso"] for p in est["pasos"]] == ["Fusionar PR de oscar-hq", "Semilla"]
    assert est["pasos"][0]["pr"] == "12" and est["pasos"][1]["repo"] == ""
    assert est["actualizado"] > 0
    assert iv.parse_estado("## x\nestado: ok", mtime=5.0)["actualizado"] == 5.0  # sin cabecera: fecha del fichero


def test_resumen_corto_y_ver_mas():
    est = iv.parse_estado(ESTADO)
    aj = iv.Ajustes(resumen_pasos=1)
    ahora = est["actualizado"] + 3600
    text, mas = iv.resumen(est, aj, ahora)
    assert "Semilla" in text and "Fusionar" not in text and mas
    assert "Fusionar PR" in iv.detalle(est, aj, ahora) and "Semilla" in iv.detalle(est, aj, ahora)
    assert iv.resumen(est, iv.Ajustes(), ahora)[1] is False


def test_estado_falta_o_viejo_se_dice():
    aj = iv.Ajustes()
    assert "no hay parte" in iv.resumen(None, aj, NOW)[0]
    est = iv.parse_estado(ESTADO)
    assert "límite 24 h" in iv.resumen(est, aj, est["actualizado"] + 30 * 3600)[0]
    assert "límite 48 h" in iv.resumen(est, iv.Ajustes(estado_max_horas=48), est["actualizado"] + 50 * 3600)[0]


def test_ajustes_defaults_validos_y_textos(tmp_path):
    assert iv.load_ajustes(tmp_path / "no.yaml") == iv.Ajustes()
    f = tmp_path / "a.yaml"
    f.write_text("hora_parte: 7\nestado_max_horas: -3\nresumen_pasos: 'x'\nrepos: {OscarHQ: oscarsao/oscar-hq, mal: x}\n"
                 "textos: {incidencia_abre: 'Cola {repo} {sha}', rota: 'x', estado_falta: '{nope}'}\n", encoding="utf-8")
    aj = iv.load_ajustes(f)
    assert aj.hora_parte == 7 and aj.estado_max_horas == 24 and aj.resumen_pasos == 3
    assert aj.repos == {"oscarhq": "oscarsao/oscar-hq"}
    assert iv.texto(aj, "incidencia_abre", repo="r", sha="s") == "Cola r s"
    assert "no hay parte" in iv.texto(aj, "estado_falta")  # plantilla rota: vale el default


def test_parse_encolar():
    assert iv.parse_encolar("OscarHQ 12") == ("oscarhq", 12)
    assert iv.parse_encolar("oscarhq #12") == ("oscarhq", 12)
    assert iv.parse_encolar("oscarhq https://github.com/o/r/pull/12") == ("oscarhq", 12)
    assert iv.parse_encolar("oscarhq") is None and iv.parse_encolar("a b c") is None


def test_ok_atado_al_commit(tmp_path):
    cola = iv.ColaExternos(tmp_path / "e.json", lambda: NOW)
    key, _ = cola.add("oscarhq", "o/r", 12, "t", SHA)
    assert key == "oscarhq#12" and not cola.con_ok(key, SHA)
    assert not cola.dar_ok(key, SHA2)           # el PR cambió desde que Oscar lo vio
    assert cola.dar_ok(key, SHA) and cola.con_ok(key, SHA) and not cola.con_ok(key, SHA2)
    cola.add("oscarhq", "o/r", 12, "t", SHA2)   # nuevo commit: el OK viejo no vale
    assert not cola.con_ok(key, SHA) and not cola.con_ok(key, SHA2)
    assert cola.quitar(key) and not cola.quitar(key) and cola.items() == {}


def test_incidencia_railway_una_vez_y_se_cierra(tmp_path):
    inc = iv.IncidenciaRailway(tmp_path / "i.json", iv.Ajustes(), lambda: NOW)
    aviso = inc.abrir("oscar-hq", SHA)
    assert "en cola por incidencia de Railway" in aviso and SHA[:9] in aviso and "oscar-hq" in aviso
    assert inc.abrir("oscar-hq", SHA) is None   # ya avisado
    assert inc.cerrar("otro") is None
    assert "vuelve a responder" in inc.cerrar("oscar-hq") and inc.cerrar("oscar-hq") is None
    assert "incidencia de Railway" in inc.abrir("oscar-hq", SHA)  # incidencia nueva, aviso nuevo


# --- comandos ---------------------------------------------------------------------------------------------------

class Bot:
    def __init__(self):
        self.sent = []

    def send_to(self, chat, thread, text, html=True, **kw):
        self.sent.append((text, kw.get("reply_markup")))
        return {"message_id": 1}

    def edit(self, *a, **k):
        return True


def centro(tmp_path, gh_out=None):
    bot = Bot()
    desk = SimpleNamespace(store=SimpleNamespace(issue=lambda rec, spec: ("tok", {"inline_keyboard": spec, "rec": rec})),
                           guard_queue=None)
    llamadas = []

    def gh(*args):
        llamadas.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(gh_out or {}), stderr="")

    desk._gh = gh
    cfg = tmp_path / "v.yaml"
    cfg.write_text("repos: {oscarhq: oscarsao/oscar-hq}\n", encoding="utf-8")
    cc = CommandCenter(bot, desk, lanes={}, hermes_for=lambda b: None, messages=MessageStore(), owner_id="1",
                       now=lambda: NOW)
    cc.estado_path, cc.externos_state, cc.guard_state, cc.vis_settings = (
        tmp_path / "estado.md", tmp_path / "ext.json", tmp_path / "cola.json", cfg)
    return cc, bot, desk, llamadas


def test_integrador_muestra_estado_y_boton(tmp_path):
    cc, bot, _, _ = centro(tmp_path)
    cc.cmd_integrador(("1", "0"), "", None)
    assert "no hay parte" in bot.sent[-1][0] and bot.sent[-1][1] is None
    (tmp_path / "estado.md").write_text(ESTADO.replace("2026-10-21T10:00:00+00:00", "2026-10-12T10:00:00+00:00")
                                        + "".join(f"## p{i}\n" for i in range(5)), encoding="utf-8")
    cc.cmd_integrador(("1", "0"), "", None)
    text, markup = bot.sent[-1]
    assert "p4" in text and "p1" not in text and markup["inline_keyboard"][0][0]["text"] == "📖 Ver más"
    cc.estado_mas({"chat_id": "1", "thread_id": "0"})
    assert "Fusionar PR de oscar-hq" in bot.sent[-1][0]


def test_encolar_valida_repo_y_pide_ok(tmp_path):
    cc, bot, desk, calls = centro(tmp_path, {"state": "OPEN", "headRefOid": SHA, "title": "Mi PR"})
    cc.cmd_encolar(("1", "0"), "otro 3", None)
    assert "Uso: /encolar" in bot.sent[-1][0] and "oscarhq" in bot.sent[-1][0] and not calls
    cc.cmd_encolar(("1", "0"), "oscarhq 12", None)
    text, markup = bot.sent[-1]
    assert "Sin tu OK no se fusiona nada" in text and calls[-1][:4] == ("pr", "view", "12", "--repo")
    assert [b["action"] for b in markup["inline_keyboard"][0]] == [EXT_OK, EXT_DROP]
    assert "oscarhq #12" in cc.integrador_resumen()[0] and "espera tu OK" in cc.integrador_resumen()[0]


def test_encolar_pr_cerrado_no_se_anota(tmp_path):
    cc, bot, _, _ = centro(tmp_path, {"state": "CLOSED", "headRefOid": SHA})
    cc.cmd_encolar(("1", "0"), "oscarhq 12", None)
    assert "No encuentro" in bot.sent[-1][0] and cc.cola_externos().items() == {}


def test_boton_ok_y_cambio_de_commit(tmp_path):
    cc, bot, desk, _ = centro(tmp_path, {"state": "OPEN", "headRefOid": SHA, "title": "t"})
    cc.cmd_encolar(("1", "0"), "oscarhq 12", None)
    where, rec = {"chat_id": "1", "thread_id": "0"}, {"ext": "oscarhq#12"}
    desk._gh = lambda *a: subprocess.CompletedProcess(a, 0, stdout=json.dumps({"state": "OPEN", "headRefOid": SHA2}), stderr="")
    assert cc.externo_step(EXT_OK, rec, where) and "cambió" in bot.sent[-1][0]
    assert not cc.cola_externos().con_ok("oscarhq#12", SHA2) and not cc.cola_externos().con_ok("oscarhq#12", SHA)
    desk._gh = lambda *a: subprocess.CompletedProcess(a, 0, stdout=json.dumps({"state": "OPEN", "headRefOid": SHA}), stderr="")
    assert cc.externo_step(EXT_OK, rec, where) and "OK registrado" in bot.sent[-1][0]
    assert cc.cola_externos().con_ok("oscarhq#12", SHA)
    assert cc.externo_step(EXT_DROP, rec, where) and cc.cola_externos().items() == {}
    assert cc.externo_step(EXT_OK, rec, where) and "ya no está" in bot.sent[-1][0]


def test_botones_enrutados_por_el_desk():
    desk = DecisionDesk.__new__(DecisionDesk)
    visto = []
    desk.commands = SimpleNamespace(estado_mas=lambda w: visto.append(("mas", w)) or True,
                                    externo_step=lambda a, r, w: visto.append((a, r["ext"])) or True)
    assert desk._act({"ext": "k"}, {"action": EXT_OK}, {"w": 1}) and desk._act({}, {"action": "estado_mas"}, {"w": 1})
    assert visto == [(EXT_OK, "k"), ("mas", {"w": 1})]


def test_parte_de_las_9_con_hora_y_lineas_del_integrador():
    from datetime import datetime
    from agent_lanes import resumen_carriles as rc
    d = lambda h: datetime(2026, 10, 21, h, 5, tzinfo=rc.MADRID)  # noqa: E731
    assert rc.due_date(d(9), set()) and rc.due_date(d(7), set()) is None and rc.due_date(d(7), set(), 7)
    texto = rc.render_summary([], [], 0, NOW, extra="🧑‍💻 Estado del integrador (x)", hour=7)
    assert "Resumen de las 7:00" in texto and "Estado del integrador" in texto
    assert "Resumen de las 9:00" in rc.render_summary([], [], 0, NOW)


def test_incidencia_en_deploy_avisa_y_cierra(tmp_path):
    from agent_lanes.integrator import Integrator, IntegratorSettings
    bot = Bot()
    lane = SimpleNamespace(repo="C:/x/oscar-hq", name="claude-oscarhq")
    integ = Integrator(IntegratorSettings(integration_telegram=("-100", "7")), {}, hermes_for=lambda b: None,
                       notifier=bot, state_dir=tmp_path, now=lambda: NOW)
    integ._railway_incident(lane, SHA, abrir=True)
    integ._railway_incident(lane, SHA, abrir=True)
    assert len(bot.sent) == 1 and "oscar-hq" in bot.sent[0][0] and SHA[:9] in bot.sent[0][0]
    integ._railway_incident(lane, SHA, abrir=False)
    integ._railway_incident(lane, SHA, abrir=False)
    assert len(bot.sent) == 2 and "vuelve a responder" in bot.sent[1][0]
