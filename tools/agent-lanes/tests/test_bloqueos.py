"""Aviso de bloqueo con motivo real y botones por clase (specs/aviso-bloqueo.md, E1). Sin red ni Hermes real."""
from agent_lanes import bloqueos
from agent_lanes.bloqueos import ESTRUCTURAL, TRANSITORIO, clasificar, load_config, texto_publico
from agent_lanes.decisions import (DETAIL, PARK, RETRY, TO_INTEGRADOR, detail_text, keyboard_spec)
from agent_lanes.notices import next_step

CASOS = [
    ("verificación mecánica fallida: toca rutas vetadas: tools/x", ESTRUCTURAL),
    ("verificación mecánica fallida: Destino-Ops no permitido", ESTRUCTURAL),
    ("verificación mecánica fallida: rama no empujada a origin", ESTRUCTURAL),
    ("! [rejected] lane/t_1 -> lane/t_1 (non-fast-forward)", ESTRUCTURAL),
    ("CONFLICT (content): Merge conflict in a.py", ESTRUCTURAL),
    ("el worker tocó alembic/versions", ESTRUCTURAL),
    ("error: cannot lock ref 'refs/heads/x'", TRANSITORIO),
    ("el worker no terminó: timed out", TRANSITORIO),
    ("API rate limit exceeded", TRANSITORIO),
    ("runner reiniciado: la tarea quedó en running", TRANSITORIO),
]


def _actions(spec):
    return [b["action"] for row in spec for b in row]


def test_clasifica_cada_patron_por_defecto():
    for reason, clase in CASOS:
        d = clasificar(reason, load_config("no-existe.yaml"))
        assert d.clase == clase, reason
        assert d.resumen


def test_sin_patron_es_desconocido_y_conserva_el_comportamiento_de_siempre():
    d = clasificar("algo raro e imprevisto", load_config("no-existe.yaml"))
    assert d.clase == bloqueos.DESCONOCIDO and texto_publico(d) is None


def test_el_texto_publico_trae_motivo_y_lo_que_falta_sin_stderr():
    d = clasificar("toca rutas vetadas: C:\\secreto\\x.py", load_config("no-existe.yaml"))
    texto = texto_publico(d, load_config("no-existe.yaml"))
    assert "Falta:" in texto and "secreto" not in texto


def test_el_yaml_sobrescribe_patrones_textos_y_botones(tmp_path):
    p = tmp_path / "bloqueos.yaml"
    p.write_text("patrones:\n  - {clase: estructural, regex: 'zzz', resumen: 'motivo mío', que_falta: 'algo mío'}\n"
                 "textos: {falta: 'Hace falta: {que_falta}'}\nbotones: {estructural: [park, detalle]}\n",
                 encoding="utf-8")
    cfg = load_config(p)
    d = clasificar("explotó zzz", cfg)
    assert d.clase == ESTRUCTURAL and texto_publico(d, cfg) == "motivo mío · Hace falta: algo mío"
    assert clasificar("cannot lock ref", cfg).clase == bloqueos.DESCONOCIDO  # la lista del yaml sustituye a la de defecto
    assert bloqueos.botones_ids(ESTRUCTURAL, cfg) == ["park", "detalle"]


def test_sin_yaml_o_yaml_roto_valen_los_defectos(tmp_path):
    rota = tmp_path / "r.yaml"
    rota.write_text("patrones: [", encoding="utf-8")
    for p in (tmp_path / "no-existe.yaml", rota):
        assert load_config(p) == bloqueos.DEFAULTS


def test_patron_mal_formado_se_ignora_sin_romper_el_aviso(tmp_path):
    cfg = load_config("no-existe.yaml")
    cfg["patrones"] = [{"clase": "rara", "regex": "x", "resumen": "a"}, {"clase": TRANSITORIO, "regex": "(", "resumen": "b"},
                       {"clase": TRANSITORIO}, "no soy un dict",
                       {"clase": TRANSITORIO, "regex": "buen", "resumen": "el bueno"}]
    assert clasificar("x ( buen", cfg).resumen == "el bueno"


def test_estructural_nunca_ofrece_reintentar_ni_con_yaml_que_lo_pida():
    cfg = load_config("no-existe.yaml")
    cfg["botones"][ESTRUCTURAL] = ["retry", "park"]
    assert "retry" not in bloqueos.botones_ids(ESTRUCTURAL, cfg)
    cfg["botones"][ESTRUCTURAL] = ["retry"]  # solo retry: cae a los botones por defecto, sin retry
    assert bloqueos.botones_ids(ESTRUCTURAL, cfg) == ["integrador", "park", "detalle"]


def test_botones_por_clase():
    estructural = _actions(keyboard_spec("blocked", block_kind="estructural"))
    assert RETRY not in estructural and TO_INTEGRADOR in estructural and PARK in estructural and DETAIL in estructural
    transitorio = _actions(keyboard_spec("blocked", block_kind="transient"))
    assert transitorio == [RETRY, PARK, DETAIL]
    retried = _actions(keyboard_spec("blocked", block_kind="transient", retried=True))
    assert retried[:3] == [RETRY, PARK, DETAIL] and "other" in retried
    assert "other" not in _actions(keyboard_spec("blocked", block_kind="estructural", retried=True))


def test_proximo_paso_del_estructural_apunta_al_integrador():
    assert "integrador" in next_step("blocked", "estructural")


def test_detail_text_junta_motivo_y_ultimos_comentarios_y_recorta():
    show = {"events": [{"kind": "blocked", "payload": {"reason": "viejo"}},
                       {"kind": "blocked", "payload": {"reason": "motivo técnico nuevo"}}],
            "comments": [{"body": f"c{i}"} for i in range(5)]}
    cfg = load_config("no-existe.yaml")
    txt = detail_text(show, cfg)
    assert "motivo técnico nuevo" in txt and "viejo" not in txt
    assert "c4" in txt and "c2" in txt and "c1" not in txt
    cfg["umbrales"]["detalle_max"] = 30
    assert len(detail_text(show, cfg)) <= 31
    assert "Sin motivo" in detail_text({}, load_config("no-existe.yaml"))
