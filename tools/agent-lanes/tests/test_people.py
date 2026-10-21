"""people.py (tools/telegram/people.yaml): falla cerrado, user_id null ignorado, capacidades fijas por rol."""
from __future__ import annotations

from agent_lanes import people
from agent_lanes.people import BASE_CAPS, CARRIL, PRODUCCION, load_people

OWNER = "6744452215"


def _write(tmp_path, text):
    p = tmp_path / "people.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_versioned_people_yaml_is_structurally_valid():
    """Valida la ESTRUCTURA del fichero versionado, no sus ids (Oscar los rellena a mano y esto no debe romper)."""
    import yaml
    entries = yaml.safe_load(people.PEOPLE_FILE.read_text(encoding="utf-8"))["people"]
    assert entries
    for key, cfg in entries.items():
        assert cfg["role"] in ("coo", "cmo", "marketing", "dev"), key  # nunca owner ni desconocido
        assert set(cfg.get("can") or ()) <= {"carril"}, key
        assert not cfg.get("can") or cfg["role"] == "coo", key
        assert cfg["user_id"] is None or str(cfg["user_id"]).isdigit(), key
    ppl = load_people(OWNER)  # y el lector la acepta tal cual, sea cual sea el número de ids rellenados
    assert ppl.lookup(OWNER).is_owner and OWNER not in ppl.collaborator_ids()
    assert len(ppl) == sum(1 for c in entries.values() if c["user_id"] is not None)


def test_entry_without_user_id_is_ignored(tmp_path):
    p = _write(tmp_path, "people:\n  maria: {user_id: null, role: coo}\n  x: {role: cmo}\n  y: {user_id: '', role: cmo}\n")
    assert len(load_people(OWNER, p)) == 0


def test_valid_entries_and_str_normalisation(tmp_path):
    p = _write(tmp_path, "people:\n  ingerber: {name: Ingerber, user_id: 111, role: cmo, brands: [Píldora]}\n"
                         "  andrea: {user_id: '222', role: marketing}\n")
    ppl = load_people(OWNER, p)
    assert ppl.lookup(111).name == "Ingerber" and ppl.lookup("111").brands == ("Píldora",)
    assert ppl.lookup(222).role == "marketing" and ppl.lookup(333) is None


def test_production_is_never_granted_to_collaborators(tmp_path):
    p = _write(tmp_path, "people:\n  m: {user_id: 1, role: coo, can: [produccion, carril, deploy]}\n"
                         "  i: {user_id: 2, role: cmo, can: [carril, produccion]}\n")
    ppl = load_people(OWNER, p)
    for uid in (1, 2):
        assert not ppl.lookup(uid).can(PRODUCCION)
    assert ppl.lookup(1).can(CARRIL)       # coo puede activar `carril` (reservado)
    assert not ppl.lookup(2).can(CARRIL)   # cmo/marketing nunca
    assert PRODUCCION in BASE_CAPS["owner"]
    assert all(PRODUCCION not in BASE_CAPS[r] for r in ("coo", "cmo", "marketing"))
    assert not ppl.lookup(1).is_owner


def test_carril_off_by_default_for_coo(tmp_path):
    ppl = load_people(OWNER, _write(tmp_path, "people:\n  m: {user_id: 1, role: coo}\n"))
    assert not ppl.lookup(1).can(CARRIL)


def test_owner_cannot_be_declared_or_duplicated(tmp_path):
    p = _write(tmp_path, f"people:\n  a: {{user_id: {OWNER}, role: cmo}}\n  b: {{user_id: 5, role: owner}}\n"
                         "  c: {user_id: 7, role: coo}\n  d: {user_id: 7, role: cmo}\n  e: {user_id: 8, role: root}\n")
    ppl = load_people(OWNER, p)
    assert ppl.collaborator_ids() == {"7"} and ppl.lookup(7).role == "coo"
    assert ppl.lookup(OWNER).is_owner and ppl.lookup(5) is None and ppl.lookup(8) is None


def test_bad_user_ids_are_ignored(tmp_path):
    p = _write(tmp_path, "people:\n  a: {user_id: -5, role: cmo}\n  b: {user_id: true, role: cmo}\n"
                         "  c: {user_id: abc, role: cmo}\n  d: {user_id: 0, role: cmo}\n")
    assert len(load_people(OWNER, p)) == 0


def test_missing_or_broken_file_fails_closed(tmp_path):
    for text in (None, "people: [\n", "- a\n- b\n", "people: 3\n", ""):
        path = tmp_path / "x.yaml"
        if text is not None:
            path.write_text(text, encoding="utf-8")
        ppl = load_people(OWNER, path)
        assert len(ppl) == 0 and ppl.lookup(OWNER).is_owner and ppl.lookup(1) is None


def test_default_path_points_to_versioned_file():
    assert people.PEOPLE_FILE.name == "people.yaml" and people.PEOPLE_FILE.parent.name == "telegram"
    assert people.PEOPLE_FILE.exists()


def test_dev_entries_mauredy_angel_are_scoped_operators_without_id_yet():
    """Mauredy (solo MigraTeam) y Angel (OscarHQ/Signal): operadores ACOTADOS por marca (`brands` ya limita lo que
    ejecutan; ver test_operador_marca.py), pero sin user_id hasta que Oscar lo rellene, así que aún no existen."""
    import yaml
    entries = yaml.safe_load(people.PEOPLE_FILE.read_text(encoding="utf-8"))["people"]
    assert entries["mauredy"]["brands"] == ["MigraTeam"]
    assert "MigraTeam" not in entries["angel"]["brands"] and "Signal" in entries["angel"]["brands"]
    for key in ("mauredy", "angel"):
        assert entries[key]["user_id"] is None and entries[key]["role"] == "dev"
        assert entries[key].get("operador") is True  # activo: la restricción por marca ya existe
        assert not entries[key].get("can")
    assert not any(p.key in ("mauredy", "angel") for p in load_people(OWNER)._by_id.values())  # sin id = ignorados


def test_dev_role_has_no_carril_or_production_and_is_not_operator(tmp_path):
    p = _write(tmp_path, "people:\n  m:\n    user_id: '111'\n    role: dev\n    brands: [MigraTeam]\n    can: [carril]\n")
    ppl = load_people(OWNER, p)
    person = ppl.lookup("111")
    assert person.role == "dev" and not person.can(CARRIL) and not person.can(PRODUCCION)
    assert not ppl.es_operador("111") and person.brands == ("MigraTeam",)
