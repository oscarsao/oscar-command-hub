"""Choques y solapes que ya no aplican (decisión de Oscar 20-11): cierre automático, limpieza de los antiguos con
dry-run, tope por pasada y vuelta atrás. Notificador falso: nada sale a Telegram."""
from __future__ import annotations

import json

from agent_lanes import canal_avisos
from agent_lanes.canal_avisos import CanalAvisos
from tests.test_canal_avisos import OWNER, TEMA, TG

DIA = 86400.0


def _par(c, mid, tipo, tids, *, at, nuevo=True, rol="solo"):
    """Aviso de choque/solape ya publicado. `nuevo` False = anterior al cierre automático (sin la clave `tids`)."""
    data = c._load()
    e = {"tipo": tipo, "rol": rol, "token": None, "head": f"{tipo} {mid}", "chat_id": TEMA[0], "message_id": mid,
         "thread_id": TEMA[1], "at": at}
    if nuevo:
        e["tids"] = list(tids)
    data["abiertas"][f"{TEMA[0]}:{mid}"] = e
    c._save(data)


def _canal(tmp_path, tarjetas, *, ahora=10 * DIA, vigentes=None, **kw):
    tg = TG()
    c = CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: False, sleep=lambda s: None, clock=lambda: ahora,
                    tarjeta=lambda tid: tarjetas.get(tid), **kw)
    c.vigentes = vigentes
    return c, tg


def test_choque_con_una_tarjeta_done_se_cierra_solo(tmp_path):
    c, tg = _canal(tmp_path, {"t_a": {"status": "done"}, "t_b": {"status": "running"}})
    _par(c, 801, "choque", ["t_a", "t_b"], at=9 * DIA)
    assert c.cerrar_obsoletas() == 1
    assert c.pendientes() == []
    assert tg.edits(801)[0]["text"].startswith("↪️ Ya no aplica: la tarjeta t_a está done")
    assert "reply_markup" not in tg.edits(801)[0]
    log = [json.loads(x) for x in c.cierres_path.read_text(encoding="utf-8").splitlines()]
    assert log[0]["key"] == f"{TEMA[0]}:801" and log[0]["entrada"]["tids"] == ["t_a", "t_b"]


def test_choque_entre_dos_tarjetas_activas_con_conflicto_real_no_se_cierra(tmp_path):
    vivos = {frozenset(("t_a", "t_b"))}
    c, tg = _canal(tmp_path, {"t_a": {"status": "running"}, "t_b": {"status": "ready"}},
                   vigentes=lambda tipo: vivos if tipo == "solape" else None)
    _par(c, 802, "solape", ["t_a", "t_b"], at=9 * DIA)
    _par(c, 803, "choque", ["t_a", "t_b"], at=9 * DIA)  # los choques sin recálculo (None) tampoco se cierran
    assert c.cerrar_obsoletas() == 0
    assert len(c.pendientes()) == 2 and not tg.edits() and not c.cierres_path.exists()


def test_el_par_que_ya_no_comparte_ficheros_se_cierra(tmp_path):
    c, tg = _canal(tmp_path, {"t_a": {"status": "running"}, "t_b": {"status": "running"}},
                   vigentes=lambda tipo: set())
    _par(c, 804, "solape", ["t_a", "t_b"], at=9 * DIA)
    assert c.cerrar_obsoletas() == 1
    assert "ya no comparten ficheros" in tg.edits(804)[0]["text"]


def test_caduca_a_los_n_dias_configurables(tmp_path):
    c, tg = _canal(tmp_path, {"t_a": {"status": "running"}, "t_b": {"status": "running"}}, caducan_dias=3)
    _par(c, 805, "choque", ["t_a", "t_b"], at=10 * DIA - 2 * DIA)
    _par(c, 806, "choque", ["t_a", "t_b"], at=10 * DIA - 4 * DIA)
    assert c.cerrar_obsoletas() == 1
    assert [e["message_id"] for e in c.pendientes()] == [805]
    assert "4 días" in tg.edits(806)[0]["text"]


def test_los_antiguos_sin_tids_no_los_toca_la_pasada_automatica(tmp_path):
    c, tg = _canal(tmp_path, {})
    _par(c, 807, "solape", [], at=1.0, nuevo=False)
    assert c.cerrar_obsoletas() == 0 and len(c.pendientes()) == 1 and not tg.edits()


def test_tope_por_pasada(tmp_path):
    c, tg = _canal(tmp_path, {"t_a": {"status": "done"}})
    for n in range(30):
        _par(c, 1000 + n, "choque", ["t_a", "t_b"], at=9 * DIA)
    assert c.cerrar_obsoletas() == canal_avisos.CERRAR_MAX
    assert len(c.pendientes()) == 30 - canal_avisos.CERRAR_MAX and len(tg.edits()) == canal_avisos.CERRAR_MAX


def test_dry_run_no_edita_ni_escribe_y_lista_lo_que_conserva(tmp_path):
    c, tg = _canal(tmp_path, {})
    _par(c, 810, "choque", [], at=1 * DIA, nuevo=False)      # 9 días: se cerraría
    _par(c, 811, "solape", [], at=9.5 * DIA, nuevo=False)    # medio día: se conserva
    _par(c, 812, "solape", [], at=1 * DIA, nuevo=False, rol="silenciada")
    antes = c.path.read_text(encoding="utf-8")
    inf = c.limpiar_antiguos()
    assert inf["dry_run"] and inf["se_cerrarian"] == 2 and inf["se_conservan"] == 1
    assert [f["key"] for f in inf["conservados"]] == [f"{TEMA[0]}:811"]
    assert not tg.calls and not c.cierres_path.exists() and c.path.read_text(encoding="utf-8") == antes


def test_limpieza_real_respeta_el_tope_solo_edita_y_se_revierte(tmp_path):
    c, tg = _canal(tmp_path, {})
    for n in range(7):
        _par(c, 820 + n, "choque", [], at=1 * DIA, nuevo=False)
    _par(c, 830, "solape", [], at=1 * DIA, nuevo=False, rol="silenciada")  # sin mensaje: no se edita
    inf = c.limpiar_antiguos(dry_run=False, max_cierres=3)
    assert inf["cerrados"] == 3 and inf["quedan"] == 5
    assert len(c.pendientes()) == 5
    assert not [1 for m, _ in tg.calls if m == "sendMessage"]  # solo edita mensajes existentes
    assert c.revertir(inf["lote"]) == 3
    assert len(c.pendientes()) == 8
    assert c.revertir(inf["lote"]) == 0  # ya revertido: idempotente


def test_enviar_anota_las_tarjetas_del_aviso(tmp_path):
    tg = TG()
    kb = {"inline_keyboard": [[{"text": "x", "callback_data": "tok123456:0"}]]}
    c = CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: True)
    c.enviar("solape", "🔀 Posible solape\n• t_0123abcd «a»\n• t_4567ef01 «b»", kb)
    assert [e["tids"] for e in c.pendientes()] == [["t_0123abcd", "t_4567ef01"]]
