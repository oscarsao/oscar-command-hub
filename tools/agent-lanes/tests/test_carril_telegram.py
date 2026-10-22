"""Carril claude-telegram (22-10): plaza reservada fuera de max_workers, repo por tarjeta (lista blanca) y enrutado
automático de las tarjetas de Telegram. Dobles en memoria: sin red, sin Telegram, sin hermes."""
from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent_lanes import resumen_carriles, telegram_lane
from agent_lanes.config import Lane, load_lanes, load_runner_settings
from agent_lanes.hermes import HermesCLI
from agent_lanes.runner import LaneRunner
from agent_lanes.service import Service

TG = "claude-telegram"
REPOS = {"hub": {"repo": "C:/hub", "github": "o/hub", "base": "main", "test_cmd": "T-HUB",
                 "forbidden_paths": [".github/workflows/"]},
         "oscar-hq": {"repo": "C:/ohq", "github": "o/ohq", "base": "master", "test_cmd": "T-OHQ", "role": "roles/implementador.md"}}
LANE = Lane(name=TG, board="default", repo="C:/hub", base="main", reserved_slots=1, repos=REPOS)


def task(title="[Telegram] arreglo", body="", tid="t_00000001", assignee=TG):
    return {"id": tid, "title": title, "body": body, "assignee": assignee}


# --- configuración real -----------------------------------------------------------------------------------------

def test_lanes_yaml_real_define_el_carril_con_plaza_y_dos_repos():
    lanes = load_lanes()
    lane = lanes[TG]
    assert lane.kind == "implement" and lane.reserved_slots == 1 and lane.max_parallel == 1
    assert lane.board == "default" and list(lane.repos) == ["hub", "oscar-hq"]
    assert lane.repos["oscar-hq"]["github"] == "oscarsao/oscar-hq" and lane.repos["oscar-hq"]["base"] == "master"
    assert TG in lanes["review"].reviews
    # mismos límites que un carril de código: workflows y estado local vetados, mismo allowed_tools
    assert ".github/workflows/" in lane.forbidden_paths and "tools/agent-lanes/.env" in lane.forbidden_paths
    assert lane.allowed_tools == lanes["claude-hub"].allowed_tools
    assert lane.role_path.is_file()


def test_el_resto_de_carriles_sigue_sin_plaza_ni_repos():
    for name, lane in load_lanes().items():
        if name != TG:
            assert lane.reserved_slots == 0 and not lane.repos


def test_aparece_como_carril_propio_en_el_resumen_de_carriles():
    h = MagicMock()
    h.list_status.side_effect = lambda assignee, status: [{"title": "x"}] if (assignee == TG and status == "ready") else []
    rows = resumen_carriles.collect_rows({TG: load_lanes()[TG]}, lambda board: h)
    assert rows[0]["name"] == TG and rows[0]["queue"] == 1
    assert TG in resumen_carriles.render_lane(rows[0])


# --- plaza reservada ----------------------------------------------------------------------------------------------

class FakeRunner:
    """LaneRunner de pega: `jobs()` devuelve las tareas; cada job espera a su evento."""

    def __init__(self, name, tids, reserved=0, gate=None):
        self.lane = SimpleNamespace(name=name, reserved_slots=reserved)
        self.tids, self.gate, self.started = list(tids), gate, []

    def jobs(self):
        def job(t):
            self.started.append(t)
            if self.gate:
                self.gate.wait(5)
            return "done"
        return [(t, lambda t=t: job(t)) for t in self.tids]


def test_con_los_generales_ocupados_el_reservado_arranca_igualmente():
    gate = threading.Event()
    general = [FakeRunner(f"g{i}", [f"t_g{i}"], gate=gate) for i in range(4)]  # 4 generales = max_workers
    tg = FakeRunner(TG, ["t_tg1", "t_tg2"], reserved=1, gate=gate)
    svc = Service(general + [tg], max_workers=4)
    pasada = threading.Thread(target=svc.run_pass)
    pasada.start()
    try:
        # la pasada general sigue esperando a sus 4 workers, y la plaza reservada lanza el suyo sin esperar
        assert svc.reserved_pass() == ["t_tg1"]
        # nunca supera su plaza: con uno vivo no arranca el segundo ni repite el primero
        assert svc.reserved_pass() == []
    finally:
        gate.set()
        pasada.join(5)
    assert svc.wait_reserved() == {"t_tg1": "done"}
    assert tg.started == ["t_tg1"]
    # libre la plaza, la siguiente pasada lanza la que quedaba (la lista de la fake sigue incluyendo la primera: la
    # vigilancia de «ya en vuelo» no la repite mientras viva, y una terminada solo volvería si el kanban la mantiene ready)
    assert svc.reserved_pass() and "t_tg2" in {t for t, _ in tg.jobs()}


def test_la_pasada_general_no_toca_los_carriles_reservados():
    tg = FakeRunner(TG, ["t_tg1"], reserved=1)
    gen = FakeRunner("g", ["t_g1"])
    assert Service([gen, tg], max_workers=4).run_pass() == {"t_g1": "done"}
    assert tg.started == []


def test_el_reservado_no_gasta_plazas_generales():
    # max_workers 1 con un general vivo: el reservado corre a la vez (no espera turno en el pool general)
    gate = threading.Event()
    gen = FakeRunner("g", ["t_g1"], gate=gate)
    tg = FakeRunner(TG, ["t_tg1"], reserved=1, gate=gate)
    svc = Service([gen, tg], max_workers=1)
    pasada = threading.Thread(target=svc.run_pass)
    pasada.start()
    svc.reserved_pass()
    for _ in range(100):
        if tg.started and gen.started:
            break
        threading.Event().wait(0.02)
    assert tg.started == ["t_tg1"] and gen.started == ["t_g1"]  # ambos vivos a la vez con max_workers=1
    gate.set()
    pasada.join(5)
    svc.wait_reserved()


def test_sin_carriles_reservados_no_hay_hilo_de_fondo():
    assert Service([FakeRunner("g", [])], max_workers=2).start_reserved_ticker(1) is None


def test_max_workers_real_no_cuenta_la_plaza_reservada():
    assert load_runner_settings()["max_workers"] == 4  # la plaza es aparte: 4 generales + 1 reservada


# --- repo por tarjeta ---------------------------------------------------------------------------------------------

def test_linea_repo_elige_y_el_lane_efectivo_trae_su_configuracion():
    eff, err = telegram_lane.para_tarjeta(LANE, task(body="Repo: oscar-hq\nTexto"))
    assert err is None and (eff.repo, eff.base, eff.test_cmd, eff.role) == ("C:/ohq", "master", "T-OHQ", "roles/implementador.md")
    assert eff.name == TG and eff.github == "o/ohq" and not eff.repos and eff.reserved_slots == 1
    eff, _ = telegram_lane.para_tarjeta(LANE, task(body="Repo: HUB"))
    assert (eff.repo, eff.test_cmd, eff.forbidden_paths) == ("C:/hub", "T-HUB", (".github/workflows/",))


def test_repo_fuera_de_la_lista_blanca_es_error():
    eff, err = telegram_lane.para_tarjeta(LANE, task(body="Repo: migrateam"))
    assert eff is LANE and "no está permitido" in err and "hub, oscar-hq" in err


def test_sin_linea_se_deduce_por_rutas_y_si_no_el_primero():
    assert telegram_lane.alias_de(LANE, task(body="toca src/services/telegram_avisos.py")) == ("oscar-hq", None)
    assert telegram_lane.alias_de(LANE, task("[Telegram] x", "toca tools/telegram/bot.py")) == ("hub", None)
    assert telegram_lane.alias_de(LANE, task(body="")) == ("hub", None)
    assert telegram_lane.alias_de(Lane(name="x"), task()) == (None, None)  # carril de un solo repo: nada que elegir


def test_la_linea_repo_manda_sobre_la_deduccion():
    assert telegram_lane.alias_de(LANE, task(body="Repo: hub\nsrc/api/routes/telegram_x.py")) == ("hub", None)


def _runner(lane=LANE, guard=None):
    h = MagicMock()
    h.claim.return_value = True
    h.drop_telegram_subs.return_value = []
    r = LaneRunner(lane, hermes=h, git=None, worker=None, verifier=None, notify=lambda *a, **k: None, repo_guard=guard)
    return r, h


def test_process_trabaja_en_el_repo_de_la_tarjeta(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(LaneRunner, "_work", lambda self, task, tid, sid, dl: seen.append((self.lane.repo, self.lane.base)) or "ok")
    r, _ = _runner()
    r.state_dir = tmp_path
    assert r.process(task(body="Repo: oscar-hq")) == "ok"
    assert r.process(task(body="Repo: hub", tid="t_00000002")) == "ok"
    assert seen == [("C:/ohq", "master"), ("C:/hub", "main")] and r.lane is LANE  # el carril base no se muta


def test_process_bloquea_con_pregunta_si_el_repo_no_esta_permitido(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(LaneRunner, "_work", lambda *a: called.append(1))
    bloqueos = []
    monkeypatch.setattr(LaneRunner, "_block", lambda self, tid, kind, reason, task=None, **kw: bloqueos.append((kind, kw)) or "b")
    r, _ = _runner()
    r.state_dir = tmp_path
    assert r.process(task(body="Repo: nextjobs")) == "b"
    kind, kw = bloqueos[0]
    assert kind == "needs_input" and not called
    assert kw["questions"][0]["options"] == ["Repo: hub", "Repo: oscar-hq"]


def test_el_candado_de_repos_se_aplica_al_repo_elegido(monkeypatch, tmp_path):
    monkeypatch.setattr(LaneRunner, "_work", lambda *a: "ok")
    bloqueos = []
    monkeypatch.setattr(LaneRunner, "_block", lambda self, tid, kind, reason, task=None, **kw: bloqueos.append(reason) or "b")
    r, _ = _runner(guard=lambda lane: f"{lane.repo} apunta a otro repo" if lane.repo == "C:/ohq" else None)
    r.state_dir = tmp_path
    assert r.process(task(body="Repo: hub")) == "ok"
    assert r.process(task(body="Repo: oscar-hq", tid="t_00000002")) == "b" and "C:/ohq" in bloqueos[0]


# --- enrutado automático ------------------------------------------------------------------------------------------

def test_es_telegram_por_titulo_y_por_bots():
    assert telegram_lane.es_telegram("[Telegram] Botón nuevo")
    assert telegram_lane.es_telegram("  [telegram] x")
    assert telegram_lane.es_telegram("Arreglar aviso", "revisa @PildoraD_BOT y equipo_tareas")
    assert telegram_lane.es_telegram("Otro", "tocar tools/telegram/people.yaml")
    assert not telegram_lane.es_telegram("[Hub] Algo", "nada que ver con mensajería")


def test_ruta_solo_reencamina_desde_hub_y_oscarhq():
    assert telegram_lane.ruta("claude-hub", "[Telegram] x") == TG
    assert telegram_lane.ruta("claude-oscarhq", "[Telegram] x") == TG
    assert telegram_lane.ruta("claude-hub", "[Hub] otra cosa") == "claude-hub"
    assert telegram_lane.ruta("claude-migrateam", "[Telegram] x") == "claude-migrateam"
    assert "Repo: oscar-hq" in telegram_lane.con_repo("cuerpo", "claude-oscarhq")
    assert telegram_lane.con_repo("cuerpo", "claude-hub") == "cuerpo"
    assert telegram_lane.con_repo("Repo: hub", "claude-oscarhq") == "Repo: hub"


def test_hermes_create_enruta_una_tarjeta_de_telegram():
    cmds = []

    def run(cmd, **kw):
        cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout="t_0000abcd", stderr="")

    HermesCLI("default", exe="hermes", runner=run).create("[Telegram] Botón", "b", "claude-hub")
    HermesCLI("default", exe="hermes", runner=run).create("[Hub] Otra", "b", "claude-hub")
    assert cmds[0][cmds[0].index("--assignee") + 1] == TG
    assert cmds[1][cmds[1].index("--assignee") + 1] == "claude-hub"


def test_hermes_create_no_enruta_a_otro_tablero():
    cmds = []

    def run(cmd, **kw):
        cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout="t_0000abcd", stderr="")

    HermesCLI("oscarhq", exe="hermes", runner=run).create("[Telegram] Botón", "b", "claude-oscarhq")
    assert cmds[0][cmds[0].index("--assignee") + 1] == "claude-oscarhq"  # Hermes no mueve tarjetas entre tableros


def test_reasignar_abiertas_solo_ready_de_telegram_del_mismo_tablero():
    lanes = {TG: LANE, "claude-hub": Lane(name="claude-hub", board="default"),
             "claude-oscarhq": Lane(name="claude-oscarhq", board="oscarhq")}
    boards = {"default": MagicMock(), "oscarhq": MagicMock()}
    boards["default"].list_ready.side_effect = lambda a: [task("[Telegram] a", tid="t_00000001", assignee=a),
                                                          task("[Hub] b", tid="t_00000002", assignee=a)] if a == "claude-hub" else []
    boards["oscarhq"].list_ready.return_value = [task("[Telegram] c", tid="t_00000003", assignee="claude-oscarhq")]
    boards["default"].reassign.return_value = True
    assert telegram_lane.reasignar_abiertas(boards.__getitem__, lanes) == ["t_00000001"]
    boards["default"].reassign.assert_called_once()
    assert boards["default"].reassign.call_args.args[:2] == ("t_00000001", TG)
    boards["oscarhq"].reassign.assert_not_called()  # otro tablero: no se mueve
    boards["default"].list_status.assert_not_called()  # solo ready: las running ni se miran


def test_reasignar_sin_el_carril_configurado_no_hace_nada():
    assert telegram_lane.reasignar_abiertas(lambda b: MagicMock(), {}) == []
