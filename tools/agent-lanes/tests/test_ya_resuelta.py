"""Cierre «ya resuelto» (21-11): el worker prueba que el hallazgo ya está en la base y la tarjeta se cierra sin PR y sin
preguntar a Oscar. Con una prueba floja se ignora y todo sigue como antes. Nada sale a Telegram: notificador falso."""
from __future__ import annotations

from agent_lanes.runner import LaneRunner
from tests.test_runner_state import GOOD, LANE, FakeGit, FakeHermes, FakeVerifier, FakeWorker, ok_outcome

EVID = "El portal recibe 404 en la ruta de descarga: ver backend/forms_routes.py y tests/test_portal_pdf.py."
YA = {"evidence": EVID, "checked_sha": "b" * 40}


class HermesCompleta(FakeHermes):
    def complete(self, task_id, result, metadata):
        self.calls.append(("complete", task_id, result, metadata))
        return True, ""


class Notif:
    def __init__(self):
        self.sent = []

    def send(self, *a, **k):
        self.sent.append((a, k))
        return None


def _correr(result, verifier=None):
    h = HermesCompleta(tasks=[{"id": "t_1", "title": "T", "body": "Cuerpo"}])
    notif = Notif()
    ver = verifier or FakeVerifier()
    out = LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome(result)]), verifier=ver,
                     notify=notif).run_once()
    return h, out


def _ya(status, **extra):
    return {**GOOD, "status": status, "changed_files": [], "already_resolved": YA, **extra}


def test_needs_input_con_ya_resuelto_cierra_la_tarjeta_sin_preguntar():
    q = {"question": "¿Cierro?", "options": ["Sí", "No"], "recommended": 0}
    h, out = _correr(_ya("needs_input", questions=[q]))
    assert out == {"t_1": "ya_resuelta"}
    assert "complete" in h.kinds() and "block" not in h.kinds() and "review" not in h.kinds()
    comment = [c for c in h.calls if c[0] == "comment" and "CERRADA COMO YA RESUELTA" in c[2]]
    assert len(comment) == 1 and EVID in comment[0][2] and ("b" * 12) in comment[0][2]
    meta = [c for c in h.calls if c[0] == "complete"][0][3]
    assert meta["kind"] == "ya_resuelta" and meta["checked_sha"] == "b" * 40 and meta["verified"] is False


def test_done_con_ya_resuelto_no_pasa_por_la_verificacion_de_commits():
    h, out = _correr(_ya("done"), verifier=FakeVerifier(ok=False))  # sin commits el verificador diría que no
    assert out == {"t_1": "ya_resuelta"} and "complete" in h.kinds() and "review" not in h.kinds()


def test_evidencia_corta_se_ignora_y_sigue_la_pregunta_de_siempre():
    q = {"question": "¿Cierro?", "options": ["Sí", "No"], "recommended": 0}
    r = _ya("needs_input", questions=[q])
    r["already_resolved"] = {"evidence": "ya está", "checked_sha": "b" * 40}
    h, out = _correr(r)
    assert "complete" not in h.kinds() and "block" in h.kinds()


def test_sha_invalido_o_con_ficheros_cambiados_se_ignora():
    q = {"question": "¿Cierro?", "options": ["Sí", "No"], "recommended": 0}
    for cambio in ({"already_resolved": {"evidence": EVID, "checked_sha": "no-es-un-sha"}},
                   {"changed_files": ["backend/x.py"]}):
        h, _ = _correr({**_ya("needs_input", questions=[q]), **cambio})
        assert "complete" not in h.kinds() and "block" in h.kinds()


def test_complete_rechazado_bloquea_transitorio_y_no_cierra():
    class Rechaza(HermesCompleta):
        def complete(self, task_id, result, metadata):
            return False, "nope"
    h = Rechaza(tasks=[{"id": "t_1", "title": "T", "body": "Cuerpo"}])
    LaneRunner(LANE, hermes=h, git=FakeGit(), worker=FakeWorker([ok_outcome(_ya("done"))]), verifier=FakeVerifier(),
               notify=Notif()).run_once()
    assert [c for c in h.calls if c[0] == "block"][0][2] == "transient"
