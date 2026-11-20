"""Aviso de carriles de código ociosos con el runner sano (t_c0cb5658): un solo aviso por episodio, con el motivo."""
from __future__ import annotations

from agent_lanes import vigilancia as vg
from tests.test_vigilancia import Mundo, ts


def tarjeta(tid, status, carril="claude-hub", creada=None, **extra):
    return dict({"id": tid, "status": status, "assignee": carril, "board": "default", "created_at": creada}, **extra)


def pendientes():
    return [tarjeta("t_00000001", "triage"), tarjeta("t_00000002", "todo"),
            tarjeta("t_00000003", "blocked", creada=ts(8)),   # pregunta de Oscar (la más antigua)
            tarjeta("t_00000004", "blocked", creada=ts(9)),
            tarjeta("t_00000005", "blocked", creada=ts(10)),   # espera a otra tarjeta
            tarjeta("t_00000006", "ready", carril="claude-integrador"),  # no es carril de código
            tarjeta("t_00000007", "ready", carril="oscar")]


def mundo(tmp_path, tasks, sano=True):
    m = Mundo(tmp_path, ts(11))
    m.shows = {"t_00000005": {"task": {}, "parents": ["t_00000002"], "comments": []}}
    m.v.runner_sano = lambda: sano
    m.tasks = tasks
    m.tick()  # siembra: abre el episodio
    m.sent.clear()
    return m


def avisos(m):
    return [s for s in m.sent if "ociosos" in s["text"]]


def test_ocioso_avisa_una_sola_vez_con_el_motivo(tmp_path):
    m = mundo(tmp_path, pendientes())
    m.tick(ts(11, 20))
    assert avisos(m) == []                      # aún no han pasado 30 min
    out = m.tick(ts(11, 35))
    assert len(avisos(m)) == 1 and out["ocioso"]
    texto = avisos(m)[0]["text"]
    assert "1 ready" not in texto               # los ready son de integrador/oscar: no cuentan
    assert "1 todo" in texto and "1 triage" in texto and "3 bloqueadas" in texto
    assert "2 esperan una respuesta tuya" in texto and "t_0000(0003)" in texto and "1 esperan otra tarjeta" in texto
    m.tick(ts(12, 30))
    m.tick(ts(14))
    assert len(avisos(m)) == 1                  # sin repetir en el mismo episodio


def test_ocioso_se_cierra_con_un_running_y_vuelve_a_avisar_en_el_siguiente(tmp_path):
    m = mundo(tmp_path, pendientes())
    m.tick(ts(11, 35))
    assert len(avisos(m)) == 1
    m.tasks = pendientes() + [tarjeta("t_00000008", "running")]
    m.tick(ts(11, 50))                          # cierra el episodio
    m.tasks = pendientes()
    m.tick(ts(12))                              # empieza otro
    m.tick(ts(12, 40))
    assert len(avisos(m)) == 2


def test_con_trabajo_en_marcha_no_avisa(tmp_path):
    m = mundo(tmp_path, pendientes() + [tarjeta("t_00000008", "running", carril="claude-oscarhq")])
    m.tick(ts(12))
    m.tick(ts(14))
    assert avisos(m) == []


def test_un_lanzamiento_reciente_cuenta_como_trabajo(tmp_path):
    m = mundo(tmp_path, pendientes() + [tarjeta("t_00000008", "review", started_at=ts(11, 50))])
    m.tick(ts(12))
    assert avisos(m) == []


def test_runner_parado_no_manda_este_aviso(tmp_path):
    m = mundo(tmp_path, pendientes(), sano=False)
    m.tick(ts(12))
    m.tick(ts(14))
    assert avisos(m) == []                      # el de «runner parado» es del monitor, no de la vigilancia


def test_sin_pendientes_de_carriles_de_codigo_no_avisa(tmp_path):
    m = mundo(tmp_path, [t for t in pendientes() if t["assignee"] in ("oscar", "claude-integrador")])
    m.tick(ts(14))
    assert avisos(m) == []


def test_interruptor_y_umbral_ajustables(tmp_path, monkeypatch):
    ajustes = {"carriles.ocioso_aviso": "no", "carriles.ocioso_minutos": 10}
    real = vg.valor
    monkeypatch.setattr(vg, "valor", lambda k: ajustes[k] if k in ajustes else real(k))
    m = mundo(tmp_path, pendientes())
    m.tick(ts(14))
    assert avisos(m) == []                      # apagado
    ajustes["carriles.ocioso_aviso"] = "si"
    m.tick(ts(14, 1))                           # empieza el episodio
    m.tick(ts(14, 5))
    assert avisos(m) == []
    m.tick(ts(14, 12))
    assert len(avisos(m)) == 1                  # umbral de 10 min
