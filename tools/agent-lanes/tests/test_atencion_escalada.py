"""t_c98e2b38: un bloqueo que se repite por lo mismo escala a Oscar con otro motivo (en vez de acabar en triage) y
/atencion lista todo lo pendiente con los mismos botones que /decisiones. Dobles: sin red, sin hermes real."""
from __future__ import annotations

from agent_lanes.commands import COMMANDS, parse_command
from agent_lanes.runner import block_fingerprint

from .test_runner_state import GOOD, FakeHermes, make, ok_outcome


class HistoryHermes(FakeHermes):
    def __init__(self, events):
        super().__init__(tasks=[{"id": "t_1", "title": "T", "body": "B"}])
        self.events = events

    def show(self, task_id):
        return {"task": {"id": task_id}, "events": self.events}


def _blocked(reason, kind="transient"):
    return {"kind": "blocked", "payload": {"kind": kind, "reason": reason}}


FAILED = ok_outcome({**GOOD, "status": "failed", "summary": "no compila"})
SAME = "worker status=failed: no compila"


def test_huella_ignora_session_id_y_numeros():
    a = block_fingerprint("el worker no terminó (tras 2 resume(s)): x (session_id=abc-123)\nresto")
    b = block_fingerprint("el worker no terminó (tras 1 resume(s)): x (session_id=zzz-9)")
    assert a == b and block_fingerprint(None) == ""


def test_primer_bloqueo_sigue_siendo_transient():
    r, h, *_ = make([FAILED], hermes=HistoryHermes([]))
    assert r.run_once() == {"t_1": "blocked:transient"}
    assert [c for c in h.calls if c[0] == "block"][0][2] == "transient"


def test_segundo_bloqueo_igual_escala_a_needs_input_con_botones():
    r, h, _, n = make([FAILED], hermes=HistoryHermes([_blocked(SAME)]))
    assert r.run_once() == {"t_1": "blocked:needs_input"}
    block = [c for c in h.calls if c[0] == "block"][0]
    assert block[2] == "needs_input"
    assert "2 veces por lo mismo" in block[3] and SAME in block[3]  # otro motivo, con el detalle
    assert any("2 veces por lo mismo" in m for m in n.msgs)


def test_bloqueo_distinto_no_escala():
    r, h, *_ = make([FAILED], hermes=HistoryHermes([_blocked("verificación mecánica fallida: otra cosa")]))
    assert r.run_once() == {"t_1": "blocked:transient"}


def test_historial_ilegible_bloquea_como_siempre():
    class Roto(HistoryHermes):
        def show(self, task_id):
            raise RuntimeError("hermes caído")

    r, *_ = make([FAILED], hermes=Roto([]))
    assert r.run_once() == {"t_1": "blocked:transient"}


def test_atencion_esta_registrado_y_se_parsea():
    assert "atencion" in [c for c, _ in COMMANDS]
    assert parse_command("/atencion@pildora_carriles_bot", "pildora_carriles_bot") == ("atencion", "")
