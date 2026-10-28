"""Permiso `aprobar_produccion` de people.yaml: Oscar, María y Angel en todas las marcas; falla cerrado. Sin red."""
from pathlib import Path

from agent_lanes.people import load_people

OSCAR, MARIA, ANGEL, INGERBER = 111, 5000000001, 5000000004, 5000000002


def test_el_yaml_real_deja_aprobar_a_oscar_maria_y_angel():
    real = load_people(str(OSCAR))
    assert real.puede_aprobar_produccion(OSCAR) and real.puede_aprobar_produccion(MARIA)
    assert real.puede_aprobar_produccion(ANGEL)  # aunque su `brands` no incluya MigraTeam
    assert not real.puede_en(ANGEL, "MigraTeam")  # el veto por marca del resto de comandos no cambia
    assert not real.puede_aprobar_produccion(INGERBER) and not real.puede_aprobar_produccion(999)


def test_aprobar_produccion_falla_cerrado(tmp_path):
    p = Path(tmp_path) / "p.yaml"
    p.write_text('people:\n'
                 '  a:\n    name: A\n    user_id: "8000000001"\n    role: dev\n'
                 '  b:\n    name: B\n    user_id: "8000000002"\n    role: dev\n'
                 '  c:\n    name: C\n    user_id: "8000000003"\n    role: dev\n'
                 '  d:\n    name: D\n    user_id: "8000000004"\n    role: dev\n', encoding="utf-8")
    hq = {"personas": [
        {"telegram_id": "8000000001", "permisos": {"MigraTeam": ["desarrollo.publicar"]}},  # publicar sin aprobar
        {"telegram_id": "8000000002", "permisos": {"MigraTeam": ["desarrollo.aprobar"]}},  # aprobar sin publicar
        {"telegram_id": "8000000003", "permisos": {"MigraTeam": ["desarrollo.aprobar", "desarrollo.publicar", "x"]}},
        {"telegram_id": "8000000004", "permisos": {"MigraTeam": "desarrollo.publicar"}}]}  # mal formado
    g = load_people(str(OSCAR), p, hq=lambda: hq)
    assert not g.puede_aprobar_produccion(8000000001)  # permiso sin operador: no vale
    assert not g.puede_aprobar_produccion(8000000002)  # operador sin permiso
    assert g.puede_aprobar_produccion(8000000003)
    assert not g.puede_aprobar_produccion(8000000004)  # permisos mal formados
    assert g.lookup(8000000003).permisos == frozenset({"aprobar_produccion"})
