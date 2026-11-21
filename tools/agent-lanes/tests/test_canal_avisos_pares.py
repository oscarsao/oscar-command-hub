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


def _canal(tmp_path, tarjetas, *, ahora=10 * DIA, vigentes=None, caducan_dias=0, encendido=True, **kw):
    kw.setdefault("ajustes_cierre", lambda: (encendido, caducan_dias))
    tg = TG()
    c = CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: False, sleep=lambda s: None, clock=lambda: ahora,
                    tarjeta=lambda tid: tarjetas.get(tid), **kw)
    c.vigentes = vigentes
    return c, tg


def test_choque_con_una_tarjeta_done_e_integrada_se_cierra_solo(tmp_path):
    c, tg = _canal(tmp_path, {"t_a": {"status": "done", "rama_integrada": True}, "t_b": {"status": "running"}})
    _par(c, 801, "choque", ["t_a", "t_b"], at=9 * DIA)
    assert c.cerrar_obsoletas() == 1
    assert c.pendientes() == []
    assert tg.edits(801)[0]["text"].startswith("↪️ Ya no aplica: la tarjeta t_a está done e integrada")
    assert "reply_markup" not in tg.edits(801)[0]
    log = [json.loads(x) for x in c.cierres_path.read_text(encoding="utf-8").splitlines()]
    assert log[0]["key"] == f"{TEMA[0]}:801" and log[0]["entrada"]["tids"] == ["t_a", "t_b"]


def test_done_sin_constancia_de_integracion_no_cierra_el_choque(tmp_path):
    for n, info in enumerate(({"status": "done", "rama_integrada": False}, {"status": "done", "rama_integrada": None},
                              {"status": "done", "comentarios": ["INTEGRADOR: revisando"]})):
        c, tg = _canal(tmp_path / str(n), {"t_a": info, "t_b": {"status": "running"}})
        _par(c, 900 + n, "choque", ["t_a", "t_b"], at=9 * DIA)
        assert c.cerrar_obsoletas() == 0 and not tg.edits()
    c, tg = _canal(tmp_path / "ok", {"t_a": {"status": "done", "comentarios": ["INTEGRADO abc123 en main"]}})
    _par(c, 910, "choque", ["t_a", "t_b"], at=9 * DIA)
    assert c.cerrar_obsoletas() == 1


def test_tarjeta_archivada_cierra_el_choque(tmp_path):
    c, tg = _canal(tmp_path, {"t_a": {"status": "archived"}, "t_b": {"status": "running"}})
    _par(c, 920, "choque", ["t_a", "t_b"], at=9 * DIA)
    assert c.cerrar_obsoletas() == 1 and "archived" in tg.edits(920)[0]["text"]


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


def test_el_choque_no_caduca_en_la_pasada_automatica(tmp_path):
    c, tg = _canal(tmp_path, {"t_a": {"status": "running"}, "t_b": {"status": "running"}}, caducan_dias=3)
    _par(c, 808, "choque", ["t_a", "t_b"], at=1 * DIA)  # 9 días: una decisión de Oscar sin contestar no se cierra sola
    assert c.cerrar_obsoletas() == 0 and not tg.edits()


def test_caduca_a_los_n_dias_configurables(tmp_path):
    c, tg = _canal(tmp_path, {"t_a": {"status": "running"}, "t_b": {"status": "running"}}, caducan_dias=3)
    _par(c, 805, "solape", ["t_a", "t_b"], at=10 * DIA - 2 * DIA)
    _par(c, 806, "solape", ["t_a", "t_b"], at=10 * DIA - 4 * DIA)
    assert c.cerrar_obsoletas() == 1
    assert [e["message_id"] for e in c.pendientes()] == [805]
    assert "4 días" in tg.edits(806)[0]["text"]


def test_los_antiguos_sin_tids_no_los_toca_la_pasada_automatica(tmp_path):
    c, tg = _canal(tmp_path, {})
    _par(c, 807, "solape", [], at=1.0, nuevo=False)
    assert c.cerrar_obsoletas() == 0 and len(c.pendientes()) == 1 and not tg.edits()


def test_tope_por_pasada(tmp_path):
    c, tg = _canal(tmp_path, {"t_a": {"status": "archived"}})
    for n in range(30):
        _par(c, 1000 + n, "choque", ["t_a", "t_b"], at=9 * DIA)
    assert c.cerrar_obsoletas() == canal_avisos.CERRAR_MAX
    assert len(c.pendientes()) == 30 - canal_avisos.CERRAR_MAX and len(tg.edits()) == canal_avisos.CERRAR_MAX


def test_dry_run_no_edita_ni_escribe_y_lista_lo_que_conserva(tmp_path):
    c, tg = _canal(tmp_path, {}, caducan_dias=3)
    _par(c, 810, "choque", [], at=1 * DIA, nuevo=False)      # 9 días, pero un choque antiguo NO se cierra nunca
    _par(c, 811, "solape", [], at=9.5 * DIA, nuevo=False)    # medio día: se conserva
    _par(c, 812, "solape", [], at=1 * DIA, nuevo=False, rol="silenciada")  # 9 días: se cerraría
    antes = c.path.read_text(encoding="utf-8")
    inf = c.limpiar_antiguos()
    assert inf["dry_run"] and inf["se_cerrarian"] == 1 and inf["se_conservan"] == 2
    assert [f["key"] for f in inf["conservados"]] == [f"{TEMA[0]}:810", f"{TEMA[0]}:811"]
    assert not tg.calls and not c.cierres_path.exists() and c.path.read_text(encoding="utf-8") == antes


def test_limpieza_real_respeta_el_tope_solo_edita_y_se_revierte(tmp_path):
    c, tg = _canal(tmp_path, {}, caducan_dias=3)
    for n in range(7):
        _par(c, 820 + n, "solape", [], at=1 * DIA, nuevo=False)
    _par(c, 830, "solape", [], at=1 * DIA, nuevo=False, rol="silenciada")  # sin mensaje: no se edita
    _par(c, 831, "choque", [], at=1 * DIA, nuevo=False)                    # el choque antiguo se queda
    inf = c.limpiar_antiguos(dry_run=False, max_cierres=3)
    assert inf["cerrados"] == 3 and inf["quedan"] == 5
    assert len(c.pendientes()) == 6
    assert not [1 for m, _ in tg.calls if m == "sendMessage"]  # solo edita mensajes existentes
    assert c.revertir(inf["lote"]) == 3
    assert len(c.pendientes()) == 9
    assert c.revertir(inf["lote"]) == 0  # ya revertido: idempotente


def test_el_contador_fijado_se_edita_y_queda_con_lo_que_sigue_abierto(tmp_path):
    c, tg = _canal(tmp_path, {}, caducan_dias=3)
    c.fijado._store({"chat_id": TEMA[0], "thread_id": TEMA[1], "message_id": 77, "text": "🎯 <b>3 decisiones</b>"})
    _par(c, 840, "solape", [], at=1 * DIA, nuevo=False)
    _par(c, 841, "needs_input", ["t_a"], at=9.9 * DIA)
    c.limpiar_antiguos(dry_run=False)
    assert "1 decisión pendiente" in tg.edits(77)[0]["text"]
    assert not [1 for m, _ in tg.calls if m == "sendMessage"]


def test_enviar_anota_las_tarjetas_del_aviso(tmp_path):
    tg = TG()
    kb = {"inline_keyboard": [[{"text": "x", "callback_data": "tok123456:0"}]]}
    c = CanalAvisos(tg, OWNER, TEMA, tmp_path, vivo=lambda t: True)
    c.enviar("solape", "🔀 Posible solape\n• t_0123abcd «a»\n• t_4567ef01 «b»", kb)
    assert [e["tids"] for e in c.pendientes()] == [["t_0123abcd", "t_4567ef01"]]


# --- t_0ff63a37, devolución de la Integradora -----------------------------------------------------------------------

def test_nace_apagado_sin_yaml_o_con_yaml_invalido_no_cierra(tmp_path):
    assert canal_avisos.cargar_ajustes_cierre(tmp_path / "no_existe.yaml") == (False, 0.0)
    for n, texto in enumerate(("[", "cerrar_avisos_solos: false", "cerrar_avisos_solos: 'true'", "- a")):
        f = tmp_path / f"a{n}.yaml"
        f.write_text(texto, encoding="utf-8")
        assert canal_avisos.cargar_ajustes_cierre(f) == (False, 0.0)
    f = tmp_path / "on.yaml"
    f.write_text("cerrar_avisos_solos: true\nsolapes_caducan_dias: 5", encoding="utf-8")
    assert canal_avisos.cargar_ajustes_cierre(f) == (True, 5.0)
    # el choques.yaml del repo nace apagado
    assert canal_avisos.cargar_ajustes_cierre() == (False, 0.0)
    c, tg = _canal(tmp_path, {"t_a": {"status": "archived"}}, encendido=False)
    _par(c, 850, "choque", ["t_a", "t_b"], at=9 * DIA)
    assert c.cerrar_obsoletas() == 0 and not tg.edits()


def test_ajustes_que_fallan_no_cierran(tmp_path):
    def roto():
        raise OSError("sin yaml")
    c, tg = _canal(tmp_path, {"t_a": {"status": "archived"}}, ajustes_cierre=roto)
    _par(c, 851, "choque", ["t_a", "t_b"], at=9 * DIA)
    assert c.cerrar_obsoletas() == 0 and not tg.edits()


def test_choque_antiguo_sin_tids_no_se_cierra_ni_con_caducidad_ni_en_la_limpieza(tmp_path):
    c, tg = _canal(tmp_path, {}, caducan_dias=1)
    _par(c, 852, "choque", [], at=0.0, nuevo=False)
    assert c.limpiar_antiguos(dry_run=False)["cerrados"] == 0
    assert len(c.pendientes()) == 1 and not tg.edits()


def test_done_integrada_por_contenido_cierra_y_sin_certeza_no(tmp_path):
    lane = type("L", (), {"repo": "/r", "base": "main", "remote": "origin"})()
    ancestro_no = lambda repo, *a: 1
    assert canal_avisos._rama_integrada([lane], "t_a", ancestro_no) is False
    assert canal_avisos._rama_integrada([lane], "t_a", ancestro_no, lambda *a: True) is True
    assert canal_avisos._rama_integrada([lane], "t_a", ancestro_no, lambda *a: False) is False
    assert canal_avisos._rama_integrada([lane], "t_a", ancestro_no, lambda *a: None) is None

    def boom(*a):
        raise RuntimeError("git")
    assert canal_avisos._rama_integrada([lane], "t_a", ancestro_no, boom) is None
    assert canal_avisos._rama_integrada([lane], "t_a", lambda repo, *a: 0, boom) is True
