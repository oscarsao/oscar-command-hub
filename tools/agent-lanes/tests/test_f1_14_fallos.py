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
