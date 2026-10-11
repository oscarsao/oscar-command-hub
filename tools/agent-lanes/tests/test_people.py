"""people.py (tools/telegram/people.yaml): falla cerrado, user_id null ignorado, capacidades fijas por rol."""
from __future__ import annotations

from agent_lanes import people
from agent_lanes.people import BASE_CAPS, CARRIL, PRODUCCION, load_people

OWNER = "6744452215"


def _write(tmp_path, text):
    p = tmp_path / "people.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_versioned_people_yaml_has_no_real_ids_yet():
    ppl = load_people(OWNER)  # el fichero versionado: todos los user_id son null (TODO de Oscar)
    assert len(ppl) == 0 and ppl.lookup(OWNER).is_owner


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
