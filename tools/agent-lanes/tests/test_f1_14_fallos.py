"""F1-14: fallos corregidos junto a la botonera (rutas ajenas, block con timeout, despliegue externo, avisos del
monitor, títulos de ids). Sin red, sin Telegram ni hermes reales."""
from __future__ import annotations

from agent_lanes.solapes import Ajustes, extraer_rutas

HUB = "C:/Users/oscar/oscar-command-hub"
A = Ajustes()


def test_rutas_absolutas_ajenas_al_repo_no_cuentan_como_fichero_comun():
    t = ("toca C:\\Users\\oscar\\AppData\\Local\\Temp\\x\\notas.md y /tmp/build/salida.py y ~/dev/otro.py "
         "y C:/Users/oscar/dev/otro-repo/src/app.py")
    assert extraer_rutas(t, A, HUB) == set()


def test_ruta_absoluta_dentro_del_repo_se_deja_relativa():
    t = "edita " + HUB + "/tools/agent-lanes/agent_lanes/solapes.py"
    assert extraer_rutas(t, A, HUB) == {"tools/agent-lanes/agent_lanes/solapes.py"}


# --- 4. el block de un needs_input da timeout ------------------------------------------------------------------

from agent_lanes.runner import LaneRunner  # noqa: E402
from tests.test_runner_state import LANE, FakeGit, FakeHermes, FakeNotifier, FakeVerifier, FakeWorker, ok_outcome  # noqa: E402


class BlockFlaky(FakeHermes):
    """`block` falla las primeras `fallos` veces (timeout); `show` dice el estado real de la tarjeta."""

    def __init__(self, fallos, status="running"):
        super().__init__(tasks=[])
        self.fallos, self.status = fallos, status

    def block(self, task_id, kind, reason):
        self.calls.append(("block", task_id, kind, reason))
        if self.fallos > 0:
            self.fallos -= 1
            return False
        self.status = "blocked"
        return True

    def show(self, tid):
        return {"task": {"id": tid, "status": self.status}}


def _runner(h):
    return LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome()]), verifier=FakeVerifier(),
                      notify=FakeNotifier())


def test_block_con_timeout_se_reintenta_y_la_tarjeta_no_queda_en_running():
    h = BlockFlaky(fallos=1)
    r = _runner(h)
    assert r._block("t_aaaaaaaa", "needs_input", "x", {"id": "t_aaaaaaaa"}, public="p") == "blocked:needs_input"
    assert h.status == "blocked" and [c[0] for c in h.calls].count("block") == 2


def test_block_que_no_se_logra_se_concilia_en_la_pasada_siguiente():
    h = BlockFlaky(fallos=3)
    r = _runner(h)
    r._block("t_aaaaaaaa", "needs_input", "x", {"id": "t_aaaaaaaa"}, public="p")
    assert h.status == "running" and "t_aaaaaaaa" in r._block_pendiente()
    r.jobs()   # la pasada siguiente reintenta antes de reclamar nada
    assert h.status == "blocked" and not r._block_pendiente()


def test_block_ya_hecho_pese_al_timeout_cuenta_como_bloqueada():
    h = BlockFlaky(fallos=1, status="blocked")   # el timeout llegó tras bloquear de verdad
    r = _runner(h)
    r._block("t_aaaaaaaa", "needs_input", "x", {"id": "t_aaaaaaaa"}, public="p")
    assert not r._block_pendiente() and [c[0] for c in h.calls].count("block") == 1


# --- 5. «fusionado sin desplegar» comprueba el SHA servido ------------------------------------------------------

import json  # noqa: E402

from tests.test_integrator import MERGE, make  # noqa: E402

OTRO = "b" * 40


def _health(sha, status="healthy"):
    return lambda url: (200, json.dumps({"status": status, "commit_sha": sha}))


def _fusionada(integ, tid="t_1"):
    integ._save(tid, lane="claude-migrateam", status="merged", merge_sha=MERGE)


def test_despliegue_externo_ya_servido_no_cuenta_como_sin_desplegar(tmp_path):
    integ, *_ = make(tmp_path, name="claude-migrateam", health=_health(MERGE))
    _fusionada(integ)
    assert integ._merged_states(integ.lanes["claude-migrateam"], "") == []
    assert integ._state("t_1")["status"] == "deployed"   # concilia el estado local


def test_si_el_servidor_sirve_otro_sha_sigue_sin_desplegar(tmp_path):
    integ, *_ = make(tmp_path, name="claude-migrateam", health=_health(OTRO))
    integ._is_ancestor = lambda lane, old, new: False
    _fusionada(integ)
    assert [t for t, _ in integ._merged_states(integ.lanes["claude-migrateam"], "")] == ["t_1"]


def test_sin_health_valido_se_avisa_como_siempre(tmp_path):
    integ, *_ = make(tmp_path, name="claude-migrateam", health=_health(MERGE, status="degraded"))
    _fusionada(integ)
    assert len(integ._merged_states(integ.lanes["claude-migrateam"], "")) == 1


# --- 8. listas de ids con título corto y enlace a la ficha -----------------------------------------------------

from agent_lanes import titulos  # noqa: E402


def test_etiqueta_da_titulo_corto_y_enlace_a_la_ficha():
    e = titulos.etiqueta("t_abcdef01", "[Hub] Botonera estándar en todas las fichas de tarjeta con muchas palabras · implementar")
    assert e.startswith("t_abcdef01 · Botonera estándar") and e.endswith("(/tarea t_abcdef01)") and "[Hub]" not in e
    assert titulos.etiqueta("t_abcdef01") == "t_abcdef01 (/tarea t_abcdef01)"


def test_al_dia_lista_lo_sin_desplegar_con_titulo_y_enlace():
    from types import SimpleNamespace

    from agent_lanes.al_dia import AlDia, ProjectState

    hermes = SimpleNamespace(show=lambda tid: {"task": {"title": "[OHQ] Arreglar el login · implementar"}})
    integ = SimpleNamespace(hermes_for=lambda board: hermes)
    st = ProjectState("oscarhq", SimpleNamespace(board="default"), None, sin_desplegar=["t_abcdef01"])
    texto = AlDia(integ, ajustes_path="no-existe.yaml").resumen(st, "dev")
    assert "t_abcdef01 · Arreglar el login (/tarea t_abcdef01)" in texto
