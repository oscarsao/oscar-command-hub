"""Aprobación de review: un timeout de `hermes kanban complete` se reintenta antes de aparcar (t_104416af, 28-10)."""
from tests.test_phase2 import make_review, verdict


def build(fallos, cierra_en_fallo=False):
    r, h, g, n, _ = make_review([verdict("approve")])
    esperas, intentos = [], []
    r._sleep = esperas.append

    def complete(tid, result, metadata):
        intentos.append(tid)
        if len(intentos) <= fallos:
            if cierra_en_fallo:  # el complete sí se aplicó aunque hermes diese timeout
                h.tasks[0]["status"] = "done"
            return False, "timeout 120s"
        h.tasks[0]["status"] = "done"
        return True, ""

    h.complete = complete
    return r, h, g, n, esperas, intentos


def test_timeout_transitorio_se_reintenta_con_espera_creciente_y_cierra():
    r, h, g, n, esperas, intentos = build(fallos=2)
    assert r.run_once() == {"t_1": "done"}
    assert len(intentos) == 3 and esperas == [10, 20]
    assert g.cleaned == ["t_1"] and "block" not in h.kinds()


def test_si_el_complete_si_se_aplico_no_se_repite_ni_se_aparca():
    r, h, g, n, esperas, intentos = build(fallos=1, cierra_en_fallo=True)
    assert r.run_once() == {"t_1": "done"}
    assert len(intentos) == 1 and "block" not in h.kinds()


def test_tras_tres_intentos_fallidos_se_aparca_como_antes():
    r, h, g, n, esperas, intentos = build(fallos=99)
    assert r.run_once() == {"t_1": "blocked:transient"}
    assert len(intentos) == 3 and g.cleaned == []
    assert any(c[0] == "block" and "complete rechazado" in c[3] for c in h.calls)
